"""Nonblocking browser/device OAuth jobs for the terminal interface."""

from __future__ import annotations

import re
import secrets
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import accounts
from .store import FlexError, Store, name, read_json
from .usage import NoRedirect

FINISHED = {"complete", "failed", "cancelled"}


class LoginManager:
    def __init__(self, store: Store, process_factory=None, timeout=600):
        self.store = store
        self.factory = process_factory or subprocess.Popen
        self.timeout = timeout
        self.lock = threading.RLock()
        self.jobs = {}
        self.threads = []
        self.closed = False

    def start(self, label: str, *, method="browser", weight=1, priority=0, provider="codex", replace=False) -> dict:
        name(label)
        if label == "all" or label in self.store.load()["accounts"] and not replace:
            raise FlexError("This account name is already in use or reserved.")
        if method not in {"browser", "device"}:
            raise FlexError("Unknown sign-in method.")
        if provider not in {"codex", "claude"} or provider == "claude" and method == "device":
            raise FlexError("Choose browser sign-in for Claude.")
        if type(weight) is not int or not 1 <= weight <= 1_000_000 or type(priority) is not int:
            raise FlexError("Invalid account weight or priority.")
        with self.lock:
            if self.closed:
                raise FlexError("Sign-in manager is closing.")
            if any(job["status"] not in FINISHED or (job.get("process") and job["process"].poll() is None) for job in self.jobs.values()):
                raise FlexError("Complete or cancel the current sign-in first.")
            # Keep a small bounded history; completed authorization URLs are cleared.
            if len(self.jobs) >= 20:
                self.jobs.pop(next(iter(self.jobs)))
            job_id = uuid.uuid4().hex
            self.jobs[job_id] = {
                "id": job_id, "name": label, "method": method, "status": "starting",
                "url": None, "device_code": None, "error": None, "created_at": time.time(),
                "process": None, "provider": provider, "replace": replace,
            }
            thread = threading.Thread(target=self._run, args=(job_id, weight, priority), daemon=True)
            self.threads.append(thread)
            thread.start()
            return self.get(job_id)

    def get(self, job_id: str) -> dict:
        with self.lock:
            if job_id not in self.jobs:
                raise FlexError("Sign-in job not found.")
            return {key: value for key, value in self.jobs[job_id].items() if key != "process"}

    def _capture(self, job_id: str, line: str):
        with self.lock:
            job = self.jobs[job_id]
            if job["status"] in FINISHED:
                return
            for candidate in re.findall(r'https://[^\s<>"\x1b]+', line):
                parsed = urlsplit(candidate)
                if (parsed.hostname == "auth.openai.com" and parsed.path in {"/oauth/authorize", "/codex/device", "/activate"}) or (parsed.hostname in {"claude.ai", "console.anthropic.com"} and parsed.path == "/oauth/authorize"):
                    job["url"] = candidate
                    job["status"] = "waiting"
            code = re.search(r"Codex device code:\s*([A-Z0-9-]+)", line)
            if code:
                job["device_code"] = code[1]
                job["status"] = "waiting"

    def _finish(self, job_id, status, error=None):
        with self.lock:
            job = self.jobs[job_id]
            job.update(status=status, error=error, url=None, device_code=None, process=None)

    def _run(self, job_id: str, weight: int, priority: int):
        timer = None
        process = None
        try:
            with tempfile.TemporaryDirectory(prefix="oauth-login-", dir=self.store.home) as directory:
                root = Path(directory)
                job = self.get(job_id)
                args, auth = accounts.prepare_login(self.store, root, device=job["method"] == "device", no_browser=True, provider=job["provider"])
                process = self.factory(args, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                with self.lock:
                    if self.jobs[job_id]["status"] == "cancelled":
                        process.terminate()
                    self.jobs[job_id]["process"] = process
                timer = threading.Timer(self.timeout, self.cancel, args=(job_id,))
                timer.daemon = True
                timer.start()
                for line in process.stdout:
                    if self.get(job_id)["status"] != "cancelled":
                        self._capture(job_id, line)
                returncode = process.wait()
                if self.get(job_id)["status"] == "cancelled":
                    self._finish(job_id, "cancelled")
                    return
                files = list(auth.glob("*.json"))
                if returncode or len(files) != 1:
                    raise FlexError("Sign-in did not complete. Try again, using the browser sign-in method.")
                with self.lock:
                    self.jobs[job_id]["status"] = "importing"
                    accounts.import_account(self.store, job["name"], accounts.normalize(read_json(files[0]), with_refresh=True, provider=job["provider"]), weight=weight, priority=priority, replace=job["replace"])
                    self._finish(job_id, "complete")
        except FlexError as exc:
            self._finish(job_id, "failed", str(exc))
        except Exception:
            self._finish(job_id, "failed", "Sign-in could not start. Check the gateway and the local callback port.")
        finally:
            if timer:
                timer.cancel()
            if process:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                if process.stdout:
                    process.stdout.close()
                if process.stdin:
                    process.stdin.close()

    def cancel(self, job_id: str):
        with self.lock:
            if job_id not in self.jobs:
                raise FlexError("Sign-in job not found.")
            job = self.jobs[job_id]
            if job["status"] in FINISHED:
                return
            job["status"] = "cancelled"
            job["url"] = None
            process = job.get("process")
            if process and process.poll() is None:
                process.terminate()

    def callback(self, job_id: str, value: str):
        job = self.get(job_id)
        parsed = urlsplit(value)
        port = 54545 if job["provider"] == "claude" else 1455
        path = "/callback" if job["provider"] == "claude" else "/auth/callback"
        if job["method"] != "browser" or job["status"] != "waiting" or not job["url"]:
            raise FlexError("This job is not waiting for a browser callback.")
        if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.port != port or parsed.path != path or parsed.username or parsed.password:
            raise FlexError(f"Paste the provider's localhost:{port}{path} callback URL.")
        expected = parse_qs(urlsplit(job["url"]).query).get("state", [""])[0]
        actual = parse_qs(parsed.query).get("state", [""])[0]
        if not expected or not secrets.compare_digest(expected, actual):
            raise FlexError("The callback does not match this sign-in attempt.")
        request = urllib.request.Request(f"http://127.0.0.1:{port}{path}?" + parsed.query)
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=10):
                pass
        except urllib.error.HTTPError as exc:
            if exc.code != 302:
                raise FlexError("The sign-in callback could not be processed.") from None
        except OSError:
            raise FlexError("The local OAuth callback server is unreachable.") from None

    def close(self):
        with self.lock:
            self.closed = True
            for job_id in list(self.jobs):
                self.cancel(job_id)
        for thread in self.threads:
            thread.join(timeout=5)
