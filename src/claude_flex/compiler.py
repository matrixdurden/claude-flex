"""Compile provider-neutral profiles into CLIProxyAPI v8 and Claude settings."""

from __future__ import annotations

import hashlib

from .providers import key_values
from .store import FlexError, Store, write_json


def model_alias(label: str) -> str:
    # Recognizably Claude for Desktop validation; the upstream remains unchanged.
    digest = hashlib.sha256(label.encode()).hexdigest()[:8]
    return f"claude-sonnet-4-6-flex-{label}-{digest}"


def cli_model_id(config: dict, label: str) -> str:
    model = config["models"][label]
    kind = config["providers"][model["provider"]]["kind"]
    return model["id"] if kind in {"codex", "claude-oauth"} else f"{model['provider']}/{model['id']}"


def picker_models(config: dict) -> list[dict]:
    selected = config.get("selected_account")
    meta = config["accounts"].get(selected, {})
    channel = meta.get("provider", "codex") if meta else None
    rows, seen = [], set()
    for label, model in config["models"].items():
        provider = config["providers"][model["provider"]]
        if not provider.get("enabled", True):
            continue
        if channel and provider["kind"] in {"codex", "claude-oauth"} and provider["kind"] != ("claude-oauth" if channel == "claude" else "codex"):
            continue
        identity = cli_model_id(config, label)
        if identity not in seen:
            rows.append({"model": identity, "label": model.get("label", model["id"]), "description": "ChatGPT" if provider["kind"] == "codex" else "Claude" if provider["kind"] == "claude-oauth" else model["provider"]})
            seen.add(identity)
    return rows


def choose(config: dict, label: str | None = None) -> tuple[str, dict]:
    label = label or config.get("active")
    label = config.get("model_redirects", {}).get(label, label)
    if not label or label not in config["models"]:
        raise FlexError("Connect an account in Claude Flex to choose a model.")
    model = config["models"][label]
    if not config["providers"][model["provider"]].get("enabled", True):
        raise FlexError("This profile's provider is disabled.")
    return label, model


def compile_gateway(store: Store, *, auth_dir=None) -> dict:
    config = store.load()
    groups = {}
    oauth = {}
    for label, model in config["models"].items():
        provider = config["providers"][model["provider"]]
        if not provider.get("enabled", True):
            continue
        aliases = [model_alias(label)] + [model_alias(source) for source, target in config.get("model_redirects", {}).items() if target == label]
        if provider["kind"] in {"codex", "claude-oauth"}:
            channel = "claude" if provider["kind"] == "claude-oauth" else "codex"
            if provider["kind"] == "codex":
                # Claude Code's built-in Default choice normalizes unknown
                # provider IDs with a claude- prefix. Keep that route working
                # without exposing a second row or pretending GPT is Sonnet.
                aliases.append("claude-" + model["id"])
            oauth.setdefault(channel, []).extend(
                {"name": model["id"], "alias": alias, "fork": True} for alias in aliases
            )
            continue
        kind = "openai-compatibility" if provider["kind"] == "openai" else "codex" if provider["kind"] == "responses" else provider["kind"]
        aliases.append(cli_model_id(config, label))
        group_key = (kind, model["provider"])
        if group_key not in groups:
            groups[group_key] = {
                "name": model["provider"], "base-url": provider["base_url"],
                "keys": key_values(store, provider), "models": [],
            }
        groups[group_key]["models"].extend(
            {"name": model["id"], "alias": alias, "display-name": model.get("label", label), "max-context-length": model.get("context", 200000)}
            for alias in aliases
        )
    api_keys = {}
    for (kind, _), group in groups.items():
        api_keys.setdefault(kind, []).append(group)
    return {
        "config-version": 8,
        "server": {"host": "127.0.0.1", "port": config["gateway"]["port"]},
        "management": {"secret-key": "", "disable-control-panel": True, "allow-remote": False},
        "access": {"api-keys": [store.secrets()["gateway_key"]]},
        "oauth": {"auth-dir": str(auth_dir or store.auth), "model-alias": oauth},
        "api-keys": api_keys,
        "routing": {
            "strategy": config["routing"]["strategy"],
            "session-affinity": config["routing"]["sticky"],
            "session-affinity-subagents": True,
            "retry": {"request-retry": 2, "max-retry-interval": 15},
        },
        "requests": {"streaming": {"keepalive-seconds": 15}},
        "upstream": {"claude": {"disable-cloaking-model-list": True}},
    }


def render(store: Store):
    path = store.runtime / "gateway.json"
    value = compile_gateway(store)
    # Avoid a config reload on every CLI invocation when nothing changed.
    from .store import read_json

    if read_json(path, {}) != value:
        write_json(path, value)
    return path


def cli_settings(store: Store) -> dict:
    env = {
        "ANTHROPIC_BASE_URL": store.url(),
        "ANTHROPIC_AUTH_TOKEN": store.secrets()["gateway_key"],
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
        "ENABLE_TOOL_SEARCH": "false",
        "API_TIMEOUT_MS": "600000",
    }
    rows = picker_models(store.load())
    return {
        "env": env,
        "modelPicker": {"replaceBuiltInOptions": True, "options": rows},
        "availableModels": [row["model"] for row in rows],
        "enforceAvailableModels": True,
    }


def desktop_profile(store: Store) -> dict:
    config = store.load()
    models = [{"name": row["model"], "labelOverride": row["label"]} for row in picker_models(config)]
    profile = {
        "inferenceProvider": "gateway", "inferenceCredentialKind": "static",
        "inferenceGatewayBaseUrl": store.url(),
        "inferenceGatewayApiKey": store.secrets()["gateway_key"],
        "inferenceGatewayAuthScheme": "bearer", "modelDiscoveryEnabled": False,
        "inferenceModels": models, "inferenceModelPricingEnabled": False,
        "toolSearchEnabled": False, "isClaudeCodeForDesktopEnabled": True,
    }
    if config.get("permission_mode") == "bypassPermissions":
        profile["disableBypassPermissionsMode"] = False
    return profile
