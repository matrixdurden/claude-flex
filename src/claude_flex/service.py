"""Small UI-independent operations shared by the TUI and automation CLI."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import webbrowser
from pathlib import Path

from . import accounts, integration, profiles
from .gateway import Gateway, find_binary, install
from .providers import discover
from .store import FlexError, Store, read_json, write_json


def ensure_backend(store: Store):
    try:
        return find_binary(store)
    except FlexError:
        return install(store)


def native_claude() -> str:
    binary = shutil.which("claude")
    if not binary:
        raise FlexError("Claude Code CLI is not installed. Install Claude Code, then reopen Flex.")
    try:
        version = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise FlexError("Claude Code could not start. Check its installation.") from None
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", version.stdout)
    if version.returncode or not match:
        raise FlexError("Claude Code's version could not be read. Check its installation.")
    if tuple(map(int, match.groups())) < (2, 1, 242):
        raise FlexError("Update Claude Code to 2.1.242 or later to select provider models in /model.")
    return binary


def add_model(store, label, provider, upstream, display=None, context=200000):
    from .store import name

    name(label)

    def change(config):
        if label in config["models"]:
            raise FlexError("Model profile already exists.")
        if provider not in config["providers"]:
            raise FlexError("Unknown provider.")
        if any(model["provider"] == provider and model["id"] == upstream and model.get("context", 200000) == context for model in config["models"].values()):
            raise FlexError("This model is already connected.")
        config.get("model_redirects", {}).pop(label, None)
        config["models"][label] = {"provider": provider, "id": upstream, "label": display or label, "context": context}
        if not config["active"]:
            config["active"] = label

    store.mutate(change)


def merge_duplicate_models(store: Store):
    """Merge identical connections, retaining aliases used by existing sessions."""
    config = store.load()
    groups = {}
    for label, model in config["models"].items():
        groups.setdefault((model["provider"], model["id"], model.get("context", 200000)), []).append(label)
    if not any(len(labels) > 1 for labels in groups.values()):
        return

    def change(current):
        redirects = current.setdefault("model_redirects", {})
        for labels in groups.values():
            if len(labels) < 2 or not all(label in current["models"] for label in labels):
                continue
            keeper = current["active"] if current["active"] in labels else labels[0]
            model = current["models"][keeper]
            # Prefer the explicit model name to legacy subscription-only labels.
            for label in labels:
                display = current["models"][label].get("label", "")
                if model.get("label") in {"GPT (ChatGPT Plus)", "GPT / Plus", "GPT"} and display not in {"GPT (ChatGPT Plus)", "GPT / Plus", "GPT", ""}:
                    model["label"] = display
            for label in labels:
                if label == keeper:
                    continue
                del current["models"][label]
                redirects[label] = keeper
                for source, target in list(redirects.items()):
                    if target == label:
                        redirects[source] = keeper

    store.mutate(change)


def remove_model(store: Store, label: str):
    def change(config):
        del config["models"][label]
        config["model_redirects"] = {key: target for key, target in config.get("model_redirects", {}).items() if target != label}
        if config["active"] == label:
            config["active"] = next(iter(config["models"]), None)
    store.mutate(change)


def default_account_models(store: Store, provider: str):
    target = "claude-oauth" if provider == "claude" else "codex"
    return sync_models(store, providers=[target], force=True)


def merge_catalog(store: Store, provider: str, entries: list[dict]) -> list[str]:
    added = []

    def change(config):
        for item in entries:
            upstream = item.get("id")
            if not isinstance(upstream, str) or not upstream:
                continue
            if any(model["provider"] == provider and model["id"] == upstream for model in config["models"].values()):
                continue
            base = suggest_name(upstream)
            label, number = base, 2
            while label in config["models"] or label in config.get("model_redirects", {}):
                label, number = f"{base}-{number}", number + 1
            context = item.get("context")
            if type(context) is not int or context < 4096:
                context = 200000
            config["models"][label] = {"provider": provider, "id": upstream, "label": item.get("label") or upstream, "context": context, "discovered": True}
            if not config["active"]:
                config["active"] = label
            added.append(label)

    store.mutate(change)
    return added


def sync_models(store: Store, *, providers=None, force=False) -> dict:
    """Union live catalogs from connected sources; keep existing routes on failure."""
    import hashlib
    import json

    config = store.load()
    cache_path = store.runtime / "model-catalog.json"
    cache = read_json(cache_path, {})
    report = {"added": [], "errors": {}}
    for label, provider in config["providers"].items():
        if providers is not None and label not in providers or not provider.get("enabled", True):
            continue
        if provider["kind"] in {"codex", "claude-oauth"}:
            channel = "claude" if provider["kind"] == "claude-oauth" else "codex"
            identities = sorted(meta["identity"] for meta in config["accounts"].values() if meta["enabled"] and accounts.provider_of(meta) == channel)
            if not identities:
                continue
        else:
            identities = []
            if not provider.get("keys") and not provider.get("no_auth"):
                continue
        signature = hashlib.sha256(json.dumps([provider, identities], sort_keys=True).encode()).hexdigest()
        previous = cache.get(label, {})
        if not force and previous.get("signature") == signature and time.time() - previous.get("at", 0) < 300:
            continue
        try:
            entries = discover(store, label)
            report["added"].extend(merge_catalog(store, label, entries))
            cache[label] = {"signature": signature, "at": time.time()}
        except (FlexError, OSError) as exc:
            report["errors"][label] = str(exc) if isinstance(exc, FlexError) else "Model catalog unavailable."
    write_json(cache_path, cache)
    if report["added"] and Gateway(store).owned_pid():
        from .compiler import render

        render(store)
    if report["added"]:
        integration.refresh_if_connected(store)
    return report


def next_account_name(store: Store, provider: str) -> str:
    base = "claude" if provider == "claude" else "chatgpt"
    existing = set(store.load()["accounts"])
    label, number = base, 2
    while label in existing or store.account_path(label).exists():
        label, number = f"{base}-{number}", number + 1
    return label


def activate_account(store: Store, label: str, *, settings_path: Path | None = None):
    native_claude()
    ensure_backend(store)
    accounts.select_accounts(store, label)
    report = sync_models(store, force=True)
    profile = profiles.prepare(store, label)
    Gateway(profile).start()
    path = integration.apply(profile, settings_path)
    return {"settings_path": str(path), "profile_home": str(profile.home), "catalog_errors": report["errors"]}


def set_permission_mode(store: Store, mode: str):
    store.mutate(lambda config: config.update(permission_mode=mode))
    # The preference is applied inside the next private profile only.


def run_claude(store: Store, account=None, args=()) -> int:
    merge_duplicate_models(store)
    binary = native_claude()
    config = store.load()
    selected = account or config.get("selected_account")
    if selected is None:
        selected = next((key for key, value in config["accounts"].items() if value["enabled"]), None)
    if selected is None:
        raise FlexError("Connect and select an account in Claude Flex first.")
    activate_account(store, selected)
    profile = profiles.prepare(store, selected)
    env = profiles.environment(profile)
    mode = store.load().get("permission_mode")
    explicit_mode = any(arg == "--permission-mode" or arg.startswith("--permission-mode=") or arg == "--dangerously-skip-permissions" for arg in args)
    permission_args = ["--permission-mode", mode] if mode and not explicit_mode else []
    return subprocess.run([binary, *permission_args, *args], env=env, check=False).returncode


def suggest_name(upstream: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", upstream.lower()).strip("-")[:45]
    return value if value and value[0].isalpha() else "model-" + value[:39]


def open_browser(url: str):
    if os.environ.get("WSL_DISTRO_NAME"):
        from .desktop import powershell, ps_quote

        powershell("Start-Process " + ps_quote(url))
    else:
        webbrowser.open(url)


def import_opencode(store: Store, label: str, source: Path | None = None):
    source = (source or Path.home() / ".local/share/opencode/auth.json").expanduser().resolve()
    data = accounts.opencode_data(source)
    existing = next((key for key, meta in store.load()["accounts"].items() if meta["identity"] == accounts.identity(data)), None)
    if existing and existing != label:
        accounts.rename_account(store, existing, label)
    accounts.import_account(store, label, data, source={"kind": "opencode", "path": str(source)}, replace=bool(existing))
    return default_account_models(store, "codex")
