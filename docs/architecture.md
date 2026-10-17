# Architecture

```text
Textual TUI → account / provider / profile services
                               ↓
Claude Code CLI or Desktop → local CLIProxyAPI → selected provider
```

## Ownership

- **Claude Flex:** account switching TUI, account labels, native connection settings and local process lifecycle.
- **CLIProxyAPI:** protocol conversion, streaming, tool translation, OAuth token
  renewal, credential scheduling, quota cooldowns and retry/failover.
- **Claude Code:** model selection, agent loop, prompts, files, terminal tools and permissions.

The TUI shows accounts only. Enter activates the selected account and updates
the native Claude Code user settings with connection credentials and the
`modelPicker` lineup. Model IDs and display labels are genuine provider IDs,
not Claude-family aliases. An original settings backup stays in private Flex
state; unrelated permissions and preferences remain intact.

Users then run the installed `claude` directly and select models in `/model`.
The optional launcher does not inject `--model`, temporary session settings,
family-model pins or subagent-model pins. The native user's model choice is
preserved across same-provider account changes. A cross-provider switch picks
an available starting model, which remains changeable in the native menu.

## Catalog discovery

The TUI asynchronously loads model catalogs at startup, after connecting an
account/provider and on refresh. Codex uses its authenticated `/codex/models`
endpoint; Claude uses its paginated `/v1/models` endpoint. Other provider
adapters query their model-list API. Catalog requests are read-only and don't
generate inference traffic. Multiple OAuth accounts contribute a union of
model IDs, without duplicate connection profiles.

Catalog refreshes are cached for five minutes, keyed by provider configuration
and enabled account identities. Newly enabled/connected accounts invalidate
that signature. Discovery adds profiles while preserving existing names,
explicit context settings, the default selection, and legacy aliases.
Failed queries do not erase existing routes. Image/audio-only and hidden
entries are excluded from the native Claude Code model picker. No model rows
or model selection workflows are rendered in Flex.

## User state

```text
config.json          versioned settings and credential references
secrets.json         local gateway key and stored API keys
auth/<label>.json    OAuth credentials; backend owns refresh
bin/                 optional checksum-verified backend download
runtime/
  gateway.json       compiled CLIProxyAPI v8 config (JSON is valid YAML)
  gateway.log
  process.json       PID, checked against its command before signaling
integration.json     native settings path that Flex connected
backups/             original native user settings backup
```

JSON writes use atomic replacement. CLI mutations use advisory file locks.
Credential edits pause an owned gateway before changing a refreshable auth file.
Linked OpenCode imports are access-only; OpenCode keeps refresh ownership.
Refreshing a linked source never silently changes its account identity.

On POSIX, private directories are 0700 and written credential JSON is 0600.
Windows state relies on the user's LocalAppData ACL. No credentials are part of
the repository, the README screenshot generator, or tests.

## Routing

OAuth accounts are marked `codex` or `claude`. The corresponding model sources
are `codex` and `claude-oauth`; API-key Anthropic connections use the distinct
`claude` provider kind. Selecting one account pauses only its provider peers.

OAuth model requests use native provider IDs. API-key routes use provider-prefixed
IDs to distinguish overlapping upstream names. Stable old compatibility aliases
remain only for older sessions, not in the visible picker. Claude Code's Default
choice can normalize an unknown ID with a `claude-` prefix; that compatibility
route maps back to the same GPT model, without impersonating Sonnet.

The native menu replaces unavailable built-in options and limits the Default
entry to configured routes. Selected-account metadata filters subscription
models to the active account provider. Main-model and subagent-model overrides
from the earlier Flex launcher are removed when connecting native settings.

Independent OAuth logins run in temporary directories. The callback URL is
captured without exposing raw subprocess output to the TUI. A pasted localhost
callback must match the pending state, expected port and provider callback path.
Cancellation terminates the login subprocess and discards temporary credentials.

## Quotas

The quota service reads provider-returned usage windows, not estimated local
token counters. Codex uses `/backend-api/wham/usage`; Claude uses
`api.anthropic.com/api/oauth/usage`. Requests never follow credential-bearing
redirects. Missing percentages and reset timestamps remain unknown. Successful
snapshots are cached and explicitly marked stale on later failure.

## Desktop

Optional official Desktop integration writes one local `configLibrary` entry
named Claude Flex. Existing entries are retained. A full app restart is needed.
WSL paths are detected dynamically. No application binaries or machine policies
are modified. Terminal usage does not depend on Desktop being installed.

## Backend and compatibility

CLIProxyAPI is pinned to 8.0.32. Downloads are checked against the SHA-256 digest
in the GitHub release metadata and only the named executable is extracted.
The backend remains an external dependency, rather than a fork.

Claude OAuth and the usage parser are covered with synthetic fixtures and login
process tests. Live Claude availability depends on the user's account and provider
responses. Tool reliability depends on the chosen model; accepting an API format
doesn't make every model a capable agent.
