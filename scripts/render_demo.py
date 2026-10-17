"""Generate README screenshots with synthetic accounts; never reads real logins."""

import asyncio
import tempfile
from pathlib import Path

from claude_flex import service
from claude_flex.store import Store
from claude_flex.tui import FlexApp


class DemoUsage:
    def cached(self):
        return {"personal": {"windows": [{"title": "5h", "duration_seconds": 18000, "remaining_percent": 72, "resets_at": None}, {"title": "7d", "duration_seconds": 604800, "remaining_percent": 91, "resets_at": None}]}, "work": {"windows": [{"title": "5h", "duration_seconds": 18000, "remaining_percent": 48, "resets_at": None}, {"title": "7d", "duration_seconds": 604800, "remaining_percent": 83, "resets_at": None}]}}

    def all(self):
        return self.cached()


async def main():
    from claude_flex.store import write_json

    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory))
        store.init()
        service.add_model(store, "opus", "claude-oauth", "claude-opus-5-5", "Claude Opus")
        service.add_model(store, "sonnet", "claude-oauth", "claude-sonnet-5-5", "Claude Sonnet")
        service.add_model(store, "gpt", "codex", "gpt-6.1-sol", "GPT-6.1 Sol")
        for label, provider, plan in (("personal", "claude", "Max"), ("work", "codex", "Plus")):
            store.mutate(lambda config, label=label, provider=provider: config["accounts"].update({label: {"provider": provider, "enabled": True, "weight": 1, "priority": 0, "identity": label, "source": None}}))
            write_json(store.account_path(label), {"type": provider, "email": "demo@example.invalid", "plan_type": plan})
        store.mutate(lambda config: config.update(selected_account="personal"))
        app = FlexApp(store, usage=DemoUsage(), catalog_sync=lambda *args, **kwargs: {"added": [], "errors": {}})
        target = Path(__file__).resolve().parents[1] / "docs/assets"
        target.mkdir(parents=True, exist_ok=True)
        async with app.run_test(size=(96, 24)) as pilot:
            await pilot.pause()
            (target / "overview.svg").write_text("\n".join(line.rstrip() for line in app.export_screenshot().splitlines()) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
