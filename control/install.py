"""Install a per-user systemd service and launcher, without root.

install.sh runs ``--check-only`` with the system Python before anything else (it needs
only the standard library), then this module from the new environment to register the
service. ``--rollback-to-0.14`` removes the service and gives the state back to Tail Harness.
"""

import argparse
import http.client
import http.cookiejar
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from .product import (
    LEGACY_SERVICE,
    PRODUCT,
    describe,
    held_by_unit,
    is_original,
    legacy_folders,
    migration_refusal,
    port_holders,
    rollback_refusal,
    rollback_state,
    waits_to_move,
)

SERVICE = PRODUCT.slug + ".service"
CHECKOUT = Path(__file__).resolve().parents[1]
# Podman, distrobox and toolbox create it in every container.
CONTAINER_MARKER = Path("/run/.containerenv")


def unit_pid(service=SERVICE):
    """The main process of ``service``, or None when it is not running."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show", "-p", "MainPID", "--value", service],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return int(value) if value.isdigit() and value != "0" else None


def port_conflict(port, service=SERVICE, stopping=None):
    """Who holds loopback ``port`` when it is not ``service``'s own process, else None.

    ``stopping`` names a unit install.sh stops next: its processes do not conflict."""
    holders = port_holders(port)
    if not holders or unit_pid(service) in [pid for pid, _ in holders]:
        return None
    if stopping and held_by_unit(holders, stopping):
        return None
    return (
        f"127.0.0.1:{port} is held by {describe(holders)}, not by {service}: stop it, then run "
        "./install.sh again."
    )


def wait_ready(port, service=None):
    """Wait for HTTP 200; with ``service``, the answer must come from that unit's own process."""
    for _ in range(120):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
                if response.status == 200:
                    break
        except OSError:
            pass
        time.sleep(0.25)
    else:
        raise RuntimeError(
            f"The service did not become available; check journalctl --user -u {PRODUCT.slug}."
        )
    if service and (conflict := port_conflict(port, service)):
        raise RuntimeError(conflict)


def container_name(marker=None):
    """The container this runs in, or None on the host."""
    marker = CONTAINER_MARKER if marker is None else marker
    if name := os.environ.get("CONTAINER_ID"):
        return name
    return "a container" if marker.exists() else None


def quoted(value):
    return json.dumps(str(value).replace("%", "%%").replace("$", "$$"))


def service_path(home):
    """The installer's PATH, then the usual folders: the service finds the CLIs the user sees."""
    entries = os.environ.get("PATH", "").split(os.pathsep)
    entries += [f"{home}/.local/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    return ":".join(dict.fromkeys(entry for entry in entries if entry.startswith("/")))


def files(home, python, port=8094, dev=False):
    home = Path(home)
    state = PRODUCT.state_path(home)
    # An editable install imports this checkout: moving or switching it changes the service.
    origin = f"# Installed with --dev: runs the code in {CHECKOUT}\n" if dev else ""
    unit = f"""{origin}[Unit]
Description={PRODUCT.name} local administration
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
ExecStart={quoted(python)} -m control --port {port} --state {quoted(state)}
Environment={quoted("PATH=" + service_path(home))}
Restart=on-failure
RestartSec=5
TimeoutStopSec=90
KillMode=mixed
UMask=0077

[Install]
WantedBy=default.target
"""
    # The explicit launcher starts the registered service before opening its local URL.
    launcher = f"""#!/bin/sh
set -eu
systemctl --user start {SERVICE}
{shlex.quote(str(python))} -c {shlex.quote("from control.install import wait_ready; wait_ready(" + str(port) + ")")}
exec xdg-open http://127.0.0.1:{port}/
"""
    return {
        home / ".config/systemd/user" / SERVICE: (unit, 0o600),
        home / (".local/bin/" + PRODUCT.slug + "-open"): (launcher, 0o700),
    }


BROWSER_ENTRIES = (PRODUCT.slug + "-browser.desktop", PRODUCT.slug + ".desktop")


def remove_browser_entry(path, home):
    """Remove only the browser shortcut this installer once wrote (S3 or pre-S3 name).

    The Exec line must be exactly our explicit launcher and the icon ours, so the desktop
    client's own entry and any edited or foreign file are left alone.
    """
    if path.is_symlink() or not path.is_file():
        return
    try:
        lines = path.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return  # unreadable or not ours: keep it
    expected = {f'Exec="{h}/.local/bin/{PRODUCT.slug}-open"' for h in (home, Path(home).resolve())}
    execs = [line for line in lines if line.startswith("Exec=")]
    if len(execs) == 1 and execs[0] in expected and "Icon=utilities-terminal" in lines:
        path.unlink()


def atomic_write(path, content, mode):
    """Replace the entry itself, never follow an existing symlink."""
    fd, name = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
            os.fchmod(stream.fileno(), mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def remove_legacy_service(home, run=subprocess.run):
    """Retire the Tail Harness service and shortcuts that this install replaces (0.15.0)."""
    if not is_original(PRODUCT):
        return
    unit = Path(home) / ".config/systemd/user/tail-harness.service"
    if unit.exists():
        run(["systemctl", "--user", "disable", "--now", unit.name], check=False)
    for path in (
        unit,
        Path(home) / ".local/bin/tail-harness-open",
        Path(home) / ".local/share/applications/tail-harness.desktop",
    ):
        path.unlink(missing_ok=True)


def rollback(home, run=subprocess.run):
    """Remove this service and its shortcuts, then give the state back to Tail Harness 0.14."""
    if refusal := rollback_refusal(home):
        raise ValueError(refusal)
    # Disabled before the state moves back: an enabled unit would take it again (OPS-R1-2).
    run(["systemctl", "--user", "disable", "--now", SERVICE], check=False)
    for path in files(home, sys.executable):
        path.unlink(missing_ok=True)
    for name in BROWSER_ENTRIES:
        remove_browser_entry(Path(home) / ".local/share/applications" / name, home)
    run(["systemctl", "--user", "daemon-reload"], check=False)
    return rollback_state(home)


def preflight(port):
    """Why the install must not go on: run in a container, a port another program holds, or
    Tail Harness state that cannot move now; None when it may."""
    if name := container_name():
        return (
            f"install.sh runs inside {name}. {PRODUCT.name}'s service must run on the host, whose "
            "systemd cannot use an environment built here: run ./install.sh in a host terminal, "
            "or `distrobox-host-exec ./install.sh` from this checkout."
        )
    return port_conflict(port, stopping=LEGACY_SERVICE) or migration_refusal(
        Path.home(), stopping=True
    )


def roll_back():
    try:
        state = rollback(Path.home())
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Rollback stopped: {exc}") from None
    print(
        f"Rolled back: {state} is Tail Harness 0.14 state again and {SERVICE} is removed. "
        "Start Tail Harness 0.14 from its own checkout."
    )


def work_refusal(port):
    """Ask the existing admin before an upgrade can restart its harness."""
    if not port_holders(port):
        return None
    from .local_access import KEY_FILE, open_ticket, read_secret

    base = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )
    try:
        secret = read_secret(PRODUCT.state_path() / KEY_FILE)
        with opener.open(f"{base}/open-admin?ticket={open_ticket(secret)}", timeout=2):
            pass
        with opener.open(base + "/api/state", timeout=2) as response:
            busy = json.load(response)["status"]["busy"]
        if not isinstance(busy, bool):
            raise ValueError("Invalid admin busy status")
    except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError):
        return (
            "Cannot verify queued or running work with the admin. "
            "Retry, or wait for work to finish and use --force to install."
        )
    if busy:
        return "There is queued or running work. Wait for it to finish, or use --force to install."
    return None


def register(args):
    os.umask(0o077)
    remove_legacy_service(Path.home())
    applications = Path.home() / ".local/share/applications"
    for name in BROWSER_ENTRIES:
        remove_browser_entry(applications / name, Path.home())
    for path, (content, mode) in files(Path.home(), sys.executable, args.port, args.dev).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, content, mode)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", SERVICE], check=True)
    subprocess.run(["systemctl", "--user", "restart", SERVICE], check=True)
    if args.boot:
        subprocess.run(["loginctl", "enable-linger", str(os.getuid())], check=True)
    subprocess.run(["systemctl", "--user", "is-active", SERVICE], check=True)
    wait_ready(args.port, SERVICE)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install service and launcher for this Linux user")
    parser.add_argument("--port", type=int, default=8094)
    parser.add_argument("--boot", action="store_true", help="Enable linger to start before login")
    parser.add_argument("--dev", action="store_true", help="Record an editable (checkout) install")
    parser.add_argument(
        "--force", action="store_true", help="Install even with queued or running work"
    )
    parser.add_argument(
        "--check-only", action="store_true", help="Only check that the install may proceed"
    )
    parser.add_argument(
        "--rollback-to-0.14",
        dest="rollback",
        action="store_true",
        help="Remove the service and give the state back to Tail Harness 0.14",
    )
    args = parser.parse_args(argv)
    if not sys.platform.startswith("linux"):
        parser.error(
            f"Service installation requires Linux/systemd; use {PRODUCT.slug} for manual execution."
        )
    if not 1024 <= args.port <= 65535:
        parser.error("Invalid port")
    if args.rollback:
        return roll_back()
    if refusal := preflight(args.port):
        raise SystemExit(refusal + (" Nothing was stopped or moved." if args.check_only else ""))
    if not args.force and (refusal := work_refusal(args.port)):
        raise SystemExit(
            refusal
            + (
                " Nothing was stopped or moved."
                if args.check_only
                else " The package is installed; the service keeps the old code until it restarts."
            )
        )
    if args.check_only:
        for old, new in legacy_folders():
            if waits_to_move(new):
                print(f"{old} moves to {new} when ./install.sh installs.")
        return
    try:
        register(args)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"{exc} Check journalctl --user -u {PRODUCT.slug}.") from None
    print(f"Service installed. Open {PRODUCT.name} from the menu or http://127.0.0.1:{args.port}/")


if __name__ == "__main__":
    main()
