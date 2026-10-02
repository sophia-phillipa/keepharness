"""The Files pane lists only project roots and reports observed Git state."""

import subprocess
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient
from test_workspaces import config

from agent_service.app import create_app


def test_authorized_roots_git_badges_and_path_boundaries(tmp_path):
    project = tmp_path / "project"
    extra = tmp_path / "reference"
    project.mkdir()
    extra.mkdir()
    (project / "changed.py").write_text("before\n")
    (project / ".env").write_text("fixture-only\n")
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(["git", "-C", str(project), "add", "changed.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(project),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
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
        selected = client.get(
            "/v1/project-files",
            params={"project_id": "p", "view": "authorized", "root_id": "additional-0"},
        )
        assert [entry["name"] for entry in selected.json()["entries"]] == ["notes.txt"]
        for params in ({"root_id": "system"}, {"path": "../reference"}, {"path": "escape"}):
            denied = client.get(
                "/v1/project-files", params={"project_id": "p", "view": "authorized", **params}
            )
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
    with (
        TestClient(app) as client,
        patch.object(
            app.state.service,
            "attach_project_files",
            AsyncMock(return_value={"attachments": [], "skipped": []}),
        ) as attach,
    ):
        client.headers["Authorization"] = "Bearer a"
        response = client.post(
            "/v1/project-files/attach?project_id=p",
            json={
                "project_root_id": "additional-0",
                "paths": ["notes.txt"],
            },
        )
        assert response.status_code == 200
        assert attach.call_args.args[2] == [("notes.txt", extra / "notes.txt")]
        for root_id, path in (
            ("system", "notes.txt"),
            ("root", "../extra/notes.txt"),
            ("additional-0", ".env"),
            ("additional-0", "escape.txt"),
        ):
            response = client.post(
                "/v1/project-files/attach?project_id=p",
                json={
                    "project_root_id": root_id,
                    "paths": [path],
                },
            )
            assert response.status_code in (403, 422)
        assert attach.call_count == 1
