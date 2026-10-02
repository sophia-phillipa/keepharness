import asyncio
import json
import os
import subprocess

import httpx
import pytest
from test_workspaces import config

from adapters.shared.scoped import prepare_scoped
from agent_service.app import create_app
from agent_service.effect_transport import scoped_enforcement, validate_scoped_private_files
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
                "codex",
                "gpt-6-astra",
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
            try:
                validate_scoped_private_files(s)
            except APIError as error:
                assert error.code == "scoped_private_file_linked"
                print(
                    json.dumps(
                        {
                            "scenario": "attachment-alias",
                            "relocated": relocated,
                            "preflight": "blocked",
                            "api": response.status_code,
                        }
                    )
                )
                return
            runtime = tmp_path / "runtime"
            (runtime / "bin").mkdir(parents=True)
            (runtime / "bin" / "python").symlink_to("/usr/bin/python3")
            auth = tmp_path / "auth.json"
            auth.write_text("{}")
            adapter = {
                "python": str(runtime / "bin" / "python"),
                "binary": "/usr/bin/python3",
                "auth_file": str(auth),
                "_validate_private_files": lambda: validate_scoped_private_files(s),
            }
            with prepare_scoped(
                adapter, {"root": str(project)}, {}, None, "codex", "auth.json"
            ) as scoped:
                enforcement = scoped_enforcement(s, scoped.command, copied_paths=[auth])
                result = subprocess.run(
                    [
                        *scoped.command,
                        "--",
                        "/usr/bin/python3",
                        "-c",
                        'from pathlib import Path; print(Path("/sources/project/other-owner.txt").read_text())',
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            facts = {
                "scenario": "attachment-alias",
                "relocated": relocated,
                "preflight": "allowed",
                "enforcement": enforcement,
                "same_inode": source.stat().st_ino == alias.stat().st_ino,
                "api": response.status_code,
                "sandbox_returncode": result.returncode,
                "private_bytes_read": sentinel in result.stdout,
            }
            print(json.dumps(facts))
            assert result.returncode == 0, result.stderr
            assert not facts["private_bytes_read"], facts
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
        try:
            validate_scoped_private_files(s)
        except APIError as error:
            assert error.code == "scoped_private_file_linked"
            return
        runtime = tmp_path / "runtime"
        (runtime / "bin").mkdir(parents=True)
        (runtime / "bin" / "python").symlink_to("/usr/bin/python3")
        auth = tmp_path / "auth.json"
        auth.write_text("{}")
        adapter = {
            "python": str(runtime / "bin" / "python"),
            "binary": "/usr/bin/python3",
            "auth_file": str(auth),
            "_validate_private_files": lambda: validate_scoped_private_files(s),
        }
        with prepare_scoped(
            adapter, {"root": str(project)}, {}, None, "codex", "auth.json"
        ) as scoped:
            enforcement = scoped_enforcement(s, scoped.command, copied_paths=[auth])
            result = subprocess.run(
                [
                    *scoped.command,
                    "--",
                    "/usr/bin/python3",
                    "-c",
                    "from pathlib import Path; print("
                    + repr(sentinel.encode())
                    + ' in Path("/sources/project/relocated-private.sqlite3").read_bytes())',
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
        facts = {
            "scenario": "single-link-database-target",
            "nlink": database.stat().st_nlink,
            "preflight": "allowed",
            "enforcement": enforcement,
            "sandbox_returncode": result.returncode,
            "private_bytes_read": result.stdout.strip() == "True",
        }
        print(json.dumps(facts))
        assert result.returncode == 0, result.stderr
        assert not facts["private_bytes_read"], facts
    finally:
        s.db.close()
