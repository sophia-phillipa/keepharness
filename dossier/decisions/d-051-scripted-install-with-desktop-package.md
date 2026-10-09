# D-051 — `install.sh` installs the desktop package through the existing desktop installer

Status: accepted. Date: 2026-10-09. Decided by: Sophia (bypass session; JEV + Opus review stand in for acceptance). Spec: [scripted-install.md](../scripted-install.md).

## Context
`install.sh` installs the venv and user service; the desktop app and its menu entry need a separate package build (`scripts/package-desktop-linux.sh`, which needs a clean tree, node and the pinned Electron zip) followed by the package's own `install-desktop-linux.sh`. Sophia requires that the whole install run from a script, with no AI help, and that the tests cover it.

## Decision
After the service step, `install.sh` takes the desktop package from `KEEPHARNESS_DESKTOP_PACKAGE`, else from `dist/keepharness-<VERSION>-linux-x64`. Both sources must match the pyproject version. It never builds and never downloads. It then runs the existing `install-desktop-linux.sh`. When no package is found, it prints the build command and skips the desktop step, unless `--require-desktop` is passed. `install.sh` consumes that flag itself and never forwards it. `--check-only` only verifies the package, read-only. The tests use a stub package through `KEEPHARNESS_DESKTOP_PACKAGE` and a temporary HOME and XDG folders.

## Rationale
The desktop installer is already idempotent, verified (SHA256SUMS, provenance) and covered by `desktop/linux-package.test.cjs`, so reusing it adds no new installer. A stub package keeps pytest free of Electron downloads and builds while still running the real installer code.

## Alternatives considered
- Build on install, always or as a fallback (`npm ci` + package): needs node and a download, and fails on a dirty tree. Rejected.
- Only download a GitHub release artifact: there are no releases yet, and it adds network trust and checksum plumbing. Deferred.
- A new Python installer that replaces both scripts: duplicates audited code. Rejected.

## Impact
`install.sh`, a new `tests/test_install_full.py`, `dossier/installation-agent-spec.md` (agents call the same script) and the release notes.

## Follow-up actions
Implement it with `python-code-engineer` (or Sonnet `task-execute-high-engineer`). Revisit when releases publish a signed desktop artifact: add it as a fourth source.

## Review 2026-10-09
An Opus security and API review made these changes. The test keeps `keepharness-install` stubbed; the real `register` waits up to 30 s and refuses containers. It forwards `install_desktop_linux.py` to the real Python. `--require-desktop` is no longer forwarded to argparse. `--check-only` no longer runs the desktop `--dry-run`, which creates a lock and folders. The partial-failure message is separate from the rollback hint. The build fallback was dropped, and every package source must match the pyproject version. The test environment is built from scratch; it checks that every systemctl call is `--user` and that no desktop-database tool runs. SHA256SUMS gives integrity, not authenticity.
