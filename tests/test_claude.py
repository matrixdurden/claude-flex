import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_flex import accounts, service
from claude_flex.compiler import compile_gateway, model_alias
from claude_flex.store import FlexError, Store, read_json
from claude_flex.usage import fetch_usage, normalize_claude_usage
from tests.helpers import token


def claude_credential(identity="claude-account"):
    return {"type": "claude", "access_token": "synthetic-claude-access", "refresh_token": "synthetic-claude-refresh", "account_uuid": identity, "email": "example@invalid.test", "expired": "2099-01-01T00:00:00Z"}


class ClaudeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.store.init()

    def test_claude_normalization_preserves_identity_and_refresh_ownership(self):
        snapshot = accounts.normalize(claude_credential())
        owned = accounts.normalize(claude_credential(), with_refresh=True)
        self.assertEqual(snapshot["type"], "claude")
        self.assertEqual(snapshot["refresh_token"], "")
        self.assertEqual(owned["refresh_token"], "synthetic-claude-refresh")

    def test_claude_and_codex_are_independent_account_pools(self):
        accounts.import_account(self.store, "claude-one", accounts.normalize(claude_credential()))
        accounts.import_account(self.store, "claude-two", accounts.normalize(claude_credential("second")))
        accounts.import_account(self.store, "gpt", accounts.normalize({"access": token()}))
        accounts.select_accounts(self.store, "claude-two")
        config = self.store.load()
        self.assertFalse(config["accounts"]["claude-one"]["enabled"])
        self.assertTrue(config["accounts"]["claude-two"]["enabled"])
        self.assertTrue(config["accounts"]["gpt"]["enabled"])

    def test_claude_oauth_aliases_never_route_to_codex(self):
        service.add_model(self.store, "opus", "claude-oauth", "claude-opus-5-5")
        service.add_model(self.store, "gpt", "codex", "gpt-6.1-sol")
        aliases = compile_gateway(self.store)["oauth"]["model-alias"]
        self.assertEqual(aliases["claude"][0]["alias"], model_alias("opus"))
        self.assertEqual(aliases["codex"][0]["alias"], model_alias("gpt"))
        self.assertTrue(all(item["name"] == "claude-opus-5-5" for item in aliases["claude"]))

    def test_renewal_requires_same_identity_and_preserves_prior_credential_on_failure(self):
        original = accounts.normalize(claude_credential(), with_refresh=True)
        accounts.import_account(self.store, "main", original)
        with self.assertRaises(FlexError):
            accounts.import_account(self.store, "main", accounts.normalize(claude_credential("other")), replace=True)
        renewed = {**original, "access_token": "renewed-synthetic"}
        accounts.import_account(self.store, "main", renewed, replace=True)
        self.assertEqual(read_json(self.store.account_path("main"))["access_token"], "renewed-synthetic")

    def test_claude_usage_keeps_additional_windows_and_absent_values(self):
        payload = {"five_hour": {"utilization": 42.5, "resets_at": "2026-10-20T12:00:00Z"}, "seven_day": {"utilization": 16, "resets_at": None}, "seven_day_sonnet": {"utilization": 10, "resets_at": None}, "extra_usage": {"is_enabled": True}, "unrelated": {"utilization": 99}}
        result = normalize_claude_usage(payload)
        self.assertEqual(len(result["windows"]), 3)
        self.assertEqual(result["windows"][0]["remaining_percent"], 57.5)
        self.assertEqual(result["windows"][1]["duration_seconds"], 604800)
        self.assertIsNone(result["windows"][1]["resets_at"])
        self.assertEqual(result["windows"][2]["scope"], "Sonnet")

    def test_claude_account_uses_claude_usage_endpoint(self):
        with patch("claude_flex.usage.fetch_claude_usage", return_value={"windows": []}) as fetch:
            fetch_usage(claude_credential())
        fetch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
