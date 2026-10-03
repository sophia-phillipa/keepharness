# Desktop client (Electron)

`desktop/` is a thin Electron shell around the local web app, following the pattern proven in KeepGlide (Electron 44.4.1, sandboxed renderer, navigation policy).

- **Start**: `desktop/launch-linux.sh`. If the admin is not answering on `127.0.0.1:8094` (`KEEPHARNESS_ADMIN_PORT`), it starts `python -m control` with `KEEPHARNESS_PYTHON` (default `.venv/bin/python`) and stops it on quit; an admin that was already running is left alone.
- **Window**: opens the harness (`127.0.0.1:8095`, `KEEPHARNESS_PORT`) when it answers within 15 s, otherwise the admin so a provider can be set up. Settings › System shows the admin inside the same window.
- **Navigation policy** (`policy.cjs`): only the admin and harness origins load in the window; any other `http` or `https` link opens in the system browser; other schemes and URLs with credentials are dropped. The renderer has no Node integration, context isolation and the Chromium sandbox are on, `<webview>` is off.
- **Port trust** (Linux): before loading an admin or harness that is already running, the app reads `/proc/net/tcp` and `/proc/net/tcp6` and refuses to open a port whose loopback listener belongs to another user (or cannot be found), with a dialog. Where `/proc/net/tcp` does not exist the check is skipped.
- **Permissions**: every web permission request (camera, microphone, location, notifications, clipboard and the rest) is denied, and so are permission checks; the app needs none of them.
- **Single instance**: a second launch focuses the existing window.
- **Bazzite**: the Python environment lives in the development container, so the menu entry runs `distrobox-enter -n <container> -- …/desktop/launch-linux.sh`.

Setup: `cd desktop && npm install --prefer-offline && node node_modules/electron/install.js` (the Electron archive comes from `~/.cache/electron` when present). Tests: `cd desktop && npm test` (policy) and an Electron smoke run under `xvfb-run`.

Not yet: a packaged build (electron-builder with a frozen Python backend, as in KeepGlide, including its licence audit).
