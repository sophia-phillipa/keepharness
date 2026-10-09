> Status: ACTIVE (accepted, 0.16.0)
> Job: When I open KeepHarness for the first time, I want to pick a theme and a few defaults and see which providers are already signed in, so I can start chatting without logging in again to accounts the CLIs already have.
> Scope: `control/routes.py`, `control/manager.py`, admin UI (`control/admin.js` + its HTML), `agent_service/ui_state.py`, `tests/test_first_run.py`, `tests/test_ui_state.py`, `tests/first-run-wizard.spec.cjs`, `tests/operator/areas/`
> Invariant docs: D-038/D-039 (facade over the CLIs' real state, single owner), D-023 (prefs in the backend store), #36 (CLI's own login)
> Decision rationale: [D-052](decisions/d-052-first-run-wizard-in-admin.md)
> Kill criteria: remove when provider setup needs no choices (every provider auto-detected and no defaults worth asking).

# First-run wizard

## 1. Why
A fresh state lands on the admin (`chooseTarget`, `desktop/main.cjs:647`: no provider, so no harness). Sophia wants a guided first open: theme, a few defaults, and a provider scan that **reuses existing logins** and never asks to log in when the CLI is already signed in.

| Choice | Rationale |
|---|---|
| Wizard in the admin UI | Only the admin is up on first run; scan, check, settings and the #36 login live there (`control/routes.py:759-770`). |
| Codex/Claude via `Manager.signed_in` (`control/manager.py:674`) | Asks the CLI (`codex login status`, `claude auth status --json`); they inherit the host env (`adapters/shared/provider_setup.py:29-30`), i.e. the user's real login (D-038). `discovery.credential_present` only tests files. |
| DeepSeek: key file present (`control/routes.py:207`) | No network; shown as "key saved, not verified". |
| Gemini: `selectedType` + credential file existence | `gemini.check` launches the CLI and mutates `manager.auth`/`provider_models` (`adapters/gemini/account.py:63-80`, `control/manager.py:733-739`); not a status read. |
| Control writes prefs directly into the owner's ui-state store | Needs a cross-process lock (below); no wait for the harness. |

## 2. How
1. Admin page load: `GET /api/first-run` → `{completed, completed_at, version}`. Not completed → wizard dialog over the providers page.
2. **Appearance**: theme from the `harness_ui` themes, previewed live.
3. **Providers**: `POST /api/first-run/scan`. Probes run concurrently (`asyncio.gather`), each under its own timeout, not under `manager.lock`, and never touch `manager.auth`/`provider_models`. Signed in → "Ready, using your existing login"; signed out → the existing #36 login button; not found → install hint; unverified → "key saved, not verified" / "credentials found, not verified".
4. **Defaults**: default model for `chat_selection` (from `/api/check` of signed-in providers only), `always_on_top` (desktop only), providers to enable.
5. **Finish**, in order: the UI calls the existing `/api/settings` (enable providers; may start the harness), then `POST /api/first-run` with prefs. `completed` is set only when both succeeded. A failed harness start leaves settings saved and `completed=false`, with the error shown. **Skip** → `POST /api/first-run` with no prefs.
6. **Run setup again**: a button on the admin providers page, which the harness shows inside Settings › System through its admin iframe (`agent_service/ui.js:9707-9733`), so the call stays on the admin origin → `POST /api/first-run:reset`.
7. **Legacy installs**: when the `first_run` field is absent, it is computed once (`completed` = some service enabled in settings) and persisted immediately; never recomputed.

### Cross-process lock (resolves the control/harness write race)
`agent_service/ui_state.py` serializes `update()` with an in-process `threading.Lock` (`:45`, used at `:294`); control and harness are separate processes. Replace it with `fcntl.flock(LOCK_EX)` on a sibling lock file `<store>.lock` in the same owner folder (not the store file itself: the atomic replace swaps the inode, so a lock on the store would bind to the old file). Held only around load → merge → atomic write; the atomic temp+rename write is kept; reads take no lock. Lock file created 0600, never deleted while in use.

### Contracts
- `GET /api/first-run` → `200 {completed: bool, completed_at: str|null, version: str}`. Local-only like every `/api`.
- `POST /api/first-run/scan` → `200 {providers: [{id: "codex"|"claude"|"deepseek"|"gemini"|"local", found: bool, signed_in: bool|null, detail: str}]}`. `detail` vocabulary: `signed_in`, `signed_out`, `not_installed`, `timeout`, `error`, `key_saved_unverified`, `credential_present_unverified`. Timeout: `discovery.command` gives up after 8 s and returns failure (`control/discovery.py:41-53`) → `signed_in: null, detail: "timeout"`, never `false`. Must-not: run `login`/`logout`/`gemini.check`; read token contents; copy or move credential files; return `identity` (`control/manager.py:705-712`), emails, org ids, paths or command output. Per-provider errors never fail the whole scan.
- `POST /api/first-run` `{prefs?: {theme?, chat_selection?, always_on_top?}}` → each key checked by `ui_state.validate(..., strict=True)`; invalid → `422` with the ui_state error codes, nothing written. Writes prefs, then the marker; returns `200 {completed: true, completed_at, version}`. Repeating it with the same body gives the same state.
- `POST /api/first-run:reset` → `200 {completed: false, completed_at: null, version}`.
- `ui_state.update(config, owner, changes)` — exclusive `flock` around read-modify-write; behavior otherwise unchanged.
- State: admin settings `first_run: {completed_at: str|null}`, written by the existing persistence layer.

### Failure modes
- Reusing `/api/check` for the scan: networked (DeepSeek balance, `control/manager.py:719-723`), lists models, mutates auth state. Only step 4 uses it, for signed-in providers.
- Showing `credential_present` as signed in: hides an expired login. Forbidden.
- Marking completed before settings/start succeeded: the next open skips the wizard with no provider. Forbidden.
- Logging: provider id + `detail` only (Claude status JSON carries `email`).
- `flock` on the store file itself: breaks after the first atomic replace. Use the sibling lock file.

### Integration points
d=1 `control/routes.py` dispatch tables, `Manager.signed_in`, `Manager.refresh`, admin providers page; d=2 `agent_service/ui_state.py` (lock change affects every `/v1/ui-state` PATCH), `desktop/main.cjs` `chooseTarget` (unchanged), #36 login flow; d=3 `agent_service/tour.js` autostart (starts on the harness after the wizard), `control/install_check.py` smoke (`GET /api/first-run` 200).

## 3. What
| Item | Value |
|---|---|
| Routes | `GET/POST /api/first-run`, `POST /api/first-run/scan`, `POST /api/first-run:reset` |
| Fixture env | `scripts/test-ui.sh` isolated state; stub CLIs on `PATH` answering `login status` (exit 0/1, or sleep for timeout) and `auth status --json` (`{"loggedIn":true|false}`) |
| Operator area | `tests/operator/areas/first-run.*` per `docs/operator-suite.md` |

### Test protocol
- `tests/test_first_run.py`: stub CLIs → codex `signed_in: true`, claude `false`, slow stub → `null/timeout`; DeepSeek key → `key_saved_unverified`; Gemini → `credential_present_unverified` and the stub records no launch; no `login` argv recorded; response has no `identity`/email; scan doesn't change `manager.auth`; invalid theme → 422, nothing written; reset; legacy field computed once and persisted.
- `tests/test_ui_state.py`: two processes (`multiprocessing`) each PATCH a different key 50 times → both final values present, no lost update.
- `tests/first-run-wizard.spec.cjs`: fresh state shows the wizard; signed-in provider has no login button; signed-out Claude shows the #36 button; finish → reload without wizard; theme applied on the harness; "Run setup again" from Settings › System reopens it; no new `localStorage` keys.
- Operator area: fixture mode, plus real mode on the installed app (D-029 visible pass; Codex, Claude and DeepSeek, SKIP with reason when not connected).

## See also
[D-052](decisions/d-052-first-run-wizard-in-admin.md), [scripted-install.md](scripted-install.md), [desktop-tray-menu.md](desktop-tray-menu.md), [provider-facade-design.md](provider-facade-design.md).
