"""Conservatively undo settings written by pre-isolation Flex versions once."""

from __future__ import annotations

import copy
import time
from pathlib import Path

from .compiler import cli_settings, model_alias
from .store import Store, read_json, write_json


def restore_legacy_settings(store: Store) -> list[str]:
    marker = store.runtime / "legacy-settings-restored.json"
    if marker.exists():
        return []
    backups = store.home / "backups"
    original = read_json(backups / "claude-settings-original.json", {})
    permission_backups = [read_json(path) for path in sorted(backups.glob("permissions-*.json"))]
    candidates = {entry["path"]: entry for entry in permission_backups if entry.get("path")}
    if original.get("path"):
        candidates[original["path"]] = original
    restored = []
    config = store.load()
    mode = config.get("permission_mode")
    native_ids = {model["id"] for model in config["models"].values()} | {model_alias(label) for label in config["models"]}
    managed_env = cli_settings(store)["env"]
    for value, backup in candidates.items():
        path = Path(value)
        if not path.exists():
            continue
        previous = read_json(path)
        updated = copy.deepcopy(previous)
        baseline = backup.get("settings", {})
        env = updated.get("env", {})
        routed_by_flex = env.get("ANTHROPIC_BASE_URL") == managed_env["ANTHROPIC_BASE_URL"] and env.get("ANTHROPIC_AUTH_TOKEN") == managed_env["ANTHROPIC_AUTH_TOKEN"]
        if routed_by_flex:
            for key, owned_value in managed_env.items():
                if env.get(key) == owned_value:
                    if key in baseline.get("env", {}):
                        env[key] = baseline["env"][key]
                    else:
                        env.pop(key, None)
            if not env and "env" not in baseline:
                updated.pop("env", None)
            for key in ("modelPicker", "availableModels", "enforceAvailableModels"):
                if key in baseline:
                    updated[key] = baseline[key]
                else:
                    updated.pop(key, None)
            if previous.get("model") in native_ids:
                if "model" in baseline:
                    updated["model"] = baseline["model"]
                else:
                    updated.pop("model", None)
        permissions = updated.get("permissions", {})
        if mode and permissions.get("defaultMode") == mode:
            if "defaultMode" in baseline.get("permissions", {}):
                permissions["defaultMode"] = baseline["permissions"]["defaultMode"]
            else:
                permissions.pop("defaultMode", None)
            if not permissions and "permissions" not in baseline:
                updated.pop("permissions", None)
            if updated.get("skipDangerousModePermissionPrompt") is True:
                if "skipDangerousModePermissionPrompt" in baseline:
                    updated["skipDangerousModePermissionPrompt"] = baseline["skipDangerousModePermissionPrompt"]
                else:
                    updated.pop("skipDangerousModePermissionPrompt", None)
        if previous != updated:
            saved = backups / "before-isolation-restoration" / (str(len(restored)) + ".json")
            write_json(saved, {"path": str(path), "settings": previous})
            write_json(path, updated)
            restored.append(str(path))
    write_json(marker, {"restored": restored, "at": time.time()})
    return restored
