import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_flex import accounts, integration, service
from claude_flex.compiler import cli_settings, model_alias
from claude_flex.store import FlexError, Store, read_json, write_json
from tests.helpers import token
from tests.test_claude import claude_credential


class NativeSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "state")
        self.store.init()
        self.path = Path(self.temp.name) / ".claude/settings.json"
        accounts.import_account(self.store, "gpt", accounts.normalize({"access": token()}))
        accounts.import_account(self.store, "claude", accounts.normalize(claude_credential()))
        service.add_model(self.store, "sol", "codex", "gpt-6.1-sol", "GPT-6.1 Sol")
        service.add_model(self.store, "luna", "codex", "gpt-6-luna", "GPT-6 Luna")
        service.add_model(self.store, "sonnet", "claude-oauth", "claude-sonnet-5-5", "Claude Sonnet")

    def test_native_picker_has_real_model_ids_and_no_sonnet_impersonation(self):
        accounts.select_accounts(self.store, "gpt")
        settings = cli_settings(self.store)
        self.assertNotIn("model", settings)
        self.assertEqual([row["model"] for row in settings["modelPicker"]["options"]], ["gpt-6.1-sol", "gpt-6-luna"])
        self.assertNotIn("ANTHROPIC_DEFAULT_SONNET_MODEL", settings["env"])
        self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL", settings["env"])
        self.assertEqual(settings["availableModels"], ["gpt-6.1-sol", "gpt-6-luna"])
        self.assertTrue(settings["enforceAvailableModels"])

    def test_apply_preserves_native_model_choice_and_unrelated_user_settings(self):
        accounts.select_accounts(self.store, "gpt")
        write_json(self.path, {"model": "gpt-6-luna", "theme": "dark", "permissions": {"defaultMode": "default"}, "env": {"PROJECT_FLAG": "yes", "ANTHROPIC_DEFAULT_SONNET_MODEL": model_alias("sol")}})
        integration.apply(self.store, self.path)
        value = read_json(self.path)
        self.assertEqual(value["model"], "gpt-6-luna")
        self.assertEqual(value["theme"], "dark")
        self.assertEqual(value["env"]["PROJECT_FLAG"], "yes")
        self.assertNotIn("ANTHROPIC_DEFAULT_SONNET_MODEL", value["env"])
        self.assertTrue((self.store.home / "backups/claude-settings-original.json").exists())

    def test_legacy_alias_migrates_to_genuine_gpt_id(self):
        accounts.select_accounts(self.store, "gpt")
        write_json(self.path, {"model": model_alias("sol")})
        integration.apply(self.store, self.path)
        self.assertEqual(read_json(self.path)["model"], "gpt-6.1-sol")

    def test_switch_provider_updates_menu_and_native_default(self):
        accounts.select_accounts(self.store, "gpt")
        integration.apply(self.store, self.path)
        accounts.select_accounts(self.store, "claude")
        integration.refresh_if_connected(self.store)
        value = read_json(self.path)
        self.assertEqual(value["model"], "claude-sonnet-5-5")
        self.assertEqual([row["model"] for row in value["modelPicker"]["options"]], ["claude-sonnet-5-5"])

    def test_plain_claude_launch_has_no_injected_model_flag(self):
        accounts.select_accounts(self.store, "gpt")
        integration.apply(self.store, self.path)
        with patch("claude_flex.service.shutil.which", return_value="/fake/claude"), patch("claude_flex.service.ensure_backend"), patch("claude_flex.service.sync_models"), patch("claude_flex.service.Gateway"), patch("claude_flex.service.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "2.1.302 (Claude Code)"
            service.run_claude(self.store, args=("--version",))
        self.assertEqual(run.call_args.args[0], ["/fake/claude", "--version"])
        self.assertNotIn("ANTHROPIC_MODEL", run.call_args.kwargs["env"])

    def test_unsupported_native_version_does_not_modify_connection_or_account(self):
        with patch("claude_flex.service.shutil.which", return_value="/fake/claude"), patch("claude_flex.service.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "2.1.100 (Claude Code)"
            with self.assertRaises(FlexError):
                service.activate_account(self.store, "gpt", settings_path=self.path)
        self.assertFalse(self.path.exists())
        self.assertIsNone(self.store.load().get("selected_account"))


if __name__ == "__main__":
    unittest.main()
