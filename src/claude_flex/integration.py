"""Connect the native Claude Code CLI, leaving model choice to its /model menu."""

from __future__ import annotations

import os
from pathlib import Path

from .compiler import cli_model_id, cli_settings
from .store import FlexError, Store, file_lock, read_json, write_json

LEGACY_ENV = {
    "ANTHROPIC_MODEL", "ANTHROPIC_CUSTOM_MODEL_OPTION", "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION", "ANTHROPIC_SMALL_FAST_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
    "ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    *(f"ANTHROPIC_DEFAULT_{family}_MODEL" for family in ("OPUS", "SONNET", "HAIKU", "FABLE")),
}


def settings_path() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "settings.json"


def apply(store: Store, path: Path | None = None) -> Path:
    path = path or settings_path()
    settings = cli_settings(store)
    options = settings["modelPicker"]["options"]
    if not options:
        raise FlexError("No account models are available yet. Refresh or renew the account sign-in.")
    with file_lock(store.home / "claude-settings.lock"):
        previous = read_json(path, {})
        backup = store.home / "backups/claude-settings-original.json"
        if not backup.exists():
            write_json(backup, {"path": str(path), "settings": previous})
        updated = {**previous}
        env = dict(updated.get("env", {}))
        for key in LEGACY_ENV:
            env.pop(key, None)
        env.update(settings["env"])
        updated["env"] = env
        updated["modelPicker"] = settings["modelPicker"]
        updated["availableModels"] = settings["availableModels"]
        updated["enforceAvailableModels"] = True
        model = updated.get("model")
        config = store.load()
        # Migrate the old Sonnet-shaped compatibility ID to its real model ID.
        from .compiler import model_alias

        for label in config["models"]:
            if model == model_alias(label):
                model = cli_model_id(config, label)
                break
        available = {row["model"] for row in options}
        if model not in available:
            model = options[0]["model"]
        updated["model"] = model
        if updated != previous:
            write_json(path, updated)
        write_json(store.home / "integration.json", {"settings_path": str(path)})
    return path


def refresh_if_connected(store: Store):
    state = read_json(store.home / "integration.json", {})
    if state.get("settings_path"):
        return apply(store, Path(state["settings_path"]))
