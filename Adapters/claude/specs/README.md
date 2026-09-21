# Claude adapter specification

**Responsible agent:** `integrate-claude_tail-harness_engineer` (`.codex/agents/integrate-claude_tail-harness_engineer.toml`).

`adapter_spec_revision: 3`
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
| Effort | The public harness contract exposes only `configured` for Claude. | Configuration and assessment validation; no undocumented CLI effort flag is used. |
| Streaming | `stream-json` events become text, thinking, tool lifecycle, token metrics and separate rate-limit events; a successful `result` is required. Token metrics are not account quota. | Stream parser tests cover representative messages. |
| Cancel | Process cleanup terminates the child and escalates to kill after its timeout. A provider-side cancellation acknowledgement is not separately validated. | Implementation behavior; add a failing cancellation test before changing it. |
| Errors | Invalid JSON, missing result, nonzero process result, provider failure and output limits map to `claude_*` errors. | Unit tests cover representative parser and native failures. |
| Authentication | The CLI uses its existing configured credentials; the scoped adapter copies the configured credential artifact into its private runtime. | No account state or credential value is read by these specifications. |

The installed Claude Code `2.1.258` help and CLI reference document `--name`, `--resume`, `--continue`, `--model`, `--input-format stream-json`, `--output-format stream-json`, and permission controls. `--name` is used only for native, persisted sessions; the scoped command contains `--no-session-persistence`, so it cannot honestly promise a persistent provider-side title. The adapter uses explicit, already-authorized permission policy; do not weaken it based only on a CLI flag becoming available.

## Aliases

The project exposes the official CLI aliases `sonnet`, `opus`, and `haiku` with `configured` effort. See their individual records in [models](models/). They remain subject to account entitlement and provider availability.

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
