# D-047 — Temporary chat without persisted conversation state

Status: implementation in progress. Date: 2026-10-08. Scope: #60.
Builds on [D-038](d-038-keepharness-facade-over-provider-state.md),
[D-039](d-039-single-owner-facade-policies.md) and
[D-043](d-043-deepseek-engine.md). This is not the retired scoped execution mode.

## Contract

New temporary chat and Ctrl+Shift+N open a conversation with a persistent,
accessible indicator. KeepHarness does not save its conversation, messages,
activity, notices, drafts or content-bearing logs. It never appears in saved
history, search, sidebar or exports. Leaving requires confirmation; saving as a
regular conversation is not supported. Ordinary conversations keep their existing
behavior. Attachments and execution artifacts exist only until discard or shutdown;
startup removes leftovers after a crash.

## Storage and lifecycle

Use a separate conversation service backed exclusively by SQLite `:memory:`.
Reuse the existing SQL-backed queue, gates and message contracts without writing
conversation rows to the persistent database. This is an in-memory store, not
flagged rows in the normal database. A temporary session token routes chat requests
to that service; it is never stored in UI preferences or browser drafts.

Temporary artifacts have a dedicated application-owned root. Close cancels active
work before removing its files; shutdown closes temporary services; startup sweeps
the temporary root before accepting chats. The sweep must not follow symlinks or
remove unrelated session folders. Provider cursor files are not created, so each
turn replays conversation context from volatile state into a fresh native session.
An expiring lease, renewed by browser heartbeats, also cleans abandoned sessions
when a tab disappears without a successful discard request. Expiration never
converts the temporary conversation into a saved one.

## Provider evidence

- **Claude Code 2.1.294:** installed `claude --help`, run with disposable `HOME`,
  `XDG_CONFIG_HOME` and `CLAUDE_CONFIG_DIR`, states that
  `--no-session-persistence` disables disk session persistence and resuming, and
  only works with `--print`. KeepHarness already uses `--print`; temporary runs
  add that flag, omit `--resume`, and do not write `claude-session.json`.
- **Codex:** the existing app-server transport already supports `ephemeral`.
  Temporary runs send `ephemeral: true` to `thread/start`, never resume disk
  markers and never write `native-thread.json`.
- **DeepSeek:** `command -v dsh` found no installed executable. There is no claim
  that dsh has a verified non-persisted mode. The actual adapter uses Codex's
  client-managed Responses transport, as recorded in D-043, so it uses the same
  ephemeral thread policy. No dsh installation or owner-state inspection occurred.
- **Local:** this adapter also uses Codex's app-server, with its existing isolated
  home under the run folder and a local Responses endpoint. It receives the same
  ephemeral policy. No separate Local conversation store is implemented in the
  adapter. Its temporary home is removed with the run artifacts.

These flags govern client session history. They do not promise deletion of records
kept by remote inference services or by user-selected external tools.
Temporary requests support Codex, Claude, DeepSeek and Local. Other providers must
fail explicitly rather than silently run with session persistence enabled.

## Alternatives and JEV

All design consultations used risk `medium`.

1. In-memory state versus flagged persistent rows: JEV returned `invalid_context`
   because the first request used an invalid context shape. No retry. Local fallback
   selected in-memory state because even short-lived persisted rows violate #60.
2. Reuse the SQL-coupled service with SQLite `:memory:` versus new dictionary
   repository overlays: JEV abstained (confidence 0.24; threshold 0.80; 654 input
   tokens, 40 output tokens, 273 ms). Local fallback selected `:memory:` to preserve
   gates, effects and queue semantics without disk storage or duplicated repositories.
3. A renewable lease versus cleanup only on DELETE/shutdown: JEV selected the
   lease (confidence 0.98; threshold 0.80; 662 input tokens, 40 output tokens,
   360 ms). Independent review identified that an offline browser can lose its
   final DELETE request while the server continues running.

The initial JEV abstentions are not approval or evidence of correctness.
Tests and independent artifact review determine acceptance.

## Validation

Provider tests in `tests/test_temporary_providers.py` cover Codex, DeepSeek and
Local thread parameters, Claude spawn flags, refusal to resume saved cursors and
preservation of normal Claude persistence. Initial failures preceded implementation;
59 affected provider tests passed before commit `daf528f`.

Backend, browser, cleanup and complete-suite evidence will be recorded in the
#60 section of [v0.16.0](../releases/v0.16.0.md) after integration.
