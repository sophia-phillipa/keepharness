# Conversation execution mode

## Cloud-scoped retirement (#52, D-044)

This section supersedes the Codex/Claude mode and legacy-resolution rules below. The earlier sections preserve the history of the original UI and permission work. [D-044](decisions/d-044-scoped-sandbox-under-facade.md) governs the retirement; issue #52 implements its backend contract and the draft safeguards below. Unknown historical modes display "Execution mode unavailable" without a native fallback. The remaining UI retirement and guidance are a separate follow-up.

The supported modes are `native` for Codex, Claude, Gemini and DeepSeek, and `scoped` for Local. New Codex/Claude conversations default to native even if a stale service setting says scoped. An explicit unsupported mode returns HTTP 422 `execution_mode_unsupported` before enqueueing. Queued work, workflow stages, scheduled occurrences and direct adapter dispatch enforce the same refusal before provider execution. Schedule creation and updates reject unsupported modes.

A stored Codex/Claude scoped conversation cannot continue, retry, resume, branch or escape retirement through a provider or permission-preset change. Its mode, history, attachments, proposals, session markers and old home files remain untouched and readable. Starting a native conversation is an explicit new-conversation action; it does not import the old provider session.

Absent or malformed model execution capabilities block sending, including restored scoped drafts. Receiving valid capabilities does not silently replace an explicitly retained draft mode. The explicit **New conversation** action establishes the selected model's supported default, preserving draft text and attachments without restoring an old mode or session and without submitting automatically.

### Explicit draft execution state

Every composer draft stores `draft_mode: {mode, modeChosen, retiredLock}` with its text and attachments:

- `mode` is the draft's execution mode.
- `modeChosen` records an explicit user mode selection. Project selection never sets it.
- `retiredLock` records that the draft has a retired cloud-scoped execution mode. It is independent of explicit choice and cannot be cleared by a model or project change.

All save/restore paths preserve these three fields together: Back/Forward, Home replay, project switches, readiness probes, catalog refresh and reload. Restoring a draft restores its own restriction, not the mode of the conversation being exited. Automatic fallback cannot discard a retained retired restriction in favor of a saved native destination draft. Send and Enter are blocked while `retiredLock` is true, with an explanation directing the user to explicitly start a new conversation.

Only an explicit **New conversation** action clears `retiredLock` and establishes a supported mode, while retaining text and same-project attachments. Main, project, file and page actions explicitly starting a new chat have this meaning; navigation replay, project selection and archive/delete side effects do not.

Restoration itself does not manufacture a mode choice. An unlocked draft with `modeChosen: false` follows the selected model's current default when its project or model changes; selecting a project and then Local therefore yields scoped execution without requiring New. A selected Local model remains selected when available in the new project. An explicitly chosen mode remains chosen, and any retired lock takes precedence over default selection.

Legacy drafts without `draft_mode` are normalized from their persisted execution mode, choice flag and provider evidence. A recorded retired cloud-scoped mode acquires the retirement lock; unknown scoped provenance also stays locked until explicit New. Normalization does not invent an explicit choice. Persisted backend evidence takes precedence over the current model catalog, so a historical Local conversation is not retired merely because its old model disappeared. The legacy mode fields remain compatibility mirrors, while the complete draft state is the restoration contract. This affects composer draft persistence only; stored conversation execution modes remain immutable.

For an existing conversation without a root mode, consistent recorded provider context and unambiguous persisted turn modes determine the historical mode. Missing or conflicting evidence for historical Codex/Claude or unknown provenance refuses execution; the displayed historical mode is unavailable. Unambiguous historical Local remains scoped and historical Gemini/DeepSeek remain native. Neither the currently selected backend nor a mutable service configuration proves that a historical cloud conversation was native. Resolution does not migrate rows or silently rewrite stored payloads.

Historical evidence collection resolves ancestry from loaded rows with memoized roots, so a long supported Local conversation does not trigger a separate database ancestry walk for every turn. This optimization preserves refusal when the same conversation contains historical cloud-scoped evidence.

Cloud-scoped executors and their registration are retired, including their effect-capability claims. The rejection path never reads or copies credentials, starts a provider, applies a proposal or makes an inference request. Local's independent sandbox and native permission/trust behavior remain covered by their existing regression tests. See the [0.16.0 release notes](releases/v0.16.0.md) for acceptance tests and actual validation.

## Original mode selection

New conversations offer a one-time isolation switch above the conversation, with native execution selected by default where supported. The explanation uses plain language about working in the project folder versus a separate space with restricted file/tool access; it avoids CLI, engine and session terminology. The switch can be changed until the first job is accepted; a failed submission preserves both the choice and the draft.

After the first message, the switch and explanation disappear. The same shield icon remains at the upper-right corner inside the prompt box. The active switch and isolated-session icon use the theme accent; native state is neutral. The icon is an accessible read-only status, not an editable switch. Reloading or reopening a conversation restores its fixed mode. A new conversation resets to the supported default.

## Provider capabilities

| Provider | Available conversation modes |
|---|---|
| Codex | Native (default), isolated |
| Claude | Native (default), isolated |
| DeepSeek | Native |
| Gemini | Native; its existing provider limitations still apply |
| Local models | Isolated only: the existing mandatory filesystem boundary is preserved |

Single-mode providers disable the switch and explain why. Switching to an incompatible model after explicitly choosing a mode does not silently change that choice; sending is blocked until a compatible model/mode is chosen. Existing conversations cannot change mode via model handoff.

The API field is `execution_mode` (`native` or `scoped`). The first request establishes the conversation mode; follow-ups inherit it. The server enforces immutability and rejects unsupported combinations. Legacy conversations retain their effective mode rather than silently adopting the new default. Execution mode is separate from `access_mode`: approval choices never grant extra project permissions or redefine isolation.

## Validation

`tests/conversation-execution-mode.spec.cjs` covers native default, keyboard switch, draft reload, failed submission, fixed mode, same icon, theme accent, follow-up inheritance, reopening, new-conversation reset, mobile overflow and mandatory/unsupported provider modes. Desktop and mobile screenshots were inspected. `tests/harness-connection.spec.cjs` verifies the existing readiness gate still blocks interaction and preserves drafts during reconnects.

No new release, branch or commit is part of this change. No provider inference is needed for these checks.

## Fix round 01: isolation and access decisions (F-58, F-110)

Two owner decisions from fix round 01 (`~/.cache/th/design/fix-round-01.md`, `~/.cache/th/design/findings.md`) change how a conversation's isolation and access mode are chosen and shown, on top of the mechanism described above. Status: decided design, implemented by work package WP-A (backend in A1, UI in A2, first in the `agent_service/ui.js` lane); this document records the target behavior for that work, not a claim that every part is merged.

**F-58 — isolation and access choice move out of the in-chat toggles.**

- **Isolation** stays a choice made only once, when the conversation opens (as already described above: the one-time switch before the first message). Once the first message is sent, the conversation shows a fixed, read-only start-of-session notice at the top — "Native conversation" or "Isolated conversation" — instead of an editable control. There is no isolation toggle anywhere in an existing conversation.
- **Access** (the approval policy: Read only / Ask for approval / Automatic / Full access) no longer carries over from one conversation to the next. Every new conversation starts in **Ask for approval**, regardless of what a previous conversation used, and a start-of-session notice states the active access mode next to the isolation notice. The browser-level memory of the last access choice (`chat-selection` in `localStorage`) stops seeding a new conversation's access mode; it may still seed unrelated preferences (last project, last model) that are not the access mode.
- This closes finding F-58 (a browser-remembered "Full access" silently applying to a new conversation with no visible warning) and, as an accepted side effect, finding F-94 (a model/mode mismatch after switching isolation): since isolation is no longer an in-chat toggle after the first message, that mismatch can only occur when picking the model at conversation start, where a clear way out (pick a compatible model, or start a new conversation) is always available — Send is never left dead behind a disabled control.
- Access mode is still only an approval policy, per the existing rule above: it never redefines isolation, and isolation never grants extra project permissions.

**F-110 — "Ask for approval" guarantees a card before any change.**

Finding F-110 recorded a live Codex turn, in `ask`, writing a file with zero approval cards: the pre-round mapping (`workspace-write` sandbox + `on-request` policy) is exactly the documented Codex behavior for unattended edits, not a bug in the provider, so `ask` had to be remapped rather than merely fixed. The decided mapping (full rationale and options considered in `~/.cache/th/design/f110-approvals.md`) is:

| Mode | Codex (thread and turn) | Claude |
|---|---|---|
| **ask** | `sandbox:"read-only"`, `approvalPolicy:"on-request"`, `approvalsReviewer:"user"`; turn `sandboxPolicy:{type:"readOnly", networkAccess:true}`; never `dangerFullAccess`, even when the account is otherwise unrestricted | `--permission-mode default` (never `bypassPermissions`); `--settings` adds `permissions.ask:["Edit","Write","NotebookEdit","Bash","mcp__*"]` and `sandbox.autoAllowBashIfSandboxed:false` |
| auto, full, read_only | unchanged from before this round (`auto` additionally pins `approvalsReviewer:"user"`) | unchanged from before this round |

What this guarantees in `ask`, for both providers:
- No file is created, edited or deleted, and (for Claude) no `Bash`/`Edit`/`Write`/`NotebookEdit`/MCP tool runs, without an approval card appearing first. For Codex this guarantee comes from the OS-level read-only sandbox forcing every write to escalate through `item/fileChange/requestApproval` or `item/commandExecution/requestApproval` — not from model compliance.
- Network stays **on** in `ask` (`networkAccess:true` for Codex; nothing in the Claude settings disables it) — `ask` restricts changes, not connectivity.
- MCP connectors and plugins stay **enabled** in `ask` for both providers; `ask` does not implicitly disable integrations.
- A read-only Codex command (one the read-only sandbox does not need to escalate) may still run without a card. This is an accepted limitation, not a defect: there is no supported Codex policy today that prompts before every command regardless of effect, only before every modification.
- The approval card carries the pending change as a diff: the harness remembers each `item/started` fileChange's `changes`, keyed by `itemId`, and attaches them to the approval event.
- `item/fileChange/requestApproval` is a recognized item in `approval_policy.full_approval_allowed`, alongside the legacy `applyPatchApproval`/`execCommandApproval` names it already knew.

This closes finding F-110. Acceptance is a live check, at most two turns per provider, in a temporary project: create a file in `ask` and decline — the file must be absent; then run a command and create a file in `ask` and approve — both must exist, confirming Codex applies the approved patch under its read-only sandbox and Claude does not silently inherit `acceptEdits`.

**New error: `isolation_unavailable`.** Choosing (or defaulting to) an isolated conversation for a non-local backend on a server without Linux/`bubblewrap` is refused with `APIError("isolation_unavailable", 422)` (`agent_service/services/conversation_service.py:validate_execution_mode`) rather than failing mid-run; the composer surfaces it as "Isolated conversations need Linux with bubblewrap on the server. Turn isolation off or ask the administrator to install bubblewrap." (`agent_service/ui.js:userErrors`). Local execution keeps its own, separate sandbox check and error (`local_filesystem_isolation_unavailable`, `adapters/local/sandbox.py`), since local conversations are always sandboxed and never offer a native choice.

Final backend validation: `.venv/bin/python -m pytest -q tests/test_execution_modes.py tests/test_conversations.py tests/test_model_handoff.py tests/test_model_permissions.py tests/test_native.py tests/test_attachment_formats.py tests/test_context_recovery.py` — **86 passed**, one external Starlette deprecation warning. Both browser scripts named above passed using the bundled Playwright runtime. JavaScript syntax and `git diff --check` passed.

Deployment: an initial guard deferred restart while one job was active. Once it completed, the guard confirmed zero active/queued jobs and `keepharness.service` was restarted. Live HTTP checks on port 8095 confirmed the simplified copy and model `execution_modes` fields. No active job was interrupted. The UI hides the new mode controls until the server advertises this contract, preserving compatibility during rollout.

## 0.15.0: access-mode ceiling, connector gating and the harness reader (WP-04)

Owner decisions D04, D06, D08, D10, D11 and D12 (KeepHarness 0.15.0 gauntlet). This section supersedes the "MCP connectors and plugins stay enabled in `ask`" and "auto, full, read_only unchanged" rows above where they differ.

**Who may pick which mode.** Automatic and Full access run without asking, so only the owner on this computer (the `local` client, including devices enrolled for it) may start them. Any other identity (the vpn bearer key, a tailnet login) gets `APIError("access_mode_owner_only", 403)` at submit. A guest run is also clamped when it starts: the native shell and hooks are off, no host connector or plugin is enabled and the provider is never unrestricted; a queued guest run that still carries `auto` or `full` fails closed with the same code. Scheduled tasks already accept only Ask and Read only.

**Connectors per mode (Codex, DeepSeek, Claude native).**

| Mode | Host connectors and plugins | Reading files |
| --- | --- | --- |
| Read only | none: every host server and plugin is disabled (Claude gets an empty MCP config and no plugin) | Claude: Read/Glob/Grep inside the project folders. Codex/DeepSeek: the harness reader |
| Ask | the allowed ones, each with `default_tools_approval_mode = "prompt"` on Codex/DeepSeek (an approval card per call); Claude already asks for `mcp__*` | native tools; Codex/DeepSeek read-only commands can read any file the account can |
| Automatic, Full (owner only) | the allowed ones, unchanged | native tools, unrestricted when the shell grant is on |
| Guest (any mode) | none | Codex/DeepSeek: the harness reader whenever the shell is off |

A per-tool `approval_mode` that the owner set in the provider's own configuration still applies in Ask, because Codex merges the generated settings over its own configuration file.

**The harness reader** (`agent_service/reader_mcp.py`). When a native Codex or DeepSeek run has the read grant but no shell, `thread_parameters` adds one stdio MCP server, `harness_reader`, started as `python -m agent_service.reader_mcp ROOT...` with `enabled_tools = ["read_file", "list_directory", "search_files"]` and `default_tools_approval_mode = "approve"`. Its roots are the authorized project roots (including the conversation-history folder) plus the conversation's extracted attachment text, when present. The reader resolves every path inside those roots and applies the project browser's name policy (no `.git`, `.ssh`, `.config`, `.env*`, `*.key`, `*.pem`, symbolic links); it has no write, shell, network or upload tool. Codex 0.157.1 rejects the `readOnly.access` / `workspaceWrite.readOnlyAccess` read allow-list (its protocol schema has no such field and the binary reports "no longer supported; use permissionProfile"), so the reader, not the sandbox, enforces the read boundary. A named permission profile (`permissions` on `thread/start`) is the follow-up for Ask's read-only commands.

**Run folders outside the state folder.** The runtime config gains `sessions_dir`, a sibling of the state folder (`~/.local/share/keepharness-sessions`, mode 0700). Each conversation's provider folder (cwd of a "No project" run, thread markers, extracted attachments, history files) lives there, so the folder that holds `vpn.key`, `deepseek.key` and the database is never a parent of a run's working directory. `sessions_dir` is private storage: it cannot be registered as a project folder. Without the key (an older runtime file) the folders stay under `state_dir/sessions`. Markers left in the old place are not migrated: the next turn of an existing conversation starts a new native thread seeded with the harness history (`conversation_context.pending_turns`).

**Not in this change.** Automatic stays unrestricted for the owner on Codex, DeepSeek and Claude when the shell grant is on (decision D11's project-bounded Automatic needs the Codex and Claude adapters together); the copy says so. The vpn key is rotated by the operator after upgrading (see the 0.15.0 release notes).

Validation: `tests/test_approval_authority.py::test_non_owner_capability_ceiling`, `tests/test_approval_policy.py::test_mcp_gating_matrix` and `::test_claude_read_only_loads_no_connector_or_plugin`, `tests/test_run_workspace_isolation.py` (including a real stdio session with the reader) and `tests/test_integrations_view.py`.

## Project-bounded Automatic and opt-in Full access (WP-04b, D11)

This section supersedes the "Automatic, Full" row of the connector table and the "Not in this change" note of the WP-04 section above.

**Automatic is bounded to the project.** Checked against the installed CLIs (codex-cli 0.157.1 app-server JSON schema, Claude Code 2.1.288 `--help`):

| | Codex and DeepSeek (`adapters/codex/native.py`) | Claude Code (`adapters/claude/native.py`) |
| --- | --- | --- |
| Automatic | `thread/start` `sandbox:"workspace-write"` (`read-only` without the write grant), `approvalPolicy:"on-request"`; turn `sandboxPolicy:{type:"workspaceWrite", writableRoots:[project root, additional roots], networkAccess:<internet grant>, excludeSlashTmp:true, excludeTmpdirEnvVar:true}`; host connectors carry `default_tools_approval_mode:"prompt"`. A command or write outside the roots, or network without the grant, escalates to an approval card. | `--permission-mode acceptEdits` with the project roots as working directories (cwd plus `--add-dir`): edits inside them are accepted, edits and reads outside them ask; `--settings` adds `permissions.ask:["Bash","mcp__*"]`, so every command and connector call asks. |
| Full access | unchanged: `danger-full-access` / `dangerFullAccess`, `approvalPolicy:"never"` when the shell grant is on | unchanged: `bypassPermissions` when the shell grant is on, `dontAsk` otherwise |

Limits that cannot be enforced with these CLIs: Codex 0.157.1 has no sandbox read allow-list (`workspaceWrite` takes no `readOnlyAccess`), so Automatic commands on Codex and DeepSeek can still read any file the account can; Claude Code's own command sandbox needs `bubblewrap` and `socat` on Linux, which a KeepHarness host does not guarantee, so Automatic on Claude asks before every command instead of running it in a folder sandbox; an owner's personal-setup permission rules (`--setting-sources user`) can still widen what `acceptEdits` accepts.

**Full access is opt-in and owner-only.** A top-level admin setting `full_access` (boolean, default `false`, an explicit boolean or the save is refused) is copied to the runtime config. While it is off, `POST /v1/jobs` with `access_mode:"full"` is refused with `APIError("full_access_disabled", 403)`, and a Full run queued before it was turned off fails closed with the same code when it starts. `GET /v1/models` returns `full_access: true` only to the local owner with the setting on; the access menu hides (and disables) the Full access option otherwise, and a restored `full` choice falls back to Ask. The owner turns it on in the admin dashboard (Settings › System › Providers, **Allow Full access**). Guests keep `access_mode_owner_only` for Automatic and Full.

Validation: `tests/test_access_mode_bounds.py`, `tests/test_integrations_view.py::test_automatic_asks_before_every_connector_call`, `tests/session-start-notice.spec.cjs` (copy, hidden Full access) and `tests/admin.spec.cjs` (the toggle).
