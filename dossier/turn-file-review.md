> Status: ACTIVE (accepted 2026-10-09, Opus review + JEV; see D-053)
> Job: When a Codex, Claude or DeepSeek turn finishes after editing project files, I want to see exactly which files that turn changed and inspect what changed, so I can check the agent's work without hunting through the Files panel or trusting a summary.
> Scope: `adapters/codex/native.py`, `adapters/claude/stream.py`, new `agent_service/turn_edits.py`, `agent_service/persistence/repositories.py`, `agent_service/routes/conversations.py`, `agent_service/ui.js`, `harness_ui/assets/themes.css` (tokens only), `tests/fixtures/fake-codex/codex`, `tests/fixtures/fake-claude/claude`, new `tests/test_turn_edits.py` and `tests/harness-turn-review.spec.cjs`.
> Invariant docs: `dossier/decisions/d-020-theme-tokens-for-copied-screens.md`, `d-031-retry-failed-turns.md`, `d-033-right-panel-accordion.md`, `d-037-chat-code-views-of-one-conversation.md`, `d-038-keepharness-facade-over-provider-state.md`, `d-043-deepseek-engine.md`, `d-044-scoped-sandbox-under-facade.md`; decision record `d-053-turn-file-review.md`.
> Decision rationale: capture completed edit items as normalized events of the turn's own job, serve them read-only, show them inline under the answer. No git baseline, no new store, no disk read, no apply or revert.
> Kill criteria: remove this design if the provider CLIs stop reporting completed edit items with paths, or if Sophia rejects a read-only review surface.

# Review files changed by a completed turn (issue #57, gap G20)

## 1. Why (motivation & context)

**Problem.** After an editing turn, the answer says what was done but the user cannot see, in one place, which files the turn touched and what changed. The Files panel browses the tree, the approval card shows a diff only while the approval is pending, and the run console shows raw tool events. G20 in `dossier/research/codex-parity-gaps-2026-10-08.md` (P1, size L) records this gap against the Codex app's edited-files summary.

**Key decisions.**

| Decision | Choice | Why |
| --- | --- | --- |
| Source of truth | Normalized `turn_edit` events written into the turn's own job (`events` table) when the provider reports a **completed** edit | Attribution is exact by construction: one job is one user turn (section 2.6). A git diff cannot tell this turn's edits from pre-existing or concurrent changes. |
| Diff data | Stored with the event as the provider supplied it; otherwise an explicit state | Never invent a diff; reading the disk later would show the current state, not the turn's edit. |
| Access | Read-only endpoint on the job, owner-checked like `job_events`; it reads only stored rows | No new filesystem read at all, so the project-root boundary is unchanged. |
| UI | Summary and per-file diff disclosures inline under the completed answer | Opening the right panel would flip Chat to Code (D-037 mirrors the panel toggle) and needs a new section type in the D-033 accordion. Inline needs neither (JEV 0.77). |

**Relation to other systems.** The run pipeline persists every provider event as a row in `events(job, time, type, data)` (`agent_service/persistence/db.py` L32) through `ConversationService.event` (`agent_service/services/conversation_service.py` L443), which accepts any `kind` and runs `redact_secrets` on the data. `job_events` serves the rows (`agent_service/routes/conversations.py` L364). Deleting a job removes its events (`agent_service/persistence/repositories.py`), so the review disappears with the turn.

## 2. How (architecture & design)

### 2.1 Discovery (what exists today, per provider)

Facts from source reads on `main @ b20d7d0` and from the app-server protocol schema generated locally with `codex app-server generate-json-schema` (codex-cli 0.157.1, no inference run).

| Provider | What exists | Consequence |
| --- | --- | --- |
| Codex (`adapters/codex/native.py` L327, L448-450, L489-500) | `item/started` and `item/completed` with `type == "fileChange"` become `tool_start`/`tool_end`. The started `changes` are cached in `file_changes` only for the approval card. **Protocol (verified in the schema):** `FileChangeThreadItem` = `{id, status, changes}`, all required; `status` is `inProgress`, `completed`, `failed` or `declined`; each `FileUpdateChange` = `{path, kind, diff}`, all required; `kind.type` is `add`, `delete` or `update` (`update` may carry `move_path`). Paths are absolute (fixture `tests/test_native.py` L127: `{"path": "/project/hello.txt", "kind": {"type": "add"}, "diff": "+hello\n"}`). | Capture on `item/completed` only when `status == "completed"`; a declined or failed patch changed nothing and is never listed. The `turn/diff/updated` aggregate notification exists but is not used (it cannot give per-file op or state). |
| Claude (`adapters/claude/stream.py` L128-175, L184-194) | `content_block_start` emits `tool_start`; its `input` is empty or partial in a real stream (input arrives as `input_json_delta`). The full input reaches `add_targets` from the `assistant` message. `tool_result` emits `tool_end` with `status` from `is_error`. | Stash the full input in `add_targets`; emit `turn_edit` at the `tool_result` only when `is_error` is false. `Edit`/`MultiEdit` give `old_string`/`new_string` fragments; `Write` gives the new content only. |
| DeepSeek | Runs through `adapters/codex/native.py` with `"engine": "codex"` (`adapters/deepseek/backend.py` L5, L65). | Inherits the Codex capture; covered by the DeepSeek fixture. |
| Shell edits | Codex `commandExecution` and Claude `Bash` can change files without an edit item. | Not attributable from the stream. The response says so (`shell_unattributed`), it never claims the list is complete. |
| Files panel | `browse_project` (`agent_service/workspaces.py` L164) lists directories only; there is no project-file content route. | Not used by the review. No "Open file" action in v1. |
| Test fixtures | `tests/fixtures/fake-codex/codex` and `tests/fixtures/fake-claude/claude` emit no edits. | Fixtures must learn to emit edits first (WP1). |

### 2.2 Options considered (capture source)

| Option | Buys | Costs |
| --- | --- | --- |
| **A. Normalized `turn_edit` events in the job's events table (chosen)** | Exact attribution; one normalizer for Codex, Claude and DeepSeek; deleted with the job; no migration. | Adapters emit one more event type; Claude edits are fragments. |
| B. Reuse `tool_start`/`tool_end` only | No new event type. | Target truncated to 160 chars and scrubbed, no op, no diff, no completed status. |
| C. Git diff against a pre-turn snapshot | Full diffs for any provider. | No baseline exists; I/O on every turn; attributes pre-existing and concurrent edits. |

JEV on the three options: **A, confidence 0.88.**

### 2.3 Data flow

```
Codex item/completed fileChange (status completed)  |  Claude tool_result (not is_error) for Edit|MultiEdit|Write|NotebookEdit
  -> adapter calls turn_edits.normalize(...) once per changed file
  -> event(job, "turn_edit", record)          # existing writer, redact_secrets applies
  -> GET /v1/jobs/{job}/file-changes          # owner check; reads only this job's rows
  -> UI: summary + per-file diff disclosures under the answer
```

### 2.4 Normalized edit record (stored in `events.data`)

```
{
  "path":        "<project-relative POSIX path>" | null,
  "moved_from":  "<project-relative POSIX path>",          # only for a Codex update with move_path
  "op":          "created" | "modified" | "deleted" | "unknown",
  "source":      "codex" | "claude",                       # engine; DeepSeek reports "codex" plus the job's backend
  "tool":        "fileChange" | "Edit" | "MultiEdit" | "Write" | "NotebookEdit",
  "diff":        "<text>" | null,
  "diff_state":  "diff" | "partial" | "binary" | "oversized" | "unavailable",
  "tool_id":     "<provider item or tool id>"
}
```

Op mapping:

| Provider item | `op` | `diff_state` |
| --- | --- | --- |
| Codex `kind.type == "add"` | created | diff (text as supplied: the new content) |
| Codex `kind.type == "update"` | modified (`path` = `move_path` when set, `moved_from` = old path) | diff (unified diff as supplied) |
| Codex `kind.type == "delete"` | deleted | diff as supplied, or unavailable when empty |
| Codex unknown `kind` | unknown | unavailable |
| Claude `Edit` / `MultiEdit` | modified | partial (each `old_string` / `new_string` pair rendered as removed / added lines) |
| Claude `Write` | unknown (never a stat; JEV 0.94) | partial (new content only, previous content not known) |
| Claude `NotebookEdit` | modified | unavailable |

Rules:

- `binary`: the text contains a NUL byte or is not valid UTF-8; body dropped.
- Caps, applied at capture by the adapter: 64 KiB per diff; 1 MiB of diff text per job; 200 records per job (JEV 0.88). Over the per-diff cap or the job budget the record keeps `diff_state: "oversized"` and no body. After 200 records the adapter writes one `{"truncated": true}` `turn_edit` marker and stops capturing for that job. The budget lives for one provider run: the rare in-job replay after `native_session_missing` starts a new run with a new budget, so such a job can hold up to two runs' worth of records and two truncation markers (review of 2026-10-09: accepted, since the replay fails before any edit in practice).
- Paths: absolute paths are made relative to the job's project root lexically; a path outside the root, with a `..` segment, or not a string becomes `path: null`, `diff_state: "unavailable"`. No disk access at capture (no `stat`, no `resolve`). The trusted project root (only) is resolved once with realpath; reported paths are still compared lexically against the stored root and its resolved form, never resolved (#80, D-053 amendment 2026-10-10).
- Diff text passes through `redact_secrets` like every event; a redacted diff is shown as stored.
- Repeated records for one path are kept in order.

### 2.5 Data contract (read endpoint)

`GET /v1/jobs/{job}/file-changes`, response 200 (`Cache-Control: no-store`):

```
{
  "job": "<job id>",
  "job_state": "completed" | "failed" | "cancelled" | "interrupted" | "running" | "queued",
  "state": "none" | "captured",
  "truncated": false,
  "shell_unattributed": false,       # the job ran a shell command (Codex commandExecution, Claude Bash)
  "files": [
    {"path": "src/a.py", "op": "modified",
     "edits": [{"op": "modified", "diff_state": "diff", "diff": "--- a/src/a.py\n...", "tool": "fileChange", "source": "codex"}]}
    # an edit from a Codex move also carries "moved_from": "<old project-relative path>"
  ]
}
```

- `files` is grouped by `path` in order of first appearance; `edits` keeps the stored order. A file's `op` is `deleted` if its last edit deleted it; `modified` if its first edit deleted it and a later edit did not (deleted and recreated in the same turn, so the file exists at the end); otherwise the op of its first edit.
- `diff` is non-null only for `diff` and `partial`.
- Errors: `404 job_not_found` only, also for another owner's job (copied from `ConversationService.job`, SEC-R1-8). No 409: a running job returns what is stored so far with its `job_state`. No 410: retired cloud-scoped jobs (D-044) are served like any job (JEV 1.00); the endpoint has no dispatch path.
- GET only; no parameter changes state.

### 2.6 Attribution rules

1. One job is one user turn (verified): each message is submitted as its own job through `submit_async`; queued follow-ups are separate child jobs (`parent_job_id`); a D-031 retry is a new child job with `retry_of`, so the failed source keeps its own edits and the retry has its own; the in-job replay after `native_session_missing` (`conversation_service.py` L2110-2117) stays in the same job, whose edits it owns. Workflow steps are separate jobs: v1 shows a summary per step job, with no workflow-wide aggregate.
2. Only `turn_edit` rows of the requested job are read.
3. A job with no rows returns `state: "none"`; the UI shows nothing (no stale list, no "no changes" claim).
4. Cancelled, failed or interrupted jobs keep what was captured and the UI labels the list "stopped before finishing".
5. Nothing is filled from disk.

### 2.7 Project-root boundary

The review reads no file. Capture is lexical only (section 2.4). The trusted project root (only) is resolved once with realpath; reported paths are still compared lexically against the stored root and its resolved form, never resolved (#80, D-053 amendment 2026-10-10). A path that reaches outside through a symlink inside the project is displayed as the provider reported it and is never opened. There is no "Open file" action in v1, so no new read path exists to bound.

### 2.8 Failure modes

- Reading the diff from disk at review time: shows later edits, breaks attribution. Store at capture.
- Capturing on `item/started` or on Claude `content_block_start`: lists declined or failed edits, and the Claude input is still fragmentary. Capture on completion only.
- Parsing `tool_start.target`: truncated at 160 chars and scrubbed.
- Adding a git baseline or snapshot per turn: I/O on every turn, still wrong for concurrent edits.
- A stat to tell Claude `Write` create from overwrite: racy (approval waits, other writers); keep `unknown`.
- Wording "applied" or "all changes": the list covers edit tools only; show "changed by this turn" and the shell note when `shell_unattributed` is true.
- Rendering `turn_edit` rows in the run console as tool steps: the console must ignore the type.

### 2.9 UI entry point

- **Summary** under the completed answer in `agent_service/ui.js`, fetched once when the job reaches a terminal state and on reload: count, op label (text, not colour alone), path (long paths wrap or ellipsize with the full path in the accessible name); max 10 visible with "and N more". Hidden when `state: "none"`. Shows "stopped before finishing" for non-completed jobs and "Shell commands may have changed other files" when `shell_unattributed`.
- **Review**: each file is a native disclosure button that expands its diff inline, labelled with its `diff_state` ("partial: fragments only", "binary", "too large to show", "not available"). No panel opens; the side panel, conversation, draft, project, attachments, provider, model, access mode and selection do not change.
- **Keyboard**: Tab reaches each disclosure; Enter or Space toggles it.
- **Theme**: existing `--th-*` tokens only (D-020); text contrast at least 4.5:1.

### 2.10 Ponytail (what is deliberately not introduced)

- No new table or migration (a new `events.type` value); no change to the event writer.
- No git, snapshot or disk read; no "Open file"; no new panel section.
- No apply, revert or per-file approval.
- No polling; no conversation-wide index; no workflow aggregate.

## 3. What (implementation reference)

### 3.1 Files to touch

| Path | Change |
| --- | --- |
| `agent_service/turn_edits.py` (new) | `normalize_codex(change, root)`, `normalize_claude(tool, args, root)`, path relativization, op and `diff_state` rules, per-diff cap; a small per-job `Budget` (bytes, count) used by both adapters. |
| `adapters/codex/native.py` L489-500 | On `item/completed` with `type == "fileChange"` and `status == "completed"`, emit one `turn_edit` per change. Approval card code unchanged. |
| `adapters/claude/stream.py` L128-194 | Keep the full input from `add_targets` for edit tools; on a non-error `tool_result`, emit `turn_edit`. |
| `agent_service/persistence/repositories.py` | `turn_edits(job)`: `SELECT data FROM events WHERE job=? AND type='turn_edit' ORDER BY id`; `ran_shell(job)`: one `EXISTS` over `tool_start` rows whose tool is `commandExecution` or `Bash`. |
| `agent_service/routes/conversations.py` | `file_changes` handler and `api_route("/v1/jobs/{job}/file-changes", file_changes)`; owner check via `service.job(identity, job)`. |
| `agent_service/ui.js` | Inline summary and disclosures; run console ignores `turn_edit`. |
| `harness_ui/assets/themes.css` | Only if a token is missing; tokens, never literals. |
| `tests/fixtures/fake-codex/codex` | `fileChange` items (add, update, update with `move_path`, delete, declined, binary, oversized, path outside root) selected by an env var. |
| `tests/fixtures/fake-claude/claude` | `content_block_start` with empty input, then `assistant` with full input, then `tool_result` (success and `is_error`) for `Edit`, `MultiEdit`, `Write`; one `Bash`. |
| `tests/test_turn_edits.py` (new), `tests/harness-turn-review.spec.cjs` (new) | Section 3.3. |

### 3.2 Contracts (function-level)

**`turn_edits.normalize_codex(change: dict, root: Path) -> dict`** and **`turn_edits.normalize_claude(tool: str, args: dict, root: Path) -> list[dict]`**
- Guarantees: records follow section 2.4; `path` is relative POSIX or null; `diff` never over 64 KiB.
- Must-not: touch the disk; return an absolute path; set `created` for Claude `Write`.
- Errors: never raise on malformed provider data; malformed input yields `op: "unknown"`, `diff_state: "unavailable"`. `normalize_claude` returns `[]` for non-edit tools.

**`repositories.turn_edits(job: str) -> list[dict]`**: only the given job, in insertion order, read-only.

**`GET /v1/jobs/{job}/file-changes`** (section 2.5): owner-scoped; `job`, `job_state`, `state`, `truncated`, `shell_unattributed`, `files` always present; no filesystem read; only error `job_not_found` (404).

### 3.3 Test plan

Python (`tests/test_turn_edits.py`, fake homes, `tmp_path` project, no paid inference):
1. Attribution: two consecutive jobs in one conversation; each endpoint returns only its own records; a pre-existing dirty file is not listed.
2. No-edit turn after an editing turn: `state: "none"`.
3. Codex create, modify, move and delete map as in section 2.4; a `declined` and a `failed` fileChange are not recorded; DeepSeek (engine codex) produces the same records.
4. Claude: `Edit`/`MultiEdit` modified and partial; `Write` unknown and partial; an `is_error` result records nothing; empty `content_block_start` input does not produce a record.
5. Diff states: diff, partial, binary, oversized (per diff and job budget), unavailable; 201st record yields the truncation marker.
6. Boundary: absolute path outside root, `../x` and a symlink-escape path give no readable content and no disk read (assert the outside file's content never appears in the response).
7. Cancellation and failure: captured records kept, `job_state` reported.
8. Reload: records identical after restarting the harness (SQLite).
9. Retired cloud-scoped job (D-044): 200 with stored records; fake CLI not invoked.
10. Another owner's job: 404 `job_not_found`.
11. D-031 retry: failed source and retry child each show only their own edits.

Browser (`tests/harness-turn-review.spec.cjs`, fake CLIs, isolated state): summary under the answer for Codex, Claude and DeepSeek fixtures; no-edit turn shows nothing; keyboard toggles a disclosure; conversation id, draft, attachments, provider, model, access mode and side panel unchanged before and after; 200-character path; light and dark palette with contrast check; no hex or `rgb(` literal in the UI diff (D-020); run console shows no `turn_edit` step.

Visible pass (D-029) before merge: one editing turn per provider on the desktop app, keyboard only, long path, no-edit turn, one light and one dark palette.

### 3.4 Work packages

**WP1: fixtures and capture** (`task-execute-high-engineer`, Sonnet). No dependency.
- Files: both fake CLIs, `agent_service/turn_edits.py`, `adapters/codex/native.py`, `adapters/claude/stream.py`, `tests/test_turn_edits.py` (tests 3-6 at capture level).
- Acceptance: tests green; `tests/test_native.py` and the Claude stream tests unchanged and green; approval cards unchanged.

**WP2: read endpoint** (`python-code-engineer`, Haiku; Sonnet on failure). Depends on WP1's record shape.
- Files: `agent_service/persistence/repositories.py`, `agent_service/routes/conversations.py`, `tests/test_turn_edits.py` (tests 1, 2, 7-11).
- Acceptance: all of `tests/test_turn_edits.py` green; full Python suite before merge; GET only.

**WP3: inline UI** (`frontend-code-engineer`). Depends on WP2.
- Files: `agent_service/ui.js`, `harness_ui/assets/themes.css` (only if needed), `tests/harness-turn-review.spec.cjs`.
- Acceptance: browser spec green under `scripts/test-ui.sh`; visible pass recorded per D-029.

## 4. Resolved questions

1. **Codex payload.** Resolved from the generated protocol schema: `item/completed` `fileChange` carries required `changes[{path, kind, diff}]` and `status`. WP1 still asserts the shape on the fixture; no real Codex run is needed.
2. **Job equals turn.** Yes, including D-031 retries (new child job), follow-ups (child jobs) and the in-job session replay (section 2.6). Workflows are per step job.
3. **Claude `Write`.** `unknown`, no stat (JEV 0.94).
4. **Limits.** 64 KiB per diff, 1 MiB per job, 200 records per job, all at capture (JEV 0.88).
5. **Retired cloud history.** Served read-only with 200 like any job; no 410, not hidden (JEV 1.00).

## See also

- `dossier/research/codex-parity-gaps-2026-10-08.md` (G20)
- `dossier/codex-parity-design.md`
- `dossier/decisions/d-053-turn-file-review.md`
- Decisions: D-020, D-029, D-031, D-033, D-037, D-038, D-043, D-044
