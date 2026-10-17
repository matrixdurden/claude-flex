import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_flex import accounts, service
from claude_flex.compiler import choose, compile_gateway, model_alias
from claude_flex.store import FlexError, Store, read_json
from claude_flex.usage import fetch_usage, normalize_claude_usage
from tests.helpers import mock_catalog, token


def claude_credential(identity="claude-account"):
    return {"type": "claude", "access_token": "synthetic-claude-access", "refresh_token": "synthetic-claude-refresh", "account_uuid": identity, "email": "example@invalid.test", "expired": "2099-01-01T00:00:00Z"}


class ClaudeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.store.init()
        catalog = patch("claude_flex.service.discover", side_effect=lambda store, provider, **options: mock_catalog(provider))
        catalog.start()
        self.addCleanup(catalog.stop)

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

    def test_automatic_models_skip_existing_upstream_and_preserve_custom_names(self):
        accounts.import_account(self.store, "claude", accounts.normalize(claude_credential()))
        service.add_model(self.store, "my-sonnet", "claude-oauth", "claude-sonnet-5-5", "My Sonnet")
        service.add_model(self.store, "opus", "codex", "some-custom-model", "Custom")
        service.default_account_models(self.store, "claude")
        service.default_account_models(self.store, "claude")
        models = self.store.load()["models"]
        self.assertEqual(models["opus"]["id"], "some-custom-model")
        self.assertTrue(any(model["provider"] == "claude-oauth" and model["id"] == "claude-opus-5-5" for model in models.values()))
        self.assertEqual(len([model for model in models.values() if model["provider"] == "claude-oauth"]), 3)
        self.assertEqual(self.store.load()["active"], "my-sonnet")

    def test_automatic_account_names_need_no_user_input_and_do_not_collide(self):
        self.assertEqual(service.next_account_name(self.store, "claude"), "claude")
        accounts.import_account(self.store, "claude", accounts.normalize(claude_credential()))
        self.assertEqual(service.next_account_name(self.store, "claude"), "claude-2")
        self.assertEqual(service.next_account_name(self.store, "codex"), "chatgpt")

    def test_duplicate_models_merge_without_breaking_running_aliases(self):
        service.add_model(self.store, "plus", "codex", "gpt-6.1-sol", "GPT (ChatGPT Plus)")
        self.store.mutate(lambda config: config["models"].update({"gpt": {**config["models"]["plus"], "label": "GPT-6.1 Sol"}}))
        service.merge_duplicate_models(self.store)
        config = self.store.load()
        self.assertEqual(list(config["models"]), ["plus"])
        self.assertEqual(config["active"], "plus")
        self.assertEqual(config["models"]["plus"]["label"], "GPT-6.1 Sol")
        self.assertEqual(choose(config, "gpt")[0], "plus")
        aliases = compile_gateway(self.store)["oauth"]["model-alias"]["codex"]
        self.assertEqual({item["alias"] for item in aliases}, {model_alias("plus"), model_alias("gpt")})
        service.merge_duplicate_models(self.store)
        self.assertEqual(config, self.store.load())

    def test_same_model_cannot_be_added_twice_but_distinct_sources_are_kept(self):
        service.add_model(self.store, "one", "codex", "shared-id")
        with self.assertRaises(FlexError):
            service.add_model(self.store, "duplicate", "codex", "shared-id")
        service.add_model(self.store, "other", "claude-oauth", "shared-id")
        service.add_model(self.store, "context", "codex", "shared-id", context=32768)
        service.merge_duplicate_models(self.store)
        self.assertEqual(len(self.store.load()["models"]), 3)


if __name__ == "__main__":
    unittest.main()
