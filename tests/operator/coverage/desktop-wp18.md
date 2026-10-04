# Coverage: desktop-wp18 (`tests/operator/areas/19-desktop-wp18.cjs`)

WP-18 as a person sees it in the packaged desktop app. Every launch uses a throwaway HOME (userData is proved to be inside it by a probe on a private Xvfb display before the first visible launch), test ports 18520 (admin) and 18521 (harness), a Claude stand-in CLI (`fixture/fake_provider.py`) and no cloud call.

How to run: package once (`scripts/package-desktop-linux.sh`), then make an inspect-enabled copy for Playwright (the production package turns the NodeCliInspect fuse off, so Playwright cannot attach): copy the package folder and flip only `EnableNodeCliInspectArguments` with `@electron/fuses`. Run with `--target browser --visible --app <copy>/keepharness-bin --areas desktop-wp18` (the area closes the runner's own Chromium window at once). `WP18_BACKEND` = a source tree with the S4 and S5 code (default: this repository), `WP18_SLICES` = `s2,s5,s4,s1,s3` subset, `WP18_PACKAGE` = the production package for the installer journeys.

| Requirement | Check(s) | Note |
|---|---|---|
| S2 splash first, then one window, no flicker | `s2.fresh-launch`, `s2.splash-then-one-window` | Window shown once, never hidden; fails on the blank gap (see defects) |
| S2 branded menu, no Reload/DevTools when packaged | `s2.menu` | Native menu read from the main process |
| S2 Admin in ONE reusable second window | `s2.admin-second-window`, `s2.admin-window-close-reopen` | Opened twice: still 2 windows |
| S2 size/position/maximized restored | `s2.bounds-save`, `s2.bounds-restore`, `s2.maximized-restore`, `s2.min-size` | Real quit and relaunch; 960x640 minimum |
| S2 renderer-crash dialog | `s2.renderer-crash` | Dialog answered through the Electron API (Reload); Playwright cannot follow a crashed page, so the reload is read from the main process |
| S2 backend-exit dialog | `s2.backend-exit` | SIGTERM to the app's own backend child; "Restart service" answered through the API |
| S2 foreign-product refusal | `s2.foreign-product` | Tiny server answering `product: other-product`; native dialog drawn, closed through the window manager; app exits; the foreign page was never requested |
| S2 own credential-requiring harness accepted on a fresh install (fix 0b8e9db) | `s2.accepts-own-harness` | Also proves the harness refuses `/v1/version` without the owner session (401/403) |
| S2 main.log rotation and redaction | `s2.log-rotation-redaction` | Backend stderr stand-in writes 2.2 MB, then fake secrets, Authorization and Cookie lines |
| S4 last_exit next to startup_error | `s4.last-exit-and-startup-error` | Harness SIGKILLed 3 times: both shown, startup error first |
| S4 log-tail panel | `s4.log-tail` | Initial hint, load on demand, read-only note, no secret |
| S4 ffmpeg/bwrap/prlimit rows with hints | `s4.environment-rows` | PATH is a sandbox folder: ffmpeg and bwrap missing, prlimit present |
| S5 About build label | `s5.about-build-label` | Compared with `build-manifest.json`; native box closed through the window manager |
| S5 title follows the conversation | `s5.title-follows-conversation` | Two conversations, switch back |
| S5 last route restored | `s5.route-save`, `s5.route-restore` | `/?conversation=<id>` |
| S5 busy taskbar indicator | `s5.busy-indicator` | The `setProgressBar`/`setBadgeCount` calls are observed; the taskbar itself is **not observable** from a test (KDE shell) |
| S5 app-origin download gets a save dialog | `s5.download-app-origin` | Admin "Export saved configuration" (blob:); dialog default path checked and a native save window seen (when not seen the step fails) |
| S5 foreign download refused | `s5.download-foreign` | Page link to another origin is handed to the browser (recorded, never opened); `downloadURL` of a foreign and a `data:` URL are refused |
| S1 restart notice after a Python change, none after a UI-only change | `s1.baseline`, `s1.ui-only-change`, `s1.python-change` | Edits only in a copy of the code made for the run (the tracked files are never touched) |
| S3 installer links, entry, no browser entry, rollback, uninstall | `s3.install-first`, `s3.install-second`, `s3.rollback`, `s3.uninstall-dry-run`, `s3.uninstall`, `s3.no-browser-entry-from-python-install` | Throwaway HOME, no window; a second package is made by copying the first and re-signing its sums |
| Visual polish at 960x640 and 1280x800, light and dark | `s2.fresh-admin-visual`, `s4.admin-visual`, plus PNGs in `shots/` | Own screenshots in the output folder |

Not covered / not observable
- Taskbar progress and badge rendering (shell-owned); only the Electron calls are observed.
- Native dialogs cannot be clicked by Playwright: most are answered through the Electron API and only About, the foreign-product box and the save dialog are drawn and closed through the window manager (`_NET_CLOSE_WINDOW`).
- Unresponsive-page dialog (needs a hung renderer), a Python-missing hint and a foreign-user-owned port (needs another account): covered by `desktop/main.test.cjs`.
- Real providers and model replies: stand-in CLI only.
- The production fuse state: Playwright needs the inspect copy; `@electron/fuses read` on the production package is the check for that.
