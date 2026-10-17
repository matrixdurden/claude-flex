"""Read-only Claude and Codex quota snapshots; missing capacity stays unknown."""

from __future__ import annotations

import concurrent.futures
import json
import math
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

from .accounts import sync_sources
from .store import FlexError, Store, read_json

USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def window(data, scope, title, now):
    if not isinstance(data, dict):
        return None
    used = number(data.get("used_percent"))
    used = max(0, min(100, used)) if used is not None and used >= 0 else None
    duration = number(data.get("limit_window_seconds"))
    reset = number(data.get("reset_at"))
    after = number(data.get("reset_after_seconds"))
    if reset is None and after is not None:
        reset = now + max(0, after)
    if duration and duration > 0:
        if duration % 86400 == 0:
            title = f"{int(duration / 86400)} gün"
        elif duration % 3600 == 0:
            title = f"{int(duration / 3600)} saat"
        else:
            title = f"{round(duration / 60)} dakika"
    return {
        "scope": scope, "title": title, "used_percent": used,
        "remaining_percent": None if used is None else round(100 - used, 2),
        "duration_seconds": duration, "resets_at": reset,
    }


def normalize_usage(payload: dict, now: float | None = None) -> dict:
    if not isinstance(payload, dict):
        raise FlexError("OpenAI kullanım yanıtı okunamadı.")
    now = now if now is not None else time.time()
    root = payload.get("rate_limits") if isinstance(payload.get("rate_limits"), dict) else payload
    main = root.get("rate_limit")
    main = main if isinstance(main, dict) else {}
    windows = []

    def append_windows(data, scope):
        if not isinstance(data, dict):
            return
        for key, title in (("primary_window", "Birincil pencere"), ("secondary_window", "İkincil pencere")):
            value = window(data.get(key), scope, title, now)
            if value is not None:
                windows.append(value)

    append_windows(main, "Codex")
    append_windows(root.get("code_review_rate_limit"), "Kod incelemesi")
    additional = payload.get("additional_rate_limits", root.get("additional_rate_limits", []))
    for item in additional if isinstance(additional, list) else []:
        if isinstance(item, dict):
            details = item.get("details", item)
            if isinstance(details, dict):
                scope = details.get("limit_name") or details.get("metered_feature")
                append_windows(details.get("rate_limit"), scope if isinstance(scope, str) else "Ek limit")
    credits = root.get("credits")
    safe_credits = None
    if isinstance(credits, dict):
        safe_credits = {
            "has_credits": credits.get("has_credits") if isinstance(credits.get("has_credits"), bool) else None,
            "unlimited": credits.get("unlimited") if isinstance(credits.get("unlimited"), bool) else None,
            "balance": str(credits["balance"])[:80] if isinstance(credits.get("balance"), (str, int, float)) else None,
        }
    return {
        "plan": root.get("plan_type") if isinstance(root.get("plan_type"), str) else None,
        "allowed": main.get("allowed") if isinstance(main.get("allowed"), bool) else None,
        "limit_reached": main.get("limit_reached") if isinstance(main.get("limit_reached"), bool) else None,
        "windows": windows, "credits": safe_credits,
        "checked_at": datetime.fromtimestamp(now, UTC).isoformat(),
        "stale": False, "error": None,
    }


def fetch_usage(credential: dict) -> dict:
    if credential.get("type") == "claude":
        return fetch_claude_usage(credential)
    token = credential.get("access_token")
    account = credential.get("account_id")
    if not token or not account:
        raise FlexError("Hesabın giriş bilgisi eksik; yeniden giriş yap.")
    request = urllib.request.Request(USAGE_URL, headers={
        "Authorization": "Bearer " + token, "ChatGPT-Account-Id": account,
        "User-Agent": "codex-cli", "Accept": "application/json",
    })
    try:
        # Never forward an account credential to a redirect destination.
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
            return normalize_usage(json.load(response))
    except urllib.error.HTTPError as exc:
        messages = {
            401: "OpenAI oturumu geçersiz veya süresi dolmuş; girişini yenile.",
            403: "OpenAI kullanım sorgusunu reddetti (403); limit bilgisi alınamadı.",
            429: "OpenAI kullanım sorgusuna hız sınırı uyguladı; biraz sonra yenile.",
        }
        raise FlexError(messages.get(exc.code, f"OpenAI kullanım sorgusu başarısız (HTTP {exc.code}).")) from None
    except (OSError, TimeoutError, ValueError):
        raise FlexError("OpenAI kullanım servisine ulaşılamadı veya yanıt okunamadı.") from None


def normalize_claude_usage(payload: dict, now=None) -> dict:
    if not isinstance(payload, dict):
        raise FlexError("Claude usage response could not be read.")
    now = time.time() if now is None else now
    windows = []
    for key, value in payload.items():
        if key == "extra_usage" or not isinstance(value, dict) or "utilization" not in value or "resets_at" not in value:
            continue
        duration = 18000 if key == "five_hour" else 604800 if key.startswith("seven_day") else None
        reset = None
        if isinstance(value.get("resets_at"), str):
            try:
                reset = datetime.fromisoformat(value["resets_at"].replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        scope = "Claude" if key in {"five_hour", "seven_day"} else key.replace("seven_day_", "").replace("_", " ").title()
        result = window({"used_percent": value.get("utilization"), "limit_window_seconds": duration, "reset_at": reset}, scope, key.replace("_", " "), now)
        windows.append(result)
    return {"plan": None, "allowed": None, "limit_reached": None, "windows": windows, "credits": None, "checked_at": datetime.fromtimestamp(now, UTC).isoformat(), "stale": False, "error": None}


def fetch_claude_usage(credential: dict) -> dict:
    if not credential.get("access_token"):
        raise FlexError("Claude login is missing. Sign in again.")
    request = urllib.request.Request(CLAUDE_USAGE_URL, headers={
        "Authorization": "Bearer " + credential["access_token"],
        "anthropic-beta": "oauth-2025-04-20", "Accept": "application/json",
        "User-Agent": "claude-cli/2.1.280 (external, cli)",
    })
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
            return normalize_claude_usage(json.load(response))
    except urllib.error.HTTPError as exc:
        raise FlexError(f"Claude usage unavailable (HTTP {exc.code}). Renew sign-in if it has expired.") from None
    except (OSError, ValueError):
        raise FlexError("Claude usage service is unavailable.") from None


class UsageService:
    def __init__(self, store: Store, ttl=60, min_interval=5):
        self.store = store
        self.ttl = ttl
        self.min_interval = min_interval
        self.cache = {}
        self.attempted = {}
        self.lock = threading.RLock()
        self.account_locks = {}
        self.identities = {}

    def cached(self) -> dict:
        accounts = self.store.load()["accounts"]
        with self.lock:
            return {label: dict(data) for label, data in self.cache.items() if label in accounts and self.identities.get(label) == accounts[label].get("identity")}

    def one(self, label: str, *, force=False) -> dict:
        account = self.store.load()["accounts"].get(label)
        if account is None:
            raise FlexError("Hesap bulunamadı.")
        with self.lock:
            account_lock = self.account_locks.setdefault(label, threading.Lock())
        with account_lock:
            now = time.monotonic()
            with self.lock:
                if self.identities.get(label) != account.get("identity"):
                    self.cache.pop(label, None)
                    self.attempted.pop(label, None)
                    self.identities[label] = account.get("identity")
                age = now - self.attempted.get(label, -math.inf)
                if label in self.cache and age < (self.min_interval if force else self.ttl):
                    return dict(self.cache[label])
                self.attempted[label] = now
            try:
                data = fetch_usage(read_json(self.store.account_path(label)))
            except (FlexError, OSError) as exc:
                with self.lock:
                    data = dict(self.cache.get(label, {"windows": [], "credits": None, "checked_at": None, "plan": None, "allowed": None, "limit_reached": None}))
                data.update(stale=True, error=str(exc) if isinstance(exc, FlexError) else "Yerel hesap dosyası okunamadı.")
            with self.lock:
                self.cache[label] = data
            return dict(data)

    def all(self, *, force=False) -> dict:
        sync_sources(self.store)
        labels = list(self.store.load()["accounts"])
        if not labels:
            return {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(labels))) as pool:
            results = list(pool.map(lambda label: self.one(label, force=force), labels))
        return dict(zip(labels, results, strict=True))
