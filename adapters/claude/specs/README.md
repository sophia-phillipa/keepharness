# Claude adapter specification

**Responsible agent:** `integrate-claude_keepharness_engineer` (`.codex/agents/integrate-claude_keepharness_engineer.toml`).

`adapter_spec_revision: 5`
`harness_baseline: 0.4.4 working-tree`

## Observed baseline

- Consulted: 2026-09-20.
- Installed executable: Claude Code `2.1.258`; `--help` was read to verify `--name`.
- Harness integration: print mode with JSONL input and `--output-format stream-json`.
- Official CLI reference: <https://code.claude.com/docs/en/cli-usage>.

The version and naming option were observed with `claude --version` and `claude --help`; no prompt, model invocation, or account operation was performed. The current official documentation is a reference for the CLI surface, not proof that an account can use every option or alias.

## Contract implemented by the harness

| Concern | Current behavior | Validation status |
| --- | --- | --- |
| Input | Native mode sends a JSONL `user` message with text and permitted base64 images. Scoped mode passes the bounded prompt through the isolated command. | Fake CLI tests cover the native message and tool approval path. |
| Session | Native mode records `claude-session.json`, passes `--resume <id>` on continuation, and passes the canonical Harness title unchanged through the official `--name` option when it is nonblank. Scoped mode reports `replayed_history` and deliberately disables Claude session persistence. | Fake CLI tests cover title transport and session ID persistence; no live resume or provider UI claim. |
| Effort | The CLI initialize catalog supplies supported levels per model; `configured` preserves provider defaults. Explicit choices are passed via `--effort` in native and scoped mode. | Catalog, admission and fake CLI transport tests. |
| Streaming | `stream-json` events become text, thinking, tool lifecycle, token metrics and separate rate-limit events; a successful `result` is required. Token metrics are not account quota. | Stream parser tests cover representative messages. |
| Cancel | Process cleanup terminates the child and escalates to kill after its timeout. A provider-side cancellation acknowledgement is not separately validated. | Implementation behavior; add a failing cancellation test before changing it. |
| Errors | Invalid JSON, missing result, nonzero process result, provider failure and output limits map to `claude_*` errors. | Unit tests cover representative parser and native failures. |
| Authentication | The CLI uses its existing configured credentials; the scoped adapter copies the configured credential artifact into its private runtime. | No account state or credential value is read by these specifications. |

The installed Claude Code `2.1.258` help and CLI reference document `--name`, `--resume`, `--continue`, `--model`, `--input-format stream-json`, `--output-format stream-json`, and permission controls. `--name` is used only for native, persisted sessions; the scoped command contains `--no-session-persistence`, so it cannot honestly promise a persistent provider-side title. The adapter uses explicit, already-authorized permission policy; do not weaken it based only on a CLI flag becoming available.

## Aliases

The project reads the authenticated CLI initialize catalog, including aliases, resolved version IDs, extended-context variants and per-model effort levels. Disabled rows are excluded. The CLI remains authoritative for account availability; the catalog is not an inference entitlement test. See [catalog contract](models/cli-catalog.md).

## Review triggers

Review when Claude Code changes version, the official CLI reference changes naming/session/stream-json/permission semantics, alias availability changes, the adapter starts supporting a non-`configured` effort, or a cancel/error contract changes.

## Connector and plugin catalogue

The administration service uses the read-only `claude plugin list --available
--json` command to discover plugins that are already installed and those offered
by configured marketplaces. If `pluginId` is absent, the stable displayed
identifier is formed as `name@marketplaceName` and must be checked by the CLI
operation before installation.

In Claude Code `2.1.258`, `claude mcp list` is textual rather than a documented
JSON catalogue. The harness extracts only conservative `name:` entries; if that
shape is unavailable it falls back to the locally configured MCP metadata and
reports the limitation. The explicit `No MCP servers configured` response is a successful empty list, not a query error. It never invents a remote MCP catalogue. Output is
bounded to 8 MiB and omits commands, environment variables, credentials,
authentication details, and stderr. Recheck these commands and output shapes on
every Claude Code upgrade.

## Explicit project and global resources (2026-09-20)

The composer resolves native resources for the selected engine on each menu open.
Selections carry canonical identity and a content revision, checked at admission
and again before execution. Directory aliases are deduplicated and metadata reads
are bounded. Project resources precede personal resources. No plugin cache is
interpreted as an enabled resource inventory.

Claude Code version was rechecked as `2.1.258`. An explicit skill selection adds
`Skill` to the existing native tool surface only when read permission is enabled.
The selected skill name/path is included in the instruction. Agent delegation
remains unavailable in this adapter and appears disabled in the selector. Native
permissions and hook settings remain enforced. Plain command templates support
positional arguments; templates requiring shell/file interpolation or special
execution frontmatter are unavailable instead of silently flattened.
Sources: <https://code.claude.com/docs/en/skills> and
<https://code.claude.com/docs/en/sub-agents> (checked 2026-09-20).
Validation: `tests/test_resources.py`, `tests/test_native.py`; no paid inference.

## Account renewal and recoverable conditions — 2026-09-21

Verified locally with Claude Code 2.1.236 (`--version`, `auth status`; no live
inference). An observed assistant event reported `authentication_failed` and an
expired OAuth session. The adapter retains known structured error codes without
copying arbitrary provider messages into application errors. Authentication and
quota conditions end the attempt as `interrupted`, with `condition` metadata,
rather than `failed`; the UI explains the next action in both live and saved turns.
Unknown failures still retain the generic failure contract.

Error kinds, 0.15.0: every kind Claude Code reports maps to a harness code. The account
variants (`oauth_org_not_allowed`, `account_on_hold`, `verification_required`,
`cloud_credential_error`) end as `claude_authentication_failed`; `billing_error` as
`provider_quota_exhausted`; `overloaded` as `provider_rate_limit`; `server_error` as
`provider_unavailable` (all `interrupted` with a `condition`); `model_not_found` (a retired
or unentitled model id) as the failed run `model_or_effort_unavailable`. For these known kinds
only, the failed result's text travels as `error_detail`: one line, redacted, at most 300
characters. Any other kind stays `claude_execution_failed` and its text is never shown.

The administration dashboard and provider editor expose account login/renewal.
Login, account checks and every Claude process use the harness-owned
`CLAUDE_CONFIG_DIR` under the control state directory
([docs/provider-homes.md](../../../docs/provider-homes.md)); the login KeepHarness
signs in with lives there, never the terminal's `~/.claude` or an inherited
`CLAUDE_CODE_OAUTH_TOKEN`. A successful browser login still records the non-secret
`claude-cli-login` marker. Credentials remain managed by the CLI. Native Claude
account renewal does not block starting the rest of the Harness.

Validation: `tests/test_claude_errors.py`, `tests/test_provider_login.py`,
`tests/account-renewal.spec.cjs`, and related native/session/runtime tests.
Browser checks use simulated APIs and accounts; an actual successful OAuth
renewal still requires the account owner's browser authorization.

## Catalog and quota — 2026-09-21

Claude Code 2.1.236 was checked on this host with non-inference control requests. `initialize` supplies models and `get_usage` supplies account quota. The latter reports percentages, unlike stream utilization fractions. Quota is fetched before a conversation exists, cached for 30 seconds, and falls back to recent owner-scoped stream observations if unavailable. Metadata requests use an empty temporary working directory, no tools, disabled hooks, and the same selected login source as execution. No user prompt is sent.

## Live throughput (2026-09-21)

The shared native/scoped stream parser now emits live usage metrics from message_start and message_delta counts, retaining the latest cumulative output count per message. Source: [official streaming contract](https://platform.claude.com/docs/en/build-with-claude/streaming). Claude Code 2.1.258 was checked locally. Offline fixtures cover multiple messages, duplicate counts and invalid metrics; no live model inference was run.

Context meter (0.15.0): `usage.input_tokens` leaves out cache reads and writes, so a one-line turn
reported "2 input tokens" for a prompt of about 57k. Each main-thread `message_start` now emits
`context_usage` with `last.totalTokens = input + cache_read + cache_creation` of that call (never
a sum over calls; subagent calls are ignored), and the final result keeps the last one. The
model's context window is not known to the adapter, so the meter shows tokens without a
percentage. The turn's `metrics.input_tokens` is now the whole prompt across the turn's calls,
cache included (as Codex counts it), with `cached_tokens` and `cache_creation_tokens` kept
separately and `usage_scope: "turn"`.

The displayed rate is output tokens divided by elapsed execution time, including tool waits; it is not decoder-only speed. Missing provider counts remain unavailable and are never estimated from text length. Final results remain authoritative when reopening a conversation.

### Invocation and question support

Native execution exposes `AskUserQuestion` and returns enrolled-human option
answers in the stdio `updatedInput.answers` envelope. A question batch becomes
sequential option gates; expiration denies the tool, and restart invalidates
pending gates. Full access never supplies a human answer automatically.

`Task` is available only with the selected project's delegation grant. The CLI
may emit the alias `Agent`; child events retain `parent_tool_use_id`. `TodoWrite`
remains unsupported by the recorded CLI probe. Selected delegated resources are
passed as execution-local `--agents` definitions containing description, prompt
and model, while tool grants remain controlled by the Harness. This catalog
materialization route is covered by synthetic adapter tests, not a paid live
certification of `--agents` execution.

Hooks use `--setting-sources project` by default. The owner's personal-setup
opt-in (`personal_setup: true`, which replaced `global_hooks` in 0.15.0) also
requires the hooks grant and changes the emitted scope to `global_and_project`:
it adds the `user` setting source and passes the owner's own `hooks` from
`~/.claude/settings.json` through `--settings`. Guests and scheduled runs never
get it. An unrelated global hook is not enabled by a project hook grant alone.

A leading native command is preserved only when the Harness can retain its
native position and context. Other invocations use the inline fallback.
Trusted path rules are copied into the private execution directory and their
original frontmatter and body are included as advisory scoped instructions;
the Harness does not claim this reproduces the CLI's native path matching. The
catalog and project symlinks are never replaced. Temporary resource files are
removed when the execution finishes.
