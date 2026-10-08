import asyncio
import os

import httpx
import pytest
from test_workspaces import config

from agent_service.app import create_app
from agent_service.effect_transport import validate_scoped_private_files
from agent_service.errors import APIError


@pytest.mark.parametrize("relocated", [False, True])
def test_other_owner_attachment_alias_never_reaches_scoped_worker(tmp_path, relocated):
    async def scenario():
        cfg = config(tmp_path / "state")
        project = tmp_path / "project"
        project.mkdir()
        cfg["projects"]["p"]["root"] = str(project)
        app = create_app(cfg)
        s = app.state.service
        private_files = s.root / "files"
        if relocated:
            target = tmp_path / "relocated-private-files"
            target.mkdir()
            private_files.symlink_to(target, target_is_directory=True)
        original = tmp_path / "original.txt"
        sentinel = "SYNTHETIC-R14-OTHER-OWNER-ATTACHMENT"
        original.write_text(sentinel)
        try:
            uploaded = await s.attach_project_files(
                ("b", cfg["clients"]["b"]),
                "p",
                [("private.txt", original)],
                [],
                "local",
                "installed-model",
                "scoped",
            )
            assert len(uploaded["attachments"]) == 1, uploaded
            fid = uploaded["attachments"][0]["file_id"]
            source = s.root / "files" / "p" / fid / "source"
            alias = project / "other-owner.txt"
            os.link(source, alias)
            # Confirm the ordinary owner API denies the same attachment to worker owner a.
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost",
                headers={"Authorization": "Bearer a"},
            ) as client:
                response = await client.get("/v1/files/" + fid + "/preview")
            assert response.status_code == 404 and response.json()["code"] == "file_not_found", (
                response.text
            )
            assert s.message_repository.owned_file(fid, "a") is None
            assert s.message_repository.owned_file(fid, "b") is not None
            with pytest.raises(APIError, match="scoped_private_file_linked"):
                validate_scoped_private_files(s)
        finally:
            await s.effects.close()
            s.db.close()

    asyncio.run(scenario())


def test_single_link_database_symlink_target_never_reaches_scoped_worker(tmp_path):
    cfg = config(tmp_path / "state")
    state, project = tmp_path / "state", tmp_path / "project"
    state.mkdir()
    project.mkdir()
    cfg["projects"]["p"]["root"] = str(project)
    database = project / "relocated-private.sqlite3"
    (state / "jobs.sqlite3").symlink_to(database)
    s = create_app(cfg).state.service
    sentinel = "SYNTHETIC-R14-OTHER-OWNER-DATABASE"
    try:
        job = s.submit(
            ("b", cfg["clients"]["b"]),
            {"project_id": "p", "backend": "codex", "model": "gpt-6-astra", "prompt": sentinel},
        )["job_id"]
        with pytest.raises(APIError):
            s.job(("a", cfg["clients"]["a"]), job)
        s.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert database.stat().st_nlink == 1
        with pytest.raises(APIError, match="scoped_private_file_linked"):
            validate_scoped_private_files(s)
    finally:
        s.db.close()
