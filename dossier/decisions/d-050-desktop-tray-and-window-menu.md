# D-050 — Desktop tray icon (SNI) and window-menu items, including Always on top

Status: accepted. Date: 2026-10-09. Decided by: Sophia (bypass session; JEV + Opus review stand in for acceptance). Spec: [desktop-tray-menu.md](../desktop-tray-menu.md). Scope: #18 and the "menu and toolbar" request.

## Context
Sophia: "we do need an entry in the menu, and in the toolbar". The launcher menu entry is the `.desktop` file (D-051). The window menu (`desktop/main.cjs:154`) has About/Quit, Edit and View only. #18 asks for a checkable Always on top item stored in the WP6 store. The host is KDE Plasma on Wayland; the app may run in a distrobox.

## Decision
Add an Electron `Tray` (Open, New chat, Always on top, Quit) and add `File › New chat` and `View › Always on top` to the window menu. The pin is stored in a new WP6 key `always_on_top`, written only by `HarnessPrefs.set` through `executeJavaScript` on the harness page. New chat clicks the existing `#new` button through `executeJavaScript` and is disabled while the admin is loaded. No preload IPC channel is added. Closing the window works as it does today. If the tray cannot be created, the app behaves as today.

## Rationale
Electron registers a StatusNotifierItem on Linux, which Plasma shows in the system tray, so no new dependency is needed. Linux tray clicks are unreliable, so every action lives in the context menu. Storing the pin in the backend store follows D-023.

## Alternatives considered
- KWin window rule (like the host's `claude-always-on-top`): reliable on KWin, but it is host configuration written outside the app and is not portable. Rejected as the app feature; kept as a documented workaround if KWin ignores `setAlwaysOnTop`.
- Plasma panel widget / launcher pin only: no actions, no pin toggle. Rejected.
- Tray via libappindicator or a helper process: adds a dependency for no gain on Plasma. Rejected.

## Impact
`desktop/main.cjs`, `agent_service/ui_state.py` and `ui-prefs.js` (new key), the asar file list in `scripts/package-desktop-linux.sh`, desktop node tests and a visible pass on KDE.

## Follow-up actions
Implement it with `frontend-code-engineer`. Record KWin's behavior for an unfocused pinned window in `dossier/releases/v0.16.0.md`. Revisit if KWin ignores the pin: then offer the KWin-rule workaround as a documented opt-in.

## Review 2026-10-09
An Opus security and API review made these changes. New preload IPC channels are forbidden (`desktop/main.test.cjs:551` pins two wrappers). The `#new` URL hash was dropped, because no handler exists for it: New chat clicks the button instead. The pin has a single writer (`HarnessPrefs.set`), with no PATCH from main. Hide-on-close was removed, because a missing SNI watcher cannot be detected. The tray icon was added to the asar list (`scripts/package-desktop-linux.sh:34`).
