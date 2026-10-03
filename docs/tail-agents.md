# Tail agents

Tail agents are agents the user creates in the harness. Each one has its own
instructions, purpose, tasks, target output and the route it runs on (provider, model
and effort). The user defines an agent once and runs it on any provider (Codex,
Claude, DeepSeek, Gemini, local). They are separate from the native agents found in
`.codex/`, `.claude/` and `.gemini/`, which keep their `/name` and `@name` tokens.

A Tail agent is a persona, not a native delegate: it runs in *conversational* mode,
which prepends text to the conversation, so every provider can run it. It stays active
across follow-up turns until the persona is released, like any conversational agent.

## Who can use and who can edit

Tail agents are machine-wide: one set per computer, not per client. Every
authenticated client lists them and uses them with `@@id`. Only the local client, the
browser on the computer that runs the harness, creates, edits or deletes them; any
other client (VPN key, tailnet login) gets `tail_agent_local_only` (403) before the
request body is read. The service decides this from the identity it resolved for the
request (`local`, see `ConversationService.identity`), never from a header, so a
proxied or forwarded request is not local.

## Data

One JSON file per agent: `<control_state_dir>/tail-agents/<id>.json`. When the
runtime config has no `control_state_dir`, `state_dir` is used instead.

```json
{
  "id": "code-reviewer",
  "name": "code-reviewer",
  "purpose": "Reviews diffs for correctness.",
  "instructions": "Read the diff and report real bugs only.",
  "tasks": ["Find bugs", "Suggest fixes"],
  "target_output": "A short, ranked review.",
  "backend": "codex",
  "model": "gpt-6-astra",
  "effort": "low",
  "created_at": "2026-10-03T12:00:00Z",
  "updated_at": "2026-10-03T12:00:00Z"
}
```

`id` equals `name` and never changes. The `revision` returned by the API is the SHA-256
of the exact file text, so any change to the file (by the API or by hand) changes it.

| Field | Rule |
| --- | --- |
| `name` / `id` | `^[a-z0-9][a-z0-9-]{1,47}$` |
| `purpose` | 1-300 characters |
| `instructions` | 1-20000 characters |
| `tasks` | optional, up to 12 items of 1-200 characters |
| `target_output` | optional, 0-500 characters |
| `backend`, `model`, `effort` | must be offered right now (see below) |

Text is trimmed before it is measured. Control characters other than tab and line breaks
are rejected. A file is at most 64 KiB and the folder holds at most 100 agents.

`backend` is one of `codex`, `claude`, `deepseek`, `gemini`, `local`. The route is
checked against the enabled services: the provider must be enabled, the model listed
under it, and the effort one of that model's efforts (`maestro.model_efforts`; `local`
and `gemini` use `configured`). The error names the first field that fails.

## API

All routes need the same authentication as the other `/v1` routes; `POST`, `PUT` and
`DELETE` also need the local client (see above). Errors use the standard body
`{code, message, retryable, request_id}`; `tail_agent_invalid` adds `field`.

| Route | Success | Errors |
| --- | --- | --- |
| `GET /v1/tail-agents` | 200 `{"agents": [...]}` sorted by name | `tail_agent_storage_unsafe` (500) |
| `POST /v1/tail-agents` | 201 the agent | `tail_agent_local_only` (403), `tail_agent_invalid` (400), `tail_agent_exists` (409), `tail_agent_limit` (409), `tail_agent_storage_unsafe` (500) |
| `PUT /v1/tail-agents/{id}` | 200 the agent | `tail_agent_local_only` (403), `tail_agent_invalid` (400), `tail_agent_not_found` (404), `tail_agent_changed` (409), `tail_agent_storage_unsafe` (500) |
| `DELETE /v1/tail-agents/{id}` | 200 `{"deleted": true}` | `tail_agent_local_only` (403), `tail_agent_invalid` (400, `revision`), `tail_agent_not_found` (404), `tail_agent_changed` (409), `tail_agent_storage_unsafe` (500) |

An agent in a response is the stored record plus `revision`, `available` and
`unavailable_reason`:

```json
{
  "id": "code-reviewer",
  "name": "code-reviewer",
  "purpose": "Reviews diffs for correctness.",
  "instructions": "Read the diff and report real bugs only.",
  "tasks": ["Find bugs", "Suggest fixes"],
  "target_output": "A short, ranked review.",
  "backend": "codex",
  "model": "gpt-6-astra",
  "effort": "low",
  "created_at": "2026-10-03T12:00:00Z",
  "updated_at": "2026-10-03T12:00:00Z",
  "revision": "9f2c…64 hex characters",
  "available": true,
  "unavailable_reason": ""
}
```

- `POST` takes `name` and the editable fields. `id`, `created_at`, `updated_at`,
  `revision`, `available` and `unavailable_reason` are accepted and ignored, so a
  listing can be sent back; any other unknown key is `tail_agent_invalid` naming it.
- `PUT` and `DELETE` take the `revision` the client last saw in the JSON body.
  `PUT` replaces every editable field (an omitted `tasks` or `target_output` becomes
  empty); `name` may be repeated but not changed (`field: "name"`). `created_at` is kept.
  A malformed id in the path is `tail_agent_not_found`.
- `available` is false when the provider, model or effort is no longer offered;
  `unavailable_reason` says which. The agent stays on disk and can be edited back to
  an available route.
- A deleted or edited agent is reflected in the next call; there is no cache.

## Resources and resolution

`resources.discover` adds every usable Tail agent to the result for any backend, in any
execution mode (a Tail agent needs no native engine). The item is:

| Key | Value |
| --- | --- |
| `kind`, `scope`, `origin`, `group` | `agent`, `tail`, `tail`, `Your agents` |
| `id`, `resource_id` | `tail/agents/<id>` |
| `name`, `description` | the id, the purpose |
| `backend`, `model`, `effort` | from the file |
| `mode` | `conversational` |
| `selectable`, `unavailable_reason` | availability for the listed project |
| `preflight_hint` | `Runs on <model> · <provider>` |
| `source` | `tail/agents/<id>.json`, a logical name (no host path) |

Availability in a resource listing is checked against the services enabled for the
project being listed; `GET /v1/tail-agents` checks against all enabled services.
The private `_body` is the persona prompt, composed from the fields; empty sections
are left out:

```
You are the agent "<name>". Purpose: <purpose>
Follow these instructions:
<instructions>
Tasks you handle:
- <task>
Target output: <target_output>
```

The token is `@@<id>`. A native agent with the same name keeps its own `/name` and
`@name`; both are listed. Resolution follows the other resources: the selection carries
`{id, revision, token}`, the prompt must contain the token, a changed file is
`resource_changed` and an unavailable agent `resource_unavailable`. `/id` and `@id`
are not accepted for a Tail agent. `//name` and an `@@name` that is not a selected
Tail agent still fail with `tail_resources_unavailable`. `prepare_prompt` adds the
persona to the execution prompt; follow-up turns carry the stored selection forward
until `release_persona`.

Two existing rules apply unchanged:

- The single-invocation gate. The item declares its backend, model and effort, so the
  composer must send the same route (`invocation_backend_mismatch`,
  `invocation_model_or_effort_mismatch`). The UI sets the composer to the agent's route
  when it is selected.
- Conversational agents cannot be chained (`conversational_chain_unsupported`), so
  Tail agents are not offered to plan lookups (`include_workflows=False`).

`GET /v1/catalog` lists Tail agents once, in `items`, not in the per-provider `agents`
list. The model must have `read` permission to select any resource, Tail agents
included.

## Security

- The folder is created `0700`, tightened to `0700` on every write, and files are
  `0600`. A symlinked folder is refused (`O_NOFOLLOW` on the directory descriptor);
  a symlinked or non-regular file is never read or written through. The API reports
  `tail_agent_storage_unsafe`; discovery skips it with a warning.
- Writes go to a temporary file in the same folder (`O_EXCL|O_NOFOLLOW`), are fsynced,
  then published atomically (`link` for a new agent, `replace` for an edit) and the
  directory is fsynced. The temporary file is removed on failure.
- A read-compare-write sequence holds one lock, so concurrent creates of one name yield
  one agent and a stale `revision` never overwrites a newer edit. The lock is per
  process; the service runs a single worker.
- Stored files are checked against the same limits as requests. A hand-edited file that
  breaks them (or is not JSON) is skipped with a warning, not fatal. It still counts
  toward the 100-agent limit until removed by hand.
- Tail agents are machine-wide and edited only from this computer. Every authenticated
  client can use them, so their instructions are prompt text that runs with the
  permissions of whoever uses them; that is why a remote client cannot change them.
- No host path reaches the model. The persona note says the user defined the agent in
  Tail Harness and that the definition follows inline; it names no file, because the
  agent has none inside the project. `source` (`tail/agents/<id>.json`) is a logical
  label for the UI and is not placed in the prompt.
