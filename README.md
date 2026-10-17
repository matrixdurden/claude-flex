<div align="center">

# claude flex

**Your models. Your accounts. Claude Code.**

A quiet terminal interface for Claude Code — switch models, manage Claude and
ChatGPT accounts, and see your remaining limits without memorizing commands.

[![Tests](https://github.com/matrixdurden/claude-flex/actions/workflows/test.yml/badge.svg)](https://github.com/matrixdurden/claude-flex/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-a8d5bc)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-858b96)](pyproject.toml)

![Claude Flex](docs/assets/overview.svg)

</div>

## Start here

Install [Claude Code](https://code.claude.com/docs/en/setup) and
[uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
uv tool install git+https://github.com/matrixdurden/claude-flex.git
claude-flex
```

That's the everyday interface. **No subcommand is required.**

From a local checkout: `uv tool install .`, or `uv sync` followed by
`uv run claude-flex`. Python 3.11+ is required. Works on Linux, macOS, Windows
and WSL. Claude Desktop is optional.

The model gateway is downloaded and checksum-verified automatically the first
time it's needed. Claude Flex launches your existing `claude` executable in
the current project directory; your code stays in Claude Code's own workflow.

## One screen

Models and connected accounts share one list. No tabs or page switching.
Move down from a model to an account with the same arrow keys; remaining
limits are visible alongside your accounts.

### Models

Choose a model, press **Enter**, and work in the real Claude Code. Exit Claude
Code to return to Flex. Separate sessions keep their selected model.

- Claude subscription models through your own Claude Pro / Max accounts.
- GPT models through your own ChatGPT / Codex accounts.
- OpenRouter, including discovery of free models declaring tool support.
- Ollama and LM Studio for models running on your own hardware.
- OpenAI, Anthropic, Gemini, Groq, GitHub Models, or a compatible custom API.

Press **a** to connect an account; its model presets appear automatically.
For other sources, use **s → Connect another model or provider**. API keys are entered in a
masked field. Model IDs and context sizes can also be entered manually.
Models must support reliable tool calls to be useful coding agents; protocol
compatibility alone doesn't guarantee agent quality.

### Accounts

Claude and ChatGPT accounts use the same controls:

- **a** — choose Claude / ChatGPT, choose a sign-in method, then sign in.
- **Space** — enable or pause the selected account.
- **Enter** — details, renewal, account selection, weight, priority, or removal.
- **r** — refresh quota snapshots.

No account-name, weight, or model form is shown during sign-in. Accounts are
named automatically (`claude`, `claude-2`, `chatgpt`, …), and the provider's
model presets appear in Models immediately after a successful connection.
Available sign-in methods include browser, a copyable link, and ChatGPT device
codes; existing OpenCode ChatGPT sign-in is under ChatGPT's sign-in methods.

The provider's own sign-in page handles your password. Independent logins keep
separate refresh tokens; the gateway owns their renewal. Duplicate account
identities are rejected. Existing OpenCode ChatGPT access can also be linked
without copying its refresh token.

**Claude and ChatGPT pools stay separate.** Selecting a single Claude account
does not disable ChatGPT accounts. Round-robin, weighted round-robin, fill-first
and sticky sessions are available under **s → Account pool**. Account-setting
changes may briefly restart the gateway, so finish an active generation first.

### Limits, honestly displayed

The account list shows **remaining** short/long-window percentages and reset
times. Additional provider/model windows appear in account details.

- ChatGPT accounts: **Codex** usage, not general ChatGPT chat-message quotas.
- Claude accounts: the usage windows returned by Claude's OAuth usage service.
- Missing data stays **—**. A `*` marks a stale, previously successful value.
- Usage queries are read-only and cached. Background refresh runs every minute.
- Other API providers' billing/quotas aren't reported as subscription limits.

## Keyboard

| Key | Action |
| --- | --- |
| `↑` / `↓` | Select a row |
| `Enter` | Launch model / open account details |
| `a` | Connect an account; model presets are added automatically |
| `Space` | Toggle account in pool |
| `r` | Refresh limits |
| `d` | Make selected model the default |
| `s` | Settings, pool and optional Desktop integration |
| `Esc` | Back / cancel sign-in |
| `q` | Quit Flex; the gateway stays available to other sessions |

No dashboard, browser management server, telemetry, or giant command menu.
The browser opens only when you sign in to a provider.
Identical model connections appear once. Existing duplicates are merged while
keeping the active selection and legacy routing aliases for running sessions.

## Claude Desktop, optionally

Under **s → Update Claude Desktop profile**, Flex creates its own local profile
without overwriting other saved profiles. Fully quit and reopen Claude Desktop,
then select the third-party profile if prompted. On Windows, WSL integration
detects your distribution and user paths automatically.

Desktop model IDs use Claude-shaped compatibility aliases; display names show
the actual profiles. The gateway maps them to your selected upstream models.
This project is independent and is not endorsed by Anthropic or OpenAI.
Gateway mode does not provide Anthropic-hosted cloud sessions or Remote Control.

## A small automation surface

Everything above is available through the TUI. Optional script hooks:

```bash
claude-flex run --profile gpt -- -p "Explain this project" --max-turns 3
claude-flex check --profile gpt --tools
claude-flex doctor
claude-flex gateway stop
```

State is private and kept **outside the repository**:

| Platform | Directory |
| --- | --- |
| Linux / WSL | `~/.config/claude-flex` (or `$XDG_CONFIG_HOME/claude-flex`) |
| macOS | `~/Library/Application Support/Claude-Flex` |
| Windows | `%LOCALAPPDATA%\Claude-Flex` |

Override with `CLAUDE_FLEX_HOME` or `claude-flex --home PATH`.
The gateway listens only on `127.0.0.1:8318`. Credentials, generated connection
profiles and logs belong in that user directory, never in Git.

## Development

```bash
uv sync
uv run python -m unittest discover -s tests -v
uvx ruff check .
uv build
```

Optional real-backend tests use a local synthetic upstream, with no cloud keys:

```bash
CLAUDE_FLEX_TEST_BACKEND=/path/to/cli-proxy-api uv run python -m unittest tests.test_integration -v
```

Textual pilot tests exercise keyboard navigation, forms and account operations
headlessly. README screenshots contain **synthetic data**, generated by
`uv run python scripts/render_demo.py`.

[Architecture](docs/architecture.md) · [Contributing](CONTRIBUTING.md) ·
[Türkçe](docs/README.tr.md)

## Credits & license

[MIT](LICENSE). Built with [Textual](https://github.com/Textualize/textual) and
[CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI). The pinned gateway
is a separately downloaded MIT-licensed dependency; its source and binaries
are not vendored here. Claude Code / Claude Desktop remain Anthropic products.
