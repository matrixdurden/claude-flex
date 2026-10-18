"""One stable gateway and one private Claude state directory per account."""

from __future__ import annotations

import os
import socket
from pathlib import Path

from . import accounts
from .gateway import Gateway
from .store import FlexError, Store, file_lock, read_json, write_json


class AccountProfile(Store):
    def __init__(self, home: Path, credential: Path):
        super().__init__(home)
        self.credential = credential
        self.auth = credential.parent

    def account_path(self, label: str) -> Path:
        if label not in self.load()["accounts"]:
            raise FlexError("Account is not part of this profile.")
        return self.credential


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def prepare(store: Store, label: str) -> AccountProfile:
    with file_lock(store.home / "profiles.lock"):
        config = store.load()
        metadata = config["accounts"].get(label)
        if metadata is None:
            raise FlexError("Unknown account.")
        identity = metadata["identity"]
        if len(identity) != 64 or any(character not in "0123456789abcdef" for character in identity):
            raise FlexError("Invalid account identity.")
        source = store.account_path(label)
        canonical = store.home / "credentials" / identity / "account.json"
        if source != canonical:
            # An old shared Flex session must not lose its refresh owner while live.
            if Gateway(store).owned_pid():
                raise FlexError("An older shared Flex gateway is still in use. Stop it after finishing that session, then retry.")
            credential = read_json(source)
            write_json(canonical, credential)
            archive = store.home / "backups/migrated-credentials" / f"{label}.json"
            write_json(archive, credential)
            store.mutate(lambda current: current["accounts"][label].update(credential_path=str(canonical)))
            source.unlink(missing_ok=True)
        profile = AccountProfile(store.home / "instances" / identity, canonical)
        if not profile.config_path.exists():
            profile.init(port=free_port())
        parent = store.load()
        channel = accounts.provider_of(parent["accounts"][label])
        target_kind = "claude-oauth" if channel == "claude" else "codex"
        def update(current):
            current["gateway"]["binary"] = parent["gateway"]["binary"]
            current["providers"] = {key: value for key, value in parent["providers"].items() if value["kind"] == target_kind or value["kind"] not in {"codex", "claude-oauth"}}
            current["models"] = {key: value for key, value in parent["models"].items() if value["provider"] in current["providers"]}
            current["model_redirects"] = {key: target for key, target in parent.get("model_redirects", {}).items() if target in current["models"]}
            current["active"] = parent.get("active") if parent.get("active") in current["models"] else next(iter(current["models"]), None)
            current["accounts"] = {label: {**parent["accounts"][label], "enabled": True}}
            current["selected_account"] = label
            current["routing"] = parent["routing"]
            if parent.get("permission_mode"):
                current["permission_mode"] = parent["permission_mode"]
        profile.mutate(update)
        secrets = profile.secrets()
        secrets["keys"] = store.secrets()["keys"]
        write_json(profile.home / "secrets.json", secrets)
        credential = read_json(canonical)
        if credential.get("disabled"):
            credential["disabled"] = False
            write_json(canonical, credential)
        return profile


def environment(profile: Store) -> dict:
    from .integration import LEGACY_ENV

    ignored = LEGACY_ENV | {"ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_USER_DATA_DIR", "CLAUDE_CDP_AUTH"}
    env = {key: value for key, value in os.environ.items() if key not in ignored}
    env["CLAUDE_CONFIG_DIR"] = str(profile.home / "claude-code")
    # Keep credential keychain namespaces separate on native Windows too.
    env["CLAUDE_SECURESTORAGE_CONFIG_DIR"] = env["CLAUDE_CONFIG_DIR"]
    return env
