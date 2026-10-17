import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_flex import desktop, service
from claude_flex.compiler import desktop_profile
from claude_flex.store import Store, read_json, write_json


class DesktopLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "state")
        self.store.init()
        self.library = Path(self.temp.name) / "library"
        service.add_model(self.store, "gpt", "codex", "gpt-6.1-sol", "GPT-6.1 Sol")

    def test_first_launch_applies_and_activates_profile_before_opening(self):
        with patch("claude_flex.desktop.running_instance", return_value=None), patch("claude_flex.desktop.launch") as launch:
            def verify(store):
                meta = read_json(self.library / "_meta.json")
                profile = read_json(self.library / (meta["appliedId"] + ".json"))
                self.assertEqual(profile, desktop_profile(store))
            launch.side_effect = verify
            restart = desktop.open_profile(self.store, self.library)
        self.assertFalse(restart)
        launch.assert_called_once_with(self.store)

    def test_unchanged_running_app_is_focused_without_restart_hint(self):
        desktop.apply(self.store, self.library, activate=True)
        with patch("claude_flex.desktop.running_instance", return_value="same-process"), patch("claude_flex.desktop.launch") as launch:
            self.assertFalse(desktop.open_profile(self.store, self.library))
        launch.assert_called_once()

    def test_stale_profile_remains_pending_until_app_actually_restarts(self):
        path = desktop.apply(self.store, self.library, activate=True)
        write_json(path, {"inferenceProvider": "gateway", "inferenceGatewayBaseUrl": "http://127.0.0.1:1"})
        with patch("claude_flex.desktop.running_instance", return_value="old-process"), patch("claude_flex.desktop.launch"):
            self.assertTrue(desktop.open_profile(self.store, self.library))
            self.assertTrue(desktop.open_profile(self.store, self.library))
        with patch("claude_flex.desktop.running_instance", return_value="new-process"), patch("claude_flex.desktop.launch"):
            self.assertFalse(desktop.open_profile(self.store, self.library))
        self.assertFalse((self.store.runtime / "desktop-restart.json").exists())

    def test_other_desktop_profiles_are_preserved(self):
        original = {"id": "00000000-0000-0000-0000-000000000001", "name": "Other"}
        write_json(self.library / "_meta.json", {"appliedId": original["id"], "entries": [original]})
        with patch("claude_flex.desktop.running_instance", return_value=None), patch("claude_flex.desktop.launch"):
            desktop.open_profile(self.store, self.library)
        meta = read_json(self.library / "_meta.json")
        self.assertIn(original, meta["entries"])
        self.assertNotEqual(meta["appliedId"], original["id"])


if __name__ == "__main__":
    unittest.main()
