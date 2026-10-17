"""Small UI-independent operations shared by the TUI and automation CLI."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import webbrowser
from pathlib import Path

from . import accounts
from .compiler import choose, cli_settings
from .gateway import Gateway, find_binary, install
from .store import FlexError, Store, write_json


def ensure_backend(store: Store):
    try:
        return find_binary(store)
    except FlexError:
        return install(store)


def add_model(store, label, provider, upstream, display=None, context=200000):
    from .store import name

    name(label)

    def change(config):
        if label in config["models"]:
            raise FlexError("Model profile already exists.")
        if provider not in config["providers"]:
            raise FlexError("Unknown provider.")
        config["models"][label] = {"provider": provider, "id": upstream, "label": display or label, "context": context}
        if not config["active"]:
            config["active"] = label

    store.mutate(change)


def default_account_models(store: Store, provider: str):
    presets = (
        ("opus", "claude-opus-5-5", "Claude Opus"),
        ("sonnet", "claude-sonnet-5-5", "Claude Sonnet"),
        ("haiku", "claude-haiku-5-5", "Claude Haiku"),
    ) if provider == "claude" else (
        ("gpt", "gpt-6.1-sol", "GPT-6.1 Sol"),
        ("astra", "gpt-6-astra", "GPT-6 Astra"),
        ("luna", "gpt-6-luna", "GPT-6 Luna"),
    )
    target = "claude-oauth" if provider == "claude" else "codex"

    def change(config):
        for base, upstream, display in presets:
            if any(model["provider"] == target and model["id"] == upstream for model in config["models"].values()):
                continue
            label, number = base, 2
            while label in config["models"]:
                label, number = f"{base}-{number}", number + 1
            config["models"][label] = {"provider": target, "id": upstream, "label": display, "context": 200000}
            if not config["active"]:
                config["active"] = label

    store.mutate(change)


def next_account_name(store: Store, provider: str) -> str:
    base = "claude" if provider == "claude" else "chatgpt"
    existing = set(store.load()["accounts"])
    label, number = base, 2
    while label in existing or store.account_path(label).exists():
        label, number = f"{base}-{number}", number + 1
    return label


def run_claude(store: Store, profile=None, args=()) -> int:
    binary = shutil.which("claude")
    if not binary:
        raise FlexError("Claude Code CLI is not installed. Install Claude Code, then reopen Flex.")
    settings = cli_settings(store, profile)
    gateway = Gateway(store)
    ensure_backend(store)
    gateway.start()
    gateway.wait_for_model(settings["model"])
    env = os.environ.copy()
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"):
        env.pop(key, None)
    env.update(settings["env"])
    with tempfile.TemporaryDirectory(prefix="session-", dir=store.runtime) as directory:
        path = Path(directory) / "settings.json"
        write_json(path, settings)
        return subprocess.run([binary, "--settings", str(path), "--model", settings["model"], *args], env=env, check=False).returncode


def use_model(store: Store, label: str):
    choose(store.load(), label)
    store.mutate(lambda config: config.update(active=label))


def suggest_name(upstream: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", upstream.lower()).strip("-")[:45]
    return value if value and value[0].isalpha() else "model-" + value[:39]


def open_browser(url: str):
    if os.environ.get("WSL_DISTRO_NAME"):
        from .desktop import powershell, ps_quote

        powershell("Start-Process " + ps_quote(url))
    else:
        webbrowser.open(url)


def import_opencode(store: Store, label: str):
    source = Path.home() / ".local/share/opencode/auth.json"
    accounts.import_account(store, label, accounts.opencode_data(source), source={"kind": "opencode", "path": str(source)})
    default_account_models(store, "codex")
