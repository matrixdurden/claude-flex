import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_flex import desktop, service
from claude_flex.compiler import desktop_profile
from claude_flex.store import FlexError, Store, read_json, write_json


class DesktopLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "state")
        self.store.init()
        root = self.store.home / "desktop"
        self.place = desktop.Layout(root, root / "Home", root / "Local", root / "Roaming", root / "Data", root / "Data/configLibrary")
        for target, value in (("claude_flex.desktop.account_profile", self.store), ("claude_flex.desktop.layout", self.place)):
            mocked = patch(target, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)
        service.add_model(self.store, "gpt", "codex", "gpt-6.1-sol", "GPT-6.1 Sol")

    def test_private_profile_is_applied_before_opening(self):
        with patch("claude_flex.desktop.launch") as launch:
            def verify(store):
                meta = read_json(self.place.library / "_meta.json")
                profile = read_json(self.place.library / (meta["appliedId"] + ".json"))
                self.assertEqual(profile, desktop_profile(store))
            launch.side_effect = verify
            self.assertFalse(desktop.open_profile(self.store))
        launch.assert_called_once_with(self.store)

    def test_normal_desktop_library_is_never_written(self):
        normal = Path(self.temp.name) / "normal/configLibrary"
        write_json(normal / "_meta.json", {"appliedId": "original", "entries": []})
        with self.assertRaises(FlexError):
            desktop.apply(self.store, normal, activate=True)
        self.assertEqual(read_json(normal / "_meta.json")["appliedId"], "original")

    def test_private_native_settings_do_not_use_global_claude_home(self):
        desktop.apply(self.store)
        settings = read_json(self.place.home / ".claude/settings.json")
        self.assertIn("modelPicker", settings)
        self.assertEqual(settings["env"]["ANTHROPIC_BASE_URL"], self.store.url())
        self.assertFalse((Path(self.temp.name) / ".claude").exists())

    def test_missing_desktop_does_not_open_or_reconfigure_normal_application(self):
        with patch("claude_flex.desktop.installed_executable", return_value=None), patch("claude_flex.desktop.Gateway") as gateway:
            with self.assertRaises(FlexError):
                desktop.launch(self.store)
        gateway.assert_not_called()

    def test_desktop_route_ids_are_compatible_but_labels_stay_honest(self):
        service.add_model(self.store, "gpt-next", "codex", "gpt-6-luna", "GPT-6 Luna")
        models = desktop_profile(self.store)["inferenceModels"]
        self.assertEqual([model["labelOverride"] for model in models], ["GPT-6.1 Sol", "GPT-6 Luna"])
        self.assertTrue(all("gpt" not in model["name"] for model in models))


if __name__ == "__main__":
    unittest.main()
