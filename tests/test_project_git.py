import subprocess

from agent_service.app import project_git


def test_project_git(tmp_path):
    assert project_git(None) is None
    assert project_git(str(tmp_path)) is None

    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()

    git("init", "-b", "feature/composer")
    assert project_git(str(tmp_path)) == "feature/composer"
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.test",
        "commit",
        "--allow-empty",
        "-m",
        "fixture",
    )
    git("checkout", "--detach")
    assert project_git(str(tmp_path)) == git("rev-parse", "--short", "HEAD")


def test_git_endpoint_only_reads_authorized_root(tmp_path):
    import hashlib
    from unittest.mock import patch

    from starlette.testclient import TestClient

    from agent_service.app import create_app

    config = {
        "state_dir": str(tmp_path / "state"),
        "projects": {"allowed": {"root": str(tmp_path)}, "private": {"root": "/private"}},
        "clients": {"a": {"sha256": hashlib.sha256(b"a").hexdigest(), "projects": ["allowed"]}},
        "services": {},
    }
    app = create_app(config)
    with patch("agent_service.routes.projects.project_git", return_value="main") as read:
        with TestClient(app, headers={"Authorization": "Bearer a"}) as client:
            assert client.get("/v1/project-git?project_id=private").status_code == 403
            read.assert_not_called()
            assert client.get("/v1/project-git?project_id=allowed").json() == {"revision": "main"}
            read.assert_called_once_with(str(tmp_path))
    app.state.service.db.close()
