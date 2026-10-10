# Workspace toggles: terminal, file editor, wide layout

> Status: DRAFT. Each option waits for its own Status line in [D-055](../dossier/decisions/d-055-workspace-toggles.md) to be accepted (Wide layout, File editor, Terminal); a package starts only when its option is accepted.
> Job: When a conversation turns into coding work, I want to open a terminal, edit a project file or widen the chat on their own, without switching to a mode, so I can stay in the same conversation and find the layout as I left it after a restart or a port change.
> Scope: `agent_service/ui_state.py`, `agent_service/ui-prefs.js`, `agent_service/ui.js`, `agent_service/ui.css`, `agent_service/index.html`, new `agent_service/routes/terminal.py` and `agent_service/terminal.py`, `agent_service/routes/files.py`, `agent_service/workspaces.py`, `docs/local-owner-access.md`.
> Invariant docs: [D-054](../dossier/decisions/d-054-remove-chat-code-switch.md) (no mode; each option needs its own record), [D-023](../dossier/decisions/d-023-wp6-preferences-in-backend-store.md)/[D-024](../dossier/decisions/d-024-wp6-limits-and-merge.md) and [ui-state.md](ui-state.md) (backend preferences, no new localStorage keys), [D-020](../dossier/decisions/d-020-theme-tokens-for-copied-screens.md) (only `--th-*` tokens), [D-033](../dossier/decisions/d-033-right-panel-accordion.md) (side panel), [local-owner-access.md](local-owner-access.md) (identity, approval sessions).
> Decision rationale: independent flat preferences in the existing store; the two new surfaces reuse the existing SSE, path-guard, child-environment and approval-session machinery; no new dependency.
> Kill criteria: remove this spec if Sophia rejects all three options of D-055, or once each accepted option has shipped and its behavior is described in its own release notes and docs. A rejected option's sections are deleted from this spec.

## 1. Why

D-054 removed the Chat | Code switch: any conversation can be a coding request, so no mode exists. Its follow-up asks for the terminal, the file editor and the wide layout as discrete options, each toggled on its own and kept as backend UI preferences, and says each option needs its own record before it ships.

Facts on `main @ 46b7c26` that shape this design:

| Fact | Evidence |
| --- | --- |
| No interactive terminal exists. The run console (`Ctrl/⌘ + J`) shows run events only. | `agent_service/run-console.js` (header comment), `keyboardShortcuts` in `ui.js` |
| No project-file editor exists. The Files panel browses and attaches; `GET /v1/files/{file}/preview` serves uploaded attachments only. The only text editors are the Space page editor and the schedule editor. | `agent_service/routes/files.py` (`ROUTES`), `index.html` (`#page-editor`) |
| `#messages` is a 1040 px box (`max-width: 1040px`) whose `padding-inline: max(16px, calc((100% - 768px) / 2))` leaves a 768 px reading column; `.composer-area` has `max-width: 1040px`; the header title has its own rule (`main > header h1 { max-width: calc(50% - 150px) }`). | `ui.css` (`#messages` and `.composer-area` blocks under "Chat and composer share one readable column", and the "Conversation column: 768 px" block) |
| Preferences live in `GET`/`PATCH /v1/ui-state`, allow-listed in `SCHEMA`; flat booleans already exist (`sidebar_collapsed`, `activity_open`, `visual_markers`, `always_on_top`); widths in `panel_widths` are tied to old localStorage keys through `WIDTHS`. | `agent_service/ui_state.py`, `agent_service/ui-prefs.js` (`WIDTHS`) |
| The client keeps only keys that have a `CONVERSIONS` entry when it reads the store; keys with no old localStorage key use the shape `{ old: () => [], read: () => undefined, write: () => [] }`. | `agent_service/ui-prefs.js:420` (filter `key in CONVERSIONS`), `:196-198` (`last_section`, `visual_markers`, `always_on_top`) |
| The server streams with SSE (`text/event-stream`); uvicorn is installed without a WebSocket library. | `routes/conversations.py`, `pyproject.toml` dependencies |
| Arbitrary local actions (`POST /v1/services`) already require the `local` identity plus an enrolled approval session; `require_approval_session(..., revalidate=True)` rereads the session and raises 403 `approval_session_required` or `approval_session_expired`. | `docs/local-owner-access.md` (Service control), `agent_service/approval_sessions.py:184` |
| `workspaces.project_path` keeps a path inside the root, refuses symlinks and the project name policy (`project_allowed`); `workspaces.credential_path` is the broader credential list (`.git-credentials`, `.npmrc`, private key names such as `id_ed25519`, key suffixes). The reader pairs them. | `agent_service/workspaces.py:118` (`credential_path`), `:152` (`project_path`); `agent_service/reader_mcp.py:113-115` (`readable`) |
| `open_attachment_source` walks a path with `dir_fd` and `O_NOFOLLOW` per component, so no component can be swapped for a symlink between check and use. | `agent_service/workspaces.py:281-303` |
| `child_environment()` passes only allow-listed variables to a child and never the harness-authority names (`KEEPHARNESS_*`, `TAIL_HARNESS_*`, `HARNESS_*`, cookies, admin tokens). | `adapters/shared/process.py:70` |
| The Codex app has `Open terminal` (`` Ctrl+` ``), `Full view by default`, `Bottom panel` and `Default terminal location` (Bottom/Right). | `dossier/research/codex-app-inventory-2026-10.md` lines 101, 144, 332 |

Key decisions:

| Decision | Choice | Why |
| --- | --- | --- |
| Records | One record (D-055) with one Status line per option | D-054 asks for a record per option; per-option status lets each be accepted, rejected or shipped alone. Whether three separate records are wanted is an open question for Sophia. |
| Storage | Flat keys in the existing store, each added by its own option's package | Per-key merge: toggling one never overwrites another from a second window; same shape as `activity_open`. |
| Scope of the preference | Global per owner, not per project or conversation | Layout is a habit, not project data; a per-project map needs caps and pruning for no stated need. The terminal and editor *contents* follow the active conversation's project. |
| Terminal transport | SSE out, `POST` in, stdlib `pty` | No new dependency (a WebSocket needs `websockets`/`wsproto`); loopback latency is fine for typing. |
| Terminal renderer | Plain-text output, no emulator, in v1 | A full emulator (xterm.js) is a new vendored dependency; line-oriented commands cover the stated need. Revisit on demand (full-screen TUIs such as `vim`, `htop`). |
| Editor | `<textarea>`, one file at a time, ETag-guarded save | Reuses the page-editor pattern; no CodeMirror/Monaco. ETag stops a save from clobbering an agent's concurrent edit. |
| Gates | Editor: `local` identity for read and write, plus an approval session for write. Terminal: `local` identity + approval session on every call and on the open stream | Same gate as service control: a shell and file writes are at least as strong. A UI preference is never a security gate. |

## 2. How

### Where each toggle lives

| Option | Rail/header control | Shortcut | Settings row | Surface |
| --- | --- | --- | --- | --- |
| Terminal | Rail button `#terminal-toggle` (icon `terminal`, title "Terminal"), after the files/activity button | `` Ctrl/⌘ + ` `` (Codex parity) | Settings › Appearance › "Workspace": switch "Terminal" | `#terminal-pane`, docked at the bottom of `main`, above the run console (`order` 10, run console stays 11) |
| File editor | Rail button `#editor-toggle` (icon `file-code`, title "File editor") | `Ctrl/⌘ + Shift + E` (Codex "Toggle navigation panel" key; free in KeepHarness) | same group: switch "File editor" | `#editor-pane`, a column between the chat and the side panel, on the side panel's side (`panel_order`) |
| Wide layout | Header button `#wide-toggle` beside the conversation title actions (icon `arrows-horizontal`, title "Wide layout") | `Ctrl/⌘ + Shift + L` | same group: switch "Wide layout" | CSS only: `body.wide-layout` |

All three controls are toggle buttons with `aria-pressed`; the Settings switches and the rail/header buttons drive one function per option, so they never disagree. The Settings › Appearance › "Workspace" group is born with the wide-layout package (P2), holding only the "Wide layout" switch, and it replaces the stale line "The theme, last model, and chosen effort are saved in this browser." with "Saved for you on this computer." The editor and terminal packages (P5, P7) each add their own switch, their own shortcut row in `keyboardShortcuts` and their own `.shortcut-help` text; nothing for an option that has not been accepted is rendered, hidden or not.

Each shortcut joins `keyboardShortcuts` in `ui.js` (so the shortcuts dialog and its search list it) and the `.shortcut-help` text. Shortcuts are ignored while a `dialog[open]` or an open popover has focus, like the existing ones; inside the terminal input, `` Ctrl+` `` still closes the terminal (it is not a shell key).

### Defaults

| Key | Default | Added by | Reason |
| --- | --- | --- | --- |
| `wide_layout` | `false` | P1 | Keeps the measured 768 px reference column. |
| `editor_open` | `false` | P5 | Chat-first: nothing opens beside the chat until asked. |
| `editor_width` | unset (CSS default) | P5 | Pane width, flat like `terminal_height`. |
| `terminal_open` | `false` | P7 | A shell is a strong capability; it starts closed and is opened on purpose. |
| `terminal_height` | unset (CSS default) | P7 | Pane height. |

A missing key reads as its default. There is no migration from `localStorage` because no old key exists, but every new key still gets one `CONVERSIONS` entry in `ui-prefs.js` in the `always_on_top` shape `{ old: () => [], read: () => undefined, write: () => [] }`; without it the client drops the key on read (`ui-prefs.js:420`). `editor_width` is not added to `WIDTHS` (that map pairs fields with old localStorage keys).

### Behavior per option

**Terminal.**
- *Session.* Opening the pane starts (or reattaches to) one shell session for the active conversation's project, cwd = the project root (`workspaces.project_root(spec, "root")`). A project without a folder, or `sem-projeto`, starts in the owner's home folder and says so in the pane header. Switching conversation to another project shows that project's session (one session per project). Closing the pane hides it; the session keeps running until "End session", the idle timeout or a harness restart.
- *Shell and environment.* The shell is the owner's login shell (`$SHELL`, fallback `/bin/sh`) under the harness process's user, with no provider sandbox: this is the owner's own terminal, not an agent tool. Its environment is `child_environment()` (`adapters/shared/process.py:70`, which removes every harness-authority name) plus `TERM=dumb`, so programs do not emit full-screen sequences the pane cannot render.
- *Input model.* The pane has a single-line input: Enter sends the line plus `\n`. Explicit key buttons (and the same keys while the input has focus) send Ctrl+C (`\x03`), Ctrl+D (`\x04`), Tab (`\t`) and Up (`\x1b[A`). There is no raw per-keystroke mode in v1.
- *Hidden input.* While the pty's `ECHO` flag is off (read with `termios.tcgetattr` on the master fd, reported to the client with each `output` event as `echo: false`), the input is masked (`type="password"`) and the sent line is never shown or kept in the client's history. This covers `sudo`, `ssh` and `read -s` prompts.
- *Output.* Plain text: ANSI colour and cursor sequences are stripped server-side. A bare `\r` (not followed by `\n`) rewrites the current line, so progress bars update in place. The pane keeps the last 5,000 lines.
- *Limits.* A session is idle when it has had no input and no attached stream for 30 minutes; idle sessions are ended. At most 4 live sessions per owner. Starting a fifth ends the oldest idle-ranked session that has no foreground child (the pty's foreground process group, `os.tcgetpgrp` on the master, is the shell's own); a session running a foreground child is never ended by the cap. When all 4 have a foreground child, the start answers 409 `terminal_limit`.
- *Session id.* The session id is a handle, not a credential: every call and the open stream re-gate on identity and approval session, so knowing an id grants nothing.

**File editor.** Opening it shows an empty state ("Open a file from the Files panel"). With the editor open, activating a file row in the Files panel (double-click or Enter; single click still selects for attach and drag) opens it in the editor. Only files inside the active conversation's project roots open; files outside the roots (the system browser) do not open in the editor in v1. Credential names never open: a path must pass `workspaces.project_path` and must not match `workspaces.credential_path` (the same pair as `reader_mcp.readable`), so `.env*`, `.git-credentials`, `.npmrc`, `id_ed25519` and key suffixes are refused. Text files up to 1 MiB, UTF-8 only; binary, larger or non-UTF-8 files show a read-only notice. Line endings: the server reports `eol` (`lf`, `crlf` or `mixed`); the client edits with `\n` and restores `\r\n` on save for `crlf`; a `mixed` file opens read-only with a notice. Save: `Ctrl/⌘ + S` or the Save button; a dirty marker on the pane title; switching file or conversation with unsaved changes asks Save / Discard / Cancel. When the agent changed the file since it was opened, save answers 412 and the pane offers "Reload" or "Overwrite". Saving while a turn is running in a conversation of the same project shows a notice ("A turn is running in this project; the agent may also change this file") using the running state the UI already tracks per conversation.

**Wide layout.** `body.wide-layout` sets `max-width: none` on `#messages` and `.composer-area`, and an inline padding of 24 px on both (replacing the 768 px column padding). Defaults are unchanged: without the class, `#messages` stays a 1040 px box with a 768 px reading column and the composer stays at 1040 px. The header title rule (`main > header h1`) is not part of this option; it is decided separately. The empty home card (640 px) and Settings pages (768 px) are unchanged. The option widens only what is left after the sidebar, the editor and the side panel: it never closes or shrinks them.

### Per provider

None of the three options depends on the provider. Coding tools stay the same in every conversation (D-054 point 3). The terminal runs as the owner, never through a provider CLI. Edits made in the editor are not `turn_edit` events (D-053): they belong to no turn; the next turn's agent sees the file as it is on disk. Codex, Claude and DeepSeek conversations behave the same; the visible test pass covers all three.

### Interactions

| Combination | Result |
| --- | --- |
| Editor + side panel | Order follows `panel_order`: chat · editor · side panel (or the mirror). Both resizable; the editor's width in the flat `editor_width` key. |
| Editor + wide layout | Wide removes the chat cap; the editor keeps its width; the chat takes the rest. |
| Terminal + run console | Both can be open; terminal above, run console below. On windows ≤ 700 px wide or ≤ 500 px high (where the run console becomes an overlay), opening one closes the other, like `closeForPanel`. |
| Any + window < 1000 px | Editor becomes an overlay dialog like the side panel (`role="dialog"`, `aria-modal`, focus trapped); opening it closes the side panel overlay and vice versa. Wide layout is a no-op (the column already fills). |
| Phone top bar (≤ 620 px) | Rail buttons for terminal and editor are dropped like Runs and Plugins; the Settings switches and shortcuts still work. |
| Temporary chat (D-047) | Same behavior; nothing of the terminal or editor is stored with the chat. |

### Contracts

**Preference keys** (`agent_service/ui_state.py` `SCHEMA`, each with one `CONVERSIONS` entry in `ui-prefs.js`):

```
"wide_layout": flag,                 # P1
"editor_open": flag,                 # P5
"editor_width": number(0, 20000),    # P5 (same check as _width)
"terminal_open": flag,               # P7
"terminal_height": number(0, 20000), # P7
```

- Purpose: durable layout choices of the local owner.
- Guarantees: per-key merge; strict writes (422 `ui_state_invalid_value` on a wrong type or range); tolerant reads; an older build keeps the raw values on write (existing rule).
- Must-not: no new `localStorage`/`sessionStorage` key; no per-project or per-conversation map; no session ids or paths stored; `panel_widths` and `WIDTHS` unchanged.
- Errors: existing ui-state errors only. Guests (403 `ui_state_local_only`) run in local mode with in-memory defaults; for them the terminal and editor controls are hidden anyway (see gates).

**`POST /v1/terminal/sessions`** `{"project_id"}` → `201 {"session_id", "cwd_label", "created": true}`, or `200` with `created: false` when the project already has a live session (reattach).
- Guarantees: `local` identity and an enrolled approval session (`require_approval_session(..., revalidate=True)`), checked before any process starts; `project_id` resolved through `service.project`; cwd via `workspaces.project_root`; environment `child_environment()` plus `TERM=dumb`.
- Must-not: never start for a non-`local` identity; never take a cwd, command or environment from the request; never put a session id in a URL that is logged; never end a session that has a foreground child to make room.
- Errors: 403 `terminal_local_only`; 403 `approval_session_required` / `approval_session_expired` (existing body); 404 `project_not_found`; 409 `terminal_limit` when 4 sessions are live and each has a foreground child; 503 `terminal_unavailable` when `pty` cannot open (non-Unix).

**`GET /v1/terminal/sessions/{id}/stream`** → `text/event-stream`, events `output {"seq", "text", "echo"}`, `exit {"code"}` and `revoked {"code"}`; `Last-Event-ID` resumes from the ring buffer.
- Guarantees: the gate (`local` identity + approval session, `revalidate=True`) runs at connect and again at least every 30 s while the stream is open; on a failed recheck the server sends `event: revoked` with the error code and closes the stream. The shell keeps running (until "End session" or the idle timeout); a new stream after re-enrolment reattaches.
- Errors: 403 `terminal_local_only`; 403 `approval_session_required` / `approval_session_expired`; 404 `terminal_not_found` (also for another owner's session).

**`POST /v1/terminal/sessions/{id}/input`** `{"data"}` (≤ 4 KiB, UTF-8) → 204. **`POST .../resize`** `{"cols", "rows"}` → 204 (1-500 each). **`DELETE /v1/terminal/sessions/{id}`** → 204, idempotent (404 only if never existed).
- Guarantees: every call rechecks identity and approval session (`revalidate=True`); the session id alone authorizes nothing.
- Errors: 403 `terminal_local_only`; 403 `approval_session_required` / `approval_session_expired`; 413 `payload_limit`; 404 `terminal_not_found`; 409 `terminal_exited` on input after exit.

**`GET /v1/project-files/content?project_id&root_id&path`** → `200 {"text", "etag", "writable", "size", "eol"}`, header `ETag`; `eol` is `lf`, `crlf` or `mixed`.
- Guarantees: identity `local` checked first, before any path resolution or read; path through `workspaces.project_path` (inside root, no symlink, project name policy) and refused when `workspaces.credential_path(path)` is true; `etag` = SHA-256 of the bytes; `writable` true only with a valid approval session and `eol != "mixed"`.
- Errors: 403 `editor_local_only` (before any read); 403 `path_not_authorized`/`symlink_denied` (existing `ToolError` codes; credential names answer `path_not_authorized`); 415 `file_not_text`; 413 `file_too_large` (> 1 MiB).

**`PUT /v1/project-files/content?project_id&root_id&path`** `{"text"}` with `If-Match: <etag>` → `200 {"etag", "size"}`.
- Guarantees: identity `local` (403 `editor_local_only` before any read) + approval session; same path pair (`project_path` and not `credential_path`); the write opens the parent folder through the same `dir_fd` + `O_NOFOLLOW` chain as `open_attachment_source` (`workspaces.py:281-303`; the walk is factored into one helper both use), creates the temp file with `O_CREAT | O_EXCL | O_NOFOLLOW` relative to that `dir_fd`, fsyncs it, renames it with `src_dir_fd`/`dst_dir_fd` and keeps the file mode; the ETag compare and the rename happen under a per-path lock.
- Must-not: never create a file that does not exist (v1 edits only); never follow a symlink at any component; never write without `If-Match`; never write a `mixed`-EOL file.
- Errors: 428 `precondition_required` without `If-Match`; 412 `file_changed` with the current `etag`; 403 `editor_local_only`; 403 `approval_session_required` / `approval_session_expired`; 403 `path_not_authorized`/`symlink_denied`; 413 `file_too_large`.

### Failure modes

- Someone will try to store the open file path or the terminal session id in ui-state "so it reopens": rejected; ui-state holds no paths or ids (ui-state.md rule). The editor reopens empty; the terminal reattaches by project.
- Someone will try to gate the terminal with the `terminal_open` preference: a preference is client-writable state, not authorization. The server gate is identity + approval session on every call and on the open stream.
- Someone will treat the session id as a bearer token and skip the gate on input or stream: the id is a handle only; every call re-gates.
- Approval session revoked or expired while a terminal stream is open → the stream closes within 30 s with `event: revoked`; the shell keeps running until "End session" or the idle timeout, and reattaches after re-enrolment.
- A shell prints a password prompt → the typed secret would echo in the pane and the client history → the `echo: false` flag from `termios.tcgetattr` masks the input and keeps it out of history (`terminal.py`).
- Someone will add xterm.js "for colours": that is a new vendored dependency with a license and payload audit; it needs its own record.
- Someone will add a WebSocket route: uvicorn has no WebSocket library installed; SSE + POST already works through the Tailscale Serve proxy path and the existing stream code.
- Someone will pass `os.environ` to the shell "so it just works": that leaks `KEEPHARNESS_*` and other harness-authority variables to any program the owner runs; the environment is `child_environment()` plus `TERM=dumb`.
- Agent edits the open file mid-edit → save would clobber it → 412 `file_changed` from the ETag check in the `PUT` handler.
- Residual cross-process race: the per-path lock only orders writers inside the harness process. A provider CLI writing the same file between the ETag compare and the rename can still lose its write. The window is milliseconds; the running-turn notice on save makes the risk visible. Closing it fully needs advisory locks every writer honours, which provider CLIs do not; accepted for v1.
- CRLF file saved unchanged → would turn into LF and produce a whole-file diff → `eol` restore on the client; a test saves a CRLF file unchanged and expects an empty diff. A `mixed` file cannot round-trip and opens read-only.
- A path component swapped for a symlink between the check and the write → write lands outside the root → the `dir_fd`/`O_NOFOLLOW` chain and `O_EXCL` temp creation refuse it.
- Credential files (`.git-credentials`, `.npmrc`, `id_ed25519`) pass `project_path` alone → they would open and save → the `credential_path` check refuses them on `GET` and `PUT`.
- Harness restarts → terminal processes die with it (children of the harness); the pane shows "Session ended" and a Start button. Preferences survive (store on disk).
- Shell never exits / runaway output → ring buffer of 5,000 lines and an output rate cap of 256 KiB/s per session (excess dropped with a marker); idle timeout per the idle definition above.
- Two windows toggle different options → per-key merge keeps both; the same option in both → last write wins (existing rule).
- A new key without a `CONVERSIONS` entry → the client drops it on read and the toggle never restores → one entry per key (P1, P5, P7 checklists).
- Store read-only (409) → toggles still work for the session in memory; the existing notice shows once.
- Hard-coded colours in the new panes → D-020 violation; review checks one light and one dark palette.

### Integration points

| Touches | Effect |
| --- | --- |
| `ui_state.SCHEMA`, `limits()` contract test | Up to 5 new flat keys, each added by its option's package; `panel_widths` unchanged; caps unchanged. |
| `ui-prefs.js` `CONVERSIONS` | One entry per new key in the `always_on_top` shape; `WIDTHS` unchanged. |
| `ui.js` `setPanelOpen`, `syncPanelToggles`, `syncWorkspaceModal`, `keyboardShortcuts`, Files panel row handlers | Editor overlay exclusivity, file-open hook, shortcuts. |
| `run-console.js` `closeForPanel` | Mirrored for the terminal on small windows. |
| `index.html` Settings › Appearance, rail | New controls. |
| `routes/files.py`, `workspaces.py` (`project_path`, `credential_path`, the `open_attachment_source` walk) | Content `GET`/`PUT`. |
| `adapters/shared/process.py` `child_environment` | Terminal environment. |
| New `routes/terminal.py`, `terminal.py`; `app.py` route table | Terminal sessions. |
| `approval_sessions.require_approval_session`, `ConversationService.identity` | Reused gates. |
| `docs/local-owner-access.md` | Two new gated surfaces (editor, terminal). |
| Desktop (`desktop/main.cjs`) | No change; the app origin is unchanged. |
| Operator suite (`docs/operator-suite.md`) | New steps per option. |

## 3. What

### Implementation order (one implementer per package)

A package starts only when its option's Status line in D-055 is accepted.

| # | Option | Package | Files | Done when |
| --- | --- | --- | --- | --- |
| P1 | Wide layout | Preference key | `ui_state.py`, `ui-prefs.js`, `tests/test_ui_state.py`, `docs/ui-state.md` | `wide_layout` validates strictly, reads tolerantly and has its `CONVERSIONS` entry; tests pass. |
| P2 | Wide layout | Wide layout + Settings "Workspace" group (P3 merged here) | `ui.css`, `ui.js`, `index.html`, `tests/workspace-wide-layout.spec.cjs` | Header button, shortcut row, help text and the group's only switch toggle `body.wide-layout`, persist through `/v1/ui-state` and survive a restart; the "saved in this browser" line is replaced; defaults unchanged without the class. |
| P4 | File editor | Editor backend | `routes/files.py`, `workspaces.py`, `tests/test_project_file_content.py`, `docs/local-owner-access.md` | `GET`/`PUT` content with local-only gate, credential refusal, `eol`, ETag, 412, 428, approval session and the `dir_fd` atomic write pass their tests. |
| — | File editor | Gate: `backend-review-engineer` review of P4 | — | No blocking finding open. |
| P5 | File editor | Editor pane + keys | `ui_state.py`, `ui-prefs.js` (`editor_open`, `editor_width`), `ui.js`, `ui.css`, `index.html`, `tests/workspace-editor.spec.cjs` | Own switch, shortcut row and help text; open from Files, edit, save, CRLF round-trip, conflict, running-turn notice, dirty prompt, overlay below 1000 px. |
| P6a | Terminal | Terminal core | `terminal.py`, `tests/test_terminal_sessions.py` | Session start, ring buffer, ANSI strip, `\r` handling, echo flag, input keys, cap with foreground-child rule and 409, idle timeout, all with an injected clock; no HTTP. |
| P6b | Terminal | Terminal routes and gates | `routes/terminal.py`, `app.py`, `tests/test_terminal_routes.py`, `docs/local-owner-access.md` | Gates on every call, stream recheck every 30 s with `revoked`, `Last-Event-ID` resume, input cap 413, environment without `KEEPHARNESS_*`. |
| — | Terminal | Gate: `backend-review-engineer` review of P6a+P6b | — | No blocking finding open. |
| P7 | Terminal | Terminal pane + keys | `ui_state.py`, `ui-prefs.js` (`terminal_open`, `terminal_height`), `ui.js`, `ui.css`, `index.html`, `tests/workspace-terminal.spec.cjs` | Own switch, shortcut row and help text; toggle, key buttons, masked input, reattach per project, coexist with the run console, small-window exclusivity. |
| P8 | All accepted | Combined and restart | `tests/workspace-toggles.spec.cjs`, `tests/ui-state-restart.spec.cjs` (extend), operator suite steps | Every combination of accepted options and a restart on another port keep the choices. |

P1-P2 can ship alone. P4-P5 and P6a-P7 are independent of each other and of P2 except for the Settings group, which P2 creates (if Wide layout is rejected, P5 or P7, whichever comes first, creates the group with the text fix).

### Test protocol

| Layer | Tests |
| --- | --- |
| pytest | `tests/test_ui_state.py` (each new key strict/tolerant, older-build raw keep); `tests/test_project_file_content.py` (guest 403 `editor_local_only` before any read, path guard, symlink, `.git-credentials`/`.npmrc`/`id_ed25519` → 403 on `GET` and `PUT`, 415/413, `eol` values, CRLF saved unchanged gives an empty diff, `mixed` not writable, ETag 412/428, no approval session 403, symlinked parent swapped before write refused, atomic write leaves no temp file, mode kept); `tests/test_terminal_sessions.py` (P6a: reattach per project, cap ends the oldest idle session without a foreground child, 4 busy → `terminal_limit`, idle = no input and no stream for 30 min with an injected clock, ANSI strip, bare `\r` rewrites the line, echo flag off on `read -s`, Ctrl+C/D/Tab/Up bytes, exit event); `tests/test_terminal_routes.py` (P6b: gate before spawn, every call re-gates, revoked session closes the stream with `revoked` within 30 s while the shell keeps running, `Last-Event-ID` resume, input cap 413, `env` output shows no `KEEPHARNESS_` name, cleanup on shutdown) |
| Playwright | `tests/workspace-wide-layout.spec.cjs`, `tests/workspace-editor.spec.cjs`, `tests/workspace-terminal.spec.cjs`, `tests/workspace-toggles.spec.cjs`, extended `tests/ui-state-restart.spec.cjs` (checks `preferences.json` on disk) |
| Accessibility checks in the specs | `aria-pressed` on every toggle; focus moves into an opened pane and back to its toggle on close; overlay focus trap below 1000 px; shortcuts listed in the dialog; masked terminal input labelled; contrast on one light and one dark palette (D-020) |
| Visible pass | Desktop app on Sophia's screen, Codex, Claude and DeepSeek conversations, each accepted option on and off |

Commands: `.venv/bin/python -m pytest -q tests/test_ui_state.py tests/test_project_file_content.py tests/test_terminal_sessions.py tests/test_terminal_routes.py`; `PYTHON="$PWD/.venv/bin/python" ./scripts/test-ui.sh` (full browser suite before merge).

## See also

[D-055](../dossier/decisions/d-055-workspace-toggles.md), [D-054](../dossier/decisions/d-054-remove-chat-code-switch.md), [D-053](../dossier/decisions/d-053-turn-file-review.md), [D-033](../dossier/decisions/d-033-right-panel-accordion.md), [D-023](../dossier/decisions/d-023-wp6-preferences-in-backend-store.md), [D-020](../dossier/decisions/d-020-theme-tokens-for-copied-screens.md), [ui-state.md](ui-state.md), [local-owner-access.md](local-owner-access.md), [codex-style-shell.md](codex-style-shell.md).
