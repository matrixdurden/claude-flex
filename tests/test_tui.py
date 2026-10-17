import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

from textual.widgets import Input, OptionList, Tabs

from claude_flex import accounts, service
from claude_flex.store import FlexError, Store
from claude_flex.tui import Choice, LoginDialog, percent
from claude_flex.tui import FlexApp as BaseFlexApp
from tests.helpers import mock_catalog, token
from tests.test_claude import claude_credential


class FakeUsage:
    def cached(self):
        return {"main": {"windows": [{"scope": "Codex", "duration_seconds": 18000, "title": "5h", "remaining_percent": 29, "resets_at": 2000000000}, {"scope": "Codex", "duration_seconds": 604800, "title": "7d", "remaining_percent": 85, "resets_at": 2000000000}], "stale": False}}

    def all(self):
        return self.cached()


def fake_activate(store, label):
    accounts.select_accounts(store, label)
    return {"settings_path": "test-only", "catalog_errors": {}}


class FlexApp(BaseFlexApp):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("catalog_sync", lambda *args, **kwargs: {"added": [], "errors": {}})
        kwargs.setdefault("activate", fake_activate)
        kwargs.setdefault("launch", Mock(return_value=0))
        kwargs.setdefault("desktop_launch", Mock(return_value=False))
        super().__init__(*args, **kwargs)

    def suspend(self):
        return nullcontext()


class FakeLogin:
    def __init__(self, store, *, complete=True):
        self.store, self.complete = store, complete
        self.started = []
        self.job = None

    def start(self, label, **options):
        self.started.append({"label": label, **options})
        self.job = {"id": "test-job", "name": label, "provider": options["provider"], "method": options["method"], "status": "starting", "url": None, "device_code": None}
        return dict(self.job)

    def get(self, _):
        if self.complete and self.job["status"] == "starting":
            credential = accounts.normalize(claude_credential() if self.job["provider"] == "claude" else {"access": token("new-account")})
            accounts.import_account(self.store, self.job["name"], credential)
            self.job["status"] = "complete"
        elif not self.complete:
            self.job.update(status="waiting", url="https://auth.openai.com/oauth/authorize?state=test")
        return dict(self.job)

    def cancel(self, _):
        self.job["status"] = "cancelled"


class TuiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.store.init()
        accounts.import_account(self.store, "main", accounts.normalize({"access": token()}))
        service.add_model(self.store, "plus", "codex", "synthetic-model", "GPT / Plus")
        catalog = patch("claude_flex.service.discover", side_effect=lambda store, provider, **options: mock_catalog(provider))
        catalog.start()
        self.addCleanup(catalog.stop)

    async def test_only_accounts_and_limits_are_visible(self):
        app = FlexApp(self.store, usage=FakeUsage())
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            self.assertFalse(list(app.query(Tabs)))
            self.assertEqual(list(app.entries.values()), [("accounts", "main")])
            svg = app.export_screenshot()
            self.assertIn("main", svg)
            self.assertIn("29%", svg)
            self.assertIn("85%", svg)
            self.assertNotIn("GPT / Plus", svg.replace("&#160;", " "))

    async def test_enter_selects_account_then_opens_terminal_without_selecting_a_model(self):
        activate = Mock(side_effect=fake_activate)
        launch = Mock(return_value=0)
        calls = Mock()
        calls.attach_mock(activate, "activate")
        calls.attach_mock(launch, "launch")
        app = FlexApp(self.store, usage=FakeUsage(), activate=activate, launch=launch)
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause(0.3)
            self.assertTrue(app.main_screen())
        activate.assert_called_once_with(self.store, "main")
        launch.assert_called_once_with(self.store)
        self.assertEqual([call[0] for call in calls.mock_calls], ["activate", "launch"])
        self.assertEqual(self.store.load()["selected_account"], "main")
        self.assertEqual(self.store.load()["active"], "plus")

    async def test_desktop_key_selects_account_and_opens_desktop_not_terminal(self):
        activate = Mock(side_effect=fake_activate)
        launch = Mock(return_value=0)
        desktop = Mock(return_value=False)
        app = FlexApp(self.store, usage=FakeUsage(), activate=activate, launch=launch, desktop_launch=desktop)
        async with app.run_test() as pilot:
            await pilot.press("d")
            await pilot.pause(0.3)
            self.assertTrue(app.main_screen())
        activate.assert_called_once_with(self.store, "main")
        desktop.assert_called_once_with(self.store)
        launch.assert_not_called()

    async def test_desktop_restart_hint_is_short_and_only_shown_when_needed(self):
        app = FlexApp(self.store, usage=FakeUsage(), desktop_launch=Mock(return_value=True))
        async with app.run_test() as pilot:
            await pilot.press("d")
            await pilot.pause(0.3)
            self.assertIn("Restart", app.export_screenshot())
            self.assertNotIn("Open claude", app.export_screenshot().replace("&#160;", " "))

    async def test_terminal_return_leaves_no_instruction_message(self):
        app = FlexApp(self.store, usage=FakeUsage())
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause(0.3)
            screenshot = app.export_screenshot().replace("&#160;", " ")
            self.assertNotIn("Open claude", screenshot)
            self.assertNotIn("selected.", screenshot)
            self.assertNotIn("choose your model", screenshot)
            self.assertNotIn("reset unknown", screenshot)

    async def test_arrow_navigation_selects_another_account(self):
        accounts.import_account(self.store, "work", accounts.normalize({"access": token("work")}))
        app = FlexApp(self.store, usage=FakeUsage())
        async with app.run_test() as pilot:
            await pilot.press("down", "enter")
            await pilot.pause(0.3)
            self.assertEqual(self.store.load()["selected_account"], "work")
            self.assertFalse(self.store.load()["accounts"]["main"]["enabled"])

    async def test_account_add_flow_requires_no_account_name_form(self):
        app = FlexApp(self.store, usage=FakeUsage())
        async with app.run_test() as pilot:
            await pilot.press("a", "enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, Choice)
            self.assertEqual(app.screen.title_text, "Claude sign-in")
            self.assertFalse(list(app.screen.query(Input)))
            await pilot.press("escape")
            await pilot.pause()
            self.assertTrue(app.main_screen())

    async def test_claude_sign_in_selects_account_and_keeps_models_out_of_flex(self):
        logins = FakeLogin(self.store)
        app = FlexApp(self.store, usage=FakeUsage(), logins=logins)
        async def backend():
            pass
        app.backend = backend
        async with app.run_test() as pilot:
            await pilot.press("a", "enter", "enter")
            await pilot.pause(0.8)
            self.assertTrue(app.main_screen())
            self.assertEqual(self.store.load()["selected_account"], "claude")
            self.assertTrue(all(kind == "accounts" for kind, _ in app.entries.values()))
            self.assertEqual(logins.started[0]["label"], "claude")
            self.assertEqual(len([model for model in self.store.load()["models"].values() if model["provider"] == "claude-oauth"]), 3)

    async def test_chatgpt_device_code_flow_is_available(self):
        logins = FakeLogin(self.store)
        app = FlexApp(self.store, usage=FakeUsage(), logins=logins)
        async def backend():
            pass
        app.backend = backend
        async with app.run_test() as pilot:
            await pilot.press("a", "down", "enter")
            await pilot.pause()
            self.assertEqual([key for key, _ in app.screen.choices], ["browser", "link", "device", "import"])
            await pilot.press("down", "down", "enter")
            await pilot.pause(0.8)
            self.assertEqual(logins.started[0]["method"], "device")
            self.assertEqual(self.store.load()["selected_account"], "chatgpt")

    async def test_link_flow_does_not_open_browser_automatically(self):
        app = FlexApp(self.store, usage=FakeUsage(), logins=FakeLogin(self.store, complete=False))
        async def backend():
            pass
        app.backend = backend
        with patch("claude_flex.tui.service.open_browser") as browser:
            async with app.run_test() as pilot:
                await pilot.press("a", "enter", "down", "enter")
                await pilot.pause(0.6)
                self.assertIsInstance(app.screen, LoginDialog)
                browser.assert_not_called()
                await pilot.press("escape")
                await pilot.pause()
                self.assertTrue(app.main_screen())

    async def test_account_details_remain_available_in_settings(self):
        app = FlexApp(self.store, usage=FakeUsage())
        async with app.run_test(size=(58, 22)) as pilot:
            await pilot.press("s", "enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, Choice)
            self.assertEqual(app.screen.title_text, "main")
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.focused, OptionList)

    async def test_failed_account_activation_is_reported(self):
        app = FlexApp(self.store, usage=FakeUsage(), activate=Mock(side_effect=FlexError("Sign-in expired")))
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause(0.2)
            self.assertIn("Sign-in", app.export_screenshot())
            self.assertIsNone(self.store.load().get("selected_account"))
            app.launch.assert_not_called()

    async def test_live_catalog_updates_backend_not_account_screen(self):
        app = BaseFlexApp(self.store, usage=FakeUsage())
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            self.assertEqual(len(self.store.load()["models"]), 4)
            self.assertEqual(len(app.entries), 1)
            self.assertEqual(app.current(), "main")


class DisplayTests(unittest.TestCase):
    def test_unknown_and_stale_limits_are_not_fabricated(self):
        self.assertEqual(percent(None, 18000), "—")
        self.assertEqual(percent({"windows": []}, 18000), "—")
        self.assertEqual(percent({"windows": [{"duration_seconds": 18000, "remaining_percent": 0}], "stale": True}, 18000), "0%*")
