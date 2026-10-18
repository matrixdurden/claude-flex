import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_flex import accounts, integration, profiles, service
from claude_flex.store import FlexError, Store, read_json, write_json
from tests.helpers import token


class ProfileIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "flex")
        self.store.init()
        self.normal = Path(self.temp.name) / "normal/.claude/settings.json"
        write_json(self.normal, {"model": "opus", "theme": "light", "permissions": {"defaultMode": "manual"}})
        for label in ("one", "two"):
            accounts.import_account(self.store, label, accounts.normalize({"access": token(label)}))
        service.add_model(self.store, "gpt", "codex", "gpt-6.1-sol", "GPT-6.1 Sol")

    def test_accounts_get_different_gateways_and_config_directories(self):
        one, two = profiles.prepare(self.store, "one"), profiles.prepare(self.store, "two")
        self.assertNotEqual(one.home, two.home)
        self.assertNotEqual(one.url(), two.url())
        self.assertNotEqual(profiles.environment(one)["CLAUDE_CONFIG_DIR"], profiles.environment(two)["CLAUDE_CONFIG_DIR"])
        self.assertEqual(set(one.load()["accounts"]), {"one"})
        self.assertEqual(set(two.load()["accounts"]), {"two"})
        self.assertEqual(set(self.store.load()["accounts"]), {"one", "two"})

    def test_switching_does_not_stop_or_disable_other_accounts(self):
        one = profiles.prepare(self.store, "one")
        integration.apply(one)
        original = integration.settings_path(one).read_bytes()
        with patch("claude_flex.gateway.Gateway.stop") as stop:
            accounts.select_accounts(self.store, "two")
        stop.assert_not_called()
        two = profiles.prepare(self.store, "two")
        integration.apply(two)
        self.assertTrue(self.store.load()["accounts"]["one"]["enabled"])
        self.assertEqual(original, integration.settings_path(one).read_bytes())
        self.assertEqual(read_json(self.normal)["model"], "opus")

    def test_credentials_have_one_authoritative_owner_and_survive_reopen(self):
        one = profiles.prepare(self.store, "one")
        canonical = self.store.account_path("one")
        self.assertEqual(one.account_path("one"), canonical)
        self.assertFalse((self.store.auth / "one.json").exists())
        value = read_json(canonical)
        value["refresh_token"] = "synthetic-refreshed-token"
        write_json(canonical, value)
        reopened = profiles.prepare(self.store, "one")
        self.assertEqual(reopened.url(), one.url())
        self.assertEqual(read_json(reopened.account_path("one"))["refresh_token"], "synthetic-refreshed-token")

    def test_legacy_live_gateway_is_not_stopped_for_migration(self):
        with patch("claude_flex.profiles.Gateway") as gateway:
            gateway.return_value.owned_pid.return_value = 123
            with self.assertRaises(FlexError):
                profiles.prepare(self.store, "one")
        gateway.return_value.stop.assert_not_called()
        self.assertTrue(self.store.account_path("one").exists())

    def test_inherited_normal_account_auth_and_profile_are_not_used(self):
        profile = profiles.prepare(self.store, "one")
        with patch.dict("os.environ", {"ANTHROPIC_AUTH_TOKEN": "normal-login", "ANTHROPIC_MODEL": "opus", "CLAUDE_CONFIG_DIR": "/normal/profile"}):
            env = profiles.environment(profile)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        self.assertNotIn("ANTHROPIC_MODEL", env)
        self.assertNotEqual(env["CLAUDE_CONFIG_DIR"], "/normal/profile")

    def test_renaming_a_migrated_account_preserves_canonical_credentials(self):
        profiles.prepare(self.store, "one")
        path = self.store.account_path("one")
        accounts.rename_account(self.store, "one", "renamed")
        self.assertEqual(path, self.store.account_path("renamed"))
        self.assertTrue(path.exists())
        self.assertEqual(read_json(path)["label"], "renamed")

    def test_renewing_a_migrated_account_preserves_credentials_and_replaces_source(self):
        source = Path(self.temp.name) / "source.json"
        write_json(source, {"openai": {"type": "oauth", "access": token("one")}})
        self.store.mutate(lambda current: current["accounts"]["one"].update(source={"kind": "opencode", "path": str(source)}))
        profile = profiles.prepare(self.store, "one")
        other = profiles.prepare(self.store, "two")
        canonical = self.store.account_path("one")
        other_credential = other.account_path("two").read_bytes()
        renewed = token("one", expiry=9999999999)
        accounts.import_account(self.store, "one", accounts.normalize({"access": renewed}), replace=True)
        self.assertEqual(self.store.account_path("one"), canonical)
        self.assertFalse((self.store.auth / "one.json").exists())
        self.assertIsNone(profile.load()["accounts"]["one"]["source"])
        self.assertEqual(accounts.sync_sources(profile), [])
        self.assertEqual(read_json(canonical)["access_token"], renewed)
        self.assertEqual(other.account_path("two").read_bytes(), other_credential)


if __name__ == "__main__":
    unittest.main()
