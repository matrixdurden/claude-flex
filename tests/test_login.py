import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_flex.login import LoginManager
from claude_flex.store import FlexError, Store, read_json
from tests.helpers import token


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.store.init()
        self.store.mutate(lambda config: config["gateway"].update(binary=sys.executable))

    def manager(self, mode="success", provider="codex"):
        def factory(args, **kwargs):
            data = {"access_token": token("new-account"), "refresh_token": "synthetic-owned-refresh"}
            if provider == "claude":
                data = {"type": "claude", "access_token": "synthetic-access", "refresh_token": "synthetic-owned-refresh", "account_uuid": "test-account", "email": "test@example.invalid"}
            url = "https://claude.ai/oauth/authorize?state=synthetic-state" if provider == "claude" else "https://auth.openai.com/oauth/authorize?state=synthetic-state"
            script = f"import json,sys,time; from pathlib import Path; print({url!r}, flush=True); "
            if mode == "success":
                script += f"time.sleep(0.1); Path('auth/login.json').write_text({json.dumps(json.dumps(data))})"
            elif mode == "wait":
                script += "time.sleep(60)"
            else:
                script += "print('access_token=synthetic-secret-echo', flush=True); sys.exit(1)"
            return subprocess.Popen([sys.executable, "-u", "-c", script], **kwargs)
        manager = LoginManager(self.store, process_factory=factory, timeout=10)
        self.addCleanup(manager.close)
        return manager

    def wait_for(self, manager, job_id, statuses):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = manager.get(job_id)
            if job["status"] in statuses:
                return job
            time.sleep(0.01)
        self.fail("OAuth job did not reach expected state")

    def test_background_login_imports_owned_credentials_and_clears_url(self):
        manager = self.manager()
        job = manager.start("second")
        done = self.wait_for(manager, job["id"], {"complete", "failed"})
        self.assertEqual(done["status"], "complete", done.get("error"))
        self.assertIsNone(done["url"])
        self.assertNotIn("process", done)
        self.assertNotIn("synthetic-owned-refresh", str(done))
        self.assertEqual(read_json(self.store.account_path("second"))["refresh_token"], "synthetic-owned-refresh")

    def test_cancel_terminates_pending_login_without_importing(self):
        manager = self.manager("wait")
        job = manager.start("second")
        self.wait_for(manager, job["id"], {"waiting"})
        with self.assertRaises(FlexError):
            manager.start("third")
        manager.cancel(job["id"])
        manager.close()
        self.assertEqual(manager.get(job["id"])["status"], "cancelled")
        self.assertFalse(self.store.account_path("second").exists())

    def test_backend_error_output_is_not_exposed_to_browser(self):
        manager = self.manager("fail")
        job = manager.start("second")
        done = self.wait_for(manager, job["id"], {"failed"})
        self.assertNotIn("synthetic-secret-echo", str(done))

    def test_claude_browser_login_imports_into_claude_pool(self):
        manager = self.manager(provider="claude")
        job = manager.start("claude", provider="claude")
        done = self.wait_for(manager, job["id"], {"complete", "failed"})
        self.assertEqual(done["status"], "complete", done.get("error"))
        self.assertEqual(self.store.load()["accounts"]["claude"]["provider"], "claude")
        self.assertEqual(read_json(self.store.account_path("claude"))["type"], "claude")

    def test_claude_callback_uses_its_own_callback_port(self):
        manager = self.manager("wait", provider="claude")
        job = manager.start("claude", provider="claude")
        self.wait_for(manager, job["id"], {"waiting"})
        with patch("claude_flex.login.urllib.request.build_opener") as opened:
            manager.callback(job["id"], "http://localhost:54545/callback?state=synthetic-state&code=test")
            request = opened.return_value.open.call_args.args[0]
            self.assertTrue(request.full_url.startswith("http://127.0.0.1:54545/callback?"))

    def test_callback_checks_local_destination_and_oauth_state(self):
        manager = self.manager("wait")
        job = manager.start("second")
        self.wait_for(manager, job["id"], {"waiting"})
        with patch("claude_flex.login.urllib.request.build_opener") as opened:
            for value in ("https://evil.invalid/auth/callback?state=synthetic-state", "http://localhost:1455/auth/callback?state=wrong&code=test"):
                with self.assertRaises(FlexError):
                    manager.callback(job["id"], value)
            opened.assert_not_called()
            manager.callback(job["id"], "http://localhost:1455/auth/callback?state=synthetic-state&code=test")
            request = opened.return_value.open.call_args.args[0]
            self.assertTrue(request.full_url.startswith("http://127.0.0.1:1455/auth/callback?"))


if __name__ == "__main__":
    unittest.main()
