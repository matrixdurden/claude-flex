"""The default command opens the TUI. Small automation hooks remain optional."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, desktop, profiles, service
from .compiler import choose, cli_model_id
from .gateway import Gateway, find_binary
from .providers import request_json
from .service import add_model  # noqa: F401 - retained as an import for early integrations
from .store import FlexError, Store


def parser():
    root = argparse.ArgumentParser(prog="claude-flex", description="Choose your account. Pick models inside Claude Code.")
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--home", type=Path, help="Private state directory (or CLAUDE_FLEX_HOME).")
    commands = root.add_subparsers(dest="command")
    run = commands.add_parser("run", help="Launch Claude Code directly for scripts.")
    run.add_argument("--account")
    run.add_argument("args", nargs=argparse.REMAINDER)
    commands.add_parser("doctor", help="Print local setup status.")
    check = commands.add_parser("check", help="Verify a model connection.")
    check.add_argument("--profile")
    check.add_argument("--tools", action="store_true")
    app = commands.add_parser("desktop", help="Optional official Claude Desktop integration.")
    app.add_argument("action", choices=("apply", "launch"))
    app.add_argument("--activate", action="store_true")
    gateway = commands.add_parser("gateway", help="Stop or check the local model gateway.")
    gateway.add_argument("action", choices=("stop", "status"))
    return root


def check_model(store, args):
    account = store.load().get("selected_account")
    if account:
        service.ensure_backend(store)
        service.sync_models(store)
        store = profiles.prepare(store, account)
    label, model = choose(store.load(), args.profile)
    gateway = Gateway(store)
    service.ensure_backend(store)
    gateway.start()
    model_id = cli_model_id(store.load(), label)
    gateway.wait_for_model(model_id)
    headers = {"Authorization": "Bearer " + store.secrets()["gateway_key"], "anthropic-version": "2023-06-01"}
    messages = [{"role": "user", "content": "Reply with FLEX_OK."}]
    body = {"model": model_id, "max_tokens": 128, "messages": messages}
    if args.tools:
        body["tools"] = [{"name": "flex_probe", "description": "Connectivity probe; call with value 323.", "input_schema": {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}}]
        body["tool_choice"] = {"type": "tool", "name": "flex_probe"}
        messages[0]["content"] = "Call flex_probe with value 323, then reply with FLEX_OK."
    response = request_json(store.url() + "/v1/messages", headers=headers, body=body, timeout=120)
    if args.tools:
        calls = [block for block in response.get("content", []) if block.get("type") == "tool_use"]
        if len(calls) != 1 or calls[0].get("name") != "flex_probe" or calls[0].get("input", {}).get("value") != 323:
            raise FlexError("The model responded, but the tool-call probe failed.")
        messages.extend([
            {"role": "assistant", "content": response["content"]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": calls[0]["id"], "content": "323"}]},
        ])
        body.pop("tool_choice")
        response = request_json(store.url() + "/v1/messages", headers=headers, body=body, timeout=120)
    text = "".join(block.get("text", "") for block in response.get("content", []) if block.get("type") == "text")
    if "FLEX_OK" not in text:
        raise FlexError("The model did not complete the requested probe.")
    print(f"OK: {label} -> {model['provider']}/{model['id']}" + ("; tool call + tool result round trip passed" if args.tools else ""))


def main(argv=None):
    args = parser().parse_args(argv)
    store = Store(args.home.expanduser() if args.home else None)
    try:
        store.init()
        from .migration import restore_legacy_settings

        restore_legacy_settings(store)
        result = 0
        if args.command is None:
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                raise FlexError("Open claude-flex in an interactive terminal. Use --help for automation.")
            from .tui import run_tui

            run_tui(store)
        elif args.command == "run":
            passthrough = args.args[1:] if args.args[:1] == ["--"] else args.args
            result = service.run_claude(store, args.account, passthrough)
        elif args.command == "check":
            check_model(store, args)
        elif args.command == "gateway":
            gateways = [Gateway(store)]
            for label, meta in store.load()["accounts"].items():
                if meta.get("credential_path"):
                    home = store.home / "instances" / meta["identity"]
                    if (home / "config.json").exists():
                        gateways.append(Gateway(profiles.AccountProfile(home, store.account_path(label))))
            if args.action == "stop":
                for gateway in gateways:
                    gateway.stop()
                print("Gateway stopped.")
            else:
                print("Running" if any(gateway.owned_pid() for gateway in gateways) else "Stopped")
        elif args.command == "desktop":
            if args.action == "apply":
                print(desktop.apply(store, activate=args.activate))
            elif args.action == "launch":
                account = store.load().get("selected_account")
                if not account:
                    raise FlexError("Select an account in Claude Flex first.")
                service.activate_account(store, account)
                if desktop.open_profile(store):
                    print("Restart Desktop.")
        elif args.command == "doctor":
            try:
                binary = str(find_binary(store))
            except FlexError:
                binary = "not installed; prepared automatically when needed"
            config = store.load()
            print(json.dumps({"version": __version__, "state": str(store.home), "backend": binary, "isolation": "per-account", "profiles": str(store.home / "instances"), "accounts": len(config["accounts"]), "models": len(config["models"])}, indent=2))
    except FlexError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except OSError:
        print("Error: local operation failed. Check paths and permissions.", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    raise SystemExit(result or 0)
