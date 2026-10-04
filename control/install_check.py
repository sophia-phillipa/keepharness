"""Smoke-test a fresh installation with isolated state, outside the checkout."""

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

from .local_access import KEY_FILE, open_ticket, read_secret


def check_admin_api(client, state):
    """Sign in with the install's local secret, as the installer does, then check the fresh state."""
    ticket = open_ticket(read_secret(Path(state) / KEY_FILE))
    if client.get("/open-admin", params={"ticket": ticket}).status_code != 200:
        raise RuntimeError("Installed server refused the local admin secret")
    snapshot = client.get("/api/state").json()
    assert not snapshot["status"]["running"]
    assert not any(s["enabled"] for s in snapshot["settings"]["services"].values())
    assert snapshot["local_profile"] == {}


def main():
    with tempfile.TemporaryDirectory(prefix="keepharness-install-check-") as folder:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        with (Path(folder) / "server.log").open("w+") as log:
            proc = subprocess.Popen(
                [sys.executable, "-m", "control", "--port", str(port), "--state", folder],
                cwd=folder,
                env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
                stdout=log,
                stderr=log,
            )
            try:
                with httpx.Client(
                    base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=1
                ) as client:
                    for _ in range(120):
                        if proc.poll() is not None:
                            raise RuntimeError("Installed server exited before startup")
                        try:
                            if client.get("/").status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(0.25)
                    else:
                        raise RuntimeError("Installed server startup timeout")
                    for path in (
                        "/admin.js",
                        "/admin.css",
                        "/assets/theme.js",
                        "/assets/themes.css",
                        "/assets/tabler.min.css",
                        "/assets/components.js",
                        "/assets/icons.svg",
                        "/assets/inter-latin.woff2",
                    ):
                        assert client.get(path).status_code == 200
                    check_admin_api(client, folder)
                    print(
                        "PASS: installed package outside checkout, fresh private state, HTTP/assets/API, no services enabled."
                    )
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


if __name__ == "__main__":
    main()
