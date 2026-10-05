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
    for name in ("ui.js", "ui-prefs.js", "run-console.js", "tour.js", "ui.css", "tour.css", "index.html",
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
    assert initial["disk_source_build"] == initial["source_build"]
    assert poll_without_reads(client) == initial
    (package / "services/worker.py").write_text("installed Python update")
    changed = poll_without_reads(client)
    assert changed["build"] == initial["build"]
    assert changed["disk_build"] != initial["disk_build"]
    assert changed["disk_source_build"] != initial["source_build"]
    assert changed["source_build"] == initial["source_build"]
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
            assert current["source_build"] == current["disk_source_build"] == changed["disk_source_build"]
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
    assert changed["disk_source_build"] != initial["source_build"]
    assert changed["source_build"] == initial["source_build"]
    assert changed["build"] == initial["build"]
    assert changed["ui_build"] == initial["ui_build"]
    added.unlink()
    assert poll_without_reads(client) == initial


def test_version_identifies_keepharness_product(version_client):
    client, _, _ = version_client
    assert client.get("/v1/version").json()["product"] == "keepharness"


def test_ui_only_edit_preserves_source_build(version_client):
    client, package, _ = version_client
    initial = poll_without_reads(client)
    (package / "ui.js").write_text("UI-only update")
    changed = poll_without_reads(client)
    assert changed["disk_build"] != initial["build"]
    assert changed["disk_source_build"] == changed["source_build"] == initial["source_build"]


def test_shared_ui_python_edit_changes_source_build(version_client, monkeypatch, tmp_path):
    import harness_ui

    module = tmp_path / "harness_ui" / "__init__.py"
    module.parent.mkdir()
    module.write_text("original Python")
    monkeypatch.setattr(harness_ui, "__file__", str(module))
    client, _, _ = version_client
    client.app.state.build_versions = system.BuildVersions()
    initial = poll_without_reads(client)
    module.write_text("updated shared UI Python")
    changed = poll_without_reads(client)
    assert changed["disk_source_build"] != initial["source_build"]
    assert changed["source_build"] == initial["source_build"]
    assert changed["ui_build"] == initial["ui_build"]
