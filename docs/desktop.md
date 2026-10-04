# Desktop client (Electron)

`desktop/` is a thin Electron shell around the local web app, following the pattern proven in KeepGlide (Electron 44.4.1, sandboxed renderer, navigation policy).

- **Start**: `desktop/launch-linux.sh`. If the admin is not answering on `127.0.0.1:8094` (`KEEPHARNESS_ADMIN_PORT`), it starts `python -m control` with `KEEPHARNESS_PYTHON` (default: the environment `install.sh` created, found with `python3 control/product.py --field venv`, else `.venv/bin/python`) and stops it on quit; an admin that was already running is left alone.
- **Window**: opens the harness (`127.0.0.1:8095`, `KEEPHARNESS_PORT`) when it answers within 15 s, otherwise the admin so a provider can be set up. The port comes from `~/.local/share/keepharness/runtime.json` (`port`); an explicit environment value wins, and missing or invalid values fall back to 8095. App-origin popups, including Admin, use one reusable second window.
- **Navigation policy** (`policy.cjs`): only the admin and harness origins load in the window; navigation and redirects to any other `http` or `https` link opens in the system browser; other schemes and URLs with credentials are dropped. The renderer has no Node integration, context isolation and the Chromium sandbox are on, `<webview>` is off.
- **Port trust** (Linux): before loading an admin or harness that is already running, the app reads `/proc/net/tcp` and `/proc/net/tcp6` and refuses to open a port whose loopback listener belongs to another user (or cannot be found), with a dialog. A responding harness must also identify `product: "keepharness"` in `/v1/version` before authentication or loading; foreign or malformed responses are refused. Where `/proc/net/tcp` does not exist the check is skipped.
- **Permissions**: only clipboard writes and notifications from app origins are allowed; all other permission requests and checks are denied.
- **Single instance**: a second launch focuses the existing window.
- **Bazzite**: the Python environment lives in the development container, so the menu entry runs `distrobox-enter -n <container> -- …/desktop/launch-linux.sh`.

Setup: `cd desktop && npm install --prefer-offline && node node_modules/electron/install.js` (the Electron archive comes from `~/.cache/electron` when present). Tests: `cd desktop && env -u DISPLAY -u WAYLAND_DISPLAY npm test`. The main-process suite uses fake Electron windows and isolated state; it never opens a real window.

Not yet: a packaged build (electron-builder with a frozen Python backend, as in KeepGlide, including its licence audit).

## Runtime recovery and desktop state

- Python is checked with a bounded `--version` invocation before starting the service or owner CLI. Failure shows “Run install.sh, or set KEEPHARNESS_PYTHON”.
- Renderer termination offers Reload / Quit. An unresponsive window offers Wait / Reload. Unexpected exit of the service started by this client shows a redacted stderr tail and Restart service / Quit; attached services remain independently managed.
- The KeepHarness menu contains About / Quit, Edit roles, zoom and full screen. Packaged builds expose neither Reload nor DevTools; development builds expose DevTools. Quit preserves the existing busy-work confirmation.
- Main-window normal bounds and maximized state persist atomically through `userData/window-state.json.tmp` and rename. Restored bounds are clamped to the containing display work area; removed displays use centered defaults on the primary display. Minimum window size is 960 × 640.
- All windows use one factory with identical renderer isolation and navigation policies. The splash rejects navigation and popups, and closes as soon as the admin answers. Content windows show on the first of `ready-to-show` and `did-finish-load`.
- Main-process diagnostics use `~/.config/KeepHarness/logs/main.log`, at most 1 MiB with one rotation (`main.log.1`), ISO timestamps and redaction of secret, ticket and cookie values. Desktop profile paths are unchanged.
