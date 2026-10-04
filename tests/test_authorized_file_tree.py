"""The Files pane lists only project roots and reports observed Git state."""

import hashlib
import subprocess
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient
from test_workspaces import config

from agent_service import workspaces
from agent_service.app import create_app
from agent_service.tools import ToolError


def test_authorized_roots_git_badges_and_path_boundaries(tmp_path):
    project = tmp_path / "project"
    extra = tmp_path / "reference"
    project.mkdir()
    extra.mkdir()
    (project / "changed.py").write_text("before\n")
    (project / ".env").write_text("fixture-only\n")
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(["git", "-C", str(project), "add", "changed.py"], check=True)
    subprocess.run(["git", "-C", str(project), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.test", "-c", "commit.gpgsign=false",
                    "commit", "-qm", "fixture"], check=True)
    (project / "changed.py").write_text("after\n")
    (project / "added.py").write_text("new\n")
    (project / ":(glob)*.txt").write_text("literal filename\n")
    (project / "nested").mkdir()
    (project / "nested" / "untracked.txt").write_text("nested\n")
    subprocess.run(["git", "-C", str(project), "add", "added.py"], check=True)
    (extra / "notes.txt").write_text("Reference\n")
    (project / "escape").symlink_to(extra, target_is_directory=True)
    cfg = config(tmp_path / "state")
    cfg["projects"]["p"].update(root=str(project), additional_roots=[str(extra)])
    with TestClient(create_app(cfg)) as client:
        client.headers["Authorization"] = "Bearer a"
        response = client.get("/v1/project-files", params={"project_id": "p", "view": "authorized"})
        assert response.status_code == 200
        data = response.json()
        assert [root["id"] for root in data["roots"]] == ["root", "additional-0"]
        assert data["can_authorize"] is False
        entries = {entry["name"]: entry for entry in data["entries"]}
        assert entries["changed.py"]["status"] == "M"
        assert entries["added.py"]["status"] == "A"
        assert entries[":(glob)*.txt"]["status"] == "?"
        assert "status" not in entries["nested"]
        assert ".env" not in entries and "escape" not in entries
        selected = client.get("/v1/project-files", params={"project_id": "p", "view": "authorized",
                                                          "root_id": "additional-0"})
        assert [entry["name"] for entry in selected.json()["entries"]] == ["notes.txt"]
        for params in ({"root_id": "system"}, {"path": "../reference"}, {"path": "escape"}):
            denied = client.get("/v1/project-files", params={"project_id": "p", "view": "authorized", **params})
            assert denied.status_code in (403, 422)
        assert client.get("/v1/project-files?project_id=unknown&view=authorized").status_code == 403


def test_authorized_tree_without_registered_root_is_empty(tmp_path):
    with TestClient(create_app(config(tmp_path))) as client:
        client.headers["Authorization"] = "Bearer a"
        data = client.get("/v1/project-files?project_id=p&view=authorized").json()
        assert data["roots"] == []
        assert data["entries"] == []


def test_attachment_uses_only_the_requested_authorized_project_root(tmp_path):
    project, extra = tmp_path / "project", tmp_path / "extra"
    project.mkdir()
    extra.mkdir()
    (extra / "notes.txt").write_text("Reference")
    (extra / ".env").write_text("fixture-only")
    (extra / "escape.txt").symlink_to(project / "outside.txt")
    (project / "outside.txt").write_text("Outside selected root")
    cfg = config(tmp_path / "state")
    cfg["projects"]["p"].update(root=str(project), additional_roots=[str(extra)])
    app = create_app(cfg)
    with TestClient(app) as client, patch.object(
        app.state.service, "attach_project_files", AsyncMock(return_value={"attachments": [], "skipped": []})
    ) as attach:
        client.headers["Authorization"] = "Bearer a"
        response = client.post("/v1/project-files/attach?project_id=p", json={
            "project_root_id": "additional-0", "paths": ["notes.txt"],
        })
        assert response.status_code == 200
        assert attach.call_args.args[2] == [("notes.txt", extra / "notes.txt")]
        for root_id, path in (("system", "notes.txt"), ("root", "../extra/notes.txt"),
                              ("additional-0", ".env"), ("additional-0", "escape.txt")):
            response = client.post("/v1/project-files/attach?project_id=p", json={
                "project_root_id": root_id, "paths": [path],
            })
            assert response.status_code in (403, 422)
        assert attach.call_count == 1


def test_system_root_is_gone(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert "system" not in {root_id for root_id, _ in workspaces.system_roots()}
    with pytest.raises(ToolError, match="system_root_denied"):
        workspaces.system_root("system")
    assert workspaces.system_root("home") == tmp_path.resolve()


def test_hidden_component_in_request_rejected(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("private key")
    (home / "work" / ".config").mkdir(parents=True)
    (home / "work" / ".config" / "token").write_text("token")
    (home / "work" / "lost+found").mkdir()
    (home / "work" / "lost+found" / "orphan").write_text("orphan")
    (home / "work" / "notes.txt").write_text("notes")
    (home / "alias").symlink_to(home / ".ssh", target_is_directory=True)
    # The children of a hidden folder were listed because only the children were checked.
    for path in (".ssh", "work/.config", "work/lost+found", "alias"):
        with pytest.raises(ToolError, match="path_not_authorized"):
            workspaces.browse_system(home, path)
    assert [entry["name"] for entry in workspaces.browse_system(home, "work")["entries"]] == [
        "notes.txt"
    ]
    monkeypatch.setenv("HOME", str(home))
    cfg = config(tmp_path / "state")
    cfg["clients"]["local"] = {"sha256": hashlib.sha256(b"local").hexdigest(), "projects": ["p"]}
    with TestClient(create_app(cfg), headers={"Authorization": "Bearer local"}) as client:
        for path in (".ssh", "work/.config", "alias"):
            response = client.get("/v1/project-files", params={"view": "tree", "path": path})
            assert (response.status_code, response.json()["code"]) == (422, "path_not_authorized")
        assert client.get("/v1/project-files?view=tree&path=work").status_code == 200
