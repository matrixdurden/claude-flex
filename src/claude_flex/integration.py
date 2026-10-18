"""Build private Claude profiles. Never write to the user's normal Claude setup."""

from __future__ import annotations

import os
from pathlib import Path

from .compiler import cli_settings
from .store import FlexError, Store, file_lock, read_json, write_json

LEGACY_ENV = {
    "ANTHROPIC_MODEL", "ANTHROPIC_CUSTOM_MODEL_OPTION", "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION", "ANTHROPIC_SMALL_FAST_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_MAX_CONTEXT_TOKENS", "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    *(f"ANTHROPIC_DEFAULT_{family}_MODEL" for family in ("OPUS", "SONNET", "HAIKU", "FABLE")),
}


def settings_path(store: Store | None = None) -> Path:
    if store is not None:
        return store.home / "claude-code/settings.json"
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "settings.json"


def permission_settings(previous: dict, mode: str | None) -> dict:
    updated = dict(previous)
    if mode is not None:
        updated["permissions"] = {**updated.get("permissions", {}), "defaultMode": mode}
        if mode == "bypassPermissions":
            updated["skipDangerousModePermissionPrompt"] = True
    return updated


def private_path(store: Store, path: Path | None) -> Path:
    path = (path or settings_path(store)).resolve()
    if not path.is_relative_to(store.home):
        raise FlexError("Claude Flex settings must stay inside its private profile.")
    return path


def apply_permissions(store: Store, path: Path | None = None) -> Path | None:
    if store.load().get("permission_mode") is None:
        return None
    path = private_path(store, path)
    with file_lock(store.home / "claude-settings.lock"):
        previous = read_json(path, {})
        updated = permission_settings(previous, store.load()["permission_mode"])
        if updated != previous:
            write_json(path, updated)
    return path


def apply(store: Store, path: Path | None = None) -> Path:
    path = private_path(store, path)
    settings = cli_settings(store)
    options = settings["modelPicker"]["options"]
    if not options:
        raise FlexError("No account models are available. Refresh or sign in again.")
    with file_lock(store.home / "claude-settings.lock"):
        previous = read_json(path, {})
        updated = permission_settings(previous, store.load().get("permission_mode"))
        env = {key: value for key, value in updated.get("env", {}).items() if key not in LEGACY_ENV}
        env.update(settings["env"])
        updated.update(settings, env=env)
        available = {row["model"] for row in options}
        if updated.get("model") not in available:
            updated["model"] = options[0]["model"]
        if updated != previous:
            write_json(path, updated)
        # A private bootstrap, not a copy of the normal user's login or history.
        bootstrap = path.parent / ".claude.json"
        if not bootstrap.exists():
            write_json(bootstrap, {"hasCompletedOnboarding": True, "theme": "dark"})
    return path


def refresh_if_connected(store: Store):
    path = settings_path(store)
    if path.exists():
        return apply(store, path)
