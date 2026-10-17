import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from claude_flex.accounts import import_account, normalize, remove_account
from claude_flex.store import FlexError, Store
from claude_flex.usage import UsageService, fetch_usage, normalize_usage
from tests.helpers import token


def usage_payload():
    return {
        "plan_type": "plus",
        "rate_limit": {"allowed": True, "limit_reached": False, "primary_window": {"used_percent": 71, "limit_window_seconds": 18000, "reset_at": 2000000000}, "secondary_window": {"used_percent": 15, "limit_window_seconds": 604800, "reset_after_seconds": 86400}},
        "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
        "account_id": "must-not-leave-the-server", "access_token": "must-not-leave-the-server",
    }


class UsageTests(unittest.TestCase):
    def test_real_schema_windows_percentages_and_resets(self):
        value = normalize_usage(usage_payload(), now=1900000000)
        self.assertEqual(value["plan"], "plus")
        self.assertEqual([window["title"] for window in value["windows"]], ["5 saat", "7 gün"])
        self.assertEqual([window["remaining_percent"] for window in value["windows"]], [29, 85])
        self.assertEqual(value["windows"][1]["resets_at"], 1900086400)
        self.assertNotIn("must-not-leave-the-server", str(value))

    def test_missing_windows_do_not_become_unlimited_or_zero(self):
        value = normalize_usage({"plan_type": "plus", "rate_limit": {"primary_window": {}}})
        self.assertIsNone(value["allowed"])
        self.assertIsNone(value["windows"][0]["remaining_percent"])
        self.assertIsNone(value["credits"])

    def test_additional_and_code_review_limits_are_separate_scopes(self):
        payload = usage_payload()
        payload["code_review_rate_limit"] = {"primary_window": {"used_percent": 5}}
        payload["additional_rate_limits"] = [{"details": {"limit_name": "Model reserve", "rate_limit": {"primary_window": {"used_percent": 90}}}}]
        scopes = [window["scope"] for window in normalize_usage(payload)["windows"]]
        self.assertIn("Kod incelemesi", scopes)
        self.assertIn("Model reserve", scopes)

    def test_invalid_numeric_values_are_unknown_not_fake_capacity(self):
        for bad in (True, "90", float("nan"), -1):
            value = normalize_usage({"rate_limit": {"primary_window": {"used_percent": bad}}})
            self.assertIsNone(value["windows"][0]["remaining_percent"])

    def test_usage_http_errors_never_echo_credentials(self):
        failure = urllib.error.HTTPError("https://chatgpt.com", 401, "no", {}, io.BytesIO(b"echoed-secret"))
        with patch("claude_flex.usage.urllib.request.build_opener") as opened:
            opened.return_value.open.side_effect = failure
            with self.assertRaises(FlexError) as error:
                fetch_usage({"access_token": "private-token", "account_id": "private-id"})
        self.assertNotIn("private", str(error.exception))
        self.assertNotIn("echoed", str(error.exception))

    def test_cache_throttles_and_preserves_last_success_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.init()
            import_account(store, "main", normalize({"access": token()}))
            service = UsageService(store, ttl=60, min_interval=5)
            with patch("claude_flex.usage.fetch_usage", return_value=normalize_usage(usage_payload())) as fetch:
                first = service.one("main")
                second = service.one("main", force=True)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(first, second)
            service.attempted["main"] = -10000
            with patch("claude_flex.usage.fetch_usage", side_effect=FlexError("Temporarily unavailable")):
                stale = service.one("main", force=True)
            self.assertTrue(stale["stale"])
            self.assertEqual(stale["windows"], first["windows"])
            self.assertEqual(stale["checked_at"], first["checked_at"])

    def test_reusing_label_for_another_account_does_not_reuse_its_quota(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.init()
            import_account(store, "main", normalize({"access": token("one")}))
            service = UsageService(store)
            with patch("claude_flex.usage.fetch_usage", return_value=normalize_usage(usage_payload())) as fetch:
                service.one("main")
                remove_account(store, "main")
                import_account(store, "main", normalize({"access": token("two")}))
                self.assertEqual(service.cached(), {})
                service.one("main")
            self.assertEqual(fetch.call_count, 2)


if __name__ == "__main__":
    unittest.main()
