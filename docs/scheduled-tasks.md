# Scheduled tasks

A scheduled task is a prompt that KeepHarness sends by itself, again and again, on a cadence:
every day at 09:00, every Monday morning, every six hours. Each run is a new conversation
that appears in the conversation list like any other, marked as started by the schedule.
This is the backend of the Scheduled area; the UI is separate.

Schedules are private. Each one belongs to the client that created it (the identity that owns
jobs and conversations) and runs as that client, in the project it names, with the access that
client has. Another client never sees it.

## Run unattended, run safely

Nobody watches a scheduled run, so it never gets automatic or full access:

- `access_mode` is `ask` (the default) or `read_only`. `auto` and `full` are refused with
  `schedule_invalid` (`field: "access_mode"`), also when a file is edited by hand: such a file
  is skipped and never runs.
- A scheduled run never waits for a person (D15). An action that needs an approval (a tool
  approval or a workflow gate) is denied at once with the reason `unattended`, the run goes on,
  and an `approval_denied` event with `{"scope": "unattended", "kind": ...}` is recorded. When the
  run ends, its `last_run` gets `"needs_you": true` and an attention item is raised (see "Attention
  items"). A run started with "Run now" follows the same rule.
- Three runs in a row that fail, are cancelled or are interrupted pause the schedule (see
  "Failures").
- A scheduled run is isolated from the owner's setup (no personal MCP servers, plugins, hooks or
  instructions; see `docs/provider-homes.md`) and has **no internet unless the task opts in**
  (`allow_internet`, default `false`, D03; see "Internet").

## Internet

`allow_internet` is a per-task boolean, off by default; the Scheduled editor shows it as
**Allow internet**. When it is off, the run's effective `internet` permission is forced to
`false` whatever the provider's grant (`ConversationService._project_config`), which each
adapter enforces as follows:

| Provider | Without internet |
| --- | --- |
| Codex, DeepSeek | `web_search="disabled"` and the turn's sandbox has `networkAccess:false`; a command that needs the network escalates and is denied (`unattended`) |
| Claude Code | `WebFetch` and `WebSearch` are not in `--tools`; `Bash` asks in Ask mode, so it is denied (`unattended`), and Read only has no `Bash` |
| Gemini | `google_web_search` and `web_fetch` are denied by the run policy; a run with connectors selected fails with `gemini_integration_denied` |
| Local models | no web research tool and no network in the sandbox |

With `allow_internet: true` the run gets the provider's own internet grant, never more.

Limits that this option cannot enforce: the provider is always reached over the internet (the
prompt, the project text the run reads and the answer go to the provider's API); a connector
that the harness itself runs (an effect such as a Jira publication) needs an approval, which an
unattended run denies; Claude Code has no network sandbox of its own, so its limit is the tool
list plus the denied approvals, not an OS boundary.

## Data

One JSON file per schedule:

```
<state_dir>/schedules/<sha256(owner)[:16]>/<id>.json
```

```json
{
  "id": "0b8f3f4d1c6a4e7b9d2a5c1e7f3b6a90",
  "owner": "local",
  "title": "Morning digest",
  "prompt": "Summarize yesterday's changes.",
  "project_id": "keepharness",
  "backend": "codex",
  "model": "gpt-6-astra",
  "effort": "low",
  "access_mode": "ask",
  "allow_internet": false,
  "cadence": {"kind": "daily", "time": "09:00"},
  "enabled": true,
  "agent": null,
  "page_ids": [],
  "created_at": "2026-10-03T12:00:00.000Z",
  "updated_at": "2026-10-03T12:00:00.000Z",
  "next_run": 1791104400,
  "last_run": null,
  "failures": 0,
  "paused_reason": ""
}
```

`owner` is the client id the run is submitted as; it is stored because the folder name is a hash,
and a file only counts when its `owner` hashes to the folder it is in. The API never returns it.
The `revision` the API returns is the SHA-256 of the exact file text, so any change to the file
(an edit, a run, a pause, or a hand edit) changes it.

| Field | Rule |
| --- | --- |
| `title` | 1-120 characters after trimming; no control characters |
| `prompt` | 1-20000 characters after trimming; tabs and line breaks are kept, other control characters are refused |
| `project_id` | a project the caller may use |
| `backend`, `model`, `effort` | must be offered for that project right now (see below) |
| `access_mode` | `ask` (default) or `read_only` |
| `allow_internet` | boolean, default `false`; a file saved before it existed reads as `false` |
| `cadence` | see below |
| `enabled` | boolean, default `true` |
| `agent` | optional name of a KeepHarness agent (D41); the run adopts the agent as it is at that moment |
| `page_ids` | optional list of up to 5 Space pages of the schedule's project (D41); every run carries the current text of each |
| per owner | at most 50 schedules |

A file is at most 128 KiB. A hand-edited file that breaks these rules (or is not JSON) is skipped
with a warning; it still counts toward the 50-schedule limit until removed by hand.

### Route

`backend` is one of `codex`, `claude`, `deepseek`, `gemini`, `local` (not `maestro` or `auto`). The
route is checked with the logic Harness agents use (`harness_agents.offered`): the provider must be
enabled for the project, the model listed under it, and the effort one of that model's efforts. The
error names the first field that fails (`backend`, `model` or `effort`). The route is checked when a
schedule is created, when an enabled schedule is saved and when a route field changes. A paused
schedule can therefore keep a route that has since disappeared and still be renamed, paused again or
deleted; turning it on needs a route that is offered.

### Cadence

Times are in the server's local time zone (the process `TZ`).

| Cadence | Shape | Runs |
| --- | --- | --- |
| daily | `{"kind": "daily", "time": "HH:MM"}` | every day at that time |
| weekly | `{"kind": "weekly", "weekday": 0-6, "time": "HH:MM"}` | on that weekday (Monday is 0, Sunday is 6) at that time |
| interval | `{"kind": "interval", "hours": 1-168}` | that many hours after the previous run, counted from when it actually ran |

`time` is `00:00` to `23:59`. The shape is exact: a missing key, an extra key or a value of the
wrong type (a boolean is not a number) is `schedule_invalid` with `field: "cadence"`.

`next_run` is a UNIX timestamp in seconds, the first run strictly after the moment it is computed:
at creation, when an edit changes the cadence, when a paused schedule is turned on, and after every
run. Daily and weekly runs keep their wall-clock time across daylight saving changes (the day is
chosen on the calendar, not by adding 24 hours); a time that does not exist on a given day (02:30
on a spring-forward day) runs at the instant the system maps it to, once that day. An interval
counts absolute hours. An edit that does not touch the cadence keeps `next_run`.

## API

All routes need the same authentication as the other `/v1` routes and answer with
`Cache-Control: no-store`. Errors use the standard body `{code, message, retryable, request_id}`;
`schedule_invalid` adds `field`.

| Route | Success | Errors |
| --- | --- | --- |
| `GET /v1/schedules` | 200 `{"schedules": [...]}`, oldest first, the caller's only | `schedule_storage_unsafe` (500) |
| `POST /v1/schedules` | 201 the schedule | `schedule_invalid` (422), `schedule_agent_unselected` (422, `prompt`), `project_denied` (403), `schedule_limit` (409), `schedule_storage_unsafe` (500) |
| `PUT /v1/schedules/{id}` | 200 the schedule | `schedule_invalid` (422), `schedule_agent_unselected` (422, `prompt`), `project_denied` (403), `schedule_not_found` (404), `schedule_changed` (409), `schedule_storage_unsafe` (500) |
| `DELETE /v1/schedules/{id}` | 200 `{"deleted": true}` | `schedule_invalid` (422, `revision`), `schedule_not_found` (404), `schedule_changed` (409), `schedule_storage_unsafe` (500) |
| `POST /v1/schedules/{id}/run` | 202 `{"job_id": "..."}` | `schedule_not_found` (404), `project_denied` (403), any error `POST /v1/jobs` would give (for example `model_denied`, `queue_full`), `schedule_agent_missing` and `schedule_page_missing` (422), `schedule_storage_unsafe` (500) |

A schedule in a response is the stored record without `owner`, plus `revision`:

```json
{
  "id": "0b8f3f4d1c6a4e7b9d2a5c1e7f3b6a90",
  "title": "Morning digest",
  "prompt": "Summarize yesterday's changes.",
  "project_id": "keepharness",
  "backend": "codex",
  "model": "gpt-6-astra",
  "effort": "low",
  "access_mode": "ask",
  "allow_internet": false,
  "cadence": {"kind": "daily", "time": "09:00"},
  "enabled": true,
  "agent": null,
  "page_ids": [],
  "created_at": "2026-10-03T12:00:00.000Z",
  "updated_at": "2026-10-03T12:00:00.000Z",
  "next_run": 1791104400,
  "last_run": {"job_id": "7d0c...", "at": 1791018005, "state": "submitted"},
  "failures": 0,
  "paused_reason": "",
  "revision": "9f2c...64 hex characters"
}
```

Requests:

```json
POST /v1/schedules
{"title": "Morning digest", "prompt": "Summarize yesterday's changes.", "project_id": "keepharness",
 "backend": "codex", "model": "gpt-6-astra", "effort": "low",
 "cadence": {"kind": "weekly", "weekday": 0, "time": "08:30"}}

PUT /v1/schedules/0b8f...        (only the fields to change, and the revision)
{"revision": "9f2c...", "enabled": false}

DELETE /v1/schedules/0b8f...
{"revision": "9f2c..."}

POST /v1/schedules/0b8f.../run   (no body; an Idempotency-Key header is honoured)
```

- `POST` takes the editable fields. `title`, `prompt`, `project_id`, `backend`, `model`, `effort` and
  `cadence` are required; `access_mode`, `allow_internet`, `enabled`, `agent` and `page_ids` default. `id`, `created_at`, `updated_at`,
  `revision`, `next_run`, `last_run`, `failures` and `paused_reason` are accepted and ignored, so a
  schedule that was read can be sent back; any other unknown key is `schedule_invalid` naming it.
- `PUT` is a partial update: only the editable fields that are sent change, the rest keep their
  stored value, and the `revision` the client last saw is required. A different revision is
  `schedule_changed` (409) and nothing changes. Note that a run or a pause also changes the
  revision, so a client that keeps a schedule open must read it again after a conflict.
- Turning `enabled` off sets `next_run` to `null` and clears `paused_reason`. Turning it on again
  computes `next_run` from now and clears `failures` and `paused_reason`. That is also how a
  schedule paused after failures is resumed.
- `DELETE` takes the `revision` in the JSON body. A malformed id in a path, a missing schedule and
  another owner's schedule are all `schedule_not_found`.
- **Run now** submits the schedule immediately, as a new conversation, through the same path as a
  due run. It works on a paused schedule and does not resume it. It does not change `next_run` or
  `failures`; it records `last_run`. If the submit is refused, the error is returned to the caller
  and nothing is counted against the schedule.

## What a run does

Every 30 seconds a background task of the agent service loads the enabled schedules whose
`next_run` has come, of every owner, earliest first, and submits each one through
`ConversationService.submit`, the code `POST /v1/jobs` uses, with the stored owner's identity:

```json
{"prompt": "...", "project_id": "...", "backend": "...", "model": "...", "effort": "...", "access_mode": "ask"}
```

There is no parent job, so every run is a fresh conversation. The prompt is sent as written, with
two exceptions that read their source at run time (D41):

- **Agent.** With `agent` set, the run sends `resource_selections` for that agent's current
  revision, and puts `@@<name>` in front of the prompt unless it already holds it. Editing the agent
  therefore changes the next run; there is no stale revision. A deleted agent is a failed run with
  `schedule_agent_missing`. Saving refuses (`schedule_agent_unselected`, `field: "prompt"`) a
  prompt that holds an `@@name` (outside a code fence) other than the picked agent's, and a `//name`
  line; `agent` must name an agent that exists (`schedule_invalid`, `field: "agent"`).
- **Pages.** With `page_ids`, each page's text is read when the run starts and appended after the
  prompt as `Page "<title>":` followed by a fenced Markdown block, long enough that the page's own
  backticks, `@@` words and `//` lines stay plain text. Files are never attached. A deleted page is a
  failed run with `schedule_page_missing`.

A paused schedule can still be renamed or deleted after its agent is gone: the agent and prompt
checks only run for an enabled schedule or when `prompt` or `agent` change.
The job payload carries `schedule_id`, `schedule_title` and `schedule_internet` (the task's
`allow_internet`); the conversation list (`GET /v1/conversations`) repeats the first two on the
item of a conversation that a schedule started, so a UI can mark it. Follow-up turns of that
conversation keep the mark; they are attended turns, so they get the provider's internet grant.
A client cannot send these three fields itself (`invalid_internal_field`), and recovering a
workflow from a scheduled job starts an unmarked conversation.

- The first check comes 30 seconds after the service starts. The task starts and stops with the app
  (the same lifespan as the job worker) and is cancelled on shutdown.
- **At most one run per schedule per tick, never a catch-up.** After a run, `next_run` is computed from
  the moment of the run. If the service was down for days, a schedule runs once when it comes
  back and the next run is the next occurrence after now.
- **Never twice for one due time.** The job is submitted with the idempotency key
  `schedule-<id>-<next_run>`. If the service stops after submitting and before the file is updated,
  the next tick submits the same key and gets the same job back instead of a second one. If the
  prompt was edited in between, the same key with a new body is an `idempotency_conflict`; the run
  then records the job it already submitted instead of a failure. The
  outcome is only written while the file still waits for that due time: a schedule that was edited,
  paused or deleted meanwhile keeps what the user made of it.
- `last_run` is `{"job_id", "at", "state": "submitted"}` (`at` is a UNIX timestamp in seconds) while
  the job waits or runs in the normal queue. Each tick first reads back the outcome of every run still
  marked `submitted`: `state` becomes `completed`, `failed`, `cancelled` or `interrupted`, with the
  job's `error` (or its provider `condition`) for the last three. A run whose conversation was
  deleted before it ended stays `submitted` and blocks nothing.
- **One run at a time.** A due time that comes while the schedule's previous run is still queued or
  running is skipped: `next_run` moves to the next occurrence, nothing is submitted and `failures`
  is left alone.

### Failures

- A refused submit (for example `model_denied`, `project_denied`) or an unexpected error is a
  failure: `failures` goes up by one, `last_run` becomes
  `{"job_id": null, "at": ..., "state": "failed", "error": "<code>"}` (an unexpected error is
  `submit_failed`, and its details only go to the server log), and `next_run` moves to the next
  occurrence.
- A run that ends `failed`, `cancelled` (also when an approval expired twice:
  `approval_expiration_limit`) or `interrupted` is a failure too (D15), counted when the scheduler
  reads its outcome back.
- An accepted submit leaves `failures` alone; a run that ends `completed` sets it back to 0.
- After 3 failures in a row the schedule is paused: `enabled` is `false`, `next_run` is `null`, and
  `paused_reason` says why, for example "Paused after 3 failed runs in a row (last error:
  model_denied). Check the route and the project, then turn the schedule back on."
- A busy queue is not a failure. If the service answers 429 (`queue_full`, `owner_queue_full`,
  `submission_rate_limit`), the run is deferred: nothing changes and the next tick tries again.
  Any other 429, such as `job_storage_limit` (the project already holds 1000 jobs), does not clear
  by waiting and is a failure.
- If the client that owns a schedule no longer exists in the service configuration, the schedule is
  paused at once with "Paused because the client that owns this schedule no longer exists." and
  `failures` is left alone.

### Attention items

`GET /v1/activity` returns `schedule_alerts`, the caller's scheduled tasks (in the projects of the
request) that need a look:

- `{"reason": "paused", "message": <paused_reason>, "job_id": null, ...}` for a paused schedule
  that has a `paused_reason`;
- `{"reason": "needs_you", "message": ..., "job_id": <last run>, ...}` when the last run skipped an
  action that needed approval.

Every item also has `schedule_id`, `title` and `project_id`. The run console shows them in the
"Needs you" inbox and counts them on the attention bell. A request filtered by `work_item` returns
an empty list.

## Security

- Folders are created `0700` (every folder this feature creates, parents included), an existing
  owner folder is tightened to `0700` on every write, and files are `0600`. A symlinked owner folder
  or `schedules` folder is refused (`O_NOFOLLOW` on the directory descriptor); a symlinked or
  non-regular schedule file is never read or written through. The API reports
  `schedule_storage_unsafe`; the scheduler logs a warning and skips what it cannot read.
- Writes go to a temporary file in the same folder (`O_EXCL|O_NOFOLLOW`), are fsynced, then published
  atomically (`link` for a new schedule, `replace` for an edit) and the directory is fsynced. The
  temporary file is removed on failure.
- Read-compare-write sequences, including the scheduler's bookkeeping, hold one lock, so a stale
  `revision` never overwrites a newer edit and the 50-schedule limit holds under concurrent creates.
  The lock is per process; the service runs a single worker.
- A run is submitted as the stored owner with the same checks as a request from that owner: the
  project, the provider, the model and the access mode are validated again at run time, so removing
  a client's project access or a model stops its schedules (as failures).
- The prompt is user text sent to a model on a timer. A schedule can only be created by a client that
  could send the same prompt by hand.

## Notes for the UI

- Show `next_run` and `last_run.at` as local times; show `paused_reason` when `enabled` is false
  and it is not empty.
- Show `last_run.state` (`submitted` reads as "running") and `needs_you`, and open the run's
  conversation from `last_run.job_id` (a scheduled run is the first turn of its conversation).
- The conversation list item has `schedule_id` and `schedule_title` only for conversations a
  schedule started.
- **Run now** moves the stored revision (it rewrites `last_run`): read the schedule again and keep
  the form's unsaved fields, or the next Save or Delete is `schedule_changed`.
- Error copy for every code above lives in `userErrors` in `agent_service/ui.js`.
