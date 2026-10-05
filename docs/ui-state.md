# Durable UI preferences

The browser's `localStorage` belongs to one origin (scheme, host, port), so a changed port, a lost runtime file or a reset Electron profile used to wipe the owner's preferences silently. They now live in a backend store, one file per owner:

`<state_dir>/ui-state/<owner hash>/preferences.json` → `{"version": 1, "values": {...}}`, mode 0600, written atomically (temp file, fsync, rename, directory fsync) by `JsonFileRepository`.

## API

| Request | Answer |
|---|---|
| `GET /v1/ui-state` | `200 {version, values, read_only, limits}`, `Cache-Control: no-store` |
| `PATCH /v1/ui-state` `{"values": {...}}` | merge per key, `null` clears a key; same shape as `GET` |

Errors: `ui_state_local_only` (403, any client but the local owner), `ui_state_unknown_key` and `ui_state_invalid_value` (422, `field` names the key), `payload_limit` (413, body over `limits.body_bytes`), `ui_state_read_only` (409, the store cannot be written).

## Rules

- The allow-list and per-key validators are `SCHEMA` in `agent_service/ui_state.py`; the table of keys and their original `localStorage` writers is in the WP6 section of `dossier/releases/v0.16.0.md`. Drafts and per-launch state stay in `sessionStorage`.
- Writes are strict and all-or-nothing; reads are tolerant (invalid fields dropped, over-cap maps keep the most recent entries, an unparsable file becomes a `.bak`).
- Caps are served in `limits` so the client shares them; maps the client grows without bound (`project_expanded`, `conversation_activity`) must be pruned to their cap before sending.
- Nothing here is conversation content, a draft, a secret or a path.

A GET may repair the store as a side effect: an unparsable file is moved aside as `.bak` (the client did not ask for it; it is an internal repair). Keys a newer build wrote are kept on write and never served. `<state_dir>` is the harness run state folder (`state/runs`).
