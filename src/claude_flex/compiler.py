"""Compile provider-neutral profiles into CLIProxyAPI v8 and Claude settings."""

from __future__ import annotations

import hashlib

from .providers import key_values
from .store import FlexError, Store, write_json


def model_alias(label: str) -> str:
    # Recognizably Claude for Desktop validation; the upstream remains unchanged.
    digest = hashlib.sha256(label.encode()).hexdigest()[:8]
    return f"claude-sonnet-4-6-flex-{label}-{digest}"


def choose(config: dict, label: str | None = None) -> tuple[str, dict]:
    label = label or config.get("active")
    if not label or label not in config["models"]:
        raise FlexError("Select a profile: models add NAME --provider NAME --id MODEL, then models use NAME.")
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
        aliases = [model_alias(label)]
        if provider["kind"] in {"codex", "claude-oauth"}:
            channel = "claude" if provider["kind"] == "claude-oauth" else "codex"
            oauth.setdefault(channel, []).extend(
                {"name": model["id"], "alias": alias, "fork": True} for alias in aliases
            )
            continue
        kind = "openai-compatibility" if provider["kind"] == "openai" else "codex" if provider["kind"] == "responses" else provider["kind"]
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
    }


def render(store: Store):
    path = store.runtime / "gateway.json"
    value = compile_gateway(store)
    # Avoid a config reload on every CLI invocation when nothing changed.
    from .store import read_json

    if read_json(path, {}) != value:
        write_json(path, value)
    return path


def cli_settings(store: Store, label: str | None = None) -> dict:
    label, model = choose(store.load(), label)
    alias = model_alias(label)
    env = {
        "ANTHROPIC_BASE_URL": store.url(),
        "ANTHROPIC_AUTH_TOKEN": store.secrets()["gateway_key"],
        "ANTHROPIC_CUSTOM_MODEL_OPTION": alias,
        "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME": model.get("label", label),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS": str(model.get("context", 200000)),
        "ENABLE_TOOL_SEARCH": "false",
        "API_TIMEOUT_MS": "600000",
        "CLAUDE_CODE_SUBAGENT_MODEL": alias,
        "ANTHROPIC_SMALL_FAST_MODEL": alias,
    }
    for family in ("OPUS", "SONNET", "HAIKU", "FABLE"):
        env[f"ANTHROPIC_DEFAULT_{family}_MODEL"] = alias
    return {"model": alias, "env": env}


def desktop_profile(store: Store) -> dict:
    config = store.load()
    active, _ = choose(config)
    ordered = [active] + [key for key in config["models"] if key != active]
    models = []
    for label in ordered:
        model = config["models"][label]
        if not config["providers"][model["provider"]].get("enabled", True):
            continue
        models.append({
            "name": model_alias(label),
            "labelOverride": model.get("label", label),
        })
    return {
        "inferenceProvider": "gateway", "inferenceCredentialKind": "static",
        "inferenceGatewayBaseUrl": store.url(),
        "inferenceGatewayApiKey": store.secrets()["gateway_key"],
        "inferenceGatewayAuthScheme": "bearer", "modelDiscoveryEnabled": False,
        "inferenceModels": models, "inferenceModelPricingEnabled": False,
        "toolSearchEnabled": False, "isClaudeCodeForDesktopEnabled": True,
    }
