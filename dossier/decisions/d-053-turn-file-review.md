# D-053 — Review files changed by a completed turn from per-turn captured edits

Status: accepted. Date: 2026-10-09. Decided by: Opus review + JEV, 2026-10-09. Spec: [turn-file-review.md](../turn-file-review.md). Builds on D-031, D-033, D-037, D-038, D-043, D-044; issue #57, gap G20.

## Context

After an editing turn, KeepHarness shows the answer but not a per-turn list of the files that turn changed. The Files panel browses the tree, the approval card shows a diff only while pending, and the run console shows raw tool events. Discovery on `main @ b20d7d0`: Codex `fileChange` and Claude `Edit`/`Write` events reach the run pipeline, but the persisted events keep only a truncated display target (160 characters), no operation and no diff; the fake Codex and Claude fixtures emit no edits. The Codex app-server protocol schema (generated locally, codex-cli 0.157.1) shows that a completed `fileChange` item carries `status` and `changes[{path, kind: add|update|delete, diff}]`. A git diff against the project root cannot separate this turn's edits from pre-existing or concurrent changes.

## Decision

1. Each **completed** provider edit is normalized into a `turn_edit` event of the turn's own job: Codex on `item/completed` with `status == "completed"`, Claude on a non-error `tool_result` using the full input from the `assistant` message. Declined or failed edits are never recorded.
2. The record has `path`, `op` (created, modified, deleted, unknown), `diff` and `diff_state` (diff, partial, binary, oversized, unavailable). Claude `Write` is `unknown` (no stat). Paths are made relative lexically; no disk access at capture.
3. Caps at capture: 64 KiB per diff, 1 MiB of diff text and 200 records per job, with `oversized` and a truncation marker.
4. A read-only `GET /v1/jobs/{job}/file-changes` returns that job's records grouped by path, with `job_state` and `shell_unattributed`; its only error is `404 job_not_found` (also for another owner's job). Retired cloud-scoped jobs (D-044) are served like any job.
5. The UI shows the summary and per-file diff disclosures inline under the completed answer. No panel opens, so Chat/Code (D-037) and the D-033 accordion are untouched. No apply, revert, approval, trust change or "Open file".

## Rationale

- Attribution is exact by construction: one job is one user turn. Follow-ups and D-031 retries are separate child jobs; the in-job session replay belongs to the same turn.
- Capturing on completion is the only point where both providers report a final outcome; capturing at start would list declined edits and, for Claude, read fragmentary input.
- One normalizer covers Codex, Claude and DeepSeek (engine `codex`, D-043).
- No new table, migration, event-writer change or file read; events are deleted with the job.
- Shell commands can edit files without an edit item; the response says so instead of claiming completeness.

## Alternatives considered

- **Reuse `tool_start`/`tool_end` only.** Rejected: truncated scrubbed target, no op, no diff, no completed status.
- **Git diff against a pre-turn snapshot.** Rejected: no baseline, I/O on every turn, attributes pre-existing and concurrent edits.
- **Right-panel Changes view.** Rejected (JEV 0.77): opening the panel flips Chat to Code (D-037) and adds a section type to the D-033 accordion.
- **Stat to tell Claude `Write` create from overwrite.** Rejected (JEV 0.94): racy across approval waits and other writers.
- **410 or hiding for retired cloud history.** Rejected (JEV 1.00): D-044 keeps stored history readable; the endpoint has no dispatch path.
- **Per-diff cap only (about 12.5 MiB worst case per turn).** Rejected (JEV 0.88) for a 1 MiB per-job budget.

## Impact

- New: `agent_service/turn_edits.py`, `GET /v1/jobs/{job}/file-changes`, `turn_edit` event type, inline summary in `agent_service/ui.js`.
- Changed: `adapters/codex/native.py` (completion capture), `adapters/claude/stream.py` (result-time capture), `agent_service/persistence/repositories.py` (two read helpers), `agent_service/routes/conversations.py`, fake Codex and Claude fixtures; the run console ignores `turn_edit`.
- Unchanged: approval policy and cards, Files panel, event writer, `job_events`, DeepSeek engine code.
- Risks accepted: Claude edits show fragments (`partial`); `Write` shows `unknown`; shell edits are not listed, only flagged.

## Follow-up actions

- WP1 fixtures and capture (`task-execute-high-engineer`, Sonnet); WP2 read endpoint (`python-code-engineer`); WP3 inline UI and visible pass per D-029 (`frontend-code-engineer`). Details in the spec, section 3.4.
- Revisit if a Codex release drops `changes` or `status` from completed `fileChange` items: supersede with a new record.
- Revisit if Sophia wants a conversation-wide or workflow-wide changed-files index, an "Open file" action or shell-edit attribution: each needs a new decision.
