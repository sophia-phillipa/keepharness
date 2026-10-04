"""Runtime identity stays fixed while metadata-only polling detects installed updates."""

import builtins
import hashlib
import io
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from agent_service.app import create_app
from agent_service.routes import system


@pytest.fixture
def version_client(tmp_path, monkeypatch):
    package = tmp_path / "package"
    package.mkdir()
    for name in ("ui.js", "run-console.js", "tour.js", "ui.css", "tour.css", "index.html",
                 "app.py", "config.py", "maestro.py", "spans.py", "work_items.py",
                 "workspaces.py", "mcp_bridge.py", "VERSION", "vendor/markdown-it.min.js",
                 "services/worker.py"):
        file = package / name
        file.parent.mkdir(exist_ok=True)
        file.write_text("0.16.0" if name == "VERSION" else "original")
    monkeypatch.setattr(system, "PACKAGE_DIR", package)
    monkeypatch.setattr(system, "VERSION_FILE", package / "VERSION")
    config = {
        "state_dir": str(tmp_path / "state"), "projects": {"p": {}}, "services": {},
        "clients": {"a": {"sha256": hashlib.sha256(b"a").hexdigest(), "projects": ["p"]}},
    }
    app = create_app(config)
    client = TestClient(app, headers={"Authorization": "Bearer a"})
    yield client, package, config
    client.close()
    app.state.service.db.close()


def poll_without_reads(client):
    with patch.object(Path, "read_bytes", side_effect=AssertionError("content read")), \
         patch.object(Path, "read_text", side_effect=AssertionError("content read")), \
         patch.object(builtins, "open", side_effect=AssertionError("content read")), \
         patch.object(io, "open", side_effect=AssertionError("content read")):
        response = client.get("/v1/version")
    assert response.status_code == 200
    return response.json()


def test_version_poll_reads_no_contents_and_keeps_runtime_identity_after_python_edit(version_client):
    client, package, _ = version_client
    initial = poll_without_reads(client)
    assert initial["disk_build"] == initial["build"]
    assert poll_without_reads(client) == initial
    (package / "services/worker.py").write_text("installed Python update")
    changed = poll_without_reads(client)
    assert changed["build"] == initial["build"]
    assert changed["disk_build"] != initial["disk_build"]
    assert changed["ui_build"] == initial["ui_build"]
    assert poll_without_reads(client) == changed


def test_ui_edit_changes_ui_build_and_restart_adopts_disk_build(version_client):
    client, package, config = version_client
    initial = poll_without_reads(client)
    (package / "ui.js").write_text("installed UI update")
    (package / "VERSION").write_text("0.16.1")
    changed = poll_without_reads(client)
    assert changed["version"] == "0.16.0"
    assert changed["build"] == initial["build"]
    assert changed["ui_build"] != initial["ui_build"]
    restarted = create_app(config)
    try:
        with TestClient(restarted, headers={"Authorization": "Bearer a"}) as second:
            current = poll_without_reads(second)
            assert current["version"] == "0.16.1"
            assert current["build"] == current["disk_build"] == changed["disk_build"]
    finally:
        restarted.state.service.db.close()


def test_added_and_removed_python_files_change_only_disk_build(version_client):
    client, package, _ = version_client
    initial = poll_without_reads(client)
    added = package / "services/new.py"
    added.write_text("new module")
    changed = poll_without_reads(client)
    assert changed["disk_build"] != initial["disk_build"]
    assert changed["build"] == initial["build"]
    assert changed["ui_build"] == initial["ui_build"]
    added.unlink()
    assert poll_without_reads(client) == initial
