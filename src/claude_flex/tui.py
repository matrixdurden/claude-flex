"""A quiet account switcher; model selection belongs to Claude Code."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from . import accounts, desktop, service
from .compiler import render
from .gateway import Gateway
from .login import FINISHED, LoginManager
from .providers import PRESETS, add_key, add_provider
from .store import STRATEGIES, FlexError, Store
from .usage import UsageService

ACCENT = "#a8d5bc"
MUTED = "#858b96"


def plain(value) -> str:
    return "".join(character for character in str(value) if character.isprintable())


def percent(usage, seconds):
    if not usage:
        return "—"
    value = next((item for item in usage.get("windows", []) if item.get("duration_seconds") == seconds), None)
    if value is None or value["remaining_percent"] is None:
        return "—"
    return f"{value['remaining_percent']:g}%" + ("*" if usage.get("stale") else "")


def reset_text(timestamp):
    if not timestamp:
        return "reset unknown"
    seconds = max(0, int(timestamp - time.time()))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    duration = f"{days}d {hours}h" if days else f"{hours}h {minutes}m" if hours else f"{minutes}m"
    return f"resets in {duration}"


class Dialog(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Back", show=False)]

    def action_cancel(self):
        self.dismiss(None)


class Choice(Dialog):
    def __init__(self, title, choices, subtitle=""):
        super().__init__()
        self.title_text, self.choices, self.subtitle = title, choices, subtitle

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.title_text, classes="dialog-title", markup=False)
            if self.subtitle:
                yield Static(self.subtitle, classes="muted", markup=False)
            yield OptionList(*(Option(text, id=key) for key, text in self.choices), id="choices")
            yield Static("enter select · esc back", classes="hint")

    def on_mount(self):
        self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def selected(self, event):
        self.dismiss(event.option.id)


class Form(Dialog):
    def __init__(self, title, fields, note=""):
        super().__init__()
        self.title_text, self.fields, self.note = title, fields, note

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Static(self.title_text, classes="dialog-title", markup=False)
            if self.note:
                yield Static(self.note, classes="muted", markup=False)
            for key, label, value, password in self.fields:
                yield Label(label)
                yield Input(value=str(value), password=password, id=key)
            yield Static("", id="form-error", classes="error", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Save", id="submit", variant="primary")
                yield Button("Back", id="cancel")

    def on_mount(self):
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def submitted(self):
        inputs = list(self.query(Input))
        current = next((index for index, widget in enumerate(inputs) if widget.has_focus), len(inputs) - 1)
        if current + 1 < len(inputs):
            inputs[current + 1].focus()
        else:
            self.submit()

    @on(Button.Pressed)
    def pressed(self, event):
        if event.button.id == "cancel":
            self.dismiss(None)
        else:
            self.submit()

    def submit(self):
        values = {key: self.query_one(f"#{key}", Input).value.strip() for key, *_ in self.fields}
        if any(not value for value in values.values()):
            self.query_one("#form-error", Static).update("Required fields are empty.")
        else:
            self.dismiss(values)


class LoginDialog(Dialog):
    BINDINGS = [Binding("p", "paste_callback", "Paste return URL", show=False), *Dialog.BINDINGS]

    def __init__(self, manager, job, *, open_automatically=True):
        super().__init__()
        self.manager, self.job = manager, job
        self.opened = False
        self.open_automatically = open_automatically

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("Sign in to Claude" if self.job["provider"] == "claude" else "Sign in to ChatGPT", classes="dialog-title")
            yield Static("Preparing sign-in…", id="login-status", markup=False)
            yield Static("", id="device-code", markup=False)
            yield Static("", id="sign-in-link", markup=False)
            with Horizontal(classes="buttons"):
                if self.open_automatically:
                    yield Button("Open browser", id="browser", variant="primary", disabled=True)
                else:
                    yield Button("Copy link", id="copy-link", variant="primary", disabled=True)
                yield Button("Cancel", id="cancel")
            yield Static("p return URL · esc cancel", classes="hint")

    def on_mount(self):
        self.set_interval(0.4, self.poll)

    async def poll(self):
        self.job = self.manager.get(self.job["id"])
        state = self.job["status"]
        self.query_one("#login-status", Static).update({"starting": "Preparing sign-in…", "waiting": "Waiting for browser sign-in…", "importing": "Saving account…"}.get(state, self.job.get("error") or state))
        ready = bool(self.job.get("url"))
        primary = "#browser" if self.open_automatically else "#copy-link"
        self.query_one(primary, Button).disabled = not ready
        self.query_one("#device-code", Static).update(self.job.get("device_code") or "")
        self.query_one("#sign-in-link", Static).update(Text(self.job["url"] or "") if not self.open_automatically else "")
        if ready and not self.opened and self.open_automatically:
            self.opened = True
            try:
                await asyncio.to_thread(service.open_browser, self.job["url"])
            except (FlexError, OSError):
                self.query_one("#login-status", Static).update("Use Open browser or paste the callback after signing in.")
        if state in FINISHED:
            self.dismiss(self.job)

    @on(Button.Pressed)
    async def pressed(self, event):
        if event.button.id == "cancel":
            self.action_cancel()
        elif event.button.id == "browser" and self.job.get("url"):
            try:
                await asyncio.to_thread(service.open_browser, self.job["url"])
            except (FlexError, OSError):
                self.query_one("#login-status", Static).update(Text(self.job["url"]))
        elif event.button.id == "copy-link" and self.job.get("url"):
            self.app.copy_to_clipboard(self.job["url"])

    def action_paste_callback(self):
        if self.job["method"] == "browser" and self.job.get("url"):
            self.app.push_screen(Form("Paste return URL", [("url", "The localhost URL shown after sign-in", "", False)]), self.handle_callback)

    def handle_callback(self, values):
        if values:
            self.forward_callback(values["url"])

    @work
    async def forward_callback(self, url):
        try:
            await asyncio.to_thread(self.manager.callback, self.job["id"], url)
        except FlexError as exc:
            self.query_one("#login-status", Static).update(str(exc))

    def action_cancel(self):
        self.manager.cancel(self.job["id"])
        self.dismiss(None)


class FlexApp(App):
    TITLE = "Claude Flex"
    ENABLE_COMMAND_PALETTE = False
    CSS_PATH = Path(__file__).with_name("tui.tcss")
    BINDINGS = [
        Binding("a", "add", "Add", show=False),
        Binding("d", "desktop", "Desktop", show=False),
        Binding("space", "toggle", "Toggle", show=False),
        Binding("r", "refresh", "Refresh", show=False),
        Binding("s", "settings", "Settings", show=False),
        Binding("q", "quit", "Quit", show=False),
    ]

    def __init__(self, store: Store, *, usage=None, logins=None, launch=None, catalog_sync=None, activate=None, desktop_launch=None):
        super().__init__()
        self.store = store
        self.usage = usage or UsageService(store)
        self.logins = logins or LoginManager(store)
        self.launch = launch or service.run_claude
        self.desktop_launch = desktop_launch or desktop.open_profile
        self.activate = activate or service.activate_account
        self.catalog_sync = catalog_sync or service.sync_models
        self.catalog_refreshing = False
        self.entries = {}
        self.selected_key = None
        self.working = False
        self.refreshing = False

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            yield Static(Text.assemble(("claude", "bold"), (" flex", ACCENT)), id="brand")
            yield OptionList(id="list")
            yield Static("", id="message", markup=False)
            yield Static("", id="keys", classes="hint", markup=False)

    def on_mount(self):
        self.theme = "textual-dark"
        self.listing = self.query_one("#list", OptionList)
        service.merge_duplicate_models(self.store)
        self.draw()
        self.listing.focus()
        self.refresh_usage()
        self.refresh_models()
        self.set_interval(60, self.refresh_usage)
        self.set_interval(300, self.refresh_models)

    def main_screen(self):
        return len(self.screen_stack) == 1

    def check_action(self, action, parameters):
        if action in {"quit", "add", "desktop", "settings", "toggle", "refresh"} and not self.main_screen():
            return False
        return True

    def draw(self):
        config = self.store.load()
        rows = []
        self.entries = {}
        width = self.size.width
        data = accounts.account_rows(self.store)
        cached = self.usage.cached()
        if data:
            rows.append(Option(Text("accounts".ljust(38 if width >= 70 else 23) + "5h / 7d left", style=MUTED), disabled=True))
        for row in data:
            usage = cached.get(row["name"])
            line = Text()
            selected = row["enabled"] and row["name"] == config.get("selected_account")
            line.append("● " if selected else "○ ", style=ACCENT if selected else MUTED)
            line.append(plain(row["name"])[:18].ljust(20))
            if width >= 70:
                line.append(("Claude" if row["provider"] == "claude" else "ChatGPT").ljust(18), style=MUTED)
            line.append(f"{percent(usage, 18000):>5} / {percent(usage, 604800):>5}", style=MUTED if not usage or not usage.get("error") else "#d8ae79")
            key = f"account:{row['name']}"
            self.entries[key] = ("accounts", row["name"])
            rows.append(Option(line, id=key))
        if not rows:
            rows.append(Option(Text("No accounts", style=MUTED), disabled=True))
        if self.selected_key not in self.entries:
            self.selected_key = f"account:{config.get('selected_account')}" if f"account:{config.get('selected_account')}" in self.entries else next(iter(self.entries), None)
        self.listing.clear_options()
        self.listing.add_options(rows)
        self.listing.highlighted = next((index for index, option in enumerate(rows) if option.id == self.selected_key), None)
        self.query_one("#keys", Static).update("enter terminal · d desktop · a add · s more · q quit")

    def current(self):
        return self.entries.get(self.selected_key, (None, None))[1]

    @on(OptionList.OptionHighlighted, "#list")
    def highlighted(self, event):
        self.selected_key = event.option.id

    def message(self, text, error=False):
        self.query_one("#message", Static).update(Text(plain(text), style="#e1a29a" if error else ACCENT))

    @work(exclusive=True, group="usage")
    async def refresh_usage(self):
        if self.refreshing:
            return
        self.refreshing = True
        try:
            await asyncio.to_thread(self.usage.all)
            if self.main_screen():
                self.draw()
        except Exception:
            if self.main_screen():
                self.message("Limits unavailable.", True)
        finally:
            self.refreshing = False

    def action_refresh(self):
        if self.main_screen():
            self.refresh_usage()
            self.refresh_models(force=True)

    @work(group="catalog")
    async def refresh_models(self, *, force=False):
        if self.catalog_refreshing:
            return
        self.catalog_refreshing = True
        try:
            report = await asyncio.to_thread(self.catalog_sync, self.store, force=force)
            if self.main_screen():
                self.draw()
                if force and report.get("errors"):
                    self.message("Models unavailable.", True)
        except (FlexError, OSError):
            if self.main_screen() and force:
                self.message("Models unavailable.", True)
        finally:
            self.catalog_refreshing = False

    async def perform(self, function, *args, **kwargs):
        self.working = True
        try:
            result = await asyncio.to_thread(function, *args, **kwargs)
            return result
        finally:
            self.working = False

    async def backend(self):
        self.message("Preparing…")
        await self.perform(service.ensure_backend, self.store)

    @on(OptionList.OptionSelected, "#list")
    def selected(self, event):
        self.selected_key = event.option.id
        self.use_account(self.current())

    @work(exclusive=True, group="operations")
    async def use_account(self, label, *, target="terminal"):
        try:
            self.message("Opening…")
            await self.perform(self.activate, self.store, label)
            if target == "desktop":
                restart_required = await self.perform(self.desktop_launch, self.store)
                self.message("Restart Desktop." if restart_required else "")
            else:
                with self.suspend():
                    code = await asyncio.to_thread(self.launch, self.store)
                self.message(f"Exit {code}." if code else "")
            self.draw()
        except (FlexError, OSError) as exc:
            self.message(str(exc), True)

    def action_desktop(self):
        if self.main_screen() and self.current():
            self.use_account(self.current(), target="desktop")

    def action_add(self):
        if self.main_screen():
            self.add_flow()

    @work(exclusive=True, group="operations")
    async def add_flow(self):
        try:
            await self.add_account()
            self.draw()
            self.refresh_usage()
        except (FlexError, OSError, ValueError) as exc:
            self.message(str(exc), True)

    async def add_account(self, *, renew=None):
        config = self.store.load()
        if renew:
            meta = config["accounts"][renew]
            provider, label = accounts.provider_of(meta), renew
        else:
            provider = await self.push_screen_wait(Choice("Choose provider", [("claude", "Claude"), ("codex", "ChatGPT")]))
            if not provider:
                return
            label = service.next_account_name(self.store, provider)
        methods = [("browser", "Sign in with browser"), ("link", "Use a sign-in link")]
        if provider == "codex":
            methods += [("device", "Sign in with device code")]
            if not renew:
                methods += [("import", "Use existing OpenCode sign-in")]
        method = await self.push_screen_wait(Choice("Claude sign-in" if provider == "claude" else "ChatGPT sign-in", methods))
        if not method:
            return
        if method == "import":
            await self.perform(service.import_opencode, self.store, label)
            await self.account_connected(label)
            return
        await self.backend()
        job = self.logins.start(label, method="device" if method == "device" else "browser", provider=provider, replace=bool(renew), weight=meta["weight"] if renew else 1, priority=meta["priority"] if renew else 0)
        result = await self.push_screen_wait(LoginDialog(self.logins, job, open_automatically=method != "link"))
        if result and result["status"] == "complete":
            report = await self.perform(service.default_account_models, self.store, provider)
            if renew:
                self.message("")
            else:
                await self.account_connected(label)
            if report.get("errors"):
                self.message("Models unavailable.", True)
        elif result and result.get("error"):
            self.message(result["error"], True)

    async def account_connected(self, label):
        self.selected_key = f"account:{label}"
        await self.perform(self.activate, self.store, label)
        self.message("")

    async def connect_provider(self):
        preset = await self.push_screen_wait(Choice("Connect provider", [(key, key.title()) for key in PRESETS] + [("__custom", "Custom endpoint")]))
        if not preset:
            return None
        fields = [("name", "Provider name", "custom" if preset == "__custom" else preset, False)]
        if preset == "__custom":
            kind = await self.push_screen_wait(Choice("API format", [("openai", "OpenAI Chat Completions"), ("responses", "OpenAI Responses"), ("claude", "Anthropic Messages"), ("gemini", "Gemini")]))
            if not kind:
                return None
            fields += [("url", "Base URL", "http://127.0.0.1:1234/v1", False)]
            no_auth = await self.push_screen_wait(Choice("Authentication", [("key", "API key"), ("none", "No key · local server")]))
            if not no_auth:
                return None
            local = no_auth == "none"
        else:
            kind, _, local = PRESETS[preset]
        if not local:
            fields += [("key", "API key", "", True)]
        values = await self.push_screen_wait(Form("Provider connection", fields))
        if not values:
            return None
        add_provider(self.store, values["name"], preset=None if preset == "__custom" else preset, kind=kind, url=values.get("url"), no_auth=local)
        if not local:
            add_key(self.store, values["name"], "default", value=values["key"])
        await self.perform(service.sync_models, self.store, providers=[values["name"]], force=True)
        return values["name"]

    def action_toggle(self):
        if self.main_screen() and self.current():
            self.toggle_account(self.current())

    @work(exclusive=True, group="operations")
    async def toggle_account(self, label):
        try:
            enabled = self.store.load()["accounts"][label]["enabled"]
            await self.perform(accounts.update_account, self.store, label, enabled=not enabled)
            self.draw()
        except (FlexError, OSError) as exc:
            self.message(str(exc), True)

    async def account_details(self, label):
        try:
            row = next(item for item in accounts.account_rows(self.store) if item["name"] == label)
            usage = self.usage.cached().get(label, {})
            detail = [f"{row['email']} · {row['plan']}"]
            for item in usage.get("windows", []):
                remaining = f"{item['remaining_percent']:g}%" if item["remaining_percent"] is not None else "—"
                detail.append(f"{item['scope']} · {item['title']}: {remaining} left · {reset_text(item['resets_at'])}")
            if usage.get("error"):
                detail.append(usage["error"])
            action = await self.push_screen_wait(Choice(label, [("select", "Use without opening"), ("toggle", "Pause" if row["enabled"] else "Enable"), ("renew", "Sign in again"), ("edit", "Routing"), ("remove", "Remove")], "\n".join(detail)))
            if action == "select":
                await self.perform(self.activate, self.store, label)
            elif action == "toggle":
                await self.perform(accounts.update_account, self.store, label, enabled=not row["enabled"])
            elif action == "renew":
                await self.add_account(renew=label)
            elif action == "edit":
                values = await self.push_screen_wait(Form("Account routing", [("weight", "Weight", row["weight"], False), ("priority", "Priority", row["priority"], False)]))
                if values:
                    await self.perform(accounts.update_account, self.store, label, weight=int(values["weight"]), priority=int(values["priority"]))
            elif action == "remove":
                result = await self.push_screen_wait(Choice(f"Remove {label}?", [("back", "Keep account"), ("remove", "Remove from this computer")]))
                if result == "remove":
                    await self.perform(accounts.remove_account, self.store, label)
            self.draw()
        except (FlexError, OSError, ValueError) as exc:
            self.message(str(exc), True)

    def action_settings(self):
        if self.main_screen():
            self.settings_flow()

    @work(exclusive=True, group="operations")
    async def settings_flow(self):
        try:
            action = await self.push_screen_wait(Choice("Settings", [("details", "Account details"), ("provider", "API provider"), ("pool", "Account pool"), ("all", "Enable all"), ("stop", "Stop gateway")]))
            if action == "details" and self.current():
                await self.account_details(self.current())
            elif action == "provider":
                await self.connect_provider()
            elif action == "pool":
                strategy = await self.push_screen_wait(Choice("Account distribution", [(key, key.replace("-", " ").title()) for key in sorted(STRATEGIES)]))
                if strategy:
                    sticky = await self.push_screen_wait(Choice("Keep each session on one account?", [("yes", "Yes · recommended"), ("no", "No · distribute each request")]))
                    if sticky:
                        self.store.mutate(lambda config: config["routing"].update(strategy=strategy, sticky=sticky == "yes"))
                        render(self.store)
            elif action == "all":
                await self.perform(accounts.select_accounts, self.store, "all")
            elif action == "stop":
                await self.perform(Gateway(self.store).stop)
                self.message("")
            self.draw()
        except (FlexError, OSError, ValueError) as exc:
            self.message(str(exc), True)


def run_tui(store: Store):
    store.init()
    app = FlexApp(store)
    try:
        app.run()
    finally:
        app.logins.close()
