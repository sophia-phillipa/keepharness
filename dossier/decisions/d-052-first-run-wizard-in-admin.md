# D-052 — First-run wizard in the admin, status-only provider scan through `Manager.signed_in`

Status: accepted. Date: 2026-10-09. Decided by: Sophia (bypass session; JEV + Opus review stand in for acceptance). Spec: [first-run-wizard.md](../first-run-wizard.md). Builds on D-023, D-038, D-039 and #36.

## Context
On a fresh state the desktop app opens the admin, because the harness is not running yet. Sophia wants the first open to show a wizard (theme, a few defaults, an initial provider scan) that reuses the existing CLI logins: "reuse the keys you already have so I skip having to log in for you".

## Decision
The wizard lives in the admin UI. The scan reports Codex and Claude sign-in through `Manager.signed_in`, which asks the CLI itself. DeepSeek is "key saved, not verified". Gemini is "credential present, not verified" (from `selectedType` and the file's existence; `gemini.check` is never called). Probes run concurrently with explicit timeouts, never return `identity`, and run no login, logout or credential copy. The control process writes prefs directly into the ui-state store, serialized across processes by an exclusive `fcntl.flock` on a sibling lock file, which replaces the in-process `threading.Lock`. Finish calls `/api/settings` first; the marker is set only after everything has succeeded. Theme and defaults go into the owner's WP6 ui-state store. Completion is a backend marker in the admin settings. The wizard can be run again from Settings › System › Providers. Upgraded installs that already have an enabled provider count as completed. This is computed once and persisted.

## Rationale
The admin is the only surface that is up on first run, and it already owns the scan, the check and the #36 login. Codex and Claude inherit the host environment (`adapters/shared/provider_setup.py:29-30`), so their status is the user's real login: reusing it requires no copying. `discovery.credential_present` only tests file existence and would hide an expired login.

## Alternatives considered
- A wizard in the harness UI next to the tour: the harness is not running without a provider, so the wizard could not run first. Rejected.
- A scan that reuses `/api/check`: it makes network calls (DeepSeek balance, model lists) and is slow. Kept only for step 4 (choosing the default model).
- Copying tokens into KeepHarness homes: violates D-038 and the request. Rejected.

## Impact
New admin routes and an admin dialog, a settings field, `ui_state` writes from the control process, Playwright and operator-suite coverage, and an `install_check` smoke step.

## Follow-up actions
Implement the backend with `python-code-engineer` and the UI with `frontend-code-engineer`. Revisit if Gemini or local need a login step.

## Review 2026-10-09
An Opus security and API review made these changes. The cross-process write race is resolved with `flock`; the lock sits on a sibling `.lock` file, because the atomic replace swaps the store's inode. The Gemini check is status-only. `identity` is never returned, and timeouts map to `null`. Probes use `asyncio.gather`, outside `manager.lock`. Finish ordering was set, and a failed start leaves the wizard not completed. The legacy rule is persisted once. Response shapes are aligned. "Run setup again" lives on the admin providers page, which Settings › System embeds through its admin iframe (`agent_service/ui.js:9707-9733`), so no cross-origin call is needed.
