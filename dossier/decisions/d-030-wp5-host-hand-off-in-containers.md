# D-030 — WP5 hand-off detection and opening on the host inside distrobox

Status: accepted. Date: 2026-10-06. Decided by: Sophia, after an Opus security review. Refines [D-026](d-026-wp5-desktop-hand-off.md). Spec: [Codex-app parity design](../codex-parity-design.md). Release notes: [v0.16.0](../releases/v0.16.0.md).

## Context

The KeepHarness desktop app runs inside the distrobox container `claude-ubuntu`, while the ChatGPT (`codex://`) and Claude (`claude://`) desktop apps are installed on the host. Electron's `app.getApplicationNameForProtocol` reads only the container's XDG database, so `handoffApps()` returned an empty list and the "Continue in another app" offer never appeared. `shell.openExternal` would also run the container's `xdg-open`, which is a symlink to `/usr/bin/distrobox-host-exec`.

## Decision

1. **Host mode** is true when `/run/.containerenv` exists, `/usr/bin/host-spawn` exists and the first absolute `PATH` entry containing `xdg-open` resolves (`realpath`) to `/usr/bin/distrobox-host-exec`. It is memoized per launch; any error means false. It is a heuristic, not a security control.
2. **Detection in host mode** asks the host: `execFile('/usr/bin/host-spawn', ['--no-pty', 'xdg-mime', 'query', 'default', 'x-scheme-handler/<scheme>'])` with a 3 s timeout, a 1 KiB output cap, a minimal environment (`PATH`, `HOME`, `XDG_RUNTIME_DIR`, and `DBUS_SESSION_BUS_ADDRESS` only when it is a user-bus socket path) and no shell. The app counts as installed only when the command exits 0 and the trimmed output is one `*.desktop` id. The host answer wins; Electron is not consulted. Any error, timeout or odd output means not installed.
3. **Opening in host mode always uses the constant short URL** (`codex://threads/new`, or the existing constant Claude URL with the fixed short request, `handoffShortUrl` in `desktop/policy.cjs`), checked against a frozen list of exactly those two strings and opened with `host-spawn --no-pty xdg-open <url>`, spawned with stdio ignored so the opened app never holds KeepHarness pipes; success is host-spawn exiting 0 within 8 s, and on timeout KeepHarness reports a failure without killing anything. The renderer gets `mode: 'short'` and runs its existing clipboard flow. No user text ever reaches a host command.
4. Outside host mode nothing changes: Electron detection and `shell.openExternal` with `handoffUrl`.

## Rationale

- The link opens on the host, so the host has to be asked whether the app exists.
- A constant argument vector removes the injection surface: nothing the user, the model or a project file wrote can shape a host command line.
- `distrobox-host-exec` runs a version check that can download `host-spawn` with `sudo`, unverified. Calling the existing `/usr/bin/host-spawn` directly avoids that.
- Electron's `openExternal` on Linux invokes `xdg-open` found through `PATH` (`shell/common/platform_util_linux.cc`, main branch), so it cannot be pointed at the host reliably.

## Alternatives

- Put the prompt in the URL with a character allowlist: rejected by Sophia, because user text would still reach the host's `xdg-open`.
- Open through `distrobox-host-exec`: rejected, for the unverified `sudo` download above.
- Detect through `distrobox-host-exec`: rejected, same reason.
- No detection inside containers: rejected, the feature would be unusable on this machine.

## Impact

`desktop/main.cjs` (host mode, detection, open), `desktop/policy.cjs` (`handoffShortUrl`), `desktop/main.test.cjs` and `desktop/policy.test.cjs`. The renderer, preload and IPC shapes are unchanged.

## Follow-up

Revisit if the desktop is installed on the host, if it ships as a Flatpak (the OpenURI portal would replace this), or if distrobox drops the `xdg-open` symlink.
