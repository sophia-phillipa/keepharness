"""Install a per-user systemd service and desktop shortcut, without root."""

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .product import PRODUCT

SERVICE = PRODUCT.slug + ".service"


def wait_ready(port):
    for _ in range(120):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.25)
    raise RuntimeError(
        f"The service did not become available; check journalctl --user -u {PRODUCT.slug}."
    )


def quoted(value):
    return json.dumps(str(value).replace("%", "%%").replace("$", "$$"))


def files(home, python, port=8094):
    home = Path(home)
    state = PRODUCT.state_path(home)
    unit = f"""[Unit]
Description={PRODUCT.name} local administration
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={quoted(python)} -m control --port {port} --state {quoted(state)}
Environment="PATH={home}/.local/bin:/usr/local/bin:/usr/bin:/bin"
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
UMask=0077

[Install]
WantedBy=default.target
"""
    # Desktop launcher starts the registered service before opening the browser.
    launcher = f"""#!/bin/sh
set -eu
systemctl --user start {SERVICE}
{shlex.quote(str(python))} -c {shlex.quote("from control.install import wait_ready; wait_ready(" + str(port) + ")")}
exec xdg-open http://127.0.0.1:{port}/
"""
    desktop = f'''[Desktop Entry]
Type=Application
Name={PRODUCT.name}
Comment=Manage local AI services
Exec="{home}/.local/bin/{PRODUCT.slug}-open"
Icon={PRODUCT.desktop_icon}
Terminal=false
Categories=Development;
StartupNotify=false
'''
    return {
        home / ".config/systemd/user" / SERVICE: (unit, 0o600),
        home / (".local/bin/" + PRODUCT.slug + "-open"): (launcher, 0o700),
        home / (".local/share/applications/" + PRODUCT.slug + ".desktop"): (desktop, 0o644),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install service and shortcut for this Linux user")
    parser.add_argument("--port", type=int, default=8094)
    parser.add_argument("--boot", action="store_true", help="Enable linger to start before login")
    args = parser.parse_args(argv)
    if not sys.platform.startswith("linux"):
        parser.error(
            f"Service installation requires Linux/systemd; use {PRODUCT.slug} for manual execution."
        )
    if not 1024 <= args.port <= 65535:
        parser.error("Invalid port")
    os.umask(0o077)
    for path, (content, mode) in files(Path.home(), sys.executable, args.port).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(mode)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", SERVICE], check=True)
    subprocess.run(["systemctl", "--user", "restart", SERVICE], check=True)
    if args.boot:
        subprocess.run(["loginctl", "enable-linger", str(os.getuid())], check=True)
    subprocess.run(["systemctl", "--user", "is-active", SERVICE], check=True)
    wait_ready(args.port)
    print(f"Service installed. Open {PRODUCT.name} from the menu or http://127.0.0.1:{args.port}/")


if __name__ == "__main__":
    main()
