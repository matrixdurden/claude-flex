import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_flex import accounts, service
from claude_flex.providers import discover_oauth, normalize_catalog, oauth_catalog
from claude_flex.store import FlexError, Store, read_json, write_json
from tests.helpers import token


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "state")
        self.store.init()
        accounts.import_account(self.store, "one", accounts.normalize({"access": token("one")}))

    def test_catalog_is_dynamic_and_filters_non_conversation_models(self):
        data = {"models": [
            {"slug": "new-model", "display_name": "New Model", "context_window": 512000},
            {"slug": "new-model", "display_name": "Duplicate"},
            {"slug": "retired", "visibility": "hide"},
            {"slug": "gpt-image-new", "output_modalities": ["image"]},
            {"slug": "image-only", "output_modalities": ["image"]},
        ]}
        self.assertEqual(normalize_catalog(data), [{"id": "new-model", "label": "New Model", "context": 512000}])
        with self.assertRaises(FlexError):
            normalize_catalog({"error": "not a catalog"})

    def test_codex_queries_authenticated_upstream_without_inference(self):
        credential = accounts.normalize({"access": token()})
        with patch("claude_flex.providers.request_json", return_value={"models": [{"slug": "fresh-model"}]}) as request:
            result = oauth_catalog(credential)
        self.assertEqual(result[0]["id"], "fresh-model")
        self.assertIn("/backend-api/codex/models?client_version=", request.call_args.args[0])
        self.assertEqual(request.call_args.kwargs["headers"]["ChatGPT-Account-Id"], credential["account_id"])
        self.assertNotIn("body", request.call_args.kwargs)

    def test_claude_catalog_follows_after_id_pagination(self):
        pages = [{"data": [{"id": "claude-new", "display_name": "Claude New"}], "has_more": True, "last_id": "claude-new"}, {"data": [{"id": "claude-next"}], "has_more": False}]
        with patch("claude_flex.providers.request_json", side_effect=pages) as request:
            result = oauth_catalog({"type": "claude", "access_token": "synthetic"})
        self.assertEqual([item["id"] for item in result], ["claude-new", "claude-next"])
        self.assertIn("after_id=claude-new", request.call_args.args[0])

    def test_accounts_are_unioned_and_failed_account_does_not_hide_other_models(self):
        accounts.import_account(self.store, "two", accounts.normalize({"access": token("two")}))
        with patch("claude_flex.providers.oauth_catalog", side_effect=[[{"id": "a"}], [{"id": "a"}, {"id": "b"}]]):
            self.assertEqual([item["id"] for item in discover_oauth(self.store, "codex")], ["a", "b"])
        with patch("claude_flex.providers.oauth_catalog", side_effect=[FlexError("expired"), [{"id": "b"}]]):
            self.assertEqual(discover_oauth(self.store, "codex"), [{"id": "b"}])

    def test_automatic_sync_keeps_existing_profiles_and_uses_live_context(self):
        service.add_model(self.store, "favorite", "codex", "already-there", "My Model", 32768)
        catalog = [{"id": "already-there", "label": "Changed upstream label", "context": 500000}, {"id": "brand-new", "label": "Brand New", "context": 512000}]
        with patch("claude_flex.service.discover", return_value=catalog) as discover:
            first = service.sync_models(self.store)
            second = service.sync_models(self.store)
        self.assertEqual(discover.call_count, 1)
        self.assertEqual(len(first["added"]), 1)
        self.assertEqual(second["added"], [])
        config = self.store.load()
        self.assertEqual(config["active"], "favorite")
        self.assertEqual(config["models"]["favorite"]["context"], 32768)
        self.assertEqual(config["models"]["brand-new"]["context"], 512000)

    def test_failed_catalog_does_not_delete_existing_models(self):
        service.add_model(self.store, "favorite", "codex", "existing")
        before = self.store.load()["models"]
        with patch("claude_flex.service.discover", side_effect=FlexError("Unavailable")):
            result = service.sync_models(self.store, force=True)
        self.assertIn("codex", result["errors"])
        self.assertEqual(self.store.load()["models"], before)

    def test_new_account_bypasses_catalog_cache_without_manual_refresh(self):
        with patch("claude_flex.service.discover", return_value=[{"id": "a"}]) as discover:
            service.sync_models(self.store)
            accounts.import_account(self.store, "two", accounts.normalize({"access": token("two")}))
            service.sync_models(self.store)
        self.assertEqual(discover.call_count, 2)

    def test_saved_opencode_accounts_link_individually_without_copying_refresh(self):
        root = Path(self.temp.name) / "saved"
        point = {"openai": {"type": "oauth", "access": token("one"), "refresh": "keep-owned-by-opencode"}}
        other = {"openai": {"type": "oauth", "access": token("two"), "refresh": "keep-owned-by-opencode"}}
        write_json(root / "point.json", point)
        write_json(root / "other.json", other)
        write_json(root / "excluded.json", {"openai": {"type": "oauth", "access": token("excluded")}})
        with patch("claude_flex.service.discover", return_value=[{"id": "live-model"}]):
            service.import_opencode(self.store, "point", root / "point.json")
            service.import_opencode(self.store, "other", root / "other.json")
        self.assertEqual(set(self.store.load()["accounts"]), {"point", "other"})
        self.assertFalse(self.store.account_path("one").exists())
        self.assertEqual(read_json(self.store.account_path("point"))["refresh_token"], "")
        self.assertEqual(read_json(root / "point.json"), point)
        self.assertEqual(self.store.load()["accounts"]["other"]["source"]["path"], str((root / "other.json").resolve()))


if __name__ == "__main__":
    unittest.main()
