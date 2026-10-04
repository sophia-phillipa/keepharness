"""Safe lazy project browser and attachment import."""

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from agent_service.app import create_app


def config(tmp_path):
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


@pytest.fixture(autouse=True)
def host_home(tmp_path, monkeypatch):
    """The owner's host folders are browsed from the home root (the filesystem root is gone)."""
    monkeypatch.setenv("HOME", str(tmp_path))


def test_system_browser_lists_and_attaches_external_folder_to_project(tmp_path):
    external = tmp_path / "external"
    (external / "src").mkdir(parents=True)
    (external / "src" / "one.txt").write_text("one")
    (external / "etc").mkdir()
    (external / "etc" / "notes.txt").write_text("user folder")
    (external / "src" / ".env").write_text("secret")
    (external / "two.txt").write_text("two")
    (external / ".hidden.txt").write_text("hidden")
    external_path = external.relative_to(tmp_path).as_posix()
    app = create_app(config(tmp_path))
    with TestClient(app, headers={"Authorization": "Bearer local"}) as client:
        tree = client.get("/v1/project-files?view=tree&root_id=home&path=" + external_path).json()
        assert tree["state"] == "ready"
        assert "system" not in {root["id"] for root in tree["roots"]}
        assert {entry["name"] for entry in tree["entries"]} == {"src", "etc", "two.txt"}
        result = client.post(
            "/v1/project-files/attach?project_id=p&max_files=2",
            json={
                "root_id": "home",
                "paths": [external_path + "/src", external_path + "/two.txt"],
                "backend": "codex",
                "model": "fixture",
            },
        ).json()
        assert {item["name"] for item in result["attachments"]} == {
            external_path + "/src/one.txt",
            external_path + "/two.txt",
        }
        assert result["skipped"] == [
            {"path": external_path + "/src/.env", "reason": "sensitive_file"}
        ]
        stored = app.state.service.db.execute("SELECT name FROM files ORDER BY name").fetchall()
        assert [row[0] for row in stored] == sorted(
            [external_path + "/src/one.txt", external_path + "/two.txt"]
        )
        assert (
            tmp_path / "state" / "files" / "p" / result["attachments"][0]["file_id"] / "source"
        ).read_text() in {"one", "two"}
    app.state.service.db.close()


def test_tree_defaults_to_home_and_hides_system_and_dot_entries(tmp_path):
    from agent_service import workspaces

    app = create_app(config(tmp_path))
    with TestClient(app, headers={"Authorization": "Bearer local"}) as client:
        tree = client.get("/v1/project-files?view=tree").json()
        assert tree["root_id"] == "home"
        assert {root["id"] for root in tree["roots"]} >= {"home"}
        assert "system" not in {root["id"] for root in tree["roots"]}
    root_entries = workspaces.browse_system(Path("/"))["entries"]
    assert not {"etc", "proc", "sys", "dev", "usr", "boot", "ostree", "var"} & {
        entry["name"] for entry in root_entries
    }
    assert not any(entry["name"].startswith(".") for entry in root_entries)
    app.state.service.db.close()


def test_system_browser_denies_escape_skips_symlink_and_needs_authentication(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    (external / "visible.txt").write_text("safe")
    secret = tmp_path / "secret.txt"
    secret.write_text("private")
    (external / "link").symlink_to(secret)
    external_path = external.relative_to(tmp_path).as_posix()
    app = create_app(config(tmp_path))
    with TestClient(app, headers={"Authorization": "Bearer local"}) as client:
        assert "link" in {
            entry["name"]
            for entry in client.get(
                "/v1/project-files?view=tree&root_id=home&path=" + external_path
            ).json()["entries"]
        }
        result = client.post(
            "/v1/project-files/attach?project_id=p",
            json={"root_id": "home", "paths": [external_path + "/link"]},
        ).json()
        assert result == {
            "attachments": [],
            "skipped": [{"path": external_path + "/link", "reason": "symlink_denied"}],
        }
        assert (
            client.post(
                "/v1/project-files/attach?project_id=p",
                json={"root_id": "home", "paths": ["../secret.txt"]},
            ).status_code
            == 422
        )
        assert client.get("/v1/project-files?view=tree&path=../").status_code == 422
    anonymous = TestClient(app)
    assert anonymous.get("/v1/project-files?view=tree").status_code == 401
    anonymous.close()
    app.state.service.db.close()


def test_system_browser_needs_no_project_but_attach_requires_read_permission(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    (external / "visible.txt").write_text("safe")
    external_path = external.relative_to(tmp_path).as_posix()
    cfg = config(tmp_path)
    cfg["services"]["codex"]["permissions"]["read"] = False
    app = create_app(cfg)
    with TestClient(app, headers={"Authorization": "Bearer local"}) as client:
        assert client.get("/v1/project-files?view=tree").status_code == 200
        assert (
            client.post(
                "/v1/project-files/attach?project_id=p",
                json={"root_id": "home", "paths": [external_path + "/visible.txt"]},
            ).status_code
            == 403
        )
    app.state.service.db.close()


def test_project_browser_attach_requires_upload_permission(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    (external / "visible.txt").write_text("safe")
    external_path = external.relative_to(tmp_path).as_posix()
    cfg = config(tmp_path)
    cfg["services"]["codex"]["permissions"]["upload"] = False
    app = create_app(cfg)
    with TestClient(app, headers={"Authorization": "Bearer local"}) as client:
        response = client.post(
            "/v1/project-files/attach?project_id=p",
            json={"root_id": "home", "paths": [external_path + "/visible.txt"]},
        )
        assert response.status_code == 403
        assert response.json()["code"] == "uploads_denied"
    app.state.service.db.close()


def test_project_selection_bounds_directory_scan(tmp_path, monkeypatch):
    from agent_service import workspaces

    root = tmp_path / "project"
    (root / "folder").mkdir(parents=True)
    for index in range(3):
        (root / "folder" / f"{index}.txt").write_text(str(index))
    monkeypatch.setattr(workspaces, "MAX_FILES", 2)
    selected, skipped = workspaces.selected_system_files(root, ["folder"], 10)
    assert len(selected) == 2
    assert skipped == [{"path": "folder", "reason": "selection_scan_limit"}]


def test_system_browser_canonicalizes_directory_symlink_without_following_it_for_attach(tmp_path):
    from agent_service import workspaces

    root = tmp_path / "root"
    (root / "real").mkdir(parents=True)
    (root / "real" / "note.txt").write_text("note")
    (root / "alias").symlink_to(root / "real", target_is_directory=True)
    assert workspaces.browse_system(root, "alias")["path"] == "real"
    _, skipped = workspaces.selected_system_files(root, ["alias"], 1)
    assert skipped == [{"path": "alias", "reason": "symlink_denied"}]


def test_system_attachment_uses_model_from_query_for_images(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    (external / "image.png").write_bytes(b"fixture")
    external_path = external.relative_to(tmp_path).as_posix()
    app = create_app(config(tmp_path))
    with (
        TestClient(app, headers={"Authorization": "Bearer local"}) as client,
        patch(
            "agent_service.tools.extract",
            new=AsyncMock(return_value=[{"media_type": "image/png"}]),
        ),
        patch.object(app.state.service, "validate_images", new=AsyncMock()) as validate,
    ):
        response = client.post(
            "/v1/project-files/attach?project_id=p&backend=codex&model=fixture",
            json={
                "root_id": "home",
                "paths": [external_path + "/image.png"],
                "backend": "wrong",
                "model": "wrong",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["attachments"][0]["media_type"] == "image/png"
        assert response.json()["attachments"][0]["preview_url"].endswith("/preview")
        validate.assert_awaited_once_with("codex", "fixture", "native")
    app.state.service.db.close()


def test_navigate_primary_project_folder_uses_visible_root(tmp_path):
    from agent_service import workspaces

    home = tmp_path / "home"
    first = home / "first"
    first.mkdir(parents=True)
    (first / "main.txt").write_text("hello")
    cfg = config(tmp_path)
    cfg["projects"]["p"] = {"root": str(first), "additional_roots": [str(home / "second")]}
    app = create_app(cfg)
    with (
        patch.object(workspaces, "system_roots", return_value=[("home", home)]),
        TestClient(app, headers={"Authorization": "Bearer local"}) as client,
    ):
        response = client.get("/v1/project-files?view=tree&navigate_project=1&project_id=p")
        assert response.status_code == 200
        data = response.json()
        assert data["root_id"] == "home" and data["path"] == "first"
        assert [entry["name"] for entry in data["entries"]] == ["main.txt"]
        assert (
            client.get(
                "/v1/project-files?view=tree&navigate_project=1&project_id=unknown"
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/v1/project-files?view=tree&navigate_project=1&project_id=sem-projeto"
            ).status_code
            == 422
        )
    app.state.service.db.close()
