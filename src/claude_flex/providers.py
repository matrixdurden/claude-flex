"""Provider presets and public catalog discovery; no model availability hard-coding."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from . import __version__
from .store import FlexError, Store, base_url, name

PRESETS = {
    "openrouter": ("openai", "https://openrouter.ai/api/v1", False),
    "ollama": ("openai", "http://127.0.0.1:11434/v1", True),
    "lmstudio": ("openai", "http://127.0.0.1:1234/v1", True),
    "openai": ("responses", "https://api.openai.com/v1", False),
    "anthropic": ("claude", "https://api.anthropic.com", False),
    "gemini": ("gemini", "https://generativelanguage.googleapis.com", False),
    "groq": ("openai", "https://api.groq.com/openai/v1", False),
    "github": ("openai", "https://models.github.ai/inference", False),
}


def opener(url: str):
    if urlsplit(url).hostname in {"localhost", "127.0.0.1", "::1"}:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def request_json(url: str, *, headers: dict | None = None, body: dict | None = None, timeout=20):
    merged = {"User-Agent": f"claude-flex/{__version__}", **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        merged["Content-Type"] = "application/json"
    request = urllib.request.Request(url, headers=merged, data=data)
    try:
        with opener(url).open(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Upstream bodies can echo request headers or credentials: never print them.
        raise FlexError(f"HTTP {exc.code} from {urlsplit(url).hostname}.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise FlexError(f"Cannot reach {urlsplit(url).hostname}. Check the service and base URL.") from None
    except ValueError:
        raise FlexError("The endpoint did not return valid JSON.") from None


def key_values(store: Store, provider: dict) -> list[dict]:
    if provider.get("no_auth"):
        return [{"api-key": "local-no-auth"}]
    secrets = store.secrets()["keys"]
    result = []
    for key in provider.get("keys", []):
        value = os.environ.get(key["env"]) if "env" in key else secrets.get(key["secret"])
        if not value:
            raise FlexError(f"Missing credential '{key['name']}'. Set its environment variable or add a stored key.")
        result.append({"api-key": value, "weight": key.get("weight", 1)})
    if not result:
        raise FlexError("Provider has no credentials. Use 'providers key add', --key-env, or --no-auth for a local endpoint.")
    return result


def add_provider(store: Store, label: str, *, preset=None, kind=None, url=None, no_auth=False):
    name(label)
    if preset:
        kind, default_url, local = PRESETS[preset]
        url = url or default_url
        no_auth = no_auth or local
    if not kind or not url:
        raise FlexError("Custom providers require --kind and --base-url.")
    provider = {"kind": kind, "base_url": base_url(url), "no_auth": no_auth, "keys": [], "enabled": True}

    def change(config):
        if label in config["providers"]:
            raise FlexError(f"Provider already exists: {label}")
        config["providers"][label] = provider

    store.mutate(change)


def add_key(store: Store, provider: str, label: str, *, env=None, value=None, weight=1):
    name(label)
    if weight < 1 or weight > 1_000_000:
        raise FlexError("Weight must be 1..1000000.")
    config = store.load()
    if provider not in config["providers"] or config["providers"][provider]["kind"] in {"codex", "claude-oauth"}:
        raise FlexError("Choose an API-key provider. Codex uses 'accounts login'.")
    if any(item["name"] == label for item in config["providers"][provider].get("keys", [])):
        raise FlexError(f"Key label already exists: {label}")
    if env and not env.isidentifier():
        raise FlexError("Invalid environment variable name.")
    key = {"name": label, "weight": weight}
    if env:
        key["env"] = env
    elif value:
        reference = f"{provider}/{label}"
        store.put_secret(reference, value)
        key["secret"] = reference
    else:
        raise FlexError("Empty API key.")

    def change(current):
        target = current["providers"][provider]
        if any(item["name"] == label for item in target["keys"]):
            raise FlexError(f"Key label already exists: {label}")
        target["keys"].append(key)
        target["no_auth"] = False

    store.mutate(change)


def discover(store: Store, label: str, *, free=False, tools=False) -> list[dict]:
    provider = store.load()["providers"].get(label)
    if provider is None:
        if label not in PRESETS:
            raise FlexError("Unknown provider.")
        kind, url, no_auth = PRESETS[label]
        provider = {"kind": kind, "base_url": url, "no_auth": no_auth, "keys": []}
    if provider["kind"] in {"codex", "claude-oauth"}:
        raise FlexError("Use 'gateway models' to discover authenticated Codex models.")
    url = provider["base_url"]
    headers = {}
    # OpenRouter's catalog is public. Its inference endpoint still requires a key.
    if urlsplit(url).hostname != "openrouter.ai" and not provider.get("no_auth"):
        key = key_values(store, provider)[0]["api-key"]
        if provider["kind"] == "claude":
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        elif provider["kind"] == "gemini":
            headers = {"x-goog-api-key": key}
        else:
            headers = {"Authorization": f"Bearer {key}"}
    endpoint = "/v1beta/models" if provider["kind"] == "gemini" else "/v1/models" if provider["kind"] == "claude" else "/models"
    payload = request_json(url + endpoint, headers=headers)
    result = []
    for item in payload.get("data", payload.get("models", [])):
        identity = item.get("id", item.get("name", ""))
        pricing = item.get("pricing", {})
        try:
            is_free = bool(pricing) and float(pricing.get("prompt", -1)) == 0 and float(pricing.get("completion", -1)) == 0
        except (ValueError, TypeError):
            is_free = False
        parameters = item.get("supported_parameters", [])
        has_tools = "tools" in parameters
        if free and not is_free:
            continue
        if tools and not has_tools:
            continue
        result.append({"id": identity, "label": item.get("name", identity), "free": is_free, "tools": has_tools, "context": item.get("context_length")})
    return result
