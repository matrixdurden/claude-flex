# Architecture

```text
Textual TUI → account / provider / profile services
                               ↓
Claude Code CLI or Desktop → local CLIProxyAPI → selected provider
```

## Ownership

- **Claude Flex:** TUI, named profiles, account labels, local process lifecycle.
- **CLIProxyAPI:** protocol conversion, streaming, tool translation, OAuth token
  renewal, credential scheduling, quota cooldowns and retry/failover.
- **Claude Code:** agent loop, prompts, files, terminal tools and permissions.

The TUI suspends its alternate screen while launching the installed `claude`
executable. The subprocess inherits the project working directory, normal
permissions and project setup. Each launch receives a separate temporary settings
file; global Claude Code settings are not rewritten.

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
  session-*/         temporary per-launch Claude settings
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

Every profile has a stable, unique compatibility alias. It compiles into the
matching `oauth.model-alias` channel or the corresponding API-key executor.
Generic Claude-family aliases are not injected across providers: this avoids
native Claude accounts accidentally serving a GPT route. Session model aliases
are deterministic, so changing a default doesn't retarget existing sessions.

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
