"""Explicit, path-bound primary folder deletion; only temporary fixtures are removed."""

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from agent_service.app import APIError, create_app
from tests.test_project_browser import config as browser_config


def config(tmp_path):
    """The browser fixture plus the owner on this computer, who alone deletes folders."""
    cfg = browser_config(tmp_path)
    cfg["clients"]["local"] = {
        "sha256": hashlib.sha256(b"local").hexdigest(),
        "projects": ["p", "sem-projeto"],
    }
    return cfg


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "primary"
    root.mkdir()
    (root / "nested").mkdir()
    (root / "nested" / "file.txt").write_text("delete me")
    extra = tmp_path / "additional"
    extra.mkdir()
    (extra / "keep.txt").write_text("keep")
    cfg = config(tmp_path)
    cfg["projects"]["p"] = {"root": str(root), "additional_roots": [str(extra)]}
    app = create_app(cfg)
    with TestClient(app, headers={"Authorization": "Bearer local"}) as client:
        yield client, app.state.service, root, extra
    app.state.service.db.close()


URL = "/v1/project-folder?project_id=p"


def payload(client):
    response = client.get(URL)
    assert response.status_code == 200, response.text
    return {**response.json(), "confirmed": True}


def delete(client, data):
    return client.request("DELETE", URL, json=data)


def test_confirmed_delete_only_primary_and_symlink_itself(setup):
    client, service, root, extra = setup
    (root / "external-link").symlink_to(extra, target_is_directory=True)
    data = payload(client)
    assert data["paths"] == [str(root)]
    response = delete(client, data)
    assert response.status_code == 200, response.text
    assert response.json()["deleted_paths"] == [str(root)]
    assert not root.exists() and (extra / "keep.txt").read_text() == "keep"
    assert "p" in service.config["projects"]  # Conversation access remains available.
    assert delete(client, data).status_code != 200


@pytest.mark.parametrize("confirmed", [None, False, "true", 1])
def test_requires_boolean_confirmation(setup, confirmed):
    client, _, root, _ = setup
    data = payload(client)
    data["confirmed"] = confirmed
    assert delete(client, data).json()["code"] == "project_folder_confirmation_required"
    assert root.exists()


def test_exact_paths_revision_and_replaced_directory(setup):
    client, _, root, extra = setup
    data = payload(client)
    assert delete(client, {**data, "paths": [str(extra)]}).status_code == 409
    assert delete(client, {**data, "revision": "stale"}).status_code == 409
    moved = root.with_name("old")
    root.rename(moved)
    root.mkdir()
    assert delete(client, data).status_code == 409
    assert root.exists() and (moved / "nested" / "file.txt").exists()


def test_authentication_and_project_grant(setup):
    client, service, root, _ = setup
    data = payload(client)
    assert (
        client.request(
            "DELETE", URL, json=data, headers={"Authorization": "Bearer unknown"}
        ).status_code
        == 401
    )
    assert (
        client.request(
            "DELETE",
            URL,
            json=data,
            headers={"Authorization": "Bearer local", "Origin": "https://foreign.invalid"},
        ).status_code
        == 403
    )
    service.config["clients"]["local"]["projects"] = []
    assert delete(client, data).status_code == 403
    assert root.exists()


@pytest.mark.parametrize(
    "target", ["home", "state", "ancestor", "system", "shared", "nested_shared", "symlink"]
)
def test_rejects_protected_shared_and_symlink_roots(setup, target):
    client, service, root, extra = setup
    if target == "home":
        service.config["projects"]["p"]["root"] = str(Path.home())
    elif target == "state":
        service.config["projects"]["p"]["root"] = str(service.root)
    elif target == "ancestor":
        service.config["projects"]["p"]["root"] = str(service.root.parent)
    elif target == "system":
        service.config["projects"]["p"]["root"] = "/"
    elif target == "shared":
        service.config["projects"]["other"] = {"root": str(root)}
    elif target == "nested_shared":
        service.config["projects"]["other"] = {
            "root": str(extra),
            "additional_roots": [str(root / "nested")],
        }
    elif target == "symlink":
        link = root.with_name("link")
        link.symlink_to(root, target_is_directory=True)
        service.config["projects"]["p"]["root"] = str(link)
    assert client.get(URL).status_code in (403, 409, 422)
    assert (root / "nested" / "file.txt").exists()


def test_busy_revalidation_failure_and_submission_during_delete(setup):
    client, service, root, _ = setup
    data = payload(client)
    with service.db:
        service.db.execute("INSERT INTO jobs(id,project,state) VALUES('busy','p','queued')")
    assert delete(client, data).json()["code"] == "project_folder_busy"
    with service.db:
        service.db.execute("DELETE FROM jobs WHERE id='busy'")

    def fail(*args, **kwargs):
        with pytest.raises(APIError, match="project_folder_busy"):
            service.submit(("a", service.config["clients"]["a"]), {"project_id": "p"})
        raise PermissionError("fixture")

    fail.avoids_symlink_attacks = True
    with patch("shutil.rmtree", fail):
        assert delete(client, data).json()["code"] == "project_folder_delete_failed"
    assert not service.deleting_project_folders and root.exists()


def test_deleted_folder_stays_out_of_catalog_after_restart(setup):
    import copy

    client, service, root, _ = setup
    cfg = copy.deepcopy(service.config)
    assert delete(client, payload(client)).status_code == 200
    assert "p" not in client.get("/v1/projects").json()["projects"]
    with TestClient(create_app(cfg), headers={"Authorization": "Bearer local"}) as restarted:
        assert "p" not in restarted.get("/v1/projects").json()["projects"]
    assert not root.exists()


def test_duplicate_registration_is_deleted_together_and_checks_busy_alias(setup):
    client, service, root, _ = setup
    service.config["projects"]["p"]["label"] = "Primary"
    service.config["projects"]["alias"] = {"label": "Primary", "root": str(root)}
    service.config["clients"]["local"]["projects"].append("alias")
    data = payload(client)
    assert data["project_ids"] == ["alias", "p"]
    with service.db:
        service.db.execute("INSERT INTO jobs(id,project,state) VALUES('busy','alias','queued')")
    assert delete(client, data).json()["code"] == "project_folder_busy"
    with service.db:
        service.db.execute("DELETE FROM jobs WHERE id='busy'")
    result = delete(client, data)
    assert result.status_code == 200, result.text
    assert not root.exists()
    assert not {"p", "alias"} & set(client.get("/v1/projects").json()["projects"])


def test_alias_requires_access_and_reconfirmation(setup):
    client, service, root, _ = setup
    data = payload(client)
    service.config["projects"]["alias"] = {"label": "p", "root": str(root)}
    assert client.get(URL).json()["code"] == "project_directory_shared"
    service.config["clients"]["local"]["projects"].append("alias")
    assert delete(client, data).json()["code"] == "project_folder_changed"
    assert root.exists()


def test_discovery_deduplicates_physical_directories(tmp_path):
    import asyncio
    from unittest.mock import AsyncMock

    from control.discovery import scan

    folder = tmp_path / "Projects"
    folder.mkdir()
    (folder / "example" / ".git").mkdir(parents=True)
    alias = tmp_path / "projects"
    if not alias.exists():
        alias.symlink_to(folder, target_is_directory=True)
    with (
        patch("control.discovery.Path.home", return_value=tmp_path),
        patch("control.discovery.shutil.which", return_value=None),
        patch("control.discovery.discover", AsyncMock(return_value=[])),
    ):
        result = asyncio.run(scan())
    assert len(result["projects"]) == 1


def test_already_missing_folder_can_remove_catalog_entry(setup):
    import shutil

    client, service, root, extra = setup
    shutil.rmtree(root)
    data = payload(client)
    assert data["missing"] is True
    result = delete(client, data)
    assert result.status_code == 200, result.text
    assert "p" not in client.get("/v1/projects").json()["projects"]
    assert extra.exists()


def test_missing_folder_reappears_requires_new_confirmation(setup):
    import shutil

    client, _, root, _ = setup
    shutil.rmtree(root)
    data = payload(client)
    root.mkdir()
    (root / "new.txt").write_text("keep")
    assert delete(client, data).status_code == 409
    assert (root / "new.txt").exists()


def test_repository_checkout_is_a_protected_folder(tmp_path):
    from agent_service.config import PACKAGE_DIR, REPOSITORY_ROOT, VERSION_FILE

    assert (REPOSITORY_ROOT / "pyproject.toml").is_file()
    assert PACKAGE_DIR == REPOSITORY_ROOT / "agent_service" and VERSION_FILE.is_file()
    cfg = config(tmp_path)
    cfg["projects"]["p"] = {"root": str(REPOSITORY_ROOT / "docs")}
    app = create_app(cfg)
    with TestClient(app, headers={"Authorization": "Bearer local"}) as client:
        response = client.get(URL)
    app.state.service.db.close()
    assert response.status_code == 403
    assert response.json()["code"] == "project_directory_forbidden"
