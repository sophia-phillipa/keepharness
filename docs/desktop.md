# Desktop client (Electron)

`desktop/` is a thin Electron shell around the local web app, following the pattern proven in KeepGlide (Electron 44.4.1, sandboxed renderer, navigation policy).

- **Start**: `desktop/launch-linux.sh`. If the admin is not answering on `127.0.0.1:8094` (`KEEPHARNESS_ADMIN_PORT`), it starts `python -m control` with `KEEPHARNESS_PYTHON` (default: the environment `install.sh` created, found with `python3 control/product.py --field venv`, else `.venv/bin/python`) and stops it on quit; an admin that was already running is left alone.
- **Window**: opens the harness (`127.0.0.1:8095`, `KEEPHARNESS_PORT`) when it answers within 15 s, otherwise the admin so a provider can be set up. The port comes from `~/.local/share/keepharness/runtime.json` (`port`); an explicit environment value wins, and missing or invalid values fall back to 8095. App-origin popups, including Admin, use one reusable second window, restoring it before focus when minimized.
- **Navigation policy** (`policy.cjs`): only the admin and harness origins load in the window; navigation and redirects to any other `http` or `https` link opens in the system browser; other schemes and URLs with credentials are dropped. The renderer has no Node integration, context isolation and the Chromium sandbox are on, `<webview>` is off.
- **Port trust** (Linux): before loading an admin or harness that is already running, the app reads `/proc/net/tcp` and `/proc/net/tcp6` and refuses to open a port whose loopback listener belongs to another user (or cannot be found), with a dialog. A responding harness must also identify `product: "keepharness"` in `/v1/version` before authentication or loading; foreign or malformed responses are refused. Concurrent navigations share one in-flight identity check and refusal dialog. Where `/proc/net/tcp` does not exist the check is skipped.
- **Permissions**: only clipboard writes and notifications from app origins are allowed; all other permission requests and checks are denied.
- **Single instance**: a second launch focuses the existing window.
- **Bazzite**: the Python environment lives in the development container, so the menu entry runs `distrobox-enter -n <container> -- …/desktop/launch-linux.sh`.

Setup: `cd desktop && npm install --prefer-offline && node node_modules/electron/install.js` (the Electron archive comes from `~/.cache/electron` when present). Tests: `cd desktop && env -u DISPLAY -u WAYLAND_DISPLAY npm test`. The main-process suite uses fake Electron windows and isolated state; it never opens a real window.

Not yet: a frozen Python backend, AppImage, .deb, signing, or auto-update.

## Runtime recovery and desktop state

- Python is checked with a bounded `--version` invocation before starting the service or owner CLI. Failure when starting the service shows “Run install.sh, or set KEEPHARNESS_PYTHON” and quits. Owner CLI failure is logged and returns no enrollment link; an attached harness still loads without Python or an enrollment cookie.
- Renderer termination offers Reload / Quit. An unresponsive window offers Wait / Reload. A renderer crash during that dialog is remembered; choosing Wait then offers Reload / Quit. Unexpected exit of the service started by this client shows a redacted stderr tail and Restart service / Quit; attached services remain independently managed.
- The KeepHarness menu contains About / Quit, Edit roles, zoom and full screen. Packaged builds expose neither Reload nor DevTools; development builds expose DevTools. Quit preserves the existing busy-work confirmation.
- Main-window normal bounds and maximized state persist atomically through `userData/window-state.json.tmp` and rename. Restored bounds use the display work area with greatest positive overlap, preserving size and maximized state before clamping. Bounds with no overlap use centered defaults on the primary display. Minimum window size is 960 × 640.
- All windows use one factory with identical renderer isolation and navigation policies. The splash rejects navigation and popups, and closes as soon as the admin answers. Content windows show on the first of `ready-to-show` and `did-finish-load`; saved maximization is applied immediately before showing, never during hidden enrollment.
- Main-process diagnostics use `~/.config/KeepHarness/logs/main.log`, at most 1 MiB with one rotation (`main.log.1`), ISO timestamps and redaction of secret, ticket, cookie, Authorization, API-key, token and password values in headers, parameters and JSON-like diagnostics. `admin=` cookie values are redacted while ordinary admin log prose is preserved. Desktop profile data uses the layout described below.

## Portable Linux package lifecycle (WP-18 S3)

Build with `KEEPHARNESS_ELECTRON_DIST=/path/to/electron/dist scripts/package-desktop-linux.sh`
from a clean Git tree with a valid HEAD; running outside a Git repository is refused.
The installer requires the manifest commit to contain exactly 40 hexadecimal characters.
The exact Electron archive must be in `~/.cache/electron/*/`, or
selected with `KEEPHARNESS_ELECTRON_ZIP`. Its SHA-256 is verified against the committed
release SHASUMS256.txt, then every runtime file is compared to that archive. A dirty
tree, changed runtime, existing output folder, or invalid version stops the build.
The package contains `resources/app.asar`, `build-manifest.json`, and exhaustive
`SHA256SUMS`. The installer checks every package member before copying. Checksums
identify corruption; they are not a signature or protection against an attacker who
can replace both files and checksums. Runtime startup refuses missing or invalid
provenance. RunAsNode, NODE_OPTIONS and CLI inspect are disabled; OnlyLoadAppFromAsar
and embedded ASAR integrity validation are enabled. Electron does not enforce ASAR
integrity on Linux. ASAR and fuses use the exact npm versions in the desktop lockfile.

Run the package's `./install-desktop-linux.sh` as the target user. Python 3 and Linux
`flock` semantics are required. Version directories are immutable:
`~/.local/opt/keepharness-<version>`. The menu uses the atomic
`~/.local/opt/keepharness/current` link; `previous` records the prior selection.
On the first upgrade from a legacy installation without `current`, an exact versioned
`Exec=` line in `keepharness.desktop` identifies the marked installation to preserve as
`previous`. Package paths are canonicalized, allowing symlinked ancestors such as
Fedora Atomic's `/home` while still refusing symlinks inside the package.
An identical reinstall preserves the version; different contents are refused.
`./install-desktop-linux.sh --rollback` swaps current and previous, and refuses a
missing or broken previous installation. Pruning follows installation order, keeps
current, previous and any running versions, and reports failures without undoing a
successful install. Downgrades are supported. A nonblocking lifecycle lock serializes
installation, rollback, pruning, and stage recovery.

`./install-desktop-linux.sh --uninstall --dry-run` prints the deletion plan.
`--uninstall --yes` applies it; otherwise a terminal confirmation is required.
The complete plan is validated before deletion: canonical user HOME, ordinary owned
roots and candidates, strict version names and ownership manifests. Unmarked folders
and foreign desktop entries remain. Symlinked candidates, mount boundaries, a live
app process or local SingletonLock, and case aliases of the Python config refuse the
operation. The current/previous links are unlinked, never followed; their parent is
removed only when empty. No previous.desktop is restored. Interrupted mktemp stages
are recovered only under the lock. This is a local, cooperating-user lifecycle lock,
not protection against another process maliciously racing filesystem mutations.

All new Electron data and caches live in `~/.config/KeepHarness` (cache:
`xdg-cache`). Uninstall removes this desktop profile and the legacy
`~/.config/keepharness/electron` only; it preserves Python configuration, state,
`local.key`, and systemd units. The launcher prints the Ubuntu AppArmor user-namespace
hint before launch when applicable; it never disables the Chromium sandbox.

The browser shortcut is `keepharness-browser.desktop`. Installing the desktop removes
only the exact owned browser shortcut; Python registration skips it while `current`
points to a marked install. Python rollback never removes the desktop client's entry.
Installation leaves a symlinked browser shortcut untouched and prints a note.
Desktop uninstall always leaves the browser shortcut untouched.
After desktop uninstall, run `./install.sh` to get the browser entry back.
