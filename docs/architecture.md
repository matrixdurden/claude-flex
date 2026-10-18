# Architecture

```text
Flex account list
  ├─ account A → private gateway A → private Claude CLI / Desktop profile A
  └─ account B → private gateway B → private Claude CLI / Desktop profile B

Normal Claude installation and sessions remain separate.
```

## Ownership and isolation

Flex reuses the installed Claude executable, but supplies its own
`CLAUDE_CONFIG_DIR` and credential namespace. Each account identity gets a
stable profile under `instances/<identity>/`, with a separate gateway port,
settings, conversations and native model choice. Selecting another account
changes only the next-launch preference; it does not disable account peers,
stop their gateways or rewrite their private Claude settings.

The native CLI owns `/model`, tool execution, project instructions and its
permission system. Flex has only account rows and limits. Profile isolation
is not a filesystem VM: users still choose which project the agent works on.

## State layout

```text
config.json                       accounts, sources, model catalog, preference
secrets.json                      local client key and API-key references
credentials/<identity>/account.json  authoritative OAuth refresh owner
instances/<identity>/
  config.json                     single-account gateway configuration
  secrets.json                    separate downstream gateway key
  claude-code/                    private native Claude configuration/history
  runtime/                        gateway process record, logs and compiled config
backups/                          recovery copies and one-time migration records
```

The account runtime points at its one authoritative credential directory.
Token refresh does not create another mutable refresh-token owner. Linked
OpenCode accounts remain access-only snapshots refreshed from their specific
source file; external refresh ownership is retained. Credential migration
refuses to stop an older shared Flex gateway while that legacy session is live.

Writes are atomic and private on POSIX. Process shutdown checks ownership by
command/config path. Account selection never shuts down another account's
runtime. Tests exercise concurrent real gateways against a local fake provider.

## Native model catalogs

Live Codex, Claude and compatible-provider catalogs are queried read-only.
Enabled account catalogs are unioned without duplicate connection rows and
cached by provider/account identity. Failures preserve existing routes.
Image/audio-only and hidden models are not shown as coding agents.

The native CLI picker uses real provider IDs and labels. Desktop validates
its model list against Claude-like routes, so it receives opaque compatible
route IDs with genuine provider labels. These aliases contain no competing
provider name and are compiled into the matching backend executor. Existing
legacy route aliases remain for backward compatibility.

## Desktop process isolation

Desktop is optional and detected before showing its shortcut key. Its installed
binary is launched directly with process-local app-data/config variables and a
private Chromium user-data path. Windows uses
`%LOCALAPPDATA%/Claude-Flex/isolated/<identity>/`; its native OS USERPROFILE
identity is retained so MSIX activation works. APPDATA, LOCALAPPDATA and
CLAUDE_CONFIG_DIR point at Flex-owned subdirectories for that child process.
No environment variable or registry policy is changed globally.

Flex writes only this private connection library. It never switches the normal
Desktop library, copies normal cookies/history, signs out the user, kills the
normal Desktop process or creates desktop shortcuts. Startup stdio is detached
so GUI child processes do not keep the launcher's captured pipes open.

## Permissions and migration

Full access / Auto / Ask is a private Flex preference. Defaults and the explicit
launcher flag apply only to the isolated engine. Fresh installations do not
force full access. Normal Claude's permission rules are not read or rewritten.

Older versions wrote shared user settings. A one-time migration uses recorded
backups and exact local routing credentials to remove only attributable Flex
changes, preserving unrelated/new settings and saving a recovery snapshot.
Native settings writers reject destinations outside their private profile.

## Backend

CLIProxyAPI 8.0.32 is an external, pinned MIT dependency. Its release archive is
SHA-256 verified and only the named executable is extracted. Public asset
redirects are allowed; authenticated provider requests do not follow redirects.
