> Status: ACTIVE (accepted, 0.16.0)
> Job: When KeepHarness runs in the background on KDE, I want to reach it from the panel and pin it above other windows, so I can start or resume work without hunting for the window.
> Scope: `desktop/main.cjs`, `desktop/main.test.cjs`, `agent_service/ui_state.py`, `agent_service/ui-prefs.js`, `scripts/package-desktop-linux.sh`
> Invariant docs: D-023/D-024 (preferences in the backend store), D-029 (visible pass), issue #18
> Decision rationale: [D-050](decisions/d-050-desktop-tray-and-window-menu.md)
> Kill criteria: remove the tray if Electron drops SNI support on Linux or KDE stops showing SNI items; remove always-on-top if KWin ignores it and #18 is closed as won't-fix.

# Desktop tray icon and window menu

## 1. Why
Sophia asked for "an entry in the menu and in the toolbar":
- **Launcher menu entry** (KDE application menu): the `.desktop` file, delivered by [scripted-install.md](scripted-install.md).
- **Window menu** (`installMenu`, `desktop/main.cjs:154`): gains `File › New chat` and `View › Always on top` (closes #18).
- **Toolbar = KDE system tray** (StatusNotifierItem): an Electron `Tray` with Open KeepHarness, New chat, Always on top, Quit.

| Choice | Rationale |
|---|---|
| Electron `Tray`, no new dependency | Electron on Linux registers an SNI item over D-Bus; Plasma shows it. |
| Every action in the context menu | Linux SNI click events are unreliable; `setContextMenu` is the supported path. |
| `always_on_top` in the WP6 store, written only through `HarnessPrefs.set` | D-023; one writer, no second PATCH path. |
| No new preload IPC | `desktop/main.test.cjs:551` pins the preload to two wrappers; main uses `executeJavaScript` on the harness page instead. |
| Close behavior unchanged | The `Tray` constructor may not throw when no SNI watcher exists, so "tray visible" is undetectable; hide-to-tray could strand the window. Out of scope. |

## 2. How
Main process only. `installTray()` runs in `start()` (`desktop/main.cjs:655`) after `createWindow('main')`. Helper `onHarness()` = `win` exists and `new URL(win.webContents.getURL()).origin === new URL(harnessUrl).origin`.
- **New chat**: `surface(win)`, then `win.webContents.executeJavaScript("document.getElementById('new')?.click()")` — the existing button (`agent_service/index.html:4`; handlers near `agent_service/ui.js:10202`/`:10211`). Enabled only when `onHarness()`; disabled (both menus) while the admin is loaded. No URL hash is added.
- **Always on top**: `setPinned(flag)` → `win.setAlwaysOnTop(flag)`, update both menus' checked state, then when `onHarness()` run `executeJavaScript("HarnessPrefs.set('always_on_top', " + JSON.stringify(flag) + ")")`. On the admin, the window state changes but is not persisted.
- **Restore**: after the harness page loads, main reads `executeJavaScript("HarnessPrefs.get('always_on_top', false)")` once (after `HarnessPrefs` is ready) and applies it.
- Menus are refreshed on `did-navigate` so the enabled state follows the loaded origin.

### Contracts
- `installTray(): Tray | null` — one tray per process; icon from the asar (`build/icons/32x32.png`); items `Open KeepHarness`, `New chat`, `Always on top` (checkbox), `Quit`. Must-not: create a second tray on `second-instance`; read or log tokens; add IPC channels. Errors: constructor throws → `null`, one log line, app as today.
- `setPinned(flag: boolean): void` — window, window menu and tray agree; the preference is written once per change, only through `HarnessPrefs.set`. Must-not: retry in a loop; PATCH `/v1/ui-state` from main. Errors: `executeJavaScript` rejects → window state kept, logged.
- `ui_state.SCHEMA["always_on_top"] = flag`; the writer is registered in `ui-prefs.js` and listed in `dossier/releases/v0.16.0.md`.
- Tray `Quit` → `app.quit()`, keeping the `before-quit` confirmation (`desktop/main.cjs:732`).
- Window close and `window-all-closed` (`desktop/main.cjs:731`) unchanged.

### Failure modes
- KWin on Wayland may ignore `setAlwaysOnTop` for an unfocused client: the item reflects the request; the visible pass records what KWin does. Do not write a KWin window rule from the app.
- Distrobox without `DBUS_SESSION_BUS_ADDRESS`: no tray shown; the window menu still works.
- Someone adds `#new` routing or a preload channel for New chat: forbidden; the button click is enough and the preload is pinned.
- Running `executeJavaScript` while the admin is loaded would throw on `HarnessPrefs`: guarded by `onHarness()`.

### Integration points
d=1 `start`, `installMenu`, `surface`, `before-quit`; d=2 `agent_service/ui_state.py` schema, `ui-prefs.js` key list, `tests/test_ui_prefs_contract.py`; d=3 the asar copy list in `scripts/package-desktop-linux.sh:34` must include `build/icons/32x32.png`.

## 3. What
| Item | Value |
|---|---|
| New pref key | `always_on_top` (bool) |
| Asar files | add `build/icons/32x32.png` to the loop at `scripts/package-desktop-linux.sh:34` |
| IPC | none added |

### Test protocol
- `cd desktop && node --test main.test.cjs`: menu template has `New chat` and `Always on top` (checkbox); tray template has four items; `installTray` returns null when `Tray` throws; New chat disabled when the loaded origin is the admin; the preload still exposes exactly two wrappers.
- `.venv/bin/python -m pytest -q tests/test_ui_state.py tests/test_ui_prefs_contract.py`: `always_on_top` accepted, non-bool rejected.
- Visible pass (D-029) on the installed app on KDE: tray visible, each item works, the pin survives a restart; KWin behavior recorded in the release notes.

## See also
[D-050](decisions/d-050-desktop-tray-and-window-menu.md), [codex-parity-design.md backlog](codex-parity-design.md#backlog-requested-not-scheduled), [scripted-install.md](scripted-install.md).
