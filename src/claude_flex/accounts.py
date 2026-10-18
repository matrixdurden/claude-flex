"""Named OAuth accounts; each independent login has one token-refresh owner."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from .store import FlexError, Store, file_lock, name, read_json, write_json


def claims(token: str) -> dict:
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError, TypeError):
        return {}


def normalize(data: dict, *, with_refresh=False, provider=None) -> dict:
    if provider == "claude" or data.get("type") == "claude" or "claudeAiOauth" in data:
        return normalize_claude(data, with_refresh=with_refresh)
    tokens = data.get("tokens", data)
    access = tokens.get("access_token", tokens.get("access", ""))
    if not isinstance(access, str) or not access:
        raise FlexError("No OAuth access token in the selected account file.")
    payload = claims(access)
    auth = payload.get("https://api.openai.com/auth", {})
    profile = payload.get("https://api.openai.com/profile", {})
    account_id = tokens.get("account_id") or tokens.get("accountId") or auth.get("chatgpt_account_id")
    if not account_id:
        raise FlexError("No ChatGPT account ID in the selected login.")
    expiry = payload.get("exp")
    if not expiry and isinstance(tokens.get("expires"), (int, float)):
        expiry = tokens["expires"] / 1000
    expired = tokens.get("expired", "")
    if expiry:
        if expiry <= time.time() + 30:
            raise FlexError("The access token has expired. Refresh the source login or log in again.")
        expired = datetime.fromtimestamp(expiry, UTC).isoformat()
    return {
        "type": "codex", "access_token": access, "account_id": account_id,
        "id_token": tokens.get("id_token", ""),
        "refresh_token": tokens.get("refresh_token", tokens.get("refresh", "")) if with_refresh else "",
        "expired": expired, "last_refresh": data.get("last_refresh") or datetime.now(UTC).isoformat(),
        "email": tokens.get("email") or profile.get("email", payload.get("email", "")),
        "plan_type": tokens.get("plan_type") or auth.get("chatgpt_plan_type", "unknown"),
    }


def normalize_claude(data: dict, *, with_refresh=False) -> dict:
    tokens = data.get("claudeAiOauth", data)
    access = tokens.get("access_token", tokens.get("accessToken"))
    if not isinstance(access, str) or not access:
        raise FlexError("No Claude OAuth access token in the selected login.")
    account = data.get("account_uuid") or tokens.get("account_uuid")
    email = data.get("email") or tokens.get("email", "")
    if not account and not email:
        raise FlexError("Claude login is missing its account identity. Use a fresh browser login.")
    expired = tokens.get("expired", "")
    expiry = tokens.get("expiresAt")
    if isinstance(expiry, (int, float)):
        if expiry / 1000 <= time.time() + 30:
            raise FlexError("Claude login has expired. Renew it in Claude Code first.")
        expired = datetime.fromtimestamp(expiry / 1000, UTC).isoformat()
    result = {
        "type": "claude", "access_token": access,
        "refresh_token": tokens.get("refresh_token", tokens.get("refreshToken", "")) if with_refresh else "",
        "account_uuid": account or "", "organization_uuid": data.get("organization_uuid", ""),
        "email": email, "expired": expired,
        "plan_type": tokens.get("subscriptionType") or data.get("plan_type", "unknown"),
        "last_refresh": data.get("last_refresh") or datetime.now(UTC).isoformat(),
    }
    if data.get("claude_device_ids"):
        result["claude_device_ids"] = data["claude_device_ids"]
    return result


def provider_of(meta: dict) -> str:
    return meta.get("provider", "codex")


def identity(data: dict) -> str:
    if data.get("type") == "claude":
        value = "claude:" + (data.get("account_uuid") or data["email"].lower())
    else:
        # Preserve the fingerprint of existing pre-TUI Codex imports.
        value = data["account_id"]
    return hashlib.sha256(value.encode()).hexdigest()


def credential_gateway(store: Store, label: str):
    from .gateway import Gateway
    from .profiles import AccountProfile

    metadata = store.load()["accounts"].get(label, {})
    home = store.home / "instances" / metadata.get("identity", "missing")
    if metadata.get("credential_path") and (home / "config.json").exists():
        return Gateway(AccountProfile(home, store.account_path(label)))
    return Gateway(store)


def import_account(store: Store, label: str, data: dict, *, source=None, weight=1, priority=0, replace=False):
    name(label)
    if label == "all":
        raise FlexError("The account label 'all' is reserved for pool selection.")
    if not 1 <= weight <= 1_000_000:
        raise FlexError("Weight must be 1..1000000.")
    config = store.load()
    if label in config["accounts"] and not replace:
        raise FlexError(f"Account already exists: {label}")
    fingerprint = identity(data)
    if any(item.get("identity") == fingerprint and key != label for key, item in config["accounts"].items()):
        raise FlexError("This account is already present. A second label is not another quota.")
    if replace and label in config["accounts"] and config["accounts"][label]["identity"] != fingerprint:
        raise FlexError("Sign in to the same account to renew this label, or add a new account.")
    with credential_gateway(store, label).paused():
        data.update(label=label, weight=weight, priority=priority, disabled=False)
        path = store.account_path(label)
        previous = read_json(path, {})
        write_json(path, data)
        try:
            def change(current):
                if (label in current["accounts"] and not replace) or any(item.get("identity") == fingerprint and key != label for key, item in current["accounts"].items()):
                    raise FlexError("Account was added by another process. Retry with 'accounts list'.")
                canonical = current["accounts"].get(label, {}).get("credential_path")
                current["accounts"][label] = {"provider": data.get("type", "codex"), "enabled": True, "weight": weight, "priority": priority, "identity": fingerprint, "source": source}
                if canonical:
                    current["accounts"][label]["credential_path"] = canonical
            store.mutate(change)
            if config["accounts"].get(label, {}).get("credential_path"):
                from .profiles import prepare

                prepare(store, label)
        except Exception:
            if previous:
                write_json(path, previous)
            else:
                path.unlink(missing_ok=True)
            raise


def opencode_data(path: Path, provider="codex") -> dict:
    data = read_json(path).get("anthropic" if provider == "claude" else "openai", {})
    if data.get("type") != "oauth":
        raise FlexError("The selected OpenCode login is not OpenAI OAuth.")
    return normalize(data)


def sync_sources(store: Store) -> list[str]:
    warnings = []
    for label, account in store.load()["accounts"].items():
        source = account.get("source")
        if not source or not account["enabled"]:
            continue
        try:
            if source["kind"] != "opencode":
                continue
            data = opencode_data(Path(source["path"]))
            if identity(data) != account["identity"]:
                raise FlexError("The source is now signed into a different account; import it with another label.")
            current = read_json(store.account_path(label))
            if current.get("access_token") != data["access_token"]:
                # Linked accounts never carry refresh tokens, so the backend cannot
                # concurrently rotate credentials while this access snapshot updates.
                current.update(data)
                write_json(store.account_path(label), current)
        except (FlexError, OSError):
            warnings.append(f"Could not sync linked account '{label}'; refresh its source login.")
    return warnings


def login(store: Store, label: str, *, device=False, no_browser=False, weight=1, priority=0, provider="codex"):
    name(label)
    if label == "all":
        raise FlexError("The account label 'all' is reserved for pool selection.")
    if label in store.load()["accounts"]:
        raise FlexError("Account label already exists.")
    with tempfile.TemporaryDirectory(prefix="login-", dir=store.home) as directory:
        root = Path(directory)
        args, auth = prepare_login(store, root, device=device, no_browser=no_browser, provider=provider)
        result = subprocess.run(args, cwd=root, check=False)
        if result.returncode:
            raise FlexError("OAuth login did not complete.")
        candidates = list(auth.glob("*.json"))
        if len(candidates) != 1:
            raise FlexError("OAuth login did not produce exactly one account file.")
        import_account(store, label, normalize(read_json(candidates[0]), with_refresh=True, provider=provider), weight=weight, priority=priority)


def prepare_login(store: Store, root: Path, *, device=False, no_browser=False, provider="codex"):
    from .gateway import find_binary

    auth = root / "auth"
    auth.mkdir(mode=0o700)
    config = root / "login.json"
    write_json(config, {"config-version": 8, "oauth": {"auth-dir": str(auth)}})
    if provider not in {"codex", "claude"} or provider == "claude" and device:
        raise FlexError("Claude uses browser sign-in; device codes are available for ChatGPT only.")
    flag = "--claude-login" if provider == "claude" else "--codex-device-login" if device else "--codex-login"
    args = [str(find_binary(store)), "--config", str(config), flag]
    if no_browser:
        args.append("--no-browser")
    return args, auth


def update_account(store: Store, label: str, *, enabled=None, weight=None, priority=None):
    name(label)
    config = store.load()
    if label not in config["accounts"]:
        raise FlexError("Unknown account label.")
    if weight is not None and not 1 <= weight <= 1_000_000:
        raise FlexError("Weight must be 1..1000000.")
    with credential_gateway(store, label).paused():
        data = read_json(store.account_path(label))
        changes = {}
        if enabled is not None:
            changes["enabled"] = enabled
            data["disabled"] = not enabled
        if weight is not None:
            changes["weight"] = data["weight"] = weight
        if priority is not None:
            changes["priority"] = data["priority"] = priority
        write_json(store.account_path(label), data)
        store.mutate(lambda current: current["accounts"][label].update(changes))


def account_rows(store: Store) -> list[dict]:
    result = []
    for label, meta in store.load()["accounts"].items():
        data = read_json(store.account_path(label), {})
        email = data.get("email", "")
        masked = email[:1] + "***@" + email.split("@")[-1] if "@" in email else "—"
        result.append({
            "name": label, "provider": provider_of(meta), "enabled": meta["enabled"], "plan": data.get("plan_type", "unknown"),
            "email": masked, "weight": meta["weight"], "priority": meta["priority"],
            "refresh": "source" if meta.get("source") else "owned" if data.get("refresh_token") else "snapshot",
            "expires": data.get("expired", "unknown"),
        })
    return result


def select_accounts(store: Store, label: str):
    config = store.load()
    if label != "all" and label not in config["accounts"]:
        raise FlexError("Unknown account.")
    # Selection is a preference for the next launch, not a shared-server switch.
    # Never disable another account or restart a gateway with a live session.
    def change(current):
        if label == "all":
            for metadata in current["accounts"].values():
                metadata["enabled"] = True
        else:
            current["accounts"][label]["enabled"] = True
        current["selected_account"] = None if label == "all" else label
    store.mutate(change)


def remove_account(store: Store, label: str):
    if label not in store.load()["accounts"]:
        raise FlexError("Unknown account.")
    gateway = credential_gateway(store, label)
    gateway.stop()
    with file_lock(store.home / "state.lock"):
        store.account_path(label).unlink(missing_ok=True)
        config = store.load()
        config["accounts"].pop(label)
        if config.get("selected_account") == label:
            config["selected_account"] = None
        write_json(store.config_path, config)


def rename_account(store: Store, old: str, new: str):
    name(new)
    if old == new:
        return
    config = store.load()
    if old not in config["accounts"] or new == "all" or new in config["accounts"] or store.account_path(new).exists():
        raise FlexError("Account name is unavailable.")
    from .gateway import Gateway

    with Gateway(store).paused():
        original = store.account_path(old)
        destination = original if config["accounts"][old].get("credential_path") else store.account_path(new)
        credential = read_json(original)
        credential["label"] = new
        write_json(destination, credential)
        try:
            def change(current):
                if new in current["accounts"]:
                    raise FlexError("Account name is unavailable.")
                current["accounts"][new] = current["accounts"].pop(old)
                if current.get("selected_account") == old:
                    current["selected_account"] = new
            store.mutate(change)
        except Exception:
            if destination != original:
                destination.unlink(missing_ok=True)
            raise
        if destination != original:
            original.unlink()
