"""install.sh also installs the desktop app and its menu entry from an unpacked package.

Every program install.sh runs is a stub except the desktop installer, which really runs through a
``python3`` stub that forwards it to the real interpreter. The environment is built from scratch
under ``tmp_path``, so nothing touches the real home, services or menus. install.sh runs from a
small checkout of its own, so a ``dist/`` folder in this repository cannot leak into a test.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tests.test_install_script import FAIL_ON, LOGGER, ROOT, VENV_LOGGER, WHEEL, stub

VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
DESKTOP = ROOT / "desktop/linux"
# Desktop-database tools install.sh must never run: only the desktop installer's own files.
FORBIDDEN_TOOLS = ("xdg-desktop-menu", "update-desktop-database", "kbuildsycoca5", "kbuildsycoca6")
# The register step of keepharness-install, reduced to the files and the unit it leaves behind.
REGISTER = (
    'echo "keepharness-install $*" >> "$CALLS"\n'
    + '[ -z "${REGISTER_FAILS:-}" ] || { echo "register failed" >&2; exit 1; }\n'
    + 'mkdir -p "$HOME/.config/systemd/user" "$HOME/.local/bin"\n'
    + 'echo "[Service]" > "$HOME/.config/systemd/user/keepharness.service"\n'
    + 'printf "#!/bin/sh\\n" > "$HOME/.local/bin/keepharness-open"\n'
    + "systemctl --user enable --now keepharness.service\n"
)
PYTHON3 = (
    LOGGER.format(name="python3")
    + FAIL_ON
    + 'case "$*" in\n'
    + '  *install_desktop_linux.py*) exec "$INSTALLER_PYTHON" "$@" ;;\n'
    + '  *"--field venv"*) echo "$VENV" ;;\n'
    + '  *"--field slug"*) echo keepharness ;;\n'
    + '  *"-m venv"*) for target; do :; done; mkdir -p "$target/bin" && cp "$VENV_PYTHON" "$target/bin/python" ;;\n'
    + "esac\n"
)
SYSTEMCTL = LOGGER.format(name="systemctl") + 'case " $* " in *" --user "*) ;; *) exit 1 ;; esac\n'


def make_package(folder, version=VERSION):
    """An unpacked desktop package as scripts/package-desktop-linux.sh lays it out."""
    files = {
        "keepharness": ("launcher.sh", 0o755),
        "install-desktop-linux.sh": ("install-desktop-linux.sh", 0o755),
        "install_desktop_linux.py": ("install_desktop_linux.py", 0o644),
        "share/applications/keepharness.desktop": ("keepharness.desktop", 0o644),
    }
    for target, (name, mode) in files.items():
        stub(folder / target, (DESKTOP / name).read_text())
        (folder / target).chmod(mode)
    icon = folder / "share/icons/hicolor/256x256/apps/keepharness.png"
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"")
    (folder / "VERSION").write_text(version + "\n")
    stub(folder / "keepharness-bin", "#!/bin/sh\n")
    (folder / "build-manifest.json").write_text(
        f'{{"version": "{version}", "product": "keepharness", "dirty": false, "commit": "{"a" * 40}"}}'
    )
    members = sorted(path for path in folder.rglob("*") if path.is_file())
    (folder / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(folder)}\n"
            for path in members
        )
    )
    return folder


def snapshot(home):
    """Every path under home with its bytes, link target or None for a folder."""
    state = {}
    for path in sorted(home.rglob("*")):
        key = path.relative_to(home).as_posix()
        if path.is_symlink():
            state[key] = ("link", os.readlink(path))
        else:
            state[key] = path.read_bytes() if path.is_file() else None
    return state


@pytest.fixture
def install(tmp_path):
    home, bin_dir, calls = tmp_path / "home", tmp_path / "bin", tmp_path / "calls"
    checkout, temporary = tmp_path / "checkout", tmp_path / "tmp"
    venv = home / ".local/share/keepharness/venv"
    for folder in (home, temporary, checkout):
        folder.mkdir()
    for name in ("install.sh", "pyproject.toml"):
        shutil.copy(ROOT / name, checkout / name)
    # install.sh verifies packages with the checkout's own desktop installer.
    shutil.copytree(DESKTOP, checkout / "desktop/linux")
    stub(bin_dir / "systemctl", SYSTEMCTL)
    stub(bin_dir / "python3", PYTHON3)
    for tool in FORBIDDEN_TOOLS:
        stub(bin_dir / tool, LOGGER.format(name=tool))
    stub(tmp_path / "venv-python", VENV_LOGGER + FAIL_ON + WHEEL)
    stub(venv / "bin/keepharness-install", REGISTER)

    def run(*args, check=True, **extra):
        environment = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local/share"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "TMPDIR": str(temporary),
            "CALLS": str(calls),
            "VENV": str(venv),
            "VENV_PYTHON": str(tmp_path / "venv-python"),
            "INSTALLER_PYTHON": sys.executable,
            **extra,
        }
        run.result = subprocess.run(
            ["sh", str(checkout / "install.sh"), *args],
            env=environment,
            check=check,
            capture_output=True,
            text=True,
        )
        run.last = run.result.stdout.strip().splitlines()[-1:]
        return calls.read_text().splitlines() if calls.exists() else []

    run.home = home
    run.checkout = checkout
    run.opt = home / ".local/opt"
    run.package = make_package(tmp_path / "pkg")
    run.environment = {"KEEPHARNESS_DESKTOP_PACKAGE": str(run.package)}
    return run


def test_install_leaves_service_venv_and_desktop_app(install):
    calls = install("--require-desktop", **install.environment)
    home = install.home
    registered = [call for call in calls if call.startswith("keepharness-install")]
    assert [call.strip() for call in registered] == ["keepharness-install"]
    assert not any("--require-desktop" in call for call in calls)
    assert "systemctl --user enable --now keepharness.service" in calls
    systemctl = [call for call in calls if call.startswith("systemctl")]
    assert systemctl and all(" --user " in call + " " for call in systemctl)
    assert not any(call.startswith(FORBIDDEN_TOOLS) for call in calls)
    # These three only prove the stubs ran in the scratch home; the real ones have their own tests.
    assert (home / ".local/share/keepharness/venv/bin/python").is_file()
    assert (home / ".config/systemd/user/keepharness.service").is_file()
    assert (home / ".local/bin/keepharness-open").is_file()
    target = install.opt / f"keepharness-{VERSION}"
    current = install.opt / "keepharness/current"
    assert target.is_dir() and current.is_symlink() and current.resolve() == target
    entry = (home / ".local/share/applications/keepharness.desktop").read_text().splitlines()
    link = install.opt / "keepharness/current"
    assert f'Exec="{link}/keepharness"' in entry
    icon = f"Icon={link}/share/icons/hicolor/256x256/apps/keepharness.png"
    assert icon in entry
    assert Path(icon.removeprefix("Icon=")).is_file()
    assert install.last[0].startswith("Service and desktop app installed")


def test_second_identical_run_changes_nothing(install):
    install("--require-desktop", **install.environment)
    before = snapshot(install.home)
    install("--require-desktop", **install.environment)
    assert snapshot(install.home) == before
    assert "identical reinstall is a no-op" in install.result.stdout
    assert install.last[0].startswith("Service and desktop app installed")


def test_the_dist_folder_is_the_default_package_source(install):
    dist = install.checkout / "dist"
    dist.mkdir()
    make_package(dist / f"keepharness-{VERSION}-linux-x64")
    install("--require-desktop")
    assert (install.opt / f"keepharness-{VERSION}").is_dir()


def test_without_a_package_the_service_installs_and_the_skip_is_named(install):
    calls = install()
    assert any(call.startswith("keepharness-install") for call in calls)
    assert not install.opt.exists()
    assert install.last[0].startswith("Service installed; desktop app skipped:")
    assert install.last[0].endswith("Build it with ./scripts/package-desktop-linux.sh")


def test_require_desktop_without_a_package_stops_before_anything_changes(install):
    calls = install("--require-desktop", check=False)
    assert install.result.returncode != 0
    assert "desktop" in install.result.stderr
    assert "Nothing was stopped or moved." in install.result.stderr
    assert not any(call.startswith(("systemctl", "keepharness-install")) for call in calls)


def test_a_package_of_another_version_is_rejected(install):
    make_package(install.package.parent / "old", "0.0.1")
    old = {"KEEPHARNESS_DESKTOP_PACKAGE": str(install.package.parent / "old")}
    calls = install(check=False, **old)
    assert install.result.returncode != 0
    assert f"is version 0.0.1, not {VERSION}" in install.result.stderr
    assert not any(call.startswith(("systemctl", "keepharness-install")) for call in calls)
    # A stale dist/ folder is skipped with its reason; the explicit variable is never ignored.
    dist = install.checkout / "dist"
    dist.mkdir()
    make_package(dist / f"keepharness-{VERSION}-linux-x64", "0.0.1")
    install()
    assert not install.opt.exists()
    assert f"is version 0.0.1, not {VERSION}" in install.last[0]


def test_a_relative_package_path_is_rejected(install):
    calls = install(check=False, KEEPHARNESS_DESKTOP_PACKAGE="pkg")
    assert install.result.returncode != 0
    assert "must be an absolute path" in install.result.stderr
    assert not any(call.startswith(("systemctl", "keepharness-install")) for call in calls)


def test_a_tampered_package_is_rejected(install):
    (install.package / "keepharness-bin").write_text("#!/bin/sh\necho tampered\n")
    install(check=False, **install.environment)
    assert install.result.returncode != 0
    assert "SHA256SUMS" in install.result.stderr
    assert not install.opt.exists()


def test_check_only_verifies_the_package_and_creates_nothing(install):
    before = snapshot(install.home)
    calls = install("--check-only", "--require-desktop", **install.environment)
    assert snapshot(install.home) == before
    assert not any(call.startswith(("systemctl", "keepharness-install")) for call in calls)
    desktop = [call for call in calls if "install_desktop_linux.py" in call]
    assert desktop and all(call.endswith("--verify") for call in desktop)
    make_package(install.package.parent / "old", "0.0.1")
    old = {"KEEPHARNESS_DESKTOP_PACKAGE": str(install.package.parent / "old")}
    install("--check-only", check=False, **old)
    assert install.result.returncode != 0
    assert f"is version 0.0.1, not {VERSION}" in install.result.stderr
    assert snapshot(install.home) == before


def test_dev_skips_the_desktop_app_unless_a_package_is_given(install):
    install("--dev")
    assert not install.opt.exists()
    assert "--dev" in install.last[0]
    install("--dev", **install.environment)
    assert (install.opt / f"keepharness-{VERSION}").is_dir()


def test_dev_with_require_desktop_needs_a_package(install):
    calls = install("--dev", "--require-desktop", check=False)
    assert install.result.returncode != 0
    assert "--dev" in install.result.stderr
    assert not any(call.startswith(("systemctl", "keepharness-install")) for call in calls)


@pytest.mark.parametrize("required", [False, True])
def test_a_failed_desktop_step_is_not_a_service_failure(install, required):
    applications = install.home / ".local/share/applications"
    applications.mkdir(parents=True)
    (applications / "keepharness.desktop").write_text("[Desktop Entry]\nExec=foreign\n")
    install(*(["--require-desktop"] if required else []), check=False, **install.environment)
    assert (install.result.returncode != 0) is required
    message = "The service is installed and running; the desktop app was not installed"
    assert message in install.result.stderr
    assert "Run ./install.sh again" in install.result.stderr
    assert "--rollback-to-0.14" not in install.result.stderr


def test_a_failed_service_step_still_offers_the_rollback(install):
    install(check=False, REGISTER_FAILS="1", **install.environment)
    assert install.result.returncode != 0
    assert "--rollback-to-0.14" in install.result.stderr
    assert "desktop app was not installed" not in install.result.stderr
    assert not install.opt.exists()
