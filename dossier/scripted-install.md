> Status: ACTIVE (accepted, 0.16.0)
> Job: When I install or upgrade KeepHarness on a Linux host, I want one command to leave the service, the venv, the desktop app and its menu entry working, so I can install without an AI agent doing steps by hand.
> Scope: `install.sh`, `tests/test_install_script.py`, new `tests/test_install_full.py`
> Invariant docs: AGENTS.md (no state or secrets committed), `dossier/installation-agent-spec.md`
> Decision rationale: [D-051](decisions/d-051-scripted-install-with-desktop-package.md)
> Kill criteria: replace when KeepHarness ships a distribution package (Flatpak/RPM) that owns these files.

# Fully scripted install

## 1. Why
Today `./install.sh` installs the venv and the user service (`control/install.py:261` `register`) and prints "Open KeepHarness from the menu" (`control/install.py:327`), but the menu entry exists only if someone also built a package (`scripts/package-desktop-linux.sh`) and ran its `install-desktop-linux.sh`. Sophia requires the whole install to be scripted and covered by tests.

| Choice | Rationale |
|---|---|
| `install.sh` calls the existing desktop installer (`desktop/linux/install_desktop_linux.py`) as its last step | Already idempotent ("identical reinstall is a no-op", `:403`), checks SHA256SUMS and the build manifest, writes `.desktop` + `current`/`previous` links, prunes. No new installer. |
| Two package sources only: `KEEPHARNESS_DESKTOP_PACKAGE`, then `dist/keepharness-<VERSION>-linux-x64` | No build or download during install; building stays a separate, explicit step. |
| No package → service installed, desktop step skipped with the build command printed, exit 0; `--require-desktop` makes it fatal | The browser path still works; tests and CI opt into strictness. |
| Tests use a stub package via `KEEPHARNESS_DESKTOP_PACKAGE` | Same shape as `fakePackage` in `desktop/linux-package.test.cjs:14-36`; no Electron in pytest. |

## 2. How
`install.sh` consumes `--require-desktop` itself and removes it from the argument list before every forward: `python3 -m control.install --check-only "$@"` (`install.sh:25`, `:78`) and `"$TH_VENV/bin/$TH_PRODUCT_SLUG-install" "$@"` (`:100`) receive the remaining arguments only (argparse in `control/install.py:280` would reject the flag). It resolves the package (below) before anything is stopped. `--check-only` resolves and verifies the package read-only (same checks as `verify_package`: SHA256SUMS, manifest, VERSION) and never runs the desktop installer, not even `--dry-run` (that path creates folders and `.keepharness.lock`, `install_desktop_linux.py:306-317`). After the service step, `install.sh` runs `"$PKG/install-desktop-linux.sh"`. `--dev` skips the desktop step unless `KEEPHARNESS_DESKTOP_PACKAGE` is set.

### Contracts
- `install.sh [--port N] [--boot] [--dev] [--check-only] [--require-desktop]` — Guarantees: non-interactive (nothing reads stdin); a second identical run changes no file content and exits 0; the last line says what was installed (`Service and desktop app installed…` or `Service installed; desktop app skipped: <reason>. Build it with ./scripts/package-desktop-linux.sh`). Must-not: download anything; build a package; write outside `$HOME` and `$TMPDIR`; call `systemctl` without `--user`; call `xdg-desktop-menu`, `update-desktop-database` or `kbuildsycoca*`.
- Package resolution — Guarantees: the chosen folder's `VERSION` equals `pyproject.toml` `project.version` for every source; mismatch → that source is rejected with a reason. A rejected `KEEPHARNESS_DESKTOP_PACKAGE` is an error (explicit input), never a silent fallback to `dist/`.
- `KEEPHARNESS_DESKTOP_PACKAGE` (public env var, naming model) — absolute path to an unpacked package folder.
- Partial failure: service done, desktop step failed → exit non-zero under `--require-desktop` (otherwise 0) with its own message "The service is installed and running; the desktop app was not installed: <reason>. Run ./install.sh again after fixing it." The `--rollback-to-0.14` hint in the trap (`install.sh:33-35`) applies only before the service step completes.
- Files after success: `~/.config/systemd/user/keepharness.service`, `~/.local/bin/keepharness-open`, `~/.local/share/keepharness/venv/bin/python`, `~/.local/opt/keepharness-<VERSION>/`, `~/.local/opt/keepharness/current` pointing to the `keepharness-<VERSION>` directory (absolute link), `~/.local/share/applications/keepharness.desktop` with `Exec="$HOME/.local/opt/keepharness/current/keepharness"` and an absolute `Icon=` into `current/share/icons/hicolor/256x256/apps/keepharness.png`.

### Failure modes
- **Tests writing into the real home**: the environment is built from scratch (pattern `tests/test_install_script.py:76-84`): `PATH` = stub dir + `/usr/bin:/bin`, `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`, `TMPDIR` under `tmp_path`; nothing inherited from `os.environ`.
- **Running the real `control.install.register` in tests**: it waits up to 30 s in `wait_ready` (`control/install.py:72-87`) and the preflight refuses containers (`:208`). Keep `keepharness-install` stubbed; unit-file mode and `--port` are already covered by `tests/test_install_service.py` (which swaps `CONTAINER_MARKER`, `:57-58`).
- Adding `npm ci` or a package build to `install.sh` brings back a network download and a dirty-tree failure on every install. Forbidden by contract.
- A stale `dist/` package of another version: rejected by the VERSION rule.
- An older version still running is not a refusal: `prune` keeps running versions (`install_desktop_linux.py:215`); the new one becomes `current`.
- SHA256SUMS and the manifest prove the copy is intact and consistent, not who built it (no signature). An attacker who can write `dist/` or set the env var can install anything as the same user; that is the same trust level as the checkout.

### Integration points
d=1 `install.sh` argument handling and trap, `install_desktop_linux.main`; d=2 `desktop/linux/launcher.sh` (finds the venv install.sh made), `tests/test_install_script.py` stub harness; d=3 the first-run wizard ([first-run-wizard.md](first-run-wizard.md)) is reached through the launcher installed here; `dossier/installation-agent-spec.md` (agents call the same script).

## 3. What
| Command | Purpose |
|---|---|
| `./install.sh` | service + venv + desktop when a package resolves |
| `./install.sh --require-desktop` | same; fail if the desktop step cannot run |
| `KEEPHARNESS_DESKTOP_PACKAGE=/abs/pkg ./install.sh` | use a given unpacked package |
| `./scripts/package-desktop-linux.sh` | separate step: builds `dist/keepharness-<VERSION>-linux-x64` (clean tree, node, pinned Electron zip) |

### Test protocol
New `tests/test_install_full.py`, reusing the `install` fixture style of `tests/test_install_script.py`:
1. Fixture builds a stub package in `tmp_path/pkg` (Python port of `fakePackage`, with the real `install-desktop-linux.sh` and `install_desktop_linux.py` copied from `desktop/linux/`), VERSION = pyproject version.
2. The `python3` stub (`tests/test_install_script.py:58-66`) forwards `*install_desktop_linux.py*` to the real interpreter (`REAL_PYTHON`), so the desktop step really runs; `keepharness-install` stays a recorder stub. The `systemctl` stub records its argv and exits 1 if `--user` is missing. Stubs `xdg-desktop-menu`, `update-desktop-database`, `kbuildsycoca5`, `kbuildsycoca6` record calls.
3. `sh install.sh --require-desktop`: assert `keepharness-install` received no `--require-desktop`; `current` symlink → `keepharness-<VERSION>`; `.desktop` Exec/Icon lines as above; icon exists; no call to the desktop-database tools; every `systemctl` call had `--user`.
4. Second run: same content hash for `.desktop`, same `current` target; output has "identical reinstall is a no-op"; exit 0.
5. No package, no flag: exit 0, last line names the skip and the build command. With `--require-desktop`: non-zero. Package with another VERSION: rejected.
6. `--check-only` with a package: no `~/.local/opt` and no `.keepharness.lock` created.
Runnable: `.venv/bin/python -m pytest -q tests/test_install_full.py tests/test_install_script.py tests/test_install_service.py && (cd desktop && node --test linux-package.test.cjs)`.
Real mode: `./scripts/package-desktop-linux.sh && distrobox-host-exec ./install.sh --require-desktop` twice on the host, then open from the KDE menu.

## See also
[D-051](decisions/d-051-scripted-install-with-desktop-package.md), [installation-agent-spec.md](installation-agent-spec.md), [desktop-tray-menu.md](desktop-tray-menu.md).
