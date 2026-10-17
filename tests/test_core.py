import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_flex import accounts, desktop
from claude_flex.cli import add_model, parser
from claude_flex.compiler import cli_settings, compile_gateway, desktop_profile, model_alias
from claude_flex.providers import add_key, add_provider, discover
from claude_flex.store import FlexError, Store, read_json, validate, write_json
from tests.helpers import token


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "private")
        self.store.init()

    def model(self, label="plus"):
        add_model(self.store, label, "codex", "test-upstream" if label == "plus" else f"test-upstream-{label}", "Test model")

    def test_init_is_idempotent_and_credentials_are_separate(self):
        secret = self.store.secrets()["gateway_key"]
        self.store.init()
        self.assertEqual(secret, self.store.secrets()["gateway_key"])
        self.assertNotIn(secret, self.store.config_path.read_text())
        if os.name != "nt":
            self.assertEqual(self.store.config_path.stat().st_mode & 0o777, 0o600)

    def test_path_traversal_and_invalid_ports_are_rejected(self):
        with self.assertRaises(FlexError):
            self.store.account_path("../oops")
        with self.assertRaises(FlexError):
            self.store.mutate(lambda config: config["gateway"].update(port=0))
        self.assertEqual(self.store.load()["gateway"]["port"], 8318)

    def test_failed_mutation_does_not_corrupt_config(self):
        with self.assertRaises(FlexError):
            self.store.mutate(lambda config: config.update(active="missing"))
        self.assertIsNone(self.store.load()["active"])

    def test_checked_in_example_is_valid(self):
        validate(read_json(Path(__file__).parents[1] / "examples/config.json"))

    def test_codex_and_background_aliases_keep_original_model(self):
        self.model()
        config = compile_gateway(self.store)
        aliases = config["oauth"]["model-alias"]["codex"]
        self.assertIn(model_alias("plus"), [item["alias"] for item in aliases])
        self.assertTrue(all(item["name"] == "test-upstream" and item["fork"] for item in aliases))

    def test_profiles_do_not_change_when_default_changes(self):
        self.model("one")
        self.model("two")
        before = cli_settings(self.store)
        self.store.mutate(lambda config: config.update(active="two"))
        self.assertEqual(before, cli_settings(self.store))
        self.assertNotIn("model", before)
        self.assertEqual([row["model"] for row in before["modelPicker"]["options"]], ["test-upstream-one", "test-upstream-two"])
        self.assertNotIn("ANTHROPIC_DEFAULT_SONNET_MODEL", before["env"])
        self.assertNotEqual(model_alias("one"), model_alias("two"))
        self.assertEqual({row["name"] for row in desktop_profile(self.store)["inferenceModels"]}, {"test-upstream-one", "test-upstream-two"})

    def test_provider_key_pool_compiles_without_secret_in_public_config(self):
        add_provider(self.store, "router", preset="openrouter")
        add_key(self.store, "router", "first", value="synthetic-first-key", weight=3)
        add_key(self.store, "router", "second", env="TEST_FLEX_SECOND")
        add_model(self.store, "free", "router", "vendor/free-model:free")
        with patch.dict(os.environ, {"TEST_FLEX_SECOND": "synthetic-second-key"}):
            value = compile_gateway(self.store)
        group = value["api-keys"]["openai-compatibility"][0]
        self.assertEqual(group["keys"][0]["weight"], 3)
        self.assertEqual(len(group["keys"]), 2)
        self.assertEqual(group["models"][0]["name"], "vendor/free-model:free")
        self.assertNotIn("synthetic-first-key", self.store.config_path.read_text())

    def test_missing_env_fails_without_echoing_credential(self):
        add_provider(self.store, "api", preset="openai")
        add_key(self.store, "api", "first", env="TEST_FLEX_ABSENT")
        add_model(self.store, "api", "api", "test")
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(FlexError):
            compile_gateway(self.store)

    def test_local_no_auth_and_other_protocols(self):
        for label, preset, kind in (("local", "ollama", "openai-compatibility"), ("api", "openai", "codex"), ("claude", "anthropic", "claude"), ("gemini", "gemini", "gemini")):
            add_provider(self.store, label, preset=preset)
            if preset != "ollama":
                add_key(self.store, label, "key", value="synthetic-key")
            add_model(self.store, label, label, "upstream")
            self.assertIn(kind, compile_gateway(self.store)["api-keys"])

    def test_free_catalog_requires_both_zero_prices_and_declared_tools(self):
        payload = {"data": [
            {"id": "free-tools", "pricing": {"prompt": "0", "completion": "0"}, "supported_parameters": ["tools"]},
            {"id": "free-no-tools", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "paid-tools", "pricing": {"prompt": "0", "completion": "1"}, "supported_parameters": ["tools"]},
            {"id": "unknown", "supported_parameters": ["tools"]},
        ]}
        with patch("claude_flex.providers.request_json", return_value=payload):
            rows = discover(self.store, "openrouter", free=True, tools=True)
        self.assertEqual([row["id"] for row in rows], ["free-tools"])

    def test_import_strips_refresh_and_rejects_duplicate_account(self):
        data = accounts.normalize({"access": token(), "refresh": "do-not-copy"})
        self.assertEqual(data["refresh_token"], "")
        accounts.import_account(self.store, "main", data)
        with self.assertRaises(FlexError):
            accounts.import_account(self.store, "duplicate", data)
        self.assertNotIn("do-not-copy", self.store.account_path("main").read_text())

    def test_multiple_accounts_toggle_and_weights(self):
        for label in ("one", "two"):
            accounts.import_account(self.store, label, accounts.normalize({"access": token(label)}))
        accounts.update_account(self.store, "two", enabled=False, weight=3, priority=2)
        data = read_json(self.store.account_path("two"))
        self.assertTrue(data["disabled"])
        self.assertEqual(data["weight"], 3)
        self.assertEqual(data["priority"], 2)
        self.assertEqual(len(accounts.account_rows(self.store)), 2)

    def test_linked_sync_updates_access_but_rejects_account_change(self):
        source = Path(self.temp.name) / "source.json"
        write_json(source, {"openai": {"type": "oauth", "access": token("one"), "refresh": "never-copy"}})
        accounts.import_account(self.store, "main", accounts.opencode_data(source), source={"kind": "opencode", "path": str(source)})
        updated = token("one", expiry=9999999999)
        write_json(source, {"openai": {"type": "oauth", "access": updated}})
        self.assertEqual(accounts.sync_sources(self.store), [])
        self.assertEqual(read_json(self.store.account_path("main"))["access_token"], updated)
        write_json(source, {"openai": {"type": "oauth", "access": token("another")}})
        self.assertEqual(len(accounts.sync_sources(self.store)), 1)
        self.assertEqual(read_json(self.store.account_path("main"))["access_token"], updated)

    def test_expired_import_rejected(self):
        with self.assertRaises(FlexError):
            accounts.normalize({"access": token(expiry=1)})

    def test_codex_cli_nested_tokens_import(self):
        value = accounts.normalize({"tokens": {"access_token": token(), "refresh_token": "test-refresh"}}, with_refresh=True)
        self.assertEqual(value["refresh_token"], "test-refresh")

    def test_desktop_apply_preserves_user_profiles_and_is_idempotent(self):
        self.model()
        library = Path(self.temp.name) / "library"
        existing = "00000000-0000-0000-0000-000000000001"
        write_json(library / "_meta.json", {"appliedId": existing, "entries": [{"id": existing, "name": "Personal"}]})
        path = desktop.apply(self.store, library)
        meta = read_json(library / "_meta.json")
        self.assertEqual(meta["appliedId"], existing)
        self.assertEqual(len(meta["entries"]), 2)
        self.assertEqual(path, desktop.apply(self.store, library, activate=True))
        self.assertEqual(read_json(library / "_meta.json")["appliedId"], path.stem)
        self.assertEqual(read_json(path)["inferenceModels"][0]["labelOverride"], "Test model")

    def test_cli_passthrough_and_secret_not_in_parser(self):
        args = parser().parse_args(["run", "--account", "main", "--", "-p", "hello", "--max-turns", "2"])
        self.assertEqual(args.args, ["--", "-p", "hello", "--max-turns", "2"])
        self.assertNotIn("--api-key", parser().format_help())


if __name__ == "__main__":
    unittest.main()
