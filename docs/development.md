# Development

See [CONTRIBUTING.md](../CONTRIBUTING.md) for setup, checks and contribution notes.

- `tui.py` / `tui.tcss`: account-only terminal interface and sign-in workflows.
- `service.py`: account activation, background catalogs and native Claude launch.
- `integration.py`: private native Claude settings and picker lineup.
- `profiles.py`: per-account gateway and credential-owner isolation.
- `migration.py`: one-time restoration of older shared-setting integrations.
- `accounts.py` / `login.py`: named OAuth accounts and browser login jobs.
- `usage.py`: read-only Claude/Codex quota parsing and cache.
- `compiler.py`: provider-neutral configuration → CLIProxyAPI and Claude settings.
- `gateway.py`: verified backend install, readiness and process ownership.
- `desktop.py`: optional official Desktop profiles.
- `cli.py`: default TUI entry and small automation hooks.

Use a temporary `CLAUDE_FLEX_HOME` when experimenting. Backend integration tests
use a real gateway and a local synthetic OpenAI-compatible upstream. Tests do
not require or upload account credentials.
