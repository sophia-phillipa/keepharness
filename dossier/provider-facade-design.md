# Provider facade over the real CLI state (D-038, D-039)

> Status: DRAFT (design for issue #32; nothing here is implemented yet; revised 2026-10-07 after the W8 review, D-039)
> Job: When I use KeepHarness next to the Codex and Claude Code CLIs, their IDE extensions and desktop apps, I want every KeepHarness run and screen to use the same login, plugins, skills, MCP servers, hooks and instructions those tools use, so I can trust that a run from KeepHarness behaves like the same request typed in the CLI, without keeping two setups in sync.
> Scope: `adapters/shared/provider_setup.py`, `adapters/shared/process.py`, `adapters/{codex,claude,deepseek}/` (native runs, login, new state readers and writers), `control/manager.py`, `control/routes.py`, `control/runtime_config.py`, `control/customize.js`, `agent_service/resources.py`, `agent_service/integrations_view.py`, `agent_service/services/conversation_service.py`; removed: the guest code paths (about 20 `guest` hits in 10 non-test files, 31 `LOCAL_CLIENT` checks, per-client projects in `control/runtime_config.py`), the shared VPN key (client `vpn`) and its project-sharing references, and `control/integrations.py`.
> Invariant docs: [D-038](decisions/d-038-keepharness-facade-over-provider-state.md); [D-039](decisions/d-039-single-owner-facade-policies.md); [D-034](decisions/d-034-wp3-customize-product-answers.md) §2-§4; [naming model](naming-model.md); `AGENTS.md` (English only, `sem-projeto` unchanged, UI prefs only through `window.HarnessPrefs`). [UC-001](UC-001-multi-user-execution.md) (multi-identity part) and the unattended rules of [scheduled tasks](../docs/scheduled-tasks.md) are superseded by D-039 and are not edited here.
> Decision rationale: one source of truth (the CLI's own files and commands) instead of a KeepHarness copy; every concept starts as close as possible to the original CLI (its own command first, else its documented config file), and KeepHarness is single-owner, so there is no second class of caller to protect the owner from.
> Kill criteria: remove this spec when every item of the issue split below is merged and the 0.15 homes are no longer read by any code, or when Sophia reverses D-038 or D-039 with a superseding decision.

Facts about the CLIs were checked on 2026-10-07 against `codex-cli 0.157.1` and `Claude Code 2.1.292` (`--version`, `--help` of the subcommands named below) and the official docs cited inline. Anything not confirmed is marked **UNVERIFIED**. The Codex docs moved: the old `developers.openai.com/codex/*` URLs now answer 308; the current ones are [config reference](https://learn.chatgpt.com/docs/config-file/config-reference) and [app server](https://learn.chatgpt.com/docs/app-server).

W13/W14 decision update (2026-10-08): [D-043](decisions/d-043-deepseek-engine.md) settles the DeepSeek engine for 1.0 and the later `dsh` gates; [D-044](decisions/d-044-scoped-sandbox-under-facade.md) retires Codex/Claude scoped execution for 1.0 while preserving Local isolation. These records refine the implementation plan below; this update changes documents only.

## #32 bullet to section map

| #32 bullet | Section |
| --- | --- |
| 1. Runs use the CLI's real home; retire the 0.15 homes and the personal-setup opt-in | [§2.1 Runs on the real homes](#21-runs-on-the-real-homes) |
| 2. Sign-in and Renew access on the CLI's own login (+ #35, #36) | [§2.2 Sign-in on the CLI's own login](#22-sign-in-on-the-clis-own-login) |
| 3. Per provider x kind: read real state, write through the CLI or the documented setting, atomic, concurrent-safe | [§2.3 State readers and writers](#23-state-readers-and-writers) |
| 4. Detect external changes and tell the person | [§2.4 External change detection](#24-external-change-detection) |
| 5. Who may run what (guests, scheduled runs, untrusted projects) | [§2.5 Owner-only access, trust and project MCP approval](#25-owner-only-access-trust-and-project-mcp-approval) |
| 6. Migration from the 0.15 homes | [§2.6 Leaving the 0.15 homes behind](#26-leaving-the-015-homes-behind) |
| 7. Tests with fake homes only | [§3.3 Test protocol](#33-test-protocol) |
| DeepSeek interim + seam (#33 runs in parallel) | [§2.7 DeepSeek and the provider seam](#27-deepseek-and-the-provider-seam) |
| Scoped sandbox after real-home runs (#34) | [§2.1.1 Scoped sandbox disposition](#211-scoped-sandbox-disposition-d-044) |

## 1. Why (motivation and context)

Since 0.15.0 each provider CLI runs in a home KeepHarness owns (`<state>/providers/home`, `CODEX_HOME=<state>/providers/home/.codex`, `CLAUDE_CONFIG_DIR=<state>/providers/home/.claude`, see `adapters/shared/provider_setup.py` `CONFIG_FOLDERS`), the owner's `~/.codex` and `~/.claude` reach a run only through the `personal_setup` opt-in, and what loads is filtered per run by `settings.services[provider].integrations`:

- Claude runs pass `--strict-mcp-config --mcp-config <home>/mcp.json`, `--setting-sources project` (or `user,project` with the opt-in and the hooks grant), and `--settings` with `enabledPlugins` and `disableAllHooks` (`adapters/claude/native.py` `build_command`).
- Codex runs pass `-c features.apps=false`, `-c features.hooks=<grant>`, and per thread `config.mcp_servers` / `config.plugins` built from the allow list (`adapters/codex/native.py` `build_command`, `thread_parameters`, `host_servers`).
- DeepSeek runs the Codex engine with `CODEX_HOME=<state>/providers/deepseek`, its key in `<PREFIX>_API_KEY` and `model_provider="tail_api"` (`adapters/deepseek/backend.py`).
- The Plugins page inventory reads the owner's real files directly from the control process (`control/integrations.py` reads `~/.codex/config.toml`, `~/.claude.json`, `~/.claude/settings.json`), while runs read the harness home: the screen and the run already disagree today.

D-038 replaces this with a facade: KeepHarness reads and writes the CLI's real state, runs always get the provider's global and project orchestration, and the allow list stops deciding what loads. D-039 settles the policy questions this design raised: KeepHarness is single-owner (server at home with her accounts signed in, client at work over Tailscale, same person), so there are no guests and no run classes; scheduled runs behave like the owner's own `codex exec` / `claude -p`.

### Key decisions in this design

| Decision | Choice | Rationale |
| --- | --- | --- |
| Governing principle | Every concept starts as close as possible to the original CLI: the CLI's own command first, else its documented config file; revisit later | KeepHarness never owns a provider schema it cannot keep up with |
| Where provider state is written | The CLI's own writer first (`claude plugin enable/disable -s user\|project\|local`, Codex app-server `config/batchWrite` and `skills/config/write`); a direct file edit only when no writer exists, in the layer the CLI itself would write (user by default, project through the CLI's scope flag or the project's config file) | The CLI owns its schema, its comment-preserving TOML writer and its own locking; KeepHarness does not add a TOML-editing dependency (Ponytail) |
| Concurrency control | Optimistic: every read returns a fingerprint (sha256 of the bytes read); every write carries it and fails with a conflict when the bytes changed; one `threading.Lock` per real path serializes KeepHarness's own writers | IDE extensions and desktop apps write the same files with no shared lock; optimistic checks detect the lost update instead of hiding it |
| `~/.claude.json` | Edited directly with safeguards (§2.3): backup in a KeepHarness-owned directory, validation before the write, refusal on a new error, revert detector | It is the only place the per-project MCP switch lives; it carries credentials |
| Change detection | `stat` fingerprints checked on demand (screen load, run start) with a minimum interval, no file-watcher dependency | A handful of files; stdlib `os.stat` is enough, and it cannot storm |
| Who may use KeepHarness | The owner only: `local` and the owner's allow-listed Tailscale logins, every project; no guests, no run classes; the remote gate stays owner-only and fails closed (§2.5) | Single person on two machines; the gate, not a per-caller policy, is the boundary |
| Scheduled runs | Load everything like the owner and use whatever permission the schedule chooses, including Automatic/Full and internet | Same as the owner's own `codex exec` / `claude -p` |
| Trust | A project trusted by either CLI is trusted in KeepHarness; untrusted shows a CLI-style prompt that writes trust into the CLI's own state | KeepHarness follows the CLI instead of inventing a trust flag |
| DeepSeek | 1.0: Codex engine with its own home and key plus #47; later `dsh` behind an opt-in flag after the D-043 gates | It must never hold the ChatGPT login; preview protocol gaps do not block the supported engine |
| Cloud scoped sandbox | Unavailable for Codex/Claude in 1.0; native permission presets remain; Local isolation stays (D-044) | Credential copying and filtered orchestration conflict with the facade; old scoped runs fail without native fallback |
| The 0.15 homes | Left untouched on disk and ignored; no migration of logins, sessions or the integrations allow list | She is not using KeepHarness now; sign-in is one click |

### What this design deliberately does not introduce

- No new service, daemon, store or file watcher library (inotify/watchdog). The control process and `os.stat` cover it.
- No TOML round-trip library (for example `tomlkit`): Codex writes its own `config.toml` through the app-server. If a Codex write path is unavailable, the switch is read-only with a reason rather than KeepHarness editing TOML itself.
- No KeepHarness copy or cache of provider state beyond the last-seen fingerprints and item maps needed for change notices, and the `~/.claude.json` backups.
- No run classes, guest baseline, per-schedule hook or connector opt-ins, and no migration tool for the 0.15 homes.
- No KeepHarness-native skills, agents, rules or hooks (D-038 §5, future work).

### Review disposition (W8 review of this design)

| Finding | Disposition | Where |
| --- | --- | --- |
| B1, B3, M2, m2, m4 | Moot: they concern guests or run classes, removed by D-039 | n/a |
| B2: `claude -p` loads project-scoped `.mcp.json` servers without asking ([MCP docs](https://code.claude.com/docs/en/mcp)) | Compute the approved set and pass the unapproved names in `--settings`; KeepHarness asks the owner like the CLI | §2.5, F15 |
| M1: untrusted project runs its hooks | A project trusted by either CLI is trusted; otherwise prompt, and until accepted Claude runs with hooks disabled and no project MCP | §2.5, F6 |
| M3, M4: `~/.claude.json` is credential-bearing and contended | Backup, validation before the write (refuse on a new error), revert detector; logs redacted | §2.3, F1, F14 |
| M5: atomic JSON writer details | `write_json_atomic` contract (realpath, temp beside the real target, mode/uid/gid, sha256 fingerprint) | Contracts |
| M6: Codex child shells inherit secret-looking variables | `ignore_default_excludes` handling, explicit exclude of `<PREFIX>_API_KEY`, `cli_auth_credentials_store="file"`, env-leak test | §2.7, F7 |
| m1: HTTP contract gaps | `GET` side-effect free, `notices:ack`, redacted `provider_message`, control error envelope | Contracts (HTTP) |
| m3: login URL handling | Open/Copy only for an allow list of auth domains; URL not persisted past the job | §2.2 |
| m5: Codex hooks pending trust | Never bypass hook trust; show "hooks pending review" | §2.1 |
| P1: sibling-file `flock` | Replaced by one `threading.Lock` per real path (`write_json_atomic` is a synchronous function; async callers use `asyncio.to_thread`) | Contracts |
| P2: `control/integrations.py` | Removed | §3.1 |
| Re-review R1-R5 and residual risks | `--setting-sources user` for untrusted Claude projects; version warn (hard fail only for `dsh`); validation before the swap, so there is nothing to restore; backups kept 3 and excluded from exports; trust routes on the harness owner gate; `local` and empty allow-list rules stated | §2.1, §2.3, §2.5, Contracts |
| DeepSeek seam gaps | Engine id, `hook`/`instructions` kinds, layered scopes, shared skills root, `set_api_key`, version hard fail, per-engine session identity | §2.7, Contracts |

## 2. How (architecture and design)

```
 Settings > Providers / Plugins page (control/customize.js, admin.js)
        |  /api/provider-state (GET, POST toggle)   /api/provider-login, /api/provider-check
        v
 control/routes.py  -->  control/manager.py  --(provider id)-->  ProviderStateAdapter (seam)
                                                   |                 |               |
                                       adapters/codex/state.py  adapters/claude/state.py  adapters/deepseek/state.py
                                          |  app-server JSON-RPC    |  `claude plugin ...`      |  Codex adapter bound to
                                          |  config/batchWrite,     |  settings.json edits      |  <state>/providers/deepseek
                                          |  skills/config/write    |  ~/.claude.json edits     |
                                          v                         v                           v
                                    real $CODEX_HOME          real ~/.claude, ~/.claude.json   harness DeepSeek home
                                    + <project>/.codex        + <project>/.claude, .mcp.json

 Runs: agent_service -> adapters/*/native.py -> provider_setup.run_environment(provider, project)
        every run (attended or scheduled) -> real home, full orchestration,
        permissions = the CLI's own sandbox/approval modes; untrusted project -> §2.5
```

### 2.1 Runs on the real homes

- `provider_setup.environment()` stops injecting `HOME`, `CODEX_HOME` and `CLAUDE_CONFIG_DIR` for Codex and Claude. The child keeps the host's `HOME` and passes `CODEX_HOME` / `CLAUDE_CONFIG_DIR` through only when the owner's own environment sets them (`child_environment` already allows both names). DeepSeek keeps `CODEX_HOME=<state>/providers/deepseek` (§2.7).
- `runtime_config` drops `provider_homes` and `auth_file` (`control/runtime_config.py` line ~179 `credential_file`). A runtime file without `provider_homes` already means "host home" (`child_source` returns `None`). No compatibility with 0.15 runtime files is kept (D-039): a stale `provider_homes` key is ignored.
- Claude runs (`adapters/claude/native.py`): remove `--strict-mcp-config`, the `enabledPlugins` override and `--setting-sources project`; keep `--mcp-config` only for the servers KeepHarness itself adds (`harness_effects`), which `--mcp-config` adds on top of the configured ones. `disableAllHooks`, `--setting-sources` and `disabledMcpjsonServers` are set only by the untrusted-project rules and the project MCP approval (§2.5).
- Codex runs (`adapters/codex/native.py`): remove `-c features.apps=false` and the per-thread `config.plugins` / `config.mcp_servers` built from the allow list; keep the harness-added servers (`harness_effects`, the reader `READER`) and the existing guard that disables any stale `harness_effects*` entry in `config.toml`. Hooks follow the CLI: active as in any harness. A Codex hook that is still pending review runs nothing; KeepHarness never passes `--dangerously-bypass-hook-trust`, shows "hooks pending review" and points to the CLI's own trust path (there is no `codex hooks` subcommand in `--help`; the review path is presumably the TUI, **UNVERIFIED**, as is where Codex persists hook trust).
- **No run classes.** Attended and scheduled runs load the same orchestration: hooks, MCP servers, plugins, skills and instructions, exactly like the owner's own `codex exec` / `claude -p`. A scheduled run uses whatever permission the schedule chooses, including Automatic/Full and internet (D-039 supersedes the D03/D15 clamps).
- **Presets map onto the CLI's own modes only.** KeepHarness's run presets become the CLI's sandbox/approval modes (Codex sandbox and approval policy; Claude `--permission-mode`), through the existing `provider_setup.command_permissions` mapping trimmed to those flags. Nothing else filters orchestration: "Read only" no longer disables connectors (D-039 supersedes that part of D04/D12), and the hooks grant stops being a KeepHarness switch.
  - Codex: `ask` uses `read-only` / `on-request`; `read_only` uses `read-only` / `never`; `auto` uses `workspace-write` / `on-request`; admitted `full` uses `danger-full-access` / `never`. Native sandbox network policy still follows the effective internet permission. Native runs do not override `features.shell_tool`, `features.unified_exec` or `web_search` to filter the owner's tools.
  - Claude: `ask` uses `default`; `read_only` uses `plan`; `auto` uses `acceptEdits`; admitted `full` uses `bypassPermissions`. There is no harness `--tools` allow list or additional per-preset `permissions.ask` rule. `plan` is the CLI's exploration mode; `dontAsk` can still run preapproved tools and is not a replacement for read-only intent ([native permission modes](https://code.claude.com/docs/en/permissions)).
- Retired: `personal_setup` (settings key, `run_settings`, `personal_setup_on`, `personal_instructions`, `personal_hooks`, `owner_file`), the appended `~/.codex/AGENTS.md` / `~/.claude/CLAUDE.md` text (the CLIs read them themselves now), the allow-list filter in `thread_parameters`/`build_command`, and `settings.services[p].integrations` as a run input. The language rule (D30 rule A) stays.
- **Version check at run start.** The adapter compares the CLI version with `tested_versions` (§2.3). For Codex and Claude, outside the range the run proceeds with a warning on the run that names the installed and tested versions (CLIs update themselves; refusing would stop scheduled runs on every update), and direct file edits are refused (§2.3). Engines that require it (`dsh`, §2.7) hard-fail with `provider_version_unsupported`.
- `agent_service/resources.py` lists user-scope skills, agents and commands from the real homes. The "listed as unavailable" states tied to the opt-in disappear.
- Sessions: Codex rollouts and Claude transcripts are written to the real homes from the first run after the switch, so a conversation can be resumed in the terminal (`codex resume`, `claude --resume`) and vice versa. Sessions of the 0.15 homes are not carried over (§2.6).

### 2.1.1 Scoped sandbox disposition (D-044)

[D-044](decisions/d-044-scoped-sandbox-under-facade.md) is the implementation contract for #34. Codex/Claude offer only `native` in 1.0, using the presets and trust rules above. Local keeps its separate `scoped` filesystem boundary; DeepSeek and Gemini remain native-only. Permission presets are independent of the retired cloud isolation toggle.

- Remove cloud-scoped admission and dispatch, including workflow, schedule, retry and already queued paths. An explicit or historical scoped request fails with `execution_mode_unsupported` before any provider spawn or credential access; it never falls back to native.
- Keep history and old files readable and untouched. Missing legacy modes use D-044's conservative resolution; unresolved modes cannot gain native execution. The owner starts a new native conversation explicitly. The UI removes the Codex/Claude isolation toggle and explains unsupported historical conversations and stale drafts.
- Do not copy the real CLI credentials, export keychain logins, bind real homes into `prepare_scoped`, or read the retired 0.15 homes. There is no cloud-scoped feature flag. A future facade-compatible sandbox requires a new decision.
- The backend admission guard must land with or before the #45 release that removes credential-file overrides. The W14 UI/retirement completion follows #45 and #46; this ordering has no dependency cycle. #45 retains ownership of native presets, home discovery and scheduled parity.

### 2.2 Sign-in on the CLI's own login

- Log in / Renew access keep their place in Settings › Providers. `login_environment(state, provider)` keeps the host session (browser, display) and stops overriding the homes, so `codex login` (or `codex login --device-auth` headless) and `claude auth login` write the CLI's own credential store. Status: `codex login status`, `claude auth status --json` (both confirmed in `--help`).
- Logout stays out of KeepHarness (signing out of the CLI everywhere is a terminal action); Renew access re-runs the login.
- #35 (sign-in URL cannot be selected or copied alone): the login job parses the first `https://` URL from the CLI output into its own field (`login_url`) of the job state; the UI shows it in a read-only input with a Copy button and an Open button, separate from the log text. Open and Copy are offered only when the URL host is on a fixed allow list of the providers' auth domains (a URL on any other host is shown as plain text with no button). The URL is held in memory for the job only: it is never written to the audit log or to disk (it may carry a one-time state parameter) and is dropped when the job ends.
- #36 (finish on its own after the code is pasted; Check account): after `submit_login_code` (Claude) or while a device-code login runs (Codex), the job polls the status command with backoff (2 s, then x1.5, cap 10 s, stop after 10 minutes or on process exit) and completes as soon as status says signed in; on completion it runs the same provider check as the Check account button (`manager.check_provider`), refreshing models. A "Check account" button is always present next to Renew access and runs that check on demand. **UNVERIFIED** alternative for Codex: the app-server `account/login/start` + `account/updated` notification ([app-server docs](https://learn.chatgpt.com/docs/app-server)) would remove polling; not adopted until its device-code flow is confirmed on 0.157.1.

### 2.3 State readers and writers

One adapter per provider implements the seam (contract below). Kinds are `plugin`, `app`, `mcp`, `skill`, `hook`, `instructions` (the last two are read-only in the first cut, needed by the DeepSeek seam, §2.7); scopes are `user`, `project`, `local` (Claude's per-person project file), `managed` (read-only) and `profile` (a named layer of the provider's config).

| Provider · kind | Read (global and project) | Write (enable/disable) | Source |
| --- | --- | --- | --- |
| Codex · plugin | `plugins."<name>@<marketplace>".enabled` in `$CODEX_HOME/config.toml`; installed list from app-server `plugin/list` | app-server `config/batchWrite` key `plugins.<id>.enabled` with `expectedVersion`. There is no `codex plugin enable` (confirmed: `codex plugin --help` lists only `add`, `list`, `marketplace`, `remove`) | [config reference](https://learn.chatgpt.com/docs/config-file/config-reference), [app-server](https://learn.chatgpt.com/docs/app-server) |
| Codex · skill | app-server `skills/list` per `cwd` (user and repo skills); `[[skills.config]]` entries (`path`, `enabled`) | app-server `skills/config/write` (enable/disable by path) | same |
| Codex · mcp | `mcp_servers.<id>` (+ `.enabled`, default true) in user config and, for a trusted project, `<project>/.codex/config.toml` | `config/batchWrite` key `mcp_servers.<id>.enabled` in the user file; for a project-scoped switch, the same call on the project's config file. **Note (2026-10-07):** codex-cli 0.157.1 refuses project-layer writes (`configLayerReadonly`, "Only writes to the user config are allowed"), so project-layer Codex rows are read-only with that reason; a project-scoped switch through the project's config file does not hold. | same |
| Codex · app | app-server `app/list` (enabled metadata); `apps.<id>.enabled` (default true) | `config/batchWrite` key `apps.<id>.enabled` | same |
| Claude · plugin | `enabledPlugins` in `~/.claude/settings.json`, `<project>/.claude/settings.json`, `<project>/.claude/settings.local.json`, managed settings (read-only) | `claude plugin enable\|disable <plugin> -s user\|project\|local --json` (confirmed in `--help`) | [settings](https://code.claude.com/docs/en/settings), [plugins reference](https://code.claude.com/docs/en/plugins-reference) |
| Claude · skill | skills from `~/.claude/skills`, `<project>/.claude/skills`, plugins; state from `skillOverrides` (`on`, `name-only`, `user-invocable-only`, `off`; absent = `on`) in the settings files | Direct JSON edit of `skillOverrides.<name>` in the chosen settings file with the `settings.json` safeguards below (no CLI command found; the interactive `/skills` menu is the CLI's own writer). Plugin skills are not affected by `skillOverrides`: their row shows "Part of plugin X" and no switch | [skills](https://code.claude.com/docs/en/skills) |
| Claude · mcp | user and local servers in `~/.claude.json` (`mcpServers` top level and under `projects["<path>"]`), project servers in `.mcp.json`; on/off from `projects["<path>"].disabledMcpServers` (opt-out list) and `enabledMcpServers` (only default-off built-ins); `.mcp.json` approval from `enabledMcpjsonServers` / `disabledMcpjsonServers` / `enableAllProjectMcpServers` | Direct JSON edit of `projects["<path>"].disabledMcpServers` in `~/.claude.json` with the safeguards below (per project, the same place `/mcp` writes). For `.mcp.json` servers, the approval keys in `.claude/settings.local.json` (§2.5) | [MCP](https://code.claude.com/docs/en/mcp) |
| Claude · app | N/A: Claude Code has no separate "app" kind; claude.ai connectors are MCP servers and follow the `mcp` row | — | [MCP](https://code.claude.com/docs/en/mcp) |
| DeepSeek · all | The Codex reader bound to `<state>/providers/deepseek` (interim, §2.7) | The Codex writer through an app-server started with that `CODEX_HOME` | — |

Notes that shape the contracts:

- Claude MCP on/off lives per project inside `~/.claude.json`, not in `settings.json`; a Claude MCP switch is therefore a per-project switch. For the "No project" scope (`sem-projeto`) the key is the folder KeepHarness runs those conversations in.
- **Write layer: where the CLI would write.** User scope by default; project scope through the CLI's scope flag (`claude plugin enable -s user|project|local`) or the project's own config file (Codex `<project>/.codex/config.toml`). There are no locked rows unless the CLI cannot write that layer (managed settings, or an item decided by a layer the CLI has no writer for); then the row shows the effective state, the deciding file and the reason.
- Codex project config is read only when the project is trusted (`projects.<path>.trust_level`); untrusted projects skip project layers. KeepHarness shows the effective value and the layer that decides it.
- **`~/.claude.json` safeguards.** The file is credential-bearing (sign-in session, MCP config, per-project trust). KeepHarness never logs or copies its session fields, and every log line that mentions it is redacted. Each write: (1) back the file up first into a KeepHarness-owned directory, `<state>/backups/claude-json/` (mode 0700, files 0600, newest 3 kept; the copies hold the sign-in session, so the directory is excluded from rollback snapshots, exports and diagnostics bundles), not into `~/.claude/backups/`, because Claude Code keeps the most recent `.claude.json.backup.<ts>` files there and KeepHarness files would push out its own (how Claude prunes that folder is **UNVERIFIED**); (2) apply the one-key change through `write_json_atomic`; (3) validate the new bytes before they replace the file: re-parse and type-check the touched key (the schemastore schema covers `settings.json` only, not `~/.claude.json`); (4) on a new error (one that the pre-write file did not have), refuse: nothing is written, the file keeps its bytes and the call returns `provider_state_validation_failed`. A revert detector (§2.4, F14) notices when Claude Code rewrites the file and brings the old value back.
- **`settings.json` edits** (for example `skillOverrides`): before the write, validate the new bytes against [the schemastore schema](https://json.schemastore.org/claude-code-settings.json) and refuse (nothing written) on a new error; `claude -p` silently ignores an invalid settings file, so a bad write would otherwise look like a no-op. The validator is the `jsonschema` package, which `requirements.txt` pins only transitively today; Issue 1 either declares it or falls back to a type check of the touched key. `claude doctor` as a machine-readable validator is **UNVERIFIED**.
- Version drift guard: each adapter declares `tested_versions` (for example `codex >=0.157,<0.158`, `claude >=2.1.292,<2.2`). Outside that range reads still show and runs warn (Codex, Claude) or fail hard (`dsh`) (§2.1); CLI-mediated writes (Claude plugin command, Codex app-server) stay allowed because the CLI validates its own schema; direct file edits (Claude `skillOverrides`, `~/.claude.json`) are refused with `provider_state_version_untested` until the range is widened by a test run.

### 2.4 External change detection

- Each adapter returns `watch_paths(project_root)`: Codex `$CODEX_HOME/config.toml`, `$CODEX_HOME/skills/`, `<project>/.codex/config.toml`, `<project>/.agents/skills/`; Claude `~/.claude/settings.json`, `~/.claude.json`, `~/.claude/plugins/installed_plugins.json` (**UNVERIFIED** path), `<project>/.claude/settings.json`, `<project>/.claude/settings.local.json`, `<project>/.mcp.json`, skill folders.
- Fingerprint = for each path `(st_mtime_ns, st_size, st_ino)`, or a "missing" marker; directories use the max mtime of their direct children. Only when the stat tuple changes is the full state re-read and diffed by item id. (The write-side conflict fingerprint is the sha256 of the bytes read, see Contracts; the stat tuple is only the cheap "did anything move" check.)
- When it runs: (a) every `GET /api/provider-state` (Plugins page and Settings › Providers load or regain focus); (b) at each run start, for the run's provider and project (cheap stat, no re-read unless changed); (c) no timer. Requests within 5 s of the last check for the same provider and project reuse the result (coalesced through one `asyncio.Lock` per provider). This bounds the cost to one stat pass per screen load or run start: no polling storm, no background loop. `GET` has no side effect: it reports pending notices but does not mark them seen.
- Last-seen item maps live in `<state>/provider-state-seen.json` (owner-only, written atomically by `ControlStateRepository._replace`). Changes made through KeepHarness update it in the same step, so they never show as external. A notice is cleared by `POST /api/provider-state/notices:ack`, not by reading it.
- What the person sees (D-039): a notice on the Plugins page, a notice on Settings › Providers, and a toast the first time it is detected: "Changed outside KeepHarness: Codex › plugin `github@openai-curated` was turned off (config.toml, 10:42)." Several changes collapse into "3 changes outside KeepHarness" with a details list. When Claude Code rewrites a value KeepHarness just wrote, the notice reads "reverted by Claude Code" (F14).
- Codex app-server `skills/changed` (emitted when watched skill files change, per the app-server docs) may refresh the skills list while an app-server is up; it is an optimization, not the mechanism.

### 2.5 Owner-only access, trust and project MCP approval

**Single owner (D-039).** KeepHarness has one person: the server runs at home with her accounts signed in, the client runs at work over Tailscale. There are no guests and no run classes. The requirement that replaces them: the remote-access gate is the only boundary, so it must stay owner-only and fail closed. Facts, all in the current code:

- The admin panel is loopback-only and refuses `tailscale-user-login` and `tailscale-funnel-request` (`control/routes.py:66-73`).
- The harness refuses Funnel with `funnel_denied` (`agent_service/services/conversation_service.py:458-460`).
- A Tailscale login reaches the harness only through Serve when the socket belongs to tailscaled; otherwise it fails closed (`conversation_service.py:534-545`, `through_tailnet_serve`).
- The Tailscale login allow list is validated at `control/manager.py:502-511` and mapped to clients at `control/runtime_config.py:243-267`.

Today each allow-listed login is its own `tailnet-<hash>` client limited to `sem-projeto`, and any non-`local` client counts as a guest (`conversation_service.py:2628`). New: `local` and the allow-listed tailnet logins are the owner, with every project. **The shared VPN key (client `vpn`) is removed.** This supersedes the multi-identity part of UC-001 (ten-user concurrency, per-identity budgets) and the D04 "owner-only" clamp for remote clients; the allow list of Tailscale logins stays, because it is the owner's gate (the integrations allow list `services.*.integrations` is a different setting and is dropped, §2.6).

Residual risk, stated: all security now rests on who counts as `local` and as an allow-listed tailnet login. An empty Tailscale login allow list means nobody enters remotely. `local` is not any loopback socket: it needs a loopback peer, a loopback `Host`, `local_access` on and a held local session (`conversation_service.py:520-531`), so another account on the same machine is not the owner unless it holds that session; implementations must keep that rule.

**Trust (D-039).** A project trusted by either CLI is trusted in KeepHarness: Codex `projects."<path>".trust_level = "trusted"`; Claude `projects["<path>"].hasTrustDialogAccepted` in `~/.claude.json` (confirmed). `claude -p` skips the workspace trust dialog ("Only use this in directories you trust", `claude --help`), so KeepHarness supplies the prompt itself:

- Untrusted project: KeepHarness shows a CLI-style trust prompt in the conversation and on the Plugins page. Because trust in either CLI counts for both, the prompt says so: accepting also enables the project's Claude hooks and the `.mcp.json` servers approved by versioned project settings. The trust and MCP-approval actions are harness routes (`agent_service`) behind the same owner gate and origin/fetch-metadata checks as every harness write, so they work from the remote client; the admin-panel routes below are their loopback twins. On accept it writes trust into the CLI's own state: Codex through app-server `config/batchWrite` (`projects."<path>".trust_level`), Claude through the backed-up `~/.claude.json` path (§2.3 safeguards).
- Until accepted: Claude runs with `--setting-sources user` and `--settings '{"disableAllHooks":true}'`, so the project's `.claude/settings.json` and `settings.local.json` (hooks, an `env` block such as `BASH_ENV`, `apiKeyHelper`, `statusLine`, `permissions.allow` rules) are not loaded, and loads no project MCP servers (fixture: a project `env` entry never reaches the run); Codex `trust_level = "untrusted"` already skips project config, hooks and rules.
- Codex hooks that are trusted by neither the project nor the owner stay "pending review" (§2.1).

The conversation and Plugins page also let the owner revoke project trust.
Revocation writes `trust_level = "untrusted"` in Codex and
`hasTrustDialogAccepted = false` in Claude for the rendered project. Both writes
use the same owner gate, canonical-root recheck, concurrency guards and own-write
receipts as acceptance. Success requires both stores to confirm the requested
state. If either write or confirmation fails, compensate completed writes in
reverse order, restoring the previous project entry (including its absence).
Codex compensation uses native `config/batchWrite` with its returned opaque
version; Claude compensation uses the guarded atomic writer. A concurrent edit
must never be overwritten: a failed compensation returns an explicit incomplete
rollback error, never success. Publish own-write receipts and notice state only
after both trust writes and confirmations succeed. Repeated request cancellation
must wait for the in-flight native writer and every reverse compensation while
both provider locks remain held, then propagate cancellation to the caller. Keyboard
focus stays on the corresponding trust action after the view refreshes.

Each trust writer and each compensation has its own finite deadline. A native
Codex session that reaches the deadline is killed as a process group and reaped
before compensation or lock release; a detached thread is not a timeout solution.
Claude file writes acquire their real path lock within the remaining deadline
and check it again immediately before replacing, creating or removing a file.
After timeout, compensate completed changes, release both provider locks, return
a clear timeout error when compensation succeeds and invalidate cached state so
the next read uses the real CLI files. If Codex wrote but never acknowledged its
version, recover the native version only when its complete user configuration
matches the expected trust-only
change, then compensate through native compare-and-swap. Preserve concurrent
changes and report incomplete rollback when recovery cannot be confirmed.

An explicit trust entry for the selected child takes precedence over a Codex
configuration layer inherited from a parent when confirming the child's trust.
When the native effective configuration has no explicit child entry, preserve
the CLI's inherited verdict. The `trust.inherited_from` metadata identifies a
trusted ancestor whose configuration remains loaded independently; both trust
panels explain that revoking the child does not revoke its parent.

**Project MCP approval (B2).** `claude -p` loads project-scoped `.mcp.json` servers without asking ([MCP docs](https://code.claude.com/docs/en/mcp)). Before a Claude run the adapter computes the approved set: names in `enabledMcpjsonServers` (or every server when `enableAllProjectMcpServers` is true) minus `disabledMcpjsonServers`, across the settings layers. Every `.mcp.json` server not in that set is passed as `--settings '{"disabledMcpjsonServers":[<names>]}'` (an entry in any settings file rejects a server, and `--settings` approvals apply even in an untrusted folder). KeepHarness then asks the owner, like the CLI would, and writes the approval to `<project>/.claude/settings.local.json` (`enabledMcpjsonServers`, validated like any `settings.json` edit); once written, the server is in the approved set on the next run. Codex has no `.mcp.json`; its project servers load only for a trusted project (trust above).

An owner-disabled project MCP server is shown as "Disabled by owner" in the
conversation and Plugins page, without Approve/Revoke actions. Stored approval
does not override the native owner switch; runtime injection remains blocked.
The server rechecks that native switch before saving an approval and refuses an
owner-disabled server, including requests from stale controls or direct POSTs.

### 2.6 Leaving the 0.15 homes behind

There is no migration (D-039; she is not using KeepHarness now). The 0.15 folders under `<state>/providers/` (`home/.codex`, `home/.claude`, their logins, sessions and the `home/.claude.json` file) are left untouched on disk and ignored: no code reads them after the switch (a conventions test asserts it). Sign-in is one click on the CLI's own login (§2.2). The release notes name the path (`<state>/providers/home`) so the owner can delete it when she wants.

- The integrations allow list `services.*.integrations` and `personal_setup` are dropped from settings; they are not applied to the CLI state and not offered back. The Tailscale login allow list stays (§2.5).
- DeepSeek keeps its own home (`<state>/providers/deepseek`). The guard stays: that home must never hold the ChatGPT `auth.json`; a run start fails with `deepseek_credential_isolation` when it does (§2.7).
- Rollback is the previous release: the 0.15 folders are intact.

### 2.7 DeepSeek and the provider seam

- **1.0 decision ([D-043](decisions/d-043-deepseek-engine.md)).** DeepSeek stays on the Codex engine with `CODEX_HOME=<state>/providers/deepseek`, the key in `<PREFIX>_API_KEY` from `api_provider.key_file`, `model_provider="tail_api"` and `requires_openai_auth=false`. #47's seam and isolation fixes are required before release. Its plugins, skills and MCP servers are those of its own home, managed on the Plugins page under the DeepSeek provider with the Codex writer bound to that home. It does not see the owner's Codex global orchestration. `dsh` remains the autonomy target; there is no supported `dsh` run path in 1.0. D-043 contains the dated official-source verification and corrections to the earlier [research](research/deepseek-cli-2026-10.md).
- **Key leakage (M6).** Codex's `shell_environment_policy.ignore_default_excludes` defaults to true, so variables whose names contain KEY, SECRET or TOKEN reach child shells. For the DeepSeek home: exclude `<PREFIX>_API_KEY` explicitly (the docs mark `exclude` as legacy in favour of `filters`; the exact `filters` syntax is **UNVERIFIED**, so the env-leak test decides which form ships); pass `-c cli_auth_credentials_store="file"` (values `file|keyring|auto|ephemeral`) so nothing lands in the OS keyring; the isolation check covers the keyring as well as `auth.json`. Test: `env` run in a model shell does not show the key.
- **Seam completion for #47** (reuse the existing types in the contracts below):
  - Wire the engine id separately from the provider id: 1.0 identifies DeepSeek's engine as `codex`; a later qualified adapter can coexist under the same provider. The spike does not expose production dispatch.
  - Wire consumers of the existing `Kind` values `hook` and `instructions` and `Scope` value `profile` (layered config and profiles); do not add duplicate types or fabricate unsupported Codex state.
  - The shared skills root `~/.agents/skills` is named in the adapter and shown on the Plugins page as affecting the other providers too.
  - A `set_api_key` credential operation (DeepSeek key file, mode 0600, never echoed or logged).
  - Hard fail at run start on an unsupported `dsh` version (§2.1); Codex and Claude only warn.
  - Session identity per engine: a session id belongs to `(provider id, engine id)`; resuming across engines is refused instead of guessed.
- **Later opt-in only.** A fixture-based spike must prove the pinned `dsh` version's ACP authentication boundary, updates, approvals, cancellation, session identity/resume, usage and permission behavior before an experimental flag exposes it. The documented ACP resume method is `session/resume`; `session/load` and transcript replay are unsupported in the audited source. Reuse Gemini transport primitives only where compatible. Unproven CLI/storage details stay **UNVERIFIED**, not implementation assumptions. There is no automatic engine fallback or cross-engine session migration. See D-043 for the full promotion gate and the items to revisit with Sophia.
- Seam: `ProviderStateAdapter` (below) plus the existing run entry `run_native(config, prompt, event, project, model, effort, session_dir, approve)`. A later DeepSeek `dsh` adapter implements both alongside the Codex route through explicit engine dispatch; provider identity remains `deepseek`. The shared control surface consumes capabilities instead of assuming every engine is Codex. #47 owns the 1.0 seam; the post-1.0 runtime is separate work under #33.

### Contracts (function-level guarantees)

Types (in `adapters/shared/provider_state.py`):

```python
Kind = Literal["plugin", "app", "mcp", "skill", "hook", "instructions"]
Scope = Literal["user", "project", "local", "managed", "profile"]

@dataclass(frozen=True)
class StateItem:
    id: str            # "<kind>:<provider-native id>", e.g. "plugin:github@openai-curated", "mcp:linear"
    kind: Kind
    name: str
    scope: Scope       # the layer that decides the effective value
    enabled: bool      # effective value after layering
    source: str        # file or command that decides it, for display ("~/.codex/config.toml")
    writable: bool
    reason: str = ""   # why not writable, shown under the switch
    affects: tuple[str, ...] = ()   # other provider ids that read the same source (shared skills root)

@dataclass(frozen=True)
class StateSnapshot:
    provider: str
    engine: str        # engine id, separate from the provider id (§2.7)
    project_root: str | None
    items: tuple[StateItem, ...]
    fingerprint: str   # sha256 over the bytes of the files read; changes when any of them changes
    cli_version: str
    warnings: tuple[str, ...] = ()
```

Note (2026-10-07, #39): the Codex adapter reads no file itself, so its `fingerprint` is a sha256 over the app-server layer `version` strings (each one already a sha256 of that layer file's content) plus the ids and flags of the skills, plugin and app lists the app-server returned.

Errors (derive from `HarnessError`, codes snake_case): `ProviderStateConflictError` (`provider_state_conflict`), `ProviderStateUnsupportedError` (`provider_state_write_unsupported`), `ProviderStateSchemaError` (`provider_state_unreadable`), `ProviderStateVersionError` (`provider_state_version_untested`), `ProviderStateValidationError` (`provider_state_validation_failed`), `ProviderVersionUnsupportedError` (`provider_version_unsupported`, run start), `ProviderCommandError` (`provider_command_failed`, carries exit code and a redacted message, never the command's environment).

**`def write_json_atomic(path: Path, change: Callable[[dict], dict], expected_sha256: str, *, backup_dir: Path | None = None, validate: Callable[[bytes], Sequence[str]] | None = None) -> str`**
- Purpose: the one direct-edit path for Claude's `settings.json` and `~/.claude.json`; returns the sha256 of the new bytes.
- Guarantees: resolves `path` with `realpath` first (stow/chezmoi symlinks), so the target is the real file; takes the `threading.Lock` of that real path (a plain synchronous function: the work is blocking file I/O and `~/.claude.json` is often hundreds of KB, so async callers run it with `asyncio.to_thread`; the lock cooperates only with KeepHarness itself, which is the only writer in the control process); reads the bytes, compares their sha256 with `expected_sha256` (content hash, not stat), applies `change` to the parsed JSON keeping every unknown key and key order and the detected indent; writes a temp file beside the real target (same filesystem) with the original mode (and uid/gid where permitted), `fsync`, `os.replace`, `fsync` the directory; when `backup_dir` is set, copies the pre-write bytes there first (0600); when `validate` is set, runs it on the pre-write bytes and on the new in-memory bytes before `os.replace` and, if the new result has an error the pre-write bytes did not, raises `ProviderStateValidationError` and writes nothing (no temp file, no backup, no restore, so a concurrent write by the CLI is never touched); uses the CLI's own lock if one exists (**UNVERIFIED** for both Codex and Claude; none is assumed).
- Must-not: write through a symlink replacement (that would swap the owner's symlink for a regular file); log file content; leave a temp file behind on any error; create the file.
- Errors: `ProviderStateConflictError` when the bytes changed or the file is gone since it was read; `ProviderStateSchemaError` when the file is not valid JSON; `ProviderStateValidationError` as above.
- Decided for #40 (Sophia, 2026-10-07): the first write of a settings file that does not exist yet (`skillOverrides` in `settings.local.json` or the user `settings.json`; later the `.mcp.json` approval keys, §2.5) goes through an explicit create mode of `write_json_atomic`: the caller passes a "missing" expectation, the file is created exclusively (`O_EXCL`) beside the target in an existing parent folder, `0600` for `settings.local.json`, and a file that appears meanwhile is a `ProviderStateConflictError`. Claude Code creates these files itself, so this is still "where the CLI would write". Without the create mode the function keeps refusing to create.

**`ProviderStateAdapter.read_state(project_root: Path | None) -> StateSnapshot`**
- Purpose: the effective state of every item of this provider for the global scope and one project.
- Guarantees: read-only; never starts a model turn; bounded time (10 s per CLI call); never returns secret values (MCP `env`, headers, tokens are dropped, as `control/integrations.py` does today); an unparseable file yields a warning and the items it could not read are absent, not guessed.
- Must-not: read credential files (`auth.json`, `.credentials.json`, keyrings) or the session fields of `~/.claude.json`; write anything; follow symlinks outside the provider's folders when listing skills.
- Errors: `ProviderStateSchemaError` only when no source is readable at all; otherwise warnings.

**`ProviderStateAdapter.set_enabled(item_id: str, scope: Scope, enabled: bool, expected_fingerprint: str, *, project_root: Path | None = None) -> StateSnapshot`** (`project_root` added 2026-10-07 for W9: project and local layers, and Claude's per-project MCP switch, need the project the item belongs to)
- Purpose: turn one item on or off in the CLI's real state, in the layer the CLI would write (§2.3).
- Guarantees: uses the CLI's writer when one exists (table §2.3); a direct edit goes through `write_json_atomic` (with `backup_dir` and `validate` for `~/.claude.json` and `settings.json`), then re-reads and confirms the value. Returns the fresh snapshot. Updates the last-seen map so the change is not reported as external.
- Must-not: write `managed` scope; create a file the CLI would not create; touch any other key; reformat JSON beyond the detected indent; add a lock row where the CLI could write the layer.
- Errors: `ProviderStateConflictError` when the fingerprint differs before the write or the confirm-read shows another value (the UI reloads and shows what changed); `ProviderStateUnsupportedError` for an item kind or scope without a writer; `ProviderStateVersionError` for a direct edit outside `tested_versions`; `ProviderCommandError` when the CLI command fails (its message is shown, the file is untouched); `ProviderStateValidationError` when the write is refused.

**`ProviderStateAdapter.watch_paths(project_root: Path | None) -> tuple[Path, ...]`** — Purpose: what the change detector stats. Guarantees: pure, no I/O beyond path resolution. Must-not: include credential files.

**`ProviderStateAdapter.is_project_trusted(project_root: Path) -> bool`** — Guarantees: true when either CLI's own state trusts the project (§2.5); `False` when unknown. Must-not: write trust.

**`ProviderStateAdapter.trust_project(project_root: Path) -> None`** — Purpose: write trust into the CLI's own state after the owner accepts the prompt (Codex `config/batchWrite` on `projects."<path>".trust_level`; Claude `projects["<path>"].hasTrustDialogAccepted` through `write_json_atomic` with the §2.3 safeguards). Must-not: trust a parent or sibling path; run without an explicit owner action. Errors: as `set_enabled`.

**`ProviderStateAdapter.approved_project_servers(project_root: Path) -> frozenset[str]`** — Purpose: the `.mcp.json` servers the owner has approved (§2.5). Guarantees: read-only; the set is `enabledMcpjsonServers` (or all when `enableAllProjectMcpServers`) minus `disabledMcpjsonServers` across the settings layers; empty when the project is untrusted. Must-not: include a server the settings layers reject. Codex: returns the empty set (no `.mcp.json`).

**`ProviderStateAdapter.run_environment(project_root: Path, trusted: bool, permission_flags: Sequence[str]) -> RunSetup`** where `RunSetup = (environment: dict[str, str], extra_args: list[str], settings_overrides: dict)`.
- Purpose: everything a native run needs to load the provider's orchestration.
- Guarantees: the same setup for attended and scheduled runs; adds the CLI's own sandbox/approval flags (`permission_flags`) and nothing else that filters orchestration; for an untrusted project applies the §2.5 rules; for Claude adds `disabledMcpjsonServers` for every unapproved `.mcp.json` server; harness-added servers respect native owner-disabled entries (in particular, a disabled `harness_reader` is not re-enabled); warns outside `tested_versions` for Codex and Claude and refuses with `ProviderVersionUnsupportedError` for engines that hard-fail (`dsh`).
- Must-not: set `HOME`, `CODEX_HOME` or `CLAUDE_CONFIG_DIR` to a harness folder (except DeepSeek interim); read the owner's files into the prompt; pass `--dangerously-bypass-hook-trust`; branch on who the caller is.
- Run notices use the existing event stream, outside the `RunSetup` tuple: `provider_warning` carries the backend, a notice code when known, and a bounded message. Native run entry performs the version check; Codex hook-review diagnostics are forwarded without changing hook trust. The conversation activity renders these messages as text.
- Before adding the native Codex reader, the existing app-server connection reads `config/read` for the run's cwd. Its effective configuration is authoritative for user settings, trusted project layers from the native project root through cwd, root markers and later-layer precedence; disabled/untrusted layers must not be reconstructed from raw files. An existing effective reader entry is left to the CLI, including a deeper layer that re-enables it. An unavailable or malformed configuration response does not authorize reader injection. DeepSeek and Local retain their separate reader setup.

**`ProviderStateAdapter.credential_isolation() -> CredentialRule`** — DeepSeek: `{"home": <state>/providers/deepseek, "forbidden_files": ("auth.json",), "keyring": "checked", "env_only": ("<PREFIX>_API_KEY",)}`; run start fails with `deepseek_credential_isolation` when a forbidden file or keyring entry exists. Codex/Claude: `None`.

**`ProviderStateAdapter.login_command(headless: bool) -> list[str]`, `login_status() -> LoginStatus`** — Same commands as today, with the real home; `LoginStatus = {signed_in: bool, account_label: str | None}`; never returns tokens.

**`ProviderStateAdapter.set_api_key(secret: SecretStr) -> None`** — Purpose: the credential operation of key-based providers (DeepSeek). Guarantees: writes the key file with mode 0600; never echoes or logs it. Must-not: write it to `auth.json`, the environment of the control process or the audit log. Errors: `ProviderStateUnsupportedError` for OAuth providers (Codex, Claude).

**HTTP (`control/routes.py`, local-only guard unchanged, remote gate as §2.5; errors use the control envelope `{"error": <code>}`, `control/routes.py:749-750`):**
- `GET /api/provider-state?provider=<id>&project_id=<id|sem-projeto>` → `200 {snapshot, external_changes: [{item_id, before, after, source, detected_at}]}`; side-effect free (no notice is marked seen); `404 provider_unknown`.
- `POST /api/provider-state` body `{provider, project_id, item_id, scope, enabled, fingerprint}` → `200 {snapshot}`; `409 provider_state_conflict` with the fresh snapshot; `422 provider_state_write_unsupported | provider_state_version_untested | provider_state_validation_failed`; `502 provider_command_failed` with a redacted `provider_message`. Not idempotency-keyed: setting the same value twice is naturally idempotent.
- `POST /api/provider-state/notices:ack` body `{provider, project_id, notice_ids}` → `200 {}`; marks notices seen.
- `POST /api/provider-state/trust` body `{provider, project_id, expected_project_root, trusted?}` → `200 {snapshot}` (`trusted` defaults to `true` for acceptance; explicit `false` revokes trust in both CLIs); `POST /api/provider-state/mcp-approvals` body `{provider, project_id, expected_project_root, server, approved}` → `200 {snapshot}`. The harness `/v1` twins use the same bodies. Each control captures its rendered provider, project id and canonical snapshot root; navigation invalidates that context. The server requires the rendered root and compares it with the current resolved project under the write locks before any CLI action. A missing context is refused; a changed project binding is a conflict requiring a fresh prompt (issue #44 security review correction).
- `POST /api/provider-check` (Check account, #36) → existing check result.
- `/api/settings` no longer accepts `services.*.integrations` or `personal_setup`.

### Failure modes

| # | What goes wrong | Prevention (where) |
| --- | --- | --- |
| F1 | Partial write corrupts the owner's `config.toml`, `settings.json` or `~/.claude.json` (crash, disk full, bad value) | CLI writers own their files; direct edits use `write_json_atomic` (temp beside the real target + fsync + `os.replace` + dir fsync), a backup in `<state>/backups/claude-json/` for `~/.claude.json`, validation before the write (refuse on a new error). Claude copies a broken `~/.claude.json` to `~/.claude/backups/` and asks ([settings docs](https://code.claude.com/docs/en/settings)), which is a last resort, not a plan |
| F2 | Lost update: Claude Code, an IDE extension or the desktop app rewrites `~/.claude.json` between KeepHarness's read and replace, and one change disappears | sha256 check of the bytes read immediately before `os.replace` and a confirm-read after; on mismatch return `409` and show the fresh state; backup allows recovery. Residual window (milliseconds) cannot be closed without a lock the CLI honours (none known, **UNVERIFIED**); documented |
| F3 | A future implementer "simplifies" by writing `config.toml` with `tomllib` + a hand-rolled serializer | Forbidden by contract: comments and formatting would be lost on the owner's main config. Codex writes only through app-server `config/batchWrite`; when it is unavailable the switch is read-only |
| F4 | The Tailscale login allow list gains a login that is not the owner's; that identity is now the owner with every project and command execution as her | Owner-only gate documented on the allow-list setting; validation at `control/manager.py:502-511`; test that an allow-listed login reaches only through Serve and that the admin panel stays loopback-only and refuses `tailscale-*` headers |
| F5 | Serve or Funnel is misconfigured and an outside request is treated as `local` or as an allow-listed login | `through_tailnet_serve` fails closed when the socket does not belong to tailscaled (`conversation_service.py:534-545`); Funnel refused with `funnel_denied` (`:458-460`); tests for each branch; no code path grants owner rights from a request header alone. The shared VPN key is gone, so no secret substitutes for identity |
| F6 | A cloned, untrusted repository runs its `.claude/settings.json` hooks because `claude -p` skips the trust dialog | `is_project_trusted` gate; untrusted → `--setting-sources user`, `--settings '{"disableAllHooks":true}'` and no project MCP until the owner accepts the prompt; Codex `trust_level="untrusted"`; test with a fake project hook writing a marker |
| F7 | DeepSeek ends up with the ChatGPT login (shared `CODEX_HOME`, a copied `auth.json`, a keyring entry, a "simplification" that drops its home) or its key leaks to model shells | DeepSeek keeps its own `CODEX_HOME`; `credential_isolation` check (file and keyring) at run start; `cli_auth_credentials_store="file"`; explicit exclude of `<PREFIX>_API_KEY`; tests: env has `CODEX_HOME` under `<state>/providers/deepseek`, `requires_openai_auth=false`, and `env` in a model shell does not show the key |
| F8 | The old 0.15 homes (logins, sessions) stay on disk and are read again by a "helpful" fallback, or are mistaken for the real state | No code reads `<state>/providers/home` (conventions test); release notes name the path; DeepSeek guard unchanged |
| F9 | CLI upgrade changes a key (for example `skillOverrides` values, `disabledMcpServers` location) and KeepHarness writes a dead or harmful key | `tested_versions` gate on direct edits, a run warning (hard fail for `dsh`); confirm-read after write; validation; reads tolerate unknown keys and report warnings |
| F10 | Change detection becomes a storm (every UI poll re-reads all files, or a watcher loop per conversation) | Detection only on screen load and run start, 5 s coalescing per provider and project, stat before parse |
| F11 | A KeepHarness-only restriction creeps back onto runs (for example "Read only" disabling connectors, a hooks grant switch) | Presets map to the CLI's own sandbox/approval modes only (`command_permissions`); a test asserts the same orchestration is loaded for every preset |
| F12 | A KeepHarness change is reported back as "changed outside KeepHarness" | `set_enabled` updates the last-seen map in the same lock |
| F13 | Someone keeps `settings.services[p].integrations` as a hidden second filter "for safety" | Contract: the allow list is not a run input; the conventions test asserts no run builder reads `integrations` |
| F14 | Claude Code rewrites `~/.claude.json` and silently reverts KeepHarness's MCP switch (or another key) | Revert detector: the touched key is re-read at the next screen load and run start; if it returned to the pre-write value without a KeepHarness write, show "reverted by Claude Code" (Plugins page, Settings › Providers, toast) and keep the owner's choice visible; the backup allows redoing it |
| F15 | A project `.mcp.json` server runs unapproved under `claude -p` (arbitrary command as the owner) | `approved_project_servers` and `disabledMcpjsonServers` in `--settings` (§2.5); test with a fake `.mcp.json` whose server writes a marker file, asserting the marker never appears until the owner approves |

### Integration points

- d=1: `adapters/shared/provider_setup.py` (`environment`, `child_source`, `login_environment`, `credential_file`, `run_settings`, `personal_setup_on`, `command_permissions`, `instructions`); `adapters/claude/native.py` `build_command`; `adapters/codex/native.py` `build_command`, `thread_parameters`, `host_servers`; `adapters/deepseek/backend.py` `runtime_options`; `control/manager.py` `integrations` (the inventory moves to the adapters), settings validation (~lines 258-260 `personal_setup`, ~475-489 `integrations`, 502-511 allow list), `check_provider` (~690-735); `control/routes.py` `login_provider`, `submit_login_code`, the remote guards (66-73).
- d=2: `control/runtime_config.py` (`homes_root`, `credential_file`, `mark_unrestricted`, per-client projects, 243-267 allow-list mapping); `agent_service/resources.py` (`homes_root`, `personal_setup_on`); `agent_service/integrations_view.py` (`CONFIG_FOLDERS`); `agent_service/services/conversation_service.py` (`child_source`, `run_settings`, 458-460, 534-545, 2628 guest rule); `adapters/claude/account.py`, `adapters/claude/auth.py`, `adapters/codex/backend.py`; `adapters/codex/scoped.py`, `adapters/claude/scoped.py` (copy the credential from the harness home; #34); `control/customize.js` (switches), `control/admin.js` (provider login UI).
- d=3: the guest and `LOCAL_CLIENT` removal reaches about 20 `guest` hits in 10 non-test files, 31 `LOCAL_CLIENT` checks, 13 project-sharing / VPN-key references and 19 test files (rg counts at the W8 review); `docs/scheduled-tasks.md` (isolation rule, superseded), `docs/provider-homes.md` (superseded), `/v1/resources`, `/v1/catalog`, `/v1/integrations` listings; quota meters reading the Claude account (D-028/D-032) now read the real login; `tests/test_env.py`, `tests/test_unsigned_provider_quarantine.py`, `tests/test_scoped_home_security.py`, `tests/admin-customize.spec.cjs`.
- Configuration touchpoints: settings `personal_setup` and `services.*.integrations` (dropped); the shared VPN key and client `vpn` (removed); runtime `provider_homes`, `auth_file` (dropped; DeepSeek keeps its home path); schedule fields (no `allow_hooks` / `allow_connectors`); new `<state>/provider-state-seen.json`, `<state>/backups/claude-json/`.
- Reference branch `feat/21-plugins-switches` (3 commits, `control/customize.js` +91, `tests/admin-plugins-switches.spec.cjs` +270): its switch UI and wizard-edit preservation are reusable; its allow-list targets (`/api/settings`) are replaced by `POST /api/provider-state`. Reference only, never merged.

## 3. What (implementation reference)

### 3.1 Paths

| Path | Change |
| --- | --- |
| `adapters/shared/provider_state.py` | New: types, errors, `ProviderStateAdapter` protocol, `write_json_atomic`, `fingerprint(paths)` |
| `adapters/codex/state.py` | New: Codex reader/writer (app-server JSON-RPC client reusing `adapters/codex/native.py` RPC plumbing) |
| `adapters/claude/state.py` | New: Claude reader/writer (`claude plugin ...`, settings and `~/.claude.json` edits, trust, project MCP approval) |
| `adapters/deepseek/state.py` | New: Codex adapter bound to the DeepSeek home + `credential_isolation` + `set_api_key` |
| `adapters/shared/provider_setup.py` | Retire homes and personal setup; `run_environment` per provider |
| `control/integrations.py` | Removed (callers move to the adapters through `control/manager.py`) |
| `<state>/backups/claude-json/` | New runtime directory: pre-write backups of `~/.claude.json` (0700 / 0600) |
| `docs/provider-homes.md` | Replaced by `docs/provider-state.md` (user-facing) when the work ships |

### 3.2 Commands and environment

| Item | Value |
| --- | --- |
| Codex login | `codex login` / `codex login --device-auth`; status `codex login status` |
| Claude login | `claude auth login`; status `claude auth status --json` |
| Claude plugin switch | `claude plugin enable <plugin> -s <user\|project\|local> --json`, `claude plugin disable ...` |
| Claude project MCP approval at run | `--settings '{"disabledMcpjsonServers":["<name>", ...]}'` |
| Claude untrusted project at run | `--settings '{"disableAllHooks":true}'` |
| Codex writes | `codex app-server --listen stdio://` then `config/batchWrite` (`edits`, `expectedVersion`), `skills/config/write` |
| DeepSeek (interim) | `-c cli_auth_credentials_store="file"` in its home |
| Env passed through | `HOME` (host), `CODEX_HOME` and `CLAUDE_CONFIG_DIR` only if set by the owner; DeepSeek `CODEX_HOME=<state>/providers/deepseek` |

Known issues: Codex `config/batchWrite` returns a conflict on a stale `expectedVersion`; the shape was pinned against 0.157.1 on 2026-10-07: JSON-RPC error `{"code": -32600, "message": "Configuration was modified since last read. Fetch latest version and retry.", "data": {"config_write_error_code": "configVersionConflict"}}`, and the fake `codex app-server` fixture reproduces it. `plugin/list` and `plugin/install` are marked "under development" in the app-server docs.

### 3.3 Test protocol

Fake homes only; fixtures never call cloud inference and never read the real home.

- An autouse fixture in `tests/conftest.py` (add or extend) sets `HOME`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR` to `tmp_path` subfolders and fails the test if `Path.home()` resolves to the real home; the same for `scripts/test-ui.sh` servers (isolated state already).
- Fixture homes: `config.toml` with comments, a `[[skills.config]]` array and an unknown table; `settings.json` with unknown keys and 4-space indent; `~/.claude.json` with `projects`, session-like fields and unknown keys; a project with `.claude/settings.json` hooks writing a marker file and an `.mcp.json` whose server writes a marker file; a home reached through a symlink (stow/chezmoi layout).
- Fake CLIs (`tests/fixtures/fake-codex`, `tests/fixtures/fake-claude`, small Python scripts on `PATH`): `claude plugin enable|disable --json` editing the fake settings; a fake `codex app-server` answering `config/batchWrite`, `skills/config/write`, `skills/list`, `plugin/list`, `app/list` over stdio JSON-RPC.
- Unit: every reader/writer row of §2.3; `write_json_atomic` (byte-exact preservation of untouched content, symlink target kept, mode kept, conflict when the bytes change between read and write, no temp file left on error); `~/.claude.json` backup written to `<state>/backups/claude-json/` and never to `~/.claude/backups/`, refusal (nothing written) on a new validation error, no session field in any log line; `settings.json` schema validation before the write; revert detector (F14); version gate, run-start warning (Codex, Claude) and hard fail (`dsh`).
- Security: the remote gate is owner-only and fails closed: Funnel refused, a Tailscale login only through Serve on a tailscaled socket, admin panel refuses `tailscale-*` headers, no `vpn` client exists; an untrusted project hook never runs; an unapproved `.mcp.json` server never starts (F15) and does after approval; a scheduled run loads hooks and MCP like an attended one and accepts the schedule's permission and internet; DeepSeek env has its own `CODEX_HOME`, no `auth.json` or keyring entry, and `env` in a model shell does not show its key.
- Release check: no code reads `<state>/providers/home`; settings validation no longer knows `services.*.integrations` or `personal_setup`.
- Browser (`tests/admin-provider-state.spec.cjs`): switch toggles, `409` reload, external change notice on both screens with ack, "reverted by Claude Code" notice, trust prompt, login URL field and Copy only for allowed domains (#35), auto-finish + Check account (#36).
- Commands: `.venv/bin/python -m pytest -q tests/test_provider_state*.py`; `PYTHON="$PWD/.venv/bin/python" ./scripts/test-ui.sh`.

## Decided (D-039)

Sophia's answers of 2026-10-07 to the product questions of the first draft, recorded in [D-039](decisions/d-039-single-owner-facade-policies.md). Governing principle: start as close as possible to the original CLI, revisit later.

| # | Question | Decision |
| --- | --- | --- |
| Q1 | What may a guest run load? | There are no guests. KeepHarness is single-owner: `local` and the owner's allow-listed Tailscale logins, every project; the remote gate stays owner-only and fails closed (§2.5) |
| Q2 | What may the owner's scheduled runs load? | Everything, like the owner's own `codex exec` / `claude -p` (hooks, MCP, plugins), and whatever permission the schedule chooses, including Automatic/Full and internet; no run classes (§2.1) |
| Q3 | Do run permissions still restrict provider orchestration? | No. Presets map onto each CLI's own sandbox/approval modes only; "Read only" no longer disables connectors (§2.1) |
| Q4 | Which layer does a switch write? | Where the CLI would write: user scope by default, project scope through the CLI's scope flag or the project's config file; no locked rows unless the CLI cannot write that layer (§2.3) |
| Q5 | Where is an external change announced? | Plugins page, Settings › Providers and a toast (§2.4) |
| Q6 | Untrusted projects | Trusted by either CLI means trusted; otherwise a CLI-style prompt that writes trust into the CLI's state; until accepted, Claude runs with hooks disabled and no project MCP (§2.5) |
| Q7 | Migrating the 0.15 logins | No migration of logins or sessions; sign in again; the old homes stay on disk and are ignored (§2.6) |
| Q8 | The old allow list | Dropped; no migration (§2.6) |
| Q9 | DeepSeek orchestration in the interim | Its own home and key only; target is `dsh`, spike in #33 (§2.7) |
| Q10 | Claude MCP switches while Claude Code may be running | Edit `~/.claude.json` directly with safeguards: sha256 conflict check, backup, validation before the write, revert detector (§2.3) |
| Extra 1 | The shared VPN key (client `vpn`) | Removed (§2.5) |
| Extra 2 | Scheduled-run permissions | Whatever the schedule chooses, including Automatic/Full and internet (Q2) |

## Proposed issue split

Order follows dependencies; #34 completion stays last. D-044's cloud-scoped admission guard must land with or before the #45 release; it does not wait for the rest of W14. D-043 and D-044 supersede the original high-level rows 13-14 below with one-PR packages in the W13/W14 issue handoff. Titles carry the acceptance in short form.

| Order | Title | Size | Depends on |
| --- | --- | --- | --- |
| 1 | Provider state seam + atomic JSON writer (M5, P1) + `~/.claude.json` backup and validation before the write | S | — |
| 2 | Codex state reader and writer (app-server) | M | 1 |
| 3 | Claude state reader and writer (`-s` scopes, `~/.claude.json` per M3/M4) | M | 1 |
| 4 | Remove guest and multi-identity code paths and the VPN key; remote gate owner-only, fails closed | L (split below before entering a wave) | — |
| 5 | Trust and project MCP approval mimicking the CLIs (M1, B2) | M | 2, 3 |
| 6 | Runs on the real homes: presets mapped to CLI modes, scheduled = owner, Codex hooks pending review, version warning (hard fail for `dsh`) | M | 1, 4c, 5 |
| 7 | Sign-in on the CLI's own login with #35 (m3) and #36 | S | 6 |
| 8 | #21 rewritten: Plugins switches write CLI state | S | 2, 3 |
| 9 | #22 rewritten: Apps and MCPs chips from real state | S | 2, 3, 8 |
| 10 | #23 rewritten: Skills chip with scope tabs + shared-root notice | S | 2, 3, 8 |
| 11 | External-change notices + `notices:ack` + revert notice | S | 2, 3 |
| 12 | Drop the 0.15 homes and allow list, no migration | S | 6, 7 |
| 13 | #47 DeepSeek seam gaps + key isolation required for 1.0; #33 later `dsh` spike and opt-in gates ([D-043](decisions/d-043-deepseek-engine.md)) | Split into one-PR packages | 1, 2 |
| 14 | #34 Retire Codex/Claude scoped routes; preserve Local isolation ([D-044](decisions/d-044-scoped-sandbox-under-facade.md)) | Two one-PR packages | Admission guard: existing mode contract; completion: guard, 6, 12 |

Item 4 is too large for one wave: `rg` shows about 20 `guest` hits in 10 non-test files, 31 `LOCAL_CLIENT` checks, 13 project-sharing / VPN-key references, per-client projects in `control/runtime_config.py` and 19 test files. Proposed three-way split (tests move with the code they cover):

| Order | Title | Size | Depends on |
| --- | --- | --- | --- |
| 4a | Remote gate owner-only and fail closed; remove the VPN key (client `vpn`) and its project-sharing references | M | — |
| 4b | Collapse per-client identities to one owner: `tailnet-<hash>` clients, per-client projects in `runtime_config.py`, the `LOCAL_CLIENT` checks and the non-`local` guest rule (`conversation_service.py:2628`) | M | 4a |
| 4c | Sweep the remaining guest code paths and tests; add the conventions test that no `guest` identifier or run class remains | S | 4b |

Issue 6 depends on 4c, the last of the three.

## Superseded

- [D-034](decisions/d-034-wp3-customize-product-answers.md) §1 (row switch = KeepHarness allow list) goes away; §2 (Add actions as provider pass-throughs), §3 (label Plugins) and §4 (grouping by marketplace) still hold.
- [UC-001](UC-001-multi-user-execution.md), multi-identity part (ten-user concurrency, per-identity budgets), by D-039. UC-001 is not edited.
- Ledger D04, "owner-only" clamp for remote clients, by D-039.
- Ledger D04/D12, "Read only disables connectors", by D-039 (presets map to the CLI's own modes).
- Ledger D03/D15, scheduled-run clamps (refuse Automatic/Full, internet off by default), by D-039.
- `docs/scheduled-tasks.md` "A scheduled run is isolated from the owner's setup", by D-039 (a scheduled run loads the owner's setup).
- [docs/provider-homes.md](../docs/provider-homes.md): the Homes table for Codex and Claude, "Sign-in ... Both use these homes", "Isolated (`scoped`) runs copy the credential file from these homes", "Sessions ... stay in these homes", and the whole "Personal setup (owner opt-in, off by default)" section. Kept: the DeepSeek row (own Codex home, never the ChatGPT login), the "Child environment" allow-list section (minus the `HOME` override), the unsigned-provider quarantine, and the language rule (D30 rule A).
- Ledger D01 and D02 (0.15 harness homes, one sign-in per provider in the harness home) as recorded in D-038.

## See also

- [D-038](decisions/d-038-keepharness-facade-over-provider-state.md), [D-039](decisions/d-039-single-owner-facade-policies.md), [D-034](decisions/d-034-wp3-customize-product-answers.md), [UC-001](UC-001-multi-user-execution.md), [native resource discovery](native-resource-discovery.md), [DeepSeek CLI research](research/deepseek-cli-2026-10.md) (#33, in progress).
- Codex: [config reference](https://learn.chatgpt.com/docs/config-file/config-reference), [app server](https://learn.chatgpt.com/docs/app-server), [openai/codex PR #7560](https://github.com/openai/codex/pull/7560) (`file_path` optional for config writes).
- Claude Code: [settings](https://code.claude.com/docs/en/settings), [settings schema](https://json.schemastore.org/claude-code-settings.json), [MCP](https://code.claude.com/docs/en/mcp), [skills](https://code.claude.com/docs/en/skills), [plugins reference](https://code.claude.com/docs/en/plugins-reference).
