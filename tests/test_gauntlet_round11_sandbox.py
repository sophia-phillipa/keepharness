import json
import subprocess

import pytest

from adapters.local.sandbox import wrap
from agent_service.tools import ToolError


@pytest.mark.host_tools("bwrap")
@pytest.mark.parametrize("location", ["project", "session"])
@pytest.mark.parametrize("writable", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_private_single_link_target_hidden_on_every_mount(
    tmp_path, monkeypatch, location, writable, nested
):
    install = tmp_path / "install"
    private = install / "local_ai" / "config"
    private.mkdir(parents=True)
    root = tmp_path / "project"
    root.mkdir()
    session = tmp_path / "session"
    session.mkdir()
    secret = (session if location == "session" else root) / "provider.json"
    sentinel = "SYNTHETIC_PRIVATE_SESSION_R11"
    secret.write_text(sentinel)
    if nested:
        relocated = tmp_path / "relocated"
        relocated.mkdir()
        (relocated / "provider").symlink_to(secret)
        (private / "nested").symlink_to(relocated)
    else:
        (private / "provider").symlink_to(secret)
    assert secret.stat().st_nlink == 1
    monkeypatch.setenv("KEEPHARNESS_ROOT", str(install))
    try:
        command = wrap(
            [
                "/usr/bin/python3",
                "-c",
                f'from pathlib import Path; p=Path({str(secret)!r}); print(p.read_text() if p.exists() else "hidden")',
            ],
            session,
            root,
            {"root": str(root), "permissions": {"read": True, "write": writable}},
        )
    except ToolError as error:
        print(json.dumps({"location": location, "rejected": str(error)}))
        assert str(error) == "local_project_hardlink_denied"
        return
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    print(
        json.dumps(
            {
                "location": location,
                "returncode": result.returncode,
                "secret_readable": sentinel in result.stdout,
            }
        )
    )
    assert result.returncode == 0, result.stderr
    assert sentinel not in result.stdout, "Runtime-private target exposed by writable session mount"
