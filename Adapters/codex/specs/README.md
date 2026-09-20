# Codex adapter specification

**Responsible agent:** `provedor_codex` (`.codex/agents/provedor_codex.toml`).

`adapter_spec_revision: 1`
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
