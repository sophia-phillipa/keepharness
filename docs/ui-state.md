# Durable UI preferences

The browser's `localStorage` belongs to one origin (scheme, host, port), so a changed port, a lost runtime file or a reset Electron profile used to wipe the owner's preferences silently. They now live in a backend store, one file per owner:

`<state_dir>/ui-state/<owner hash>/preferences.json` → `{"version": 1, "values": {...}}`, mode 0600, written atomically (temp file, fsync, rename, directory fsync) by `JsonFileRepository`.

## API

| Request | Answer |
|---|---|
| `GET /v1/ui-state` | `200 {version, values, read_only, limits}`, `Cache-Control: no-store` |
| `PATCH /v1/ui-state` `{"values": {...}}` | merge per key, `null` clears a key; same shape as `GET` |

Errors: `ui_state_local_only` (403, any client but the local owner), `ui_state_unknown_key` and `ui_state_invalid_value` (422, `field` names the key; also a single value over `limits.value_bytes`, 16 KB serialized), `payload_limit` (413, only the whole body over `limits.body_bytes`, 64 KB), `ui_state_read_only` (409, the store cannot be written).

## Rules

- The allow-list and per-key validators are `SCHEMA` in `agent_service/ui_state.py`; the table of keys and their original `localStorage` writers is in the WP6 section of `dossier/releases/v0.16.0.md`. Drafts and per-launch state stay in `sessionStorage`.
- Writes are strict and all-or-nothing; reads are tolerant (invalid fields dropped, over-cap maps keep the most recent entries, an unparsable file becomes a `.bak`). A write rewrites every stored key it does not touch with its raw stored value, including a known key whose value this build rejects or truncates, so an older build never erases what a newer one wrote; `GET` still serves only valid values.
- A `PATCH` that changes nothing (empty, or the merged document equals the stored one) answers the current view without writing the file.
- `chat_selection.model` and `.effort` accept only catalog identifiers (`[A-Za-z0-9._:/[\]-]`, at most 128 characters, `""` allowed), never free text.
- Concurrency: the merge is per top-level key and the last write wins per key. Two windows editing the same map key (for example `project_expanded`) overwrite each other, and the later `PATCH` wins; there is no ETag or version check.
- Caps are served in `limits` so the client shares them; maps the client grows without bound (`project_expanded`, `conversation_activity`) must be pruned to their cap before sending.
- Nothing here is conversation content, a draft, a secret or a path.

A GET may repair the store as a side effect: an unparsable file is moved aside as `.bak` (the client did not ask for it; it is an internal repair). Keys a newer build wrote are kept on write and never served. `<state_dir>` is the harness run state folder (`state/runs`).

## Frontend

`agent_service/ui-prefs.js` (loaded before `ui.js`) exposes `window.HarnessPrefs` (`get`, `set`, `flush`, `ready`, `onNotice`, `server`). It reads the store with one synchronous `GET` at boot; only a version 1 answer with a `values` object starts server mode. Anything else (a guest's 403, a mock, a network error) is local mode, the previous `localStorage` behaviour.

- Writes are debounced, sent as per-key `PATCH`es, flushed on `pagehide` and when the page is hidden; transient failures back off, never retrying a key a newer value replaced.
- Migration: an old `localStorage` key with no stored value is converted into the store; the old key is erased only after the server accepted it. A stored value wins over an old key. `theme` keeps its `localStorage` first-paint cache. The `workspace-section-*` keys become the one `workspace_sections` map; a stored `last_section` that matches no Settings button opens Appearance.
- Errors: 413 with several keys is split per key; a single key that still does not fit is dropped with a console warning. 422 with a `field` drops that key and resends the rest. 409 `ui_state_read_only` stops all writes and shows the notice once.
- Tests: `tests/ui-state-restart.spec.cjs` starts its own harnesses (ports 18096-18099) to restart on another port against the same state folder, and checks `preferences.json` on disk, not only the API. `scripts/test-ui.sh` resets the store before each spec.
