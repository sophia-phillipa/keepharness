# W10 Track A implementer contract (#21, #43)

Decision: dossier/decisions/d-041-provider-state-routes-and-notices.md (worktree w10-a). Facts: track-a-facts.md (same folder).
Order: P1 → P2 (mockable in parallel with P1) ; P3 after P1 ; P4 after P2 and P3. TDD: failing test → minimal code.
Fence: no Apps/MCP/Skills chips (#22/#23), no trust or .mcp.json approval (#44), no run-home change (#45), no DeepSeek adapter (#33/#47). Adapters (`adapters/*/state.py`, `adapters/shared/provider_state.py`) are NOT edited. `conversation_service.py` is NOT edited (Track B is in it).

## Shared rules (all packages)
- Every adapter call: `await asyncio.to_thread(...)`. Never in the event loop.
- Provider ids: `codex`, `claude`. Anything else → 404 `provider_unknown` (DeepSeek included, until #33).
- Never put `.claude.json` content, MCP env/headers, or file bytes into responses, logs, or notices. Logs carry `exc.code` only, never `str(exc)`.
- Adapter-authored messages go into a body only as `message`/`provider_message`, truncated to 300 chars.
- Tests use fake homes only (autouse `isolated_provider_homes`, conftest.py:72-89) and the fake CLIs on PATH; Claude adapters in tests get `managed_dir=tmp_path/"managed"`.
- Owner and provider-state directory resolution rejects symbolic-link loops, including looping ancestors of absent leaves, on every supported Python version. Valid missing directories remain allowed, and accepted custom aliases retain their configured spelling.
- Time: `detected_at` is ISO 8601 UTC with `Z`, seconds precision. Coalescing uses an injected monotonic clock.

## P1 — #21 backend (routes + service)

Files: NEW `control/provider_state.py`; `control/routes.py` (dispatch entries + Response pass-through); `control/manager.py` (build service near :112); NEW `tests/test_admin_provider_state.py`.

### control/provider_state.py (P1 part)
```python
PROVIDERS = ("codex", "claude")
COALESCE_SECONDS = 5.0
NO_PROJECT = "sem-projeto"

class ProviderStateService:
    def __init__(self, state: Path, projects: Callable[[], list[dict]],
                 adapters: Mapping[str, ProviderStateAdapter] | None = None, *,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time): ...
    # adapters default (lazy, first use): {"codex": CodexStateAdapter(), "claude": ClaudeStateAdapter(state)}
    # self.locks: dict[str, asyncio.Lock] one per provider (created on first use)
    # self.cache: dict[tuple[str, str], tuple[float, StateSnapshot]]
    def resolve(self, provider: str, project_id: str) -> Path | None   # APIError 404 provider_unknown / project_unknown
    async def read(self, provider: str, project_id: str) -> dict       # {"snapshot", "external_changes"}
    async def write(self, provider, project_id, item_id, scope, enabled, fingerprint) -> dict  # {"snapshot"}
def snapshot_json(snapshot: StateSnapshot) -> dict    # dataclasses.asdict; Path → str; tuples → lists
def error_response(exc: _ProviderStateError, **extra) -> JSONResponse
```
- `resolve`: `sem-projeto` → `None`; else the entry of `projects()` with that `id` → `Path(root)`; not found → `APIError("project_unknown", 404)`.
- `read` (P1): under `locks[provider]`; if cache entry younger than `COALESCE_SECONDS` → reuse its snapshot; else `read_state` in a thread, store in the cache. `external_changes` is `[]` in P1 (P3 fills it).
- `write`: under `locks[provider]` (the caller already holds `manager.lock`); `set_enabled(item_id, scope, enabled, fingerprint, project_root=root)` in a thread; on success put the returned snapshot in the cache with the current clock; return `{"snapshot": snapshot_json(...)}`.
- On `ProviderStateConflictError`: a fresh `read_state` (bypass the cache, refresh it), then `error_response(exc, snapshot=..., external_changes=...)`.

### routes.py
- `GET_ROUTES["/api/provider-state"] = read_provider_state` (query `provider`, `project_id` default `sem-projeto`).
- `POST_ROUTES["/api/provider-state"] = write_provider_state`.
- `endpoint`: in both the GET and POST branches, `if isinstance(result, Response): return result` before `JSONResponse(result)`. No other change to `endpoint`; no `except HarnessError`.
- Handlers catch `_ProviderStateError` (import the base or the 7 classes) around service calls and return `error_response(exc)`.
- manager.py: `self.provider_state = ProviderStateService(self.state, lambda: self.settings.get("projects", []))`.

### HTTP shapes
Request validation (raise `APIError(code, status)` → existing envelope `{"error": code}`):
| check | status | error |
|---|---|---|
| `provider` not a str, or not in PROVIDERS | 404 | `provider_unknown` |
| `project_id` unknown (not `sem-projeto`, not registered) | 404 | `project_unknown` |
| missing/wrong type: `item_id` str 1-300, `scope` in user/project/local/managed/profile, `enabled` bool, `fingerprint` str 1-200, `project_id` str | 400 | `invalid_request` |

`GET /api/provider-state?provider=codex&project_id=sem-projeto` → 200
```json
{"snapshot": {"provider": "codex", "engine": "codex", "project_root": null,
  "items": [{"id": "plugin:github@openai-curated", "kind": "plugin", "name": "github", "scope": "user",
             "enabled": true, "source": "config.toml", "writable": true, "reason": "", "affects": []}],
  "fingerprint": "<opaque>", "cli_version": "0.157.1", "warnings": []},
 "external_changes": []}
```
GET errors: 422 `{"error": "provider_state_unreadable", "message": "..."}`, 502 `{"error": "provider_command_failed", "provider_message": "..."}`.

`POST /api/provider-state` (header `X-Harness-Admin: 1`)
```json
{"provider": "codex", "project_id": "sem-projeto", "item_id": "plugin:github@openai-curated",
 "scope": "user", "enabled": false, "fingerprint": "<from last snapshot of THIS provider>"}
```
- 200 `{"snapshot": {...}}`
- 409 `{"error": "provider_state_conflict", "snapshot": {...fresh...}, "external_changes": [...]}`
- 422 `{"error": "<code>", "message": "<adapter text ≤300>"}` with code one of `provider_state_write_unsupported`, `provider_state_version_untested`, `provider_state_validation_failed`, `provider_state_unreadable`. `ProviderStateValidationError.errors` is NOT included.
- 502 `{"error": "provider_command_failed", "provider_message": "<already redacted ≤300>"}` (no `exit_code`, no paths).
- 429 from `manager.lock` as today; UI must not issue concurrent POSTs.

### P1 tests (`tests/test_admin_provider_state.py`, pattern from test_admin_catalogs.py:1-35)
- `test_get_codex_snapshot_shape` / `test_get_claude_snapshot_shape` (fake CLIs; JSON keys above; `affects` is a list).
- `test_get_unknown_provider_404` (`deepseek`, `x`), `test_unknown_project_404`, `test_get_sem_projeto_has_null_root`.
- `test_post_ok_returns_fresh_snapshot` (codex plugin off; fake app-server log shows the write).
- `test_post_conflict_returns_409_with_fresh_snapshot` (stale fingerprint).
- `test_post_unsupported_422`, `test_post_validation_failed_422` (Claude skill or MCP path with fake bytes, or monkeypatched adapter), `test_post_version_untested_422` (fake-claude-version outside the range on a skill write), `test_post_unreadable_422`.
- `test_post_command_failed_502_redacted` (fake-claude-fail; body has no home path, no `.claude.json` content; caplog has no message text).
- `test_post_requires_admin_header`, `test_post_invalid_fields_400` (parametrized).
- `test_get_coalesces_within_5s` (injected clock; fake app-server log shows one session for two GETs; a third after 5 s reads again).
- `test_post_refreshes_cache` (GET, POST, GET within 5 s returns the post-write fingerprint).
- `test_adapter_calls_run_off_loop` (monkeypatch `asyncio.to_thread` spy, or assert the adapter method runs in a non-main thread).
- Keep green: test_provider_state*.py, test_admin_*.py.

## P2 — #21 UI + Playwright

Files: `control/customize.js`, `control/admin.css`, `control/admin.js` (requestRaw error fields, action() aria-label fallback, new error sentences), NEW `tests/admin-plugins-switches.spec.cjs`; `tests/admin-customize.spec.cjs` only if the row DOM breaks it.
- admin.js `requestRaw`: the thrown Error gets `error.status = r.status` and `error.body = value` (additive). Message map adds: `provider_state_conflict`, `provider_state_write_unsupported`, `provider_state_version_untested`, `provider_state_validation_failed`, `provider_state_unreadable`, `provider_command_failed`, `provider_unknown`, `project_unknown`; when `value.message`/`value.provider_message` exists, append it after the sentence.
- admin.js `action()`: label fallback `trigger.getAttribute("aria-label")` (3 lines from the reference branch).
- customize.js: on `enter()`, load `GET provider-state?provider=codex&project_id=sem-projeto` and the same for claude (in parallel; a failure of one shows its message for that provider's pills and no switches). Keep `snapshots[provider]`.
- Rows (D-041 item 4): catalog rows as today, plus snapshot-only `kind=="plugin"` items appended (name from the StateItem). For each provider pill on a row: a StateItem with the same id → `button role="switch"` (`aria-checked`, `aria-label="<Name> in <CLI>"`, `data-item-id`, `data-provider`, `aria-describedby` → row note); none → note "Not installed in <CLI>", no switch. DeepSeek or no snapshot → note "<CLI> state is not readable here yet" (or the GET message), no switch.
- Row note (`.plugins-row-note`, small text): `<Scope> · <source>`; when `!writable`, the switch is `disabled` and the note adds `reason`. Scope labels: User, Project, Local, Managed, Profile.
- Click → `action(async () => { POST provider-state {provider, project_id:"sem-projeto", item_id, scope:item.scope, enabled:!item.enabled, fingerprint:snapshots[provider].fingerprint} })`.
  - 200 → `snapshots[provider] = body.snapshot`; redraw; status "<Name> is now on/off in <CLI>."
  - 409 → `snapshots[provider] = err.body.snapshot`; redraw; status "<CLI> changed since this page loaded: <Name> is now on/off. Try again." (or "…was removed." if absent).
  - 422/502 → status = the error message; the row note shows it; redraw from the unchanged snapshot.
  - Never flip ahead of the server: redraw from state in `finally`, then refocus `[data-item-id="…"][data-provider="…"]` via `CSS.escape`.
- Status line `<p role="status" class="plugins-status" hidden>` + `report(text)` (reference branch). No wizard `unsaved`/`profileDirty` guard (D-041 item 11).
- admin.css: `.plugins-switch` from the reference branch (aria-checked, focus-visible, reduced-motion, disabled look); `.plugins-row-note`.
- Do NOT port: anything with `settings.services[p].integrations`, `isAllowed`, `setAllowed`, `/api/settings` posts, "Allow X in KeepHarness".

### P2 tests (`tests/admin-plugins-switches.spec.cjs`, mocked admin style of admin-customize.spec.cjs:34-48)
- `switch shows scope, source and reason` (writable and read-only row; disabled switch has the reason in its described-by note).
- `toggle posts once with the provider fingerprint` (assert the body: provider, item_id, scope, enabled, fingerprint; never `/api/settings`).
- `two providers on one row get two switches`; `catalog-only row has no switch`; `snapshot-only plugin appears as a row`.
- `409 replaces the snapshot and explains` (switch shows the server value from the 409 body).
- `422 and 502 show the message and keep the switch` (`aria-checked` unchanged).
- `no concurrent posts` (`maxInFlight` ≤ 1 under double click); `focus returns to the switch`.
- `deepseek pill has no switch`.

## P3 — #43 backend (seen map, notices, ack, run start)

Files: `control/provider_state.py`; `control/routes.py` (`POST_ROUTES["/api/provider-state/notices:ack"]`); `agent_service/services/queue_worker.py` (run start); NEW `tests/test_provider_state_notices.py`, NEW `tests/test_provider_state_run_start.py`.

### `<state>/provider-state-seen.json` (written only by control, `ControlStateRepository._replace`, 0600)
```json
{"version": 1,
 "entries": {
  "codex|sem-projeto": {
   "stat": "<fingerprint(watch_paths) hex>",
   "items": {"plugin:github@openai-curated": {"enabled": true, "source": "config.toml", "name": "github"}},
   "writes": {"plugin:github@openai-curated": {"before": true, "after": false, "at": "2026-10-08T10:40:00Z"}},
   "notices": [{"id": "n_3f9a0c1b2d4e5f60", "item_id": "plugin:github@openai-curated", "name": "github",
                "change": "changed", "before": false, "after": true, "source": "config.toml",
                "detected_at": "2026-10-08T10:42:00Z"}]}}}
```
### `<control_state>/provider-state-runs.json` (written only by the harness, same `_replace`)
```json
{"version": 1, "entries": {"codex|sem-projeto": {"stat": "<hex>", "detected_at": "2026-10-08T10:41:10Z"}}}
```
- Unknown `version`, unreadable or invalid JSON → treat as empty (log the code `provider_state_seen_unreadable` only); never raise into a GET or a run.
- Neither file goes into `CONFIGURATION_FILES`; no backup exclusion (no credentials). Do not add new files to state_merge.

### Service additions
- The seen map is loaded once into memory; every mutation is followed by a synchronous `_replace` with NO `await` between load-modify-save (atomic across coroutines; two provider locks can both mutate safely).
- `read` (extends P1): stat `fingerprint(adapter.watch_paths(root))` first (in the same thread call as read_state when not coalesced). No entry for the key → baseline (items + stat), no notices. Stat equal to the entry's → no diff. Stat changed → `diff_items` → merge notices → save the new items and stat. `external_changes` = the key's pending notices (from memory, even on a coalesced read).
- `diff_items(old_items, new_items, writes, exclude=None) -> list[notice]`: added (`before: null`), removed (`after: null`), changed (enabled differs); `reverted` when `writes[item].after == old value` and `new value == writes[item].before`; any detected change on an item clears its `writes` entry. `source` is the StateItem source (for removed: the old source).
- `detected_at`: if `runs.json[key].stat` differs from the entry's old stat and equals the new stat → its `detected_at`; else now.
- Merge: one pending notice per `item_id`; a new one keeps the old `before`; if `after == before` → drop it; cap 50 per key (oldest dropped). `id = "n_" + sha256(f"{provider}|{project}|{item_id}|{before}|{after}|{detected_at}").hexdigest()[:16]`.
- F12 inside `write` after success, same lock: diff the returned snapshot against seen excluding `item_id` (other differences become notices), then set `items[item_id]` from the snapshot, `writes[item_id] = {before: old enabled, after: enabled, at: now}`, `stat` = a fresh stat fingerprint. An own write never produces a notice.
- 409 path: the fresh read runs the same detection, so `external_changes` is filled.
- `async ack(provider, project_id, notice_ids) -> dict`: under the provider lock; remove matching ids from the key; unknown ids ignored; returns `{}`.
- `run_start_check(control_state: Path, provider: str, project_id: str, project_root: Path | None) -> None` (sync, module level): adapter for watch_paths only (`CodexStateAdapter()` / `ClaudeStateAdapter(control_state)`), `fingerprint(...)`, compare with seen (read-only) and runs; write runs only when the stat differs from both; no seen entry → nothing. Never reads or parses CLI files beyond stat.

### `POST /api/provider-state/notices:ack`
Body `{"provider": "codex", "project_id": "sem-projeto", "notice_ids": ["n_3f9a0c1b2d4e5f60"]}` → 200 `{}`. `notice_ids` must be a list of ≤100 str (each ≤40) → else 400 `invalid_request`; provider/project → 404 as P1. Under `manager.lock` (normal POST path).

### Run-start hook (queue_worker.py `run_job`, after `request_data = json.loads(row["payload"])`, before `service.execute`)
```python
await provider_state_run_check(service, row, request_data)  # new small function in queue_worker.py
```
- Only when `request_data.get("backend") in ("codex", "claude")` and `service.config.get("control_state_dir")`.
- Root: `service.config.get("projects", {}).get(row["project"], {}).get("root")` (None for sem-projeto).
- 5 s per-key coalescing in a module dict with `time.monotonic`; `await asyncio.to_thread(run_start_check, ...)`; `except Exception` → `logger.debug("provider-state run check skipped")` (no paths), never raises, never changes `row`, `request_data` or the run.

### P3 tests
`tests/test_provider_state_notices.py` (service + routes, fake homes, injected clock/wall):
- `test_first_read_is_baseline_without_notices`
- `test_external_edit_shows_once_until_ack` (edit fake config.toml / Claude settings.json: one notice; repeated GETs return the same id; ack clears; GET after ack is empty)
- `test_get_does_not_mark_seen`; `test_ack_unknown_ids_is_ok`; `test_ack_invalid_body_400`
- `test_own_write_never_shows` (F12) and `test_external_change_during_own_write_still_shows`
- `test_revert_shows_reverted` (POST off, then the fake file is edited back to on → `change == "reverted"`)
- `test_added_and_removed_items`; `test_change_back_drops_notice`; `test_notice_cap_50`
- `test_stat_unchanged_skips_diff` (spy on diff_items); `test_coalescing_reuses_snapshot_but_not_notices`
- `test_seen_file_mode_0600_and_atomic`; `test_corrupt_seen_file_is_empty`
- `test_no_credentials_in_seen_or_bodies` (seed `.claude.json` with a fake `oauthAccount` and MCP `env`/`headers` token strings; assert absent from the files, responses and caplog)
- `test_409_body_has_external_changes`
`tests/test_provider_state_run_start.py`:
- `test_run_start_records_detected_at_only_on_change`; `test_run_start_never_parses` (spy `read_state` not called); `test_run_start_without_baseline_writes_nothing`; `test_run_start_errors_never_raise` (unwritable dir); `test_control_uses_run_detected_at`; `test_run_job_calls_hook_once_per_5s` (monkeypatched check).

## P4 — #43 UI + Playwright

Files: `control/customize.js` (Plugins notice block), `control/admin.js` (Providers notices, toast, ack, refresh triggers), `control/index.html` (`<div id="provider-state-notices" hidden>` at the top of `#dashboard`, above `#configured-providers`), `control/admin.css`, NEW `tests/admin-provider-notices.spec.cjs`.
- A shared renderer `renderProviderNotices(container, providerNotices)` in admin.js (global, customize.js reuses it). Data: `external_changes` of the codex and claude GETs (sem-projeto).
- Text (revisit with Sophia; keep exact for tests): one → `Changed outside KeepHarness: Codex › plugin github@openai-curated was turned off (config.toml, 10:42).` (`added` → "was added", `removed` → "was removed", `on`/`off` from `after`; the item id is rendered in `<code>`; time is local HH:MM of `detected_at`). Reverted → `Reverted by Claude Code: plugin x is off again (.claude.json, 10:42). Your choice was on.` (CLI name from the provider). Several → `3 changes outside KeepHarness` + `<details>` with one line each.
- A "Dismiss" button acks all shown ids per provider (`POST provider-state/notices:ack` inside `action()`), then re-renders from the response-free local removal and a fresh GET.
- Toast: `HarnessUI.toast(text)` for notice ids not yet in an in-memory `Set` (page session); only from the Plugins and Providers screens. No `localStorage`/`sessionStorage`.
- Refresh: on entering `#plugins` or the providers panel (`renderPanel`), and on `window` `focus` / `document` `visibilitychange` (visible) while one of them is active. No `setInterval`/`setTimeout` polling.
- Text is set with `textContent`/`element()`, never `innerHTML` (names and sources come from files).

### P4 tests (`tests/admin-provider-notices.spec.cjs`, mocked `/api/provider-state*`)
- `single notice text on plugins and providers`; `several collapse with details`; `reverted text`
- `toast fires once per id per page session` (re-entry does not toast again; a new id does)
- `dismiss posts ack with the shown ids and hides the notice`
- `refresh on focus and visibilitychange, no polling` (count GETs over an idle 7 s: 0 beyond entry)
- `names render as text` (a name with `<img onerror>` stays text)

## Implementation checklist (each package)
- A failing test first; adapters untouched; no edit to conversation_service.py; no new dependency.
- Run only this package's tests plus test_provider_state*.py / the touched spec; full suites only before a merge.
- No credential, file content or `str(exc)` in logs; bodies only as specified.
- Clean up TMPDIR/basetemp and any server started.
