import tempfile
import unittest
from pathlib import Path

from claude_flex import service
from claude_flex.compiler import cli_settings
from claude_flex.migration import restore_legacy_settings
from claude_flex.store import Store, read_json, write_json


class LegacyMigrationTests(unittest.TestCase):
    def test_only_flex_owned_settings_are_restored_and_new_user_preferences_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "flex")
            store.init()
            service.add_model(store, "gpt", "codex", "gpt-6.1-sol")
            store.mutate(lambda config: config.update(permission_mode="bypassPermissions"))
            normal = Path(directory) / "normal/settings.json"
            original = {"model": "opus", "theme": "dark"}
            write_json(store.home / "backups/claude-settings-original.json", {"path": str(normal), "settings": original})
            current = {**cli_settings(store), "model": "gpt-6.1-sol", "theme": "light", "permissions": {"defaultMode": "bypassPermissions", "deny": ["Edit(secret)"]}, "skipDangerousModePermissionPrompt": True}
            current["env"]["USER_SETTING"] = "keep"
            write_json(normal, current)
            self.assertEqual(restore_legacy_settings(store), [str(normal)])
            value = read_json(normal)
            self.assertEqual(value["model"], "opus")
            self.assertEqual(value["theme"], "light")
            self.assertEqual(value["env"], {"USER_SETTING": "keep"})
            self.assertEqual(value["permissions"], {"deny": ["Edit(secret)"]})
            self.assertNotIn("modelPicker", value)
            write_json(normal, {"model": "sonnet"})
            self.assertEqual(restore_legacy_settings(store), [])
            self.assertEqual(read_json(normal), {"model": "sonnet"})

    def test_different_current_gateway_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "flex")
            store.init()
            normal = Path(directory) / "normal/settings.json"
            write_json(store.home / "backups/claude-settings-original.json", {"path": str(normal), "settings": {}})
            value = {"env": {"ANTHROPIC_BASE_URL": "https://user-gateway.invalid"}, "model": "opus"}
            write_json(normal, value)
            restore_legacy_settings(store)
            self.assertEqual(read_json(normal), value)
