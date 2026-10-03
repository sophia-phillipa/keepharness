# Gemini CLI adapter specification

**Status: partially implemented, hidden from the administration panel.** OAuth access for individual accounts is unavailable; native settings enforcement remains unresolved. Antigravity migration is deferred pending verification of credit-overage controls.

**Responsible agent:** `integrate-gemini_keepharness_engineer` (`.codex/agents/integrate-gemini_keepharness_engineer.toml`).

`adapter_spec_revision: 2`
`harness_baseline: 0.5.0`

## Documented baseline

- Consulted: 2026-09-20; installed executable: Gemini CLI `0.60.0`.
- ACP (`gemini --acp`) is JSON-RPC over stdio and documents `initialize`, `authenticate`, `newSession`, `loadSession`, `prompt`, and `cancel`.
- ACP exposes image prompt content, session updates, and `session/request_permission` callbacks.
- Sessions are project-specific and are loaded by ID with `session/load`.

Sources: <https://geminicli.com/docs/cli/acp-mode>, <https://geminicli.com/docs/cli/cli-reference>, and <https://geminicli.com/docs/cli/session-management>.

## Policy-engine evidence

On 2026-09-20, Gemini CLI 0.60.0 passed a real ACP initialization without a prompt or model invocation. In a separate local probe, its installed PolicyEngine loaded the generated administrator policy and reported `read_file` as `ask_user` at priority 5.95 and `write_file`, `run_shell_command`, and an unknown tool as `deny` at priority 5.9. This validates policy loading and precedence only; it does not validate an account, OAuth refresh, a model request, or tool execution.

## Contract implemented by the harness

| Concern | Current behavior | Validation status |
| --- | --- | --- |
| Input | Uses ACP `session/prompt` from the prepared authorized project directory, with text and permitted base64 images. | Fake ACP tests. |
| Session | Persists the ACP session ID in a mode-0600 marker and later calls `session/load`. Sessions remain project-specific because the prepared workspace determines `cwd`. | Fake ACP tests; no live resume claim. |
| Effort | Only `configured` is accepted. Gemini CLI documentation consulted here does not establish a harness-level effort parameter. | Unit test. |
| Streaming | Maps bounded ACP `session/update` message, thought, tool, and usage updates; turn results expose aggregate token metrics when present. | Parser and fake ACP tests. |
| Permissions | The per-turn policy denies unknown tools, removes API-key routing, forces personal OAuth, disables extensions, skills, agents and automatic memory, and exposes only granted native tools plus selected MCP servers. ACP `session/request_permission` is then mapped to one turn-scoped `allow_once` or rejection. | ACP and policy tests. |
| Cancel | The process starts a new session; cleanup sends SIGTERM to its process group and escalates to SIGKILL after three seconds. | Implementation contract; no live execution. |
| Scoped execution | Explicitly unavailable until an isolated Gemini CLI policy/tool contract is implemented. | Unit test. |

## Review triggers

Review whenever Gemini CLI changes its ACP protocol, policy engine, session semantics, attachment transport, or approval protocol, and before enabling scoped mode.

Account-check regression (2026-09-20): missing OAuth selection/credentials returns an unauthenticated result before spawning the CLI. Startup/protocol/timeout failures also return an unauthenticated result with no model catalog. Eighteen targeted account and panel integration tests passed, including the previously unhandled missing-login path.

Login/startup correction (2026-09-20): the installed CLI rejects an empty MCP name. A unique nonmatching name disables configured MCP selection. Real ACP initialize passed with no session or model request. Account-only probes no longer point at a user-owned system settings file: Gemini ignores such files because they require root ownership. Login/check use a temporary working directory and a deny-all tool policy, and never create a model session. This finding invalidates the earlier claim that per-run system-settings overrides were enforced by the CLI; native session settings enforcement still needs a supported replacement. The login helper reports progress and concise errors instead of a Python traceback.

Provider retirement confirmed (2026-09-20): a real initialize succeeded, but authenticate returned RPC -32000 stating this client is no longer supported for Gemini Code Assist for individuals and directing migration to Antigravity. Official notice: https://github.com/google-gemini/gemini-cli/discussions/28017 . Treat gemini_client_retired as non-retryable, show migration instructions and do not recommend terminal Gemini login as a remedy. No model inference was performed. Antigravity requires its own validated integration; do not infer protocol compatibility.

## Release integration 0.5.0

The ACP client identity now reports harness 0.5.0. The adapter contract and
`adapter_spec_revision` remain unchanged. This packaging change does not upgrade
the observed Gemini CLI or certify live authentication, inference or policy
enforcement. The provider remains hidden in administration.

## Conversation display title (2026-09-20)

The service passes `_conversation_title` separately from the prompt: the conversation root prompt truncated to 100 characters, overridden by an explicit Harness rename. Provider/model handoffs retain that title; workspace metadata and history wrappers are never used as its source.

Installed Gemini CLI 0.60.0 documents ACP new/load/prompt and mode/model control, but implements no session-title setter. The adapter emits `session_title_sync_unsupported` (`gemini_acp_title_unsupported`); it does not fabricate a rename method, mutate CLI-owned session files, or claim external title synchronization. Sources inspected: installed `bundle/docs/cli/acp-mode.md` and ACP agent implementation in `bundle/gemini-ZTU7EMI3.js`. Scoped execution remains unsupported.
