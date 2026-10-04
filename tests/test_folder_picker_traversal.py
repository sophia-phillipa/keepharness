"""Folder-picker and system-browser path traversal (spec P5-13 and P5-14); cases not
already covered by tests/test_folder_picker.py, tests/test_project_browser.py and
tests/test_workspaces_system_files.py."""

import hashlib
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from agent_service import workspaces
from agent_service.app import create_app
from agent_service.tools import ToolError
from control.server import create_app as create_admin_app
from tests.owner_session import sign_in


@pytest.fixture
def admin_client():
    with tempfile.TemporaryDirectory() as tmp:
        with patch("control.manager.Manager.refresh", AsyncMock()):
            app = create_admin_app(Path(tmp) / "state", 8094)
            with TestClient(app, base_url="http://127.0.0.1:8094") as test_client:
                sign_in(test_client).get("/")  # receive the admin cookie
                yield test_client, Path(tmp)


def test_dot_dot_path_resolves_to_the_ancestor_folder(admin_client):
    client, tmp_path = admin_client
    root = tmp_path / "root"
    (root / "x").mkdir(parents=True)
    response = client.get("/api/folders", params={"path": str(root / "x" / ".." / "..")})
    assert response.status_code == 200
    assert response.json()["path"] == str(root.parent.resolve())


def test_percent_encoded_dot_dot_segment_resolves_or_is_rejected_but_never_crashes(admin_client):
    client, tmp_path = admin_client
    root = tmp_path / "root"
    root.mkdir()
    # Pass the already-encoded "%2e%2e" verbatim in the URL so the server decodes it itself.
    response = client.get(f"/api/folders?path={root}/%2e%2e")
    assert response.status_code in (200, 400)
    if response.status_code == 200:
        assert response.json()["path"] == str(root.parent.resolve())


def test_relative_path_is_rejected(admin_client):
    client, _ = admin_client
    response = client.get("/api/folders", params={"path": "a/b"})
    assert response.status_code == 400


def test_symlink_loop_is_rejected_not_a_server_error(admin_client):
    client, tmp_path = admin_client
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    response = client.get("/api/folders", params={"path": str(loop)})
    assert response.status_code == 400


def test_proc_is_bounded_and_never_returns_a_server_error(admin_client):
    client, _ = admin_client
    if not Path("/proc").is_dir():
        pytest.skip("/proc is not available")
    response = client.get("/api/folders", params={"path": "/proc"})
    assert response.status_code == 200
    data = response.json()
    assert len(data["directories"]) <= 200
    assert isinstance(data["truncated"], bool)


def project_files_config(tmp_path):
    return {
        "state_dir": str(tmp_path / "state"),
        "origins": [],
        "uploads_enabled": True,
        "projects": {"p": {}, "sem-projeto": {}},
        "clients": {
            name: {
                "sha256": hashlib.sha256(name.encode()).hexdigest(),
                "projects": ["p", "sem-projeto"],
            }
            for name in ("a", "local")
        },
        "services": {
            "codex": {
                "enabled": True,
                "models": ["fixture"],
                "projects": ["p", "sem-projeto"],
                "permissions": {"read": True, "upload": True},
            }
        },
        "codex_models": {"fixture": ["low"]},
    }


def test_system_root_hides_proc_and_etc_entries_at_any_depth(tmp_path):
    # The filesystem root is no longer a browsable root (SEC-RC-10); the hiding still guards
    # any folder handed to the browser as "/", and now refuses the request itself.
    for path in ("proc", "etc"):
        with pytest.raises(ToolError, match="path_not_authorized"):
            workspaces.browse_system(Path("/"), path)
    app = create_app(project_files_config(tmp_path))
    with TestClient(app, headers={"Authorization": "Bearer local"}) as client:
        response = client.get("/v1/project-files?view=tree&root_id=system&path=etc")
        assert (response.status_code, response.json()["code"]) == (422, "system_root_denied")
    app.state.service.db.close()


def test_runtime_and_ostree_physical_roots_are_hidden_system_directories():
    """/run holds session credentials (X11/ICE cookies, keyrings, registry auth), and on
    ostree hosts /sysroot mirrors /etc and /var outside their canonical paths (F-22)."""
    names = {entry["name"] for entry in workspaces.browse_system(Path("/"))["entries"]}
    assert not {"run", "sysroot"} & names
    for path in ("/run/user/1000/xauth_fixture", "/sysroot/ostree/deploy/os/var/log/x.log"):
        assert workspaces.hidden_system_entry(Path(path), Path(path).name, Path("/"))
    runtime = Path("/run/user") / str(os.getuid())
    files = sorted(p for p in runtime.glob("*") if p.is_file() and not p.is_symlink())
    if files:
        name = files[0].relative_to("/").as_posix()
        selected, skipped = workspaces.selected_system_files(Path("/"), [name], 10)
        assert selected == []
        assert skipped == [{"path": name, "reason": "sensitive_file"}]


@pytest.mark.parametrize("path", ["../", "%2e%2e/"])
def test_system_tree_traversal_outside_the_root_is_rejected(tmp_path, path):
    app = create_app(project_files_config(tmp_path))
    with TestClient(app, headers={"Authorization": "Bearer local"}) as client:
        response = client.get(f"/v1/project-files?view=tree&path={path}")
        assert response.status_code == 422
        assert response.json()["code"] == "path_not_authorized"
    app.state.service.db.close()


def test_attach_of_procfs_and_etc_files_is_never_actually_attached(tmp_path):
    environ = f"proc/{os.getpid()}/environ"
    if not (Path("/") / environ).is_file():
        pytest.skip("procfs is not available")
    # /proc/<pid> is not a symlink (unlike /proc/self), so this one is caught by the
    # sensitive-file filter rather than the symlink guard.
    for name in (environ, "etc/hostname"):
        selected, skipped = workspaces.selected_system_files(Path("/"), [name], 10)
        assert selected == []
        assert skipped == [{"path": name, "reason": "sensitive_file"}]
    # The filesystem root is no longer reachable through the API at all.
    app = create_app(project_files_config(tmp_path))
    with TestClient(app, headers={"Authorization": "Bearer local"}) as client:
        for name in (environ, "etc/hostname"):
            response = client.post(
                "/v1/project-files/attach?project_id=p",
                json={"root_id": "system", "paths": [name]},
            )
            assert (response.status_code, response.json()["code"]) == (422, "system_root_denied")
    app.state.service.db.close()


def test_home_root_does_not_list_or_attach_a_symlink_pointing_outside(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "visible.txt").write_text("hi")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret-ish")
    (home / "escape-link").symlink_to(outside)
    with patch("pathlib.Path.home", return_value=home):
        app = create_app(project_files_config(tmp_path))
        with TestClient(app, headers={"Authorization": "Bearer local"}) as client:
            tree = client.get("/v1/project-files?view=tree&root_id=home").json()
            assert [entry["name"] for entry in tree["entries"]] == ["visible.txt"]
            response = client.post(
                "/v1/project-files/attach?project_id=p",
                json={"root_id": "home", "paths": ["escape-link"]},
            )
            assert response.status_code in (200, 422)
            if response.status_code == 200:
                body = response.json()
                assert body["attachments"] == []
                assert body["skipped"][0]["reason"] == "symlink_denied"
            else:
                assert response.json()["code"] == "path_not_authorized"
        app.state.service.db.close()
