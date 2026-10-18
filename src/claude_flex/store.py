"""Portable user state. Credentials and generated profiles never belong in the repo."""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


class FlexError(Exception):
    """An actionable, credential-free error suitable for the CLI."""


KINDS = {"codex", "claude-oauth", "openai", "responses", "claude", "gemini"}
STRATEGIES = {"round-robin", "weighted-round-robin", "fill-first"}
PERMISSION_MODES = {"bypassPermissions", "auto", "manual"}
NAME = re.compile(r"[a-z][a-z0-9-]{0,47}\Z")


def name(value: str) -> str:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise FlexError("Names must start with a-z and contain only a-z, 0-9, hyphens (max 48).")
    return value


def base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise FlexError("Base URL must be an absolute http(s) URL.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise FlexError("Use a plain base URL; supply credentials separately.")
    return value.rstrip("/")


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        if default is not None:
            return default
        raise FlexError(f"Missing {path}. Run 'claude-flex init'.") from None
    except (ValueError, UnicodeError):
        raise FlexError(f"Invalid JSON: {path}") from None


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".flex-", delete=False
        ) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


@contextlib.contextmanager
def file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if not handle.tell():
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def default_home() -> Path:
    if os.environ.get("CLAUDE_FLEX_HOME"):
        return Path(os.environ["CLAUDE_FLEX_HOME"]).expanduser().resolve()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Claude-Flex"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Claude-Flex"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "claude-flex"


def validate(config: dict) -> None:
    try:
        if config["version"] != 1:
            raise FlexError("Unsupported config version.")
        port = config["gateway"]["port"]
        if type(port) is not int or not 1 <= port <= 65535:
            raise FlexError("Gateway port must be 1..65535.")
        if config["routing"]["strategy"] not in STRATEGIES:
            raise FlexError("Unknown routing strategy.")
        if type(config["routing"]["sticky"]) is not bool:
            raise FlexError("Routing sticky must be true or false.")
        if config.get("permission_mode") not in PERMISSION_MODES | {None}:
            raise FlexError("Unknown permission mode.")
        for key, provider in config["providers"].items():
            name(key)
            if provider["kind"] not in KINDS:
                raise FlexError(f"Unsupported provider kind: {key}")
            if provider["kind"] not in {"codex", "claude-oauth"}:
                base_url(provider["base_url"])
            for credential in provider.get("keys", []):
                name(credential["name"])
                if ("env" in credential) == ("secret" in credential):
                    raise FlexError(f"Provider {key}: each key requires exactly one env or secret reference.")
        for key, model in config["models"].items():
            name(key)
            if model["provider"] not in config["providers"]:
                raise FlexError(f"Unknown provider for model {key}.")
            if not isinstance(model["id"], str) or not model["id"].strip():
                raise FlexError(f"Empty upstream model ID: {key}")
            if type(model.get("context", 200000)) is not int or model.get("context", 200000) < 4096:
                raise FlexError(f"Invalid context size: {key}")
        if config.get("active") and config["active"] not in config["models"]:
            raise FlexError("The active model does not exist.")
        for key, account in config["accounts"].items():
            name(key)
            if type(account.get("enabled")) is not bool:
                raise FlexError(f"Account enabled must be true or false: {key}")
    except (KeyError, TypeError, AttributeError):
        raise FlexError("Invalid config schema. See examples/config.json and docs/architecture.md.") from None


class Store:
    def __init__(self, home: Path | None = None):
        self.home = (home or default_home()).resolve()
        self.config_path = self.home / "config.json"
        self.auth = self.home / "auth"
        self.runtime = self.home / "runtime"

    def init(self, port: int = 8318) -> dict:
        with file_lock(self.home / "state.lock"):
            if self.config_path.exists():
                config = self.load()
                if "claude-oauth" not in config["providers"]:
                    config["providers"]["claude-oauth"] = {"kind": "claude-oauth", "enabled": True, "keys": []}
                    write_json(self.config_path, config)
                return config
            config = {
                "version": 1,
                "gateway": {"port": port, "binary": ""},
                "routing": {"strategy": "round-robin", "sticky": True},
                "active": None,
                "providers": {
                    "codex": {"kind": "codex", "enabled": True, "keys": []},
                    "claude-oauth": {"kind": "claude-oauth", "enabled": True, "keys": []},
                },
                "models": {},
                "accounts": {},
            }
            validate(config)
            self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.home, 0o700)
            self.auth.mkdir(mode=0o700, exist_ok=True)
            self.runtime.mkdir(mode=0o700, exist_ok=True)
            write_json(self.home / "secrets.json", {"gateway_key": secrets.token_urlsafe(36), "keys": {}})
            write_json(self.config_path, config)
            return config

    def load(self) -> dict:
        config = read_json(self.config_path)
        validate(config)
        return config

    def mutate(self, fn):
        with file_lock(self.home / "state.lock"):
            config = self.load()
            result = fn(config)
            validate(config)
            write_json(self.config_path, config)
            return result

    def secrets(self) -> dict:
        return read_json(self.home / "secrets.json")

    def put_secret(self, key: str, value: str) -> None:
        with file_lock(self.home / "state.lock"):
            data = self.secrets()
            data["keys"][key] = value
            write_json(self.home / "secrets.json", data)

    def url(self) -> str:
        return f"http://127.0.0.1:{self.load()['gateway']['port']}"

    def account_path(self, label: str) -> Path:
        name(label)
        metadata = read_json(self.config_path, {}).get("accounts", {}).get(label, {})
        if metadata.get("credential_path"):
            path = Path(metadata["credential_path"]).resolve()
            if not path.is_relative_to(self.home / "credentials"):
                raise FlexError("Invalid account credential path.")
            return path
        return self.auth / f"{label}.json"
