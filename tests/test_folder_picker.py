from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from control.server import create_app


def test_folder_listing_and_creation(tmp_path):
    root = tmp_path / "folders"
    root.mkdir()
    (root / "Project").mkdir()
    (root / "file.txt").write_text("not returned")
    with (
        patch("control.server.Manager.refresh", AsyncMock()),
        patch("control.server.Path.home", return_value=root),
    ):
        with TestClient(create_app(tmp_path / "state"), base_url="http://127.0.0.1:8094") as client:
            assert client.get("/api/folders").status_code == 401
            client.get("/")
            data = client.get("/api/folders").json()
            assert data["path"] == str(root)
            assert data["directories"] == [{"name": "Project", "path": str(root / "Project")}]
            assert not data["truncated"]
            assert (
                client.get("/api/folders", params={"path": str(root / "file.txt")}).status_code
                == 400
            )
            assert client.get("/api/folders", headers={"Host": "evil.test"}).status_code == 403
            headers = {"X-Harness-Admin": "1"}
            for name in ("../escape", "/absolute", "..", "a/b", "a\\b", ""):
                assert (
                    client.post(
                        "/api/folders/create",
                        json={"parent": str(root), "name": name},
                        headers=headers,
                    ).status_code
                    == 400
                )
            created = client.post(
                "/api/folders/create",
                json={"parent": str(root), "name": "New project"},
                headers=headers,
            )
            assert created.status_code == 200 and (root / "New project").is_dir()
            assert (
                client.post(
                    "/api/folders/create",
                    json={"parent": str(root), "name": "New project"},
                    headers=headers,
                ).status_code
                == 400
            )
            for n in range(202):
                (root / f"dir{n:03}").mkdir()
            data = client.get("/api/folders").json()
            assert data["truncated"] and len(data["directories"]) == 200
            with patch("control.server.os.scandir", side_effect=PermissionError):
                response = client.get("/api/folders")
                assert response.status_code == 400
                assert "permission" in response.json()["error"]
