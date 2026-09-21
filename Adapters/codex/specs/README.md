# Codex adapter specification

**Responsible agent:** `integrate-codex_tail-harness_engineer` (`.codex/agents/integrate-codex_tail-harness_engineer.toml`).

`adapter_spec_revision: 4`
`harness_baseline: 0.4.4 working-tree`

## Observed baseline

- Consulted: 2026-09-19.
- Installed executable: `codex-cli 0.155.0-alpha.9.2`.
- Harness integration: `codex app-server --listen stdio://`, JSON-RPC/JSONL.
- Official protocol reference: <https://developers.openai.com/pt-BR/docs/app-server>.

The installed version was observed only with `codex --version`; no model turn or paid inference was run. The protocol reference documents app-server behavior but does not certify that every documented feature is present in this alpha CLI.

## Contract implemented by the harness

| Concern | Current behavior | Validation status |
| --- | --- | --- |
| Input | `turn/start` sends text and, in native mode, allowed images. | Contract tests exercise the selected adapter; no live provider turn. |
| Session | Native mode stores `native-thread.json`; scoped mode stores `remote-thread.json`; the next compatible run uses `thread/resume`, otherwise `thread/start`. | Fake app-server test covers resume. |
| Effort | The selected `effort` is sent in `turn/start`; supported values come from authenticated `model/list`. | Config validation only; capability is account and CLI dependent. |
| Streaming | Agent text, reasoning deltas, tool lifecycle, compaction and token usage are translated into harness events. | Fake app-server tests cover representative events. |
| Cancel | The job owner controls cancellation. This adapter has no separately verified `turn/interrupt` call. | Documental limitation; keep cancellation semantics under contract test before changing it. |
| Errors | JSON-RPC errors become `codex_rpc_error`; failed turns become `codex_execution_failed`; output is bounded by `codex_output_limit`. | Unit tests cover failure paths selectively. |
| Authentication | Native mode uses the installed Codex profile. Scoped mode copies its configured credential artifact into the private runtime with restrictive permissions. No credential is documented here. | Implementation review; no account validation claimed. |

The official reference specifies JSON-RPC responses and notifications, failed-turn `error` events, and approval requests. It also documents `experimentalApi` as opt-in; do not make an experimental method part of this contract without a failing behavior test and a versioned compatibility check.

## Model catalog

Codex has no static model alias list in this repository. During provider verification, `control/server.py` calls the authenticated CLI method `model/list` and persists only returned IDs and `supportedReasoningEfforts`. A model is executable only after it appears in that result and is enabled for the project.

See [the dynamic catalog rule](models/catalogo-cli.md), [project identifiers pending verification](models/identificadores-do-projeto.md), and individual records for [gpt-6-astra](models/gpt-6-astra.md), [gpt-5.6-sol](models/gpt-5.6-sol.md), [gpt-5.6-terra](models/gpt-5.6-terra.md), [gpt-5.6-luna](models/gpt-5.6-luna.md), and [gpt-5.5](models/gpt-5.5.md).

## Review triggers

Review this specification when the CLI version changes, `model/list` changes, app-server protocol/schema changes, a new approval or cancel route is implemented, or an adapter contract test changes. Regenerate or compare the version-matched schema before relying on protocol fields beyond the documented stable surface.

## Connector and plugin catalogue

For this observed CLI revision, the administration service reads the installed and
available catalogue with the read-only commands `codex mcp list --json` and
`codex plugin list --available --json`. The latter intentionally includes plugins
that are not installed yet. Its returned `pluginId` is the identifier supplied to
`codex plugin add`; MCP names are supplied to `codex mcp add`.

The harness reads at most 8 MiB, discards stderr, and returns only an identifier,
display name, kind, enabled flag, and `configured`, `installed`, or `available`
status. It never returns the MCP command, environment, authentication state, or
other CLI output. Catalogue contents depend on the currently configured
marketplaces and can change without an adapter release; refresh before presenting
an install choice and review this section when the plugin CLI schema changes.

## Installed plugin runtime inventory (2026-09-20)

When `config.plugin_inventory` is present, native thread creation treats it as the
authoritative list of installed complete plugin IDs (`plugin:name@marketplace`).
It emits every listed plugin into app-server configuration and enables only IDs
also selected in `config.integrations`; installed but unselected plugins remain
disabled. An explicit empty list disables all plugins. Configurations produced
before this field existed continue to fall back to the legacy Codex catalogue.
Isolated runtimes still emit empty MCP and plugin configuration.

Evidence: `thread_parameters` contract tests in
`tests/test_native_plugin_inventory.py` cover the authoritative, empty, and
legacy-fallback cases without an app-server turn or model inference. Documentation
consulted: this adapter specification's catalogue contract and the existing
app-server reference recorded above. Review this section if the CLI plugin ID
format or app-server plugin configuration schema changes.

## Explicit project and global resources (2026-09-20)

The composer resolves native resources for the selected engine on each menu open.
Selections carry canonical identity and a content revision, checked at admission
and again before execution. Directory aliases are deduplicated and metadata reads
are bounded. Project resources precede personal resources. No plugin cache is
interpreted as an enabled resource inventory.

The installed CLI version was rechecked as `0.155.0-alpha.9.2`. Explicit skills
reload `skills/list` with `forceReload: true` in the execution process and require
an enabled matching path before adding a `skill` item to `turn/start`. The text
uses `$name` while the composer uses `/name`. Agent selections request actual
native delegation by registered name; shadowed agents cannot be selected.
Local/DeepSeek isolated executors do not expose host-global resources or agent
profiles as callable. Existing sandbox and model routing are unchanged.
Source: <https://learn.chatgpt.com/docs/app-server> (checked 2026-09-20).
Validation: `tests/test_resources.py`, `tests/test_native.py`; no paid inference.

The installed generated JSON schema confirms `skill` turn input and `forceReload` in `SkillsListParams`; generation performed without a model turn.

## Conversation display title (2026-09-20)

The service passes `_conversation_title` separately from the prompt: the conversation root prompt truncated to 100 characters, overridden by an explicit Harness rename. Provider/model handoffs retain that title; workspace metadata and history wrappers are never used as its source.

The shared Codex executor calls `thread/name/set` on session creation/resume before starting the model turn. This also covers local and DeepSeek inference through Codex; Codex scoped execution uses the same helper. Errors/timeouts emit `session_title_sync_failed` and do not claim successful synchronization. Renames made while no turn is starting are propagated at the next execution, not in real time. Calls without a nonblank title leave existing native titles unchanged.

Contract checked against installed Codex CLI 0.155.0-alpha.9.2 generated `v2/ThreadSetNameParams.json` (`threadId`, `name`) and https://learn.chatgpt.com/docs/app-server . Offline tests validate transport and handoff; no live model inference. This display metadata does not change session identity, isolation or inference model.
