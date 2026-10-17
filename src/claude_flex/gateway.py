"""Pinned backend installation and ownership-checked local process lifecycle."""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import platform
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

from .compiler import render
from .providers import opener, request_json
from .store import FlexError, Store, file_lock, read_json, write_json

BACKEND_VERSION = "8.0.32"
REPOSITORY = "router-for-me/CLIProxyAPI"
_CHILDREN: dict[int, subprocess.Popen] = {}


def executable_name() -> str:
    return "cli-proxy-api.exe" if os.name == "nt" else "cli-proxy-api"


def install(store: Store, version: str = BACKEND_VERSION) -> Path:
    import re

    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise FlexError("Backend version must be a pinned version such as 8.0.32.")
    system = {"Linux": "linux", "Darwin": "darwin", "Windows": "windows"}.get(platform.system())
    arch = {"x86_64": "amd64", "AMD64": "amd64", "arm64": "aarch64", "aarch64": "aarch64"}.get(platform.machine())
    if not system or not arch:
        raise FlexError("No release for this platform. Build CLIProxyAPI and use 'gateway binary PATH'.")
    release = request_json(f"https://api.github.com/repos/{REPOSITORY}/releases/tags/v{version}")
    suffix = "zip" if system == "windows" else "tar.gz"
    asset_name = f"CLIProxyAPI_{version}_{system}_{arch}.{suffix}"
    asset = next((item for item in release.get("assets", []) if item["name"] == asset_name), None)
    if not asset:
        raise FlexError(f"Release asset not found: {asset_name}")
    digest = asset.get("digest", "")
    if not digest.startswith("sha256:"):
        raise FlexError("Release has no SHA-256 digest; refusing an unverified binary.")
    try:
        with opener(asset["browser_download_url"]).open(asset["browser_download_url"], timeout=120) as response:
            content = response.read()
    except OSError:
        raise FlexError("Backend download failed. Retry 'gateway install'.") from None
    if hashlib.sha256(content).hexdigest() != digest.removeprefix("sha256:"):
        raise FlexError("Backend checksum mismatch.")
    filename = executable_name()
    if suffix == "zip":
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            binary = archive.read(filename)
    else:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
            member = archive.getmember(filename)
            if not member.isfile():
                raise FlexError("Backend archive does not contain a regular executable.")
            binary = archive.extractfile(member).read()
    target = store.home / "bin" / filename
    target.parent.mkdir(mode=0o700, exist_ok=True)
    temporary = target.with_suffix(".download")
    temporary.write_bytes(binary)
    temporary.chmod(0o700)
    os.replace(temporary, target)
    store.mutate(lambda config: config["gateway"].update(binary=str(target)))
    return target


def find_binary(store: Store) -> Path:
    configured = store.load()["gateway"].get("binary")
    candidate = configured or shutil.which(executable_name())
    if not candidate or not Path(candidate).is_file():
        raise FlexError("Gateway backend missing. Run 'claude-flex gateway install'.")
    return Path(candidate).resolve()


def process_command(pid: int) -> str:
    if sys.platform.startswith("linux"):
        try:
            return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            return ""
    if os.name == "nt":
        script = f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"
        command = ["powershell.exe", "-NoProfile", "-Command", script]
    else:
        command = ["ps", "-p", str(pid), "-o", "command="]
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


class Gateway:
    def __init__(self, store: Store):
        self.store = store
        self.record_path = store.runtime / "process.json"

    def owned_pid(self) -> int | None:
        record = read_json(self.record_path, {})
        pid = record.get("pid")
        if type(pid) is not int or pid < 1:
            return None
        command = process_command(pid)
        expected = str(self.store.runtime / "gateway.json")
        if expected in command and executable_name() in command:
            return pid
        return None

    def models(self) -> list[dict]:
        data = request_json(
            self.store.url() + "/v1/models",
            headers={"Authorization": "Bearer " + self.store.secrets()["gateway_key"]},
            timeout=3,
        )
        return data.get("data", [])

    def wait_for_model(self, alias: str, timeout: float = 15):
        # The HTTP listener becomes ready before OAuth files/catalogs finish
        # loading. Config hot reloads also register model aliases asynchronously.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(item.get("id") == alias for item in self.models()):
                return
            time.sleep(0.1)
        raise FlexError("Model route did not become available. Check enabled accounts, provider credentials and the upstream model ID.")

    def start(self) -> int:
        from .accounts import sync_sources

        with file_lock(self.store.home / "gateway.lock"):
            sync_sources(self.store)
            path = render(self.store)
            if pid := self.owned_pid():
                self.models()
                return pid
            # A listening process with a different key must never be replaced.
            import socket

            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", self.store.load()["gateway"]["port"])) == 0:
                    raise FlexError("Gateway port is already occupied. Change the port in config.json.")
            binary = find_binary(self.store)
            log_path = self.store.runtime / "gateway.log"
            with log_path.open("ab") as log:
                os.chmod(log_path, 0o600)
                kwargs = {"start_new_session": True} if os.name != "nt" else {
                    "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
                }
                process = subprocess.Popen(
                    [str(binary), "--config", str(path)], cwd=self.store.runtime,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, **kwargs,
                )
            _CHILDREN[process.pid] = process
            write_json(self.record_path, {"pid": process.pid})
            for _ in range(100):
                if process.poll() is not None:
                    _CHILDREN.pop(process.pid, None)
                    self.record_path.unlink(missing_ok=True)
                    raise FlexError(f"Gateway exited. Inspect {log_path}.")
                try:
                    self.models()
                    return process.pid
                except FlexError:
                    time.sleep(0.1)
            self._stop_unlocked()
            raise FlexError(f"Gateway did not become ready. Inspect {log_path}.")

    def _stop_unlocked(self):
        pid = self.owned_pid()
        if pid:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T"], capture_output=True, check=False)
            else:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGTERM)
            for _ in range(100):
                if not self.owned_pid():
                    break
                time.sleep(0.1)
            else:
                raise FlexError("Gateway is still shutting down. Retry 'gateway stop'.")
            process = _CHILDREN.pop(pid, None)
            if process is not None:
                process.wait(timeout=5)
        self.record_path.unlink(missing_ok=True)

    def stop(self):
        with file_lock(self.store.home / "gateway.lock"):
            self._stop_unlocked()

    @contextlib.contextmanager
    def paused(self):
        running = bool(self.owned_pid())
        if running:
            self.stop()
        try:
            yield
        finally:
            if running:
                self.start()
