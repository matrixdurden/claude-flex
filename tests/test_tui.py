import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from textual.widgets import Input, OptionList

from claude_flex import accounts, service
from claude_flex.store import Store
from claude_flex.tui import Choice, FlexApp, Form, percent
from tests.helpers import token


class FakeUsage:
    def cached(self):
        return {"main": {"windows": [{"duration_seconds": 18000, "title": "5h", "remaining_percent": 29, "resets_at": 2000000000}, {"duration_seconds": 604800, "title": "7d", "remaining_percent": 85, "resets_at": 2000000000}], "stale": False}}

    def all(self):
        return self.cached()


class TuiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.store.init()
        accounts.import_account(self.store, "main", accounts.normalize({"access": token()}))
        service.add_model(self.store, "plus", "codex", "synthetic-model", "GPT / Plus")

    async def test_two_tabs_account_limits_and_keyboard_toggle(self):
        app = FlexApp(self.store, usage=FakeUsage())
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            self.assertEqual(app.section, "models")
            await pilot.press("tab")
            await pilot.pause()
            self.assertEqual(app.section, "accounts")
            self.assertEqual(app.current(), "main")
            text = str(app.listing.get_option_at_index(0).prompt)
            self.assertIn("29%", text)
            self.assertIn("85%", text)
            self.assertIn("29%", app.export_screenshot())
            self.assertIn("main", app.export_screenshot())
            await pilot.press("space")
            await pilot.pause(0.3)
            self.assertFalse(self.store.load()["accounts"]["main"]["enabled"])
            await pilot.press("tab")
            await pilot.pause()
            self.assertEqual(app.section, "models")

    async def test_account_add_flow_is_inside_terminal_and_escape_returns(self):
        app = FlexApp(self.store, usage=FakeUsage())
        async with app.run_test() as pilot:
            await pilot.press("tab", "a")
            await pilot.pause()
            self.assertIsInstance(app.screen, Choice)
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, Form)
            await pilot.press("w", "o", "r", "k")
            self.assertEqual(app.screen.query_one(Input).value, "work")
            await pilot.press("escape")
            await pilot.pause()
            self.assertTrue(app.main_screen())

    async def test_model_form_can_be_completed_without_shell_commands(self):
        app = FlexApp(self.store, usage=FakeUsage())
        async with app.run_test() as pilot:
            await pilot.press("a")
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

    async def test_first_run_starts_on_empty_accounts_without_setup_commands(self):
        store = Store(Path(self.temp.name) / "fresh")
        store.init()
        app = FlexApp(store, usage=FakeUsage())
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertEqual(app.section, "accounts")
            await pilot.press("a")
            await pilot.pause()
            self.assertIsInstance(app.screen, Choice)

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
