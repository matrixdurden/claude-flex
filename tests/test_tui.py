import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from textual.widgets import Input, OptionList, Tabs

from claude_flex import accounts, service
from claude_flex.store import Store
from claude_flex.tui import Choice, Form, LoginDialog, percent
from claude_flex.tui import FlexApp as BaseFlexApp
from tests.helpers import mock_catalog, token
from tests.test_claude import claude_credential


class FakeUsage:
    def cached(self):
        return {"main": {"windows": [{"duration_seconds": 18000, "title": "5h", "remaining_percent": 29, "resets_at": 2000000000}, {"duration_seconds": 604800, "title": "7d", "remaining_percent": 85, "resets_at": 2000000000}], "stale": False}}

    def all(self):
        return self.cached()


class FlexApp(BaseFlexApp):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("catalog_sync", lambda *args, **kwargs: {"added": [], "errors": {}})
        super().__init__(*args, **kwargs)


class FakeLogin:
    def __init__(self, store, *, complete=True):
        self.store = store
        self.complete = complete
        self.started = []
        self.job = None

    def start(self, label, **options):
        self.started.append({"label": label, **options})
        self.job = {"id": "test-job", "name": label, "provider": options["provider"], "method": options["method"], "status": "starting", "url": None, "device_code": None}
        return dict(self.job)

    def get(self, _):
        if self.complete and self.job["status"] == "starting":
            provider = self.job["provider"]
            credential = accounts.normalize(claude_credential() if provider == "claude" else {"access": token("new-account")})
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
        for target in ("claude_flex.service.discover", "claude_flex.tui.discover"):
            catalog = patch(target, side_effect=lambda store, provider, **options: mock_catalog(provider))
            catalog.start()
            self.addCleanup(catalog.stop)

    async def test_models_and_accounts_share_one_page_with_keyboard_navigation(self):
        app = FlexApp(self.store, usage=FakeUsage())
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            self.assertEqual(app.section, "models")
            self.assertFalse(list(app.query(Tabs)))
            self.assertIn("GPT", app.export_screenshot())
            self.assertIn("29%", app.export_screenshot())
            await pilot.press("down")
            await pilot.pause()
            self.assertEqual(app.section, "accounts")
            self.assertEqual(app.current(), "main")
            text = str(app.listing.get_option_at_index(app.listing.highlighted).prompt)
            self.assertIn("29%", text)
            self.assertIn("85%", text)
            self.assertIn("29%", app.export_screenshot())
            self.assertIn("main", app.export_screenshot())
            await pilot.press("space")
            await pilot.pause(0.3)
            self.assertFalse(self.store.load()["accounts"]["main"]["enabled"])
            await pilot.press("up")
            await pilot.pause()
            self.assertEqual(app.section, "models")

    async def test_account_add_flow_is_inside_terminal_and_escape_returns(self):
        app = FlexApp(self.store, usage=FakeUsage())
        async with app.run_test() as pilot:
            await pilot.press("a")
            await pilot.pause()
            self.assertIsInstance(app.screen, Choice)
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, Choice)
            self.assertEqual(app.screen.title_text, "Claude sign-in")
            self.assertFalse(list(app.screen.query(Input)))
            await pilot.press("escape")
            await pilot.pause()
            self.assertTrue(app.main_screen())

    async def test_claude_sign_in_adds_models_without_any_form(self):
        logins = FakeLogin(self.store)
        app = FlexApp(self.store, usage=FakeUsage(), logins=logins)
        async def backend():
            pass
        app.backend = backend
        async with app.run_test() as pilot:
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("enter")  # Claude
            await pilot.pause()
            await pilot.press("enter")  # Browser
            await pilot.pause(0.8)
            self.assertTrue(app.main_screen())
            self.assertEqual(app.section, "models")
            self.assertIn("claude", self.store.load()["accounts"])
            self.assertEqual(logins.started[0]["label"], "claude")
            models = self.store.load()["models"]
            self.assertEqual({model["label"] for model in models.values() if model["provider"] == "claude-oauth"}, {"Claude Opus", "Claude Sonnet", "Claude Haiku"})
            self.assertIn("Claude", app.export_screenshot())

    async def test_chatgpt_exposes_device_code_and_auto_names_account(self):
        logins = FakeLogin(self.store)
        app = FlexApp(self.store, usage=FakeUsage(), logins=logins)
        async def backend():
            pass
        app.backend = backend
        async with app.run_test() as pilot:
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("down", "enter")  # ChatGPT
            await pilot.pause()
            self.assertEqual([key for key, _ in app.screen.choices], ["browser", "link", "device", "import"])
            await pilot.press("down", "down", "enter")
            await pilot.pause(0.8)
            self.assertEqual(logins.started[0]["method"], "device")
            self.assertIn("chatgpt", self.store.load()["accounts"])
            self.assertEqual(app.section, "models")
            self.assertEqual(len([model for model in self.store.load()["models"].values() if model["provider"] == "codex"]), 4)

    async def test_link_method_displays_link_without_opening_browser(self):
        logins = FakeLogin(self.store, complete=False)
        app = FlexApp(self.store, usage=FakeUsage(), logins=logins)
        async def backend():
            pass
        app.backend = backend
        with patch("claude_flex.tui.service.open_browser") as browser:
            async with app.run_test() as pilot:
                await pilot.press("a")
                await pilot.pause()
                await pilot.press("enter")  # Claude
                await pilot.pause()
                await pilot.press("down", "enter")  # Link
                await pilot.pause(0.6)
                self.assertIsInstance(app.screen, LoginDialog)
                self.assertFalse(app.screen.open_automatically)
                browser.assert_not_called()
                await pilot.press("escape")
                await pilot.pause()
                self.assertTrue(app.main_screen())
                self.assertNotIn("claude", self.store.load()["accounts"])

    async def test_model_form_can_be_completed_without_shell_commands(self):
        app = FlexApp(self.store, usage=FakeUsage())
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            await pilot.press("enter")  # Connect another model/provider
            await pilot.pause()
            await pilot.press("enter")  # Codex source
            await pilot.pause()
            await pilot.press("enter")  # GPT model
            await pilot.pause()
            self.assertIsInstance(app.screen, Form)
            app.screen.query_one("#name", Input).value = "new-model"
            await pilot.press("enter", "enter", "enter")
            await pilot.pause()
            self.assertIn("new-model", self.store.load()["models"])

    async def test_small_terminal_can_open_settings_and_return(self):
        app = FlexApp(self.store, usage=FakeUsage())
        async with app.run_test(size=(58, 22)) as pilot:
            await pilot.press("s")
            await pilot.pause()
            self.assertIsInstance(app.screen, Choice)
            await pilot.press("escape")
            await pilot.pause()
            self.assertTrue(app.main_screen())
            self.assertIsInstance(app.focused, OptionList)

    async def test_first_run_can_connect_without_tabs_or_setup_commands(self):
        store = Store(Path(self.temp.name) / "fresh")
        store.init()
        app = FlexApp(store, usage=FakeUsage())
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertFalse(list(app.query(Tabs)))
            self.assertIsNone(app.current())
            await pilot.press("a")
            await pilot.pause()
            self.assertIsInstance(app.screen, Choice)

    async def test_duplicate_gpt_connections_are_one_visible_model(self):
        self.store.mutate(lambda config: config["models"].update({"duplicate": {**config["models"]["plus"], "label": "Same GPT"}}))
        app = FlexApp(self.store, usage=FakeUsage())
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertEqual(len([kind for kind, _ in app.entries.values() if kind == "models"]), 1)
            self.assertEqual(len(self.store.load()["models"]), 1)
            self.assertEqual(self.store.load()["active"], "plus")

    async def test_live_catalog_is_loaded_automatically_on_startup(self):
        app = BaseFlexApp(self.store, usage=FakeUsage())
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            models = self.store.load()["models"]
            self.assertEqual(len(models), 4)
            self.assertIn("GPT-6.1 Sol", app.export_screenshot().replace("&#160;", " "))
            self.assertEqual(self.store.load()["active"], "plus")

    async def test_enter_hands_selected_profile_to_real_launcher(self):
        launch = Mock(return_value=0)
        app = FlexApp(self.store, usage=FakeUsage(), launch=launch)
        # Textual's headless driver has no terminal to suspend, so validate the
        # handoff using a no-op suspend context while retaining the full event path.
        from contextlib import nullcontext

        app.suspend = lambda: nullcontext()
        async def backend():
            pass
        app.backend = backend
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await pilot.pause(0.2)
        launch.assert_called_once_with(self.store, "plus")


class DisplayTests(unittest.TestCase):
    def test_unknown_and_stale_limits_are_not_fabricated(self):
        self.assertEqual(percent(None, 18000), "—")
        self.assertEqual(percent({"windows": []}, 18000), "—")
        self.assertEqual(percent({"windows": [{"duration_seconds": 18000, "remaining_percent": 0}], "stale": True}, 18000), "0%*")
