"""Two tabs, a handful of keys, and the real Claude Code underneath."""

from __future__ import annotations

import asyncio
import time

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static, Tab, Tabs
from textual.widgets.option_list import Option

from . import accounts, desktop, service
from .compiler import render
from .gateway import Gateway
from .login import FINISHED, LoginManager
from .providers import PRESETS, add_key, add_provider, discover
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
            yield Static("↑↓ select    enter continue    esc back", classes="hint")

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
                yield Button("Continue", id="submit", variant="primary")
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
            self.query_one("#form-error", Static).update("Fill in each field.")
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
            yield Static("Choose the right account in your browser.\nYour password stays with the provider.", classes="muted")
            yield Static("Preparing sign-in…", id="login-status", markup=False)
            yield Static("", id="device-code", markup=False)
            yield Static("", id="sign-in-link", markup=False)
            with Horizontal(classes="buttons"):
                if self.open_automatically:
                    yield Button("Open browser", id="browser", variant="primary", disabled=True)
                else:
                    yield Button("Copy link", id="copy-link", variant="primary", disabled=True)
                yield Button("Cancel", id="cancel")
            yield Static("p paste return URL if needed · esc cancel", classes="hint")

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
    CSS_PATH = "tui.tcss"
    BINDINGS = [
        Binding("tab", "switch_tab", "Switch tab", show=False, priority=True),
        Binding("a", "add", "Add", show=False),
        Binding("space", "toggle", "Toggle", show=False),
        Binding("r", "refresh", "Refresh", show=False),
        Binding("s", "settings", "Settings", show=False),
        Binding("d", "default", "Default", show=False),
        Binding("q", "quit", "Quit", show=False),
    ]

    def __init__(self, store: Store, *, usage=None, logins=None, launch=None):
        super().__init__()
        self.store = store
        self.usage = usage or UsageService(store)
        self.logins = logins or LoginManager(store)
        self.launch = launch or service.run_claude
        self.section = "models"
        self.rows = []
        self.selection = {"models": 0, "accounts": 0}
        self.working = False
        self.refreshing = False

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            yield Static(Text.assemble(("claude", "bold"), (" flex", ACCENT)), id="brand")
            yield Tabs(Tab("Models", id="models"), Tab("Accounts", id="accounts"), id="tabs")
            yield Static("", id="columns", classes="muted", markup=False)
            yield OptionList(id="list")
            yield Static("", id="detail", markup=False)
            yield Static("", id="message", markup=False)
            yield Static("", id="keys", classes="hint", markup=False)

    def on_mount(self):
        self.theme = "textual-dark"
        self.listing = self.query_one("#list", OptionList)
        config = self.store.load()
        if not config["models"] and not config["accounts"]:
            self.section = "accounts"
            self.query_one(Tabs).active = "accounts"
        self.draw()
        self.listing.focus()
        self.refresh_usage()
        self.set_interval(60, self.refresh_usage)

    def main_screen(self):
        return len(self.screen_stack) == 1

    def check_action(self, action, parameters):
        if action in {"switch_tab", "quit", "add", "settings", "toggle", "refresh", "default"} and not self.main_screen():
            return False
        return True

    def draw(self):
        config = self.store.load()
        rows = []
        width = self.size.width
        if self.section == "models":
            self.query_one("#columns", Static).update("Choose a model. Enter opens Claude Code in this folder.")
            self.rows = list(config["models"])
            for label, model in config["models"].items():
                provider = config["providers"][model["provider"]]
                source = "Claude" if provider["kind"] == "claude-oauth" else "ChatGPT" if provider["kind"] == "codex" else model["provider"]
                title = plain(model.get("label", label))
                line = Text()
                line.append("● " if label == config["active"] else "  ", style=ACCENT)
                line.append(title[:30].ljust(32 if width >= 70 else 23))
                line.append(plain(source), style=MUTED)
                rows.append(Option(line, id=label))
            keys = "↑↓ select   enter start   tab accounts   a add   s settings   q quit"
        else:
            self.query_one("#columns", Static).update("Accounts                          Remaining · 5h / 7d" if width >= 65 else "Accounts · remaining limits")
            data = accounts.account_rows(self.store)
            self.rows = [row["name"] for row in data]
            cached = self.usage.cached()
            for row in data:
                usage = cached.get(row["name"])
                source = "Claude" if row["provider"] == "claude" else "ChatGPT"
                line = Text()
                line.append("● " if row["enabled"] else "○ ", style=ACCENT if row["enabled"] else MUTED)
                line.append(plain(row["name"])[:18].ljust(20))
                line.append(source.ljust(10), style=MUTED)
                if width >= 75:
                    line.append(plain(row["plan"])[:9].ljust(11), style=MUTED)
                line.append(f"{percent(usage, 18000):>5}  /  {percent(usage, 604800):>5}", style=ACCENT if not usage or not usage.get("error") else "#d8ae79")
                rows.append(Option(line, id=row["name"]))
            keys = "↑↓ select   enter details   space on/off   a add   r refresh   tab models"
        self.listing.clear_options()
        self.listing.add_options(rows)
        self.listing.highlighted = min(self.selection[self.section], len(rows) - 1) if rows else None
        self.query_one("#keys", Static).update(keys)
        self.update_detail()

    def current(self):
        index = self.listing.highlighted
        return self.rows[index] if index is not None and index < len(self.rows) else None

    def update_detail(self):
        label = self.current()
        detail = self.query_one("#detail", Static)
        if not label:
            detail.update("No models yet. Press a to add one." if self.section == "models" else "No accounts yet. Press a to sign in.")
        elif self.section == "models":
            model = self.store.load()["models"][label]
            detail.update(Text(plain(model["id"]), style=MUTED))
        else:
            usage = self.usage.cached().get(label)
            if not usage:
                detail.update("Loading limits…" if self.refreshing else "Limits unavailable · r to refresh")
            elif usage.get("error"):
                detail.update(Text(plain(usage["error"]), style="#d8ae79"))
            else:
                windows = usage.get("windows", [])
                detail.update(" · ".join(f"{item['title']}: {reset_text(item['resets_at'])}" for item in windows[:2]) or "No quota windows returned.")

    @on(OptionList.OptionHighlighted, "#list")
    def highlighted(self, event):
        self.selection[self.section] = event.option_index
        self.update_detail()

    @on(Tabs.TabActivated)
    def tab_changed(self, event):
        self.section = event.tab.id
        self.draw()
        self.listing.focus()

    def action_switch_tab(self):
        if self.main_screen():
            self.query_one(Tabs).active = "accounts" if self.section == "models" else "models"

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
                self.message("Limits unavailable. Press r to retry.", True)
        finally:
            self.refreshing = False

    def action_refresh(self):
        if self.main_screen():
            self.refresh_usage()

    async def perform(self, function, *args, **kwargs):
        self.working = True
        try:
            result = await asyncio.to_thread(function, *args, **kwargs)
            return result
        finally:
            self.working = False

    async def backend(self):
        self.message("Preparing gateway…")
        await self.perform(service.ensure_backend, self.store)

    @on(OptionList.OptionSelected, "#list")
    def selected(self, event):
        if self.section == "models":
            self.start_model(event.option.id)
        else:
            self.account_details(event.option.id)

    @work(exclusive=True, group="operations")
    async def start_model(self, label):
        try:
            self.message("Opening Claude Code…")
            await self.backend()
            # Only the subprocess runs outside Textual's alternate screen.
            with self.suspend():
                code = await asyncio.to_thread(self.launch, self.store, label)
            self.draw()
            self.message("Returned from Claude Code." if code == 0 else f"Claude Code exited ({code}).")
        except (FlexError, OSError) as exc:
            self.message(str(exc), True)

    def action_add(self):
        if self.main_screen():
            self.add_flow()

    @work(exclusive=True, group="operations")
    async def add_flow(self):
        try:
            if self.section == "accounts":
                await self.add_account()
            else:
                await self.add_model()
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
            self.account_connected(provider)
            return
        await self.backend()
        job = self.logins.start(label, method="device" if method == "device" else "browser", provider=provider, replace=bool(renew), weight=meta["weight"] if renew else 1, priority=meta["priority"] if renew else 0)
        result = await self.push_screen_wait(LoginDialog(self.logins, job, open_automatically=method != "link"))
        if result and result["status"] == "complete":
            service.default_account_models(self.store, provider)
            if renew:
                self.message("Sign-in renewed.")
            else:
                self.account_connected(provider)
        elif result and result.get("error"):
            self.message(result["error"], True)

    def account_connected(self, provider):
        target = "claude-oauth" if provider == "claude" else "codex"
        models = self.store.load()["models"]
        self.selection["models"] = next((index for index, model in enumerate(models.values()) if model["provider"] == target), 0)
        self.section = "models"
        self.query_one(Tabs).active = "models"
        self.message("Account connected. Choose a model and press Enter.")

    async def add_model(self):
        config = self.store.load()
        providers = [(key, "Claude account" if value["kind"] == "claude-oauth" else "ChatGPT account" if value["kind"] == "codex" else key) for key, value in config["providers"].items()]
        provider = await self.push_screen_wait(Choice("Model source", providers + [("__new", "+ Connect a provider")]))
        if not provider:
            return
        if provider == "__new":
            provider = await self.connect_provider()
            if not provider:
                return
        kind = self.store.load()["providers"][provider]["kind"]
        if kind in {"codex", "claude-oauth"}:
            choices = [("claude-opus-5-5", "Claude Opus"), ("claude-sonnet-5-5", "Claude Sonnet")] if kind == "claude-oauth" else [("gpt-6.1-sol", "GPT-6.1 Sol"), ("gpt-6-astra", "GPT-6 Astra"), ("gpt-6-luna", "GPT-6 Luna")]
        else:
            self.message("Finding available models…")
            try:
                catalog = await self.perform(discover, self.store, provider, free="openrouter.ai" in self.store.load()["providers"][provider].get("base_url", ""), tools="openrouter.ai" in self.store.load()["providers"][provider].get("base_url", ""))
                choices = [(item["id"], plain(item["label"])) for item in catalog[:150]]
            except FlexError:
                choices = []
        upstream = await self.push_screen_wait(Choice("Choose a model", choices + [("__custom", "Enter a model ID…")]))
        if not upstream:
            return
        if upstream == "__custom":
            values = await self.push_screen_wait(Form("Custom model", [("model", "Upstream model ID", "", False)]))
            if not values:
                return
            upstream = values["model"]
        display = next((text for key, text in choices if key == upstream), upstream)
        values = await self.push_screen_wait(Form("Save model", [("name", "Profile name", service.suggest_name(upstream), False), ("label", "Display name", display, False), ("context", "Context window (tokens)", "200000", False)]))
        if values:
            service.add_model(self.store, values["name"], provider, upstream, values["label"], int(values["context"]))
            self.message("Model added.")

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
        return values["name"]

    def action_toggle(self):
        if self.main_screen() and self.section == "accounts" and self.current():
            self.toggle_account(self.current())

    @work(exclusive=True, group="operations")
    async def toggle_account(self, label):
        try:
            enabled = self.store.load()["accounts"][label]["enabled"]
            await self.perform(accounts.update_account, self.store, label, enabled=not enabled)
            self.draw()
        except (FlexError, OSError) as exc:
            self.message(str(exc), True)

    @work(exclusive=True, group="operations")
    async def account_details(self, label):
        try:
            row = next(item for item in accounts.account_rows(self.store) if item["name"] == label)
            usage = self.usage.cached().get(label, {})
            detail = [f"{row['email']} · {row['plan']}"]
            for item in usage.get("windows", []):
                detail.append(f"{item['scope']} · {item['title']}: {item['remaining_percent']}% left · {reset_text(item['resets_at'])}")
            if usage.get("error"):
                detail.append(usage["error"])
            action = await self.push_screen_wait(Choice(label, [("select", "Use only this account · same provider"), ("toggle", "Pause account" if row["enabled"] else "Enable account"), ("renew", "Renew sign-in"), ("edit", "Weight and priority"), ("remove", "Remove account")], "\n".join(detail)))
            if action == "select":
                await self.perform(accounts.select_accounts, self.store, label)
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

    def action_default(self):
        if self.main_screen() and self.section == "models" and self.current():
            service.use_model(self.store, self.current())
            self.draw()

    def action_settings(self):
        if self.main_screen():
            self.settings_flow()

    @work(exclusive=True, group="operations")
    async def settings_flow(self):
        try:
            action = await self.push_screen_wait(Choice("Settings", [("default", "Make selected model the default"), ("remove", "Remove selected model"), ("pool", "Account pool"), ("all", "Enable all accounts"), ("desktop", "Update Claude Desktop profile"), ("launch", "Open Claude Desktop"), ("backend", "Prepare gateway"), ("stop", "Stop gateway")]))
            if action == "default" and self.section == "models" and self.current():
                service.use_model(self.store, self.current())
            elif action == "remove" and self.section == "models" and self.current():
                label = self.current()
                def remove(config):
                    del config["models"][label]
                    if config["active"] == label:
                        config["active"] = next(iter(config["models"]), None)
                self.store.mutate(remove)
                render(self.store)
            elif action == "pool":
                strategy = await self.push_screen_wait(Choice("Account distribution", [(key, key.replace("-", " ").title()) for key in sorted(STRATEGIES)]))
                if strategy:
                    sticky = await self.push_screen_wait(Choice("Keep each session on one account?", [("yes", "Yes · recommended"), ("no", "No · distribute each request")]))
                    if sticky:
                        self.store.mutate(lambda config: config["routing"].update(strategy=strategy, sticky=sticky == "yes"))
                        render(self.store)
            elif action == "all":
                await self.perform(accounts.select_accounts, self.store, "all")
            elif action == "desktop":
                await self.perform(desktop.apply, self.store, activate=True)
                self.message("Desktop profile updated. Fully quit and reopen Claude Desktop.")
            elif action == "launch":
                await self.backend()
                await self.perform(desktop.launch, self.store)
            elif action == "backend":
                await self.backend()
                self.message("Gateway ready.")
            elif action == "stop":
                await self.perform(Gateway(self.store).stop)
                self.message("Gateway stopped.")
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
