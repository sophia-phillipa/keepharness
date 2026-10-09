"""Temporary sessions never enter durable conversation storage."""

import asyncio
import copy
import logging
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient
from test_model_handoff import add_turn
from test_workspaces import config

from agent_service.app import create_app
from agent_service.services.conversation_service import ConversationService


def test_temporary_turns_uploads_and_close(tmp_path, monkeypatch):
    async def execute(service, row):
        return {"answer": "private answer", "metrics": None}

    monkeypatch.setattr(ConversationService, "execute", execute)
    app = create_app(config(tmp_path))
    with TestClient(app, headers={"Authorization": "Bearer a"}) as client:
        response = client.post("/v1/temporary")
        assert response.status_code == 201, response.text
        sid = response.json()["id"]
        headers = {"X-KeepHarness-Temporary": sid}
        upload = client.post(
            "/v1/files?project_id=p",
            headers={**headers, "X-Filename": "private.txt"},
            content=b"private attachment",
        )
        assert upload.status_code == 201, upload.text
        data = {
            "project_id": "p",
            "backend": "codex",
            "model": "gpt-6-astra",
            "prompt": "private prompt",
            "temporary": True,
            "file_ids": [upload.json()["file_id"]],
        }
        sent = client.post("/v1/jobs", headers=headers, json=data)
        assert sent.status_code == 202, sent.text
        job = sent.json()["job_id"]
        service = app.state.service.temporary.sessions[sid].service
        assert service.db.execute("PRAGMA database_list").fetchone()[2] == ""
        assert app.state.service.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        assert app.state.service.db.execute("SELECT count(*) FROM files").fetchone()[0] == 0
        assert client.get("/v1/jobs/" + job).status_code == 404
        assert client.get("/v1/jobs/" + job, headers=headers).status_code == 200
        assert client.get("/v1/history").json()["jobs"] == []
        assert client.get("/v1/conversations?q=private").json()["conversations"] == []
        assert client.get("/v1/history", headers=headers).json()["jobs"] == []
        assert client.get("/v1/conversations", headers=headers).json()["conversations"] == []
        assert (
            client.get("/v1/jobs/" + job + "/artifacts/result.json", headers=headers).status_code
            == 409
        )
        assert (
            client.delete("/v1/temporary/" + sid, headers={"Authorization": "Bearer b"}).status_code
            == 404
        )
        root = service.root
        assert root.exists()
        assert client.delete("/v1/temporary/" + sid).status_code == 200
        assert not root.exists()
        assert client.get("/v1/jobs/" + job, headers=headers).status_code == 404
        for path in tmp_path.rglob("*"):
            if path.is_file():
                assert b"private prompt" not in path.read_bytes()
                assert b"private answer" not in path.read_bytes()
                assert b"private attachment" not in path.read_bytes()


def test_temporary_shutdown_and_crash_sweep(tmp_path):
    app = create_app(config(tmp_path))
    stale = tmp_path / "temporary-chats" / ("a" * 32)
    stale.mkdir(parents=True)
    (stale / "source").write_text("crash residue")
    restarted = create_app(config(tmp_path))
    assert not stale.exists()
    with TestClient(restarted, headers={"Authorization": "Bearer a"}) as client:
        sid = client.post("/v1/temporary").json()["id"]
        root = restarted.state.service.temporary.sessions[sid].service.root
        assert root.exists()
    assert not root.exists()
    app.state.service.db.close()


def test_crash_sweep_also_removes_the_stale_run_folders(tmp_path):
    app = create_app(config(tmp_path))
    sid = "a" * 32
    (tmp_path / "temporary-chats" / sid).mkdir(parents=True)
    residue = app.state.service.temporary.sessions_base / sid / "conversation" / "codex"
    residue.mkdir(parents=True)
    (residue / "native-thread.json").write_text("crash residue")
    create_app(config(tmp_path))
    assert not residue.parent.parent.exists()
    app.state.service.db.close()


def test_symlinked_run_folder_never_blocks_the_crash_sweep(tmp_path):
    app = create_app(config(tmp_path))
    sid = "b" * 32
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("not ours")
    (tmp_path / "temporary-chats" / sid).mkdir(parents=True)
    link = app.state.service.temporary.sessions_base / sid
    link.symlink_to(outside, target_is_directory=True)
    restarted = create_app(config(tmp_path))
    assert not (tmp_path / "temporary-chats" / sid).exists()
    assert not link.is_symlink()
    assert (outside / "keep.txt").read_text() == "not ours"
    app.state.service.db.close()
    restarted.state.service.db.close()


def test_discard_removes_the_state_folder_even_when_the_run_folder_is_a_symlink(tmp_path):
    async def scenario():
        parent = ConversationService(config(tmp_path))
        identity = ("a", parent.config["clients"]["a"])
        sid = await parent.temporary.open(identity)
        outside = tmp_path / "outside"
        outside.mkdir()
        link = parent.temporary.sessions_base / sid
        if link.exists():
            link.rmdir()
        link.symlink_to(outside, target_is_directory=True)
        await parent.temporary.close(identity, sid)
        assert not (parent.temporary.root / sid).exists()
        assert not link.is_symlink()
        assert outside.is_dir()
        parent.db.close()

    asyncio.run(scenario())


def test_cancelled_open_removes_its_folders(tmp_path):
    import threading

    async def scenario():
        parent = ConversationService(config(tmp_path))
        identity = ("a", parent.config["clients"]["a"])
        publish = parent.temporary._publish
        started, release = threading.Event(), threading.Event()

        def slow(root):
            started.set()
            release.wait(5)
            return publish(root)

        parent.temporary._publish = slow
        task = asyncio.create_task(parent.temporary.open(identity))
        await asyncio.to_thread(started.wait, 5)
        task.cancel()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()
        assert not parent.temporary.sessions
        assert not list(parent.temporary.root.iterdir())
        assert not list(parent.temporary.sessions_base.iterdir())
        parent.db.close()

    asyncio.run(scenario())


def test_open_during_a_slow_close_keeps_the_event_loop_responsive(tmp_path, monkeypatch):
    import shutil
    import threading
    import time

    async def scenario():
        parent = ConversationService(config(tmp_path))
        identity = ("a", parent.config["clients"]["a"])
        first = await parent.temporary.open(identity)
        started, release = threading.Event(), threading.Event()
        remove = shutil.rmtree

        def slow(path, *args, **kwargs):
            started.set()
            release.wait(5)
            remove(path, *args, **kwargs)

        monkeypatch.setattr(shutil, "rmtree", slow)
        threading.Timer(1.0, release.set).start()  # a blocked loop cannot release it itself
        closing = asyncio.create_task(parent.temporary.close(identity, first))
        await asyncio.to_thread(started.wait, 5)
        opening = asyncio.create_task(parent.temporary.open(identity))
        begun = time.monotonic()
        await asyncio.sleep(0.01)
        assert time.monotonic() - begun < 0.5
        assert not opening.done()
        release.set()
        await closing
        second = await opening
        assert second in parent.temporary.sessions
        await parent.temporary.close_all()
        parent.db.close()

    asyncio.run(scenario())


def test_temporary_marker_without_session_never_persists(tmp_path):
    with TestClient(create_app(config(tmp_path)), headers={"Authorization": "Bearer a"}) as client:
        response = client.post(
            "/v1/jobs",
            json={
                "temporary": True,
                "project_id": "p",
                "backend": "codex",
                "model": "gpt-6-astra",
                "prompt": "private prompt",
            },
        )
        assert response.status_code == 409
        assert client.app.state.service.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_temporary_errors_do_not_log_content(tmp_path, monkeypatch, caplog):
    async def execute(service, row):
        logging.getLogger("adapters.fixture").error("private provider diagnostic")
        raise RuntimeError("private provider exception")

    monkeypatch.setattr(ConversationService, "execute", execute)
    with TestClient(create_app(config(tmp_path)), headers={"Authorization": "Bearer a"}) as client:
        sid = client.post("/v1/temporary").json()["id"]
        headers = {"X-KeepHarness-Temporary": sid}
        result = client.post(
            "/v1/jobs",
            headers=headers,
            json={
                "project_id": "p",
                "backend": "codex",
                "model": "gpt-6-astra",
                "prompt": "private prompt",
            },
        )
        assert result.status_code == 202
        job = result.json()["job_id"]
        for _ in range(50):
            state = client.get("/v1/jobs/" + job, headers=headers).json()["state"]
            if state == "failed":
                break
        assert state == "failed"
    assert "private provider" not in caplog.text


def test_temporary_history_replayed_inline_and_provider_flags(tmp_path):
    async def scenario():
        cfg = config(tmp_path)
        cfg["codex"] = {}
        parent = ConversationService(cfg)
        identity = ("a", cfg["clients"]["a"])
        sid = await parent.temporary.open(identity)
        service = parent.temporary.get(identity, sid).service
        service.quota = AsyncMock(return_value={"available": False})
        row, data = add_turn(service, "first", "codex", model="gpt-6-astra")
        service.finish("first", "completed", {"answer": "volatile first answer"})
        row, data = add_turn(service, "second", "codex", parent="first", model="gpt-6-astra")
        captured = {}

        async def run(config, prompt, event, project, model, effort, session, provider, approve):
            captured.update(config=config, project=project, prompt=prompt)
            event("session_turn_started", {"thread_id": "volatile-thread"})
            return {"answer": "volatile second answer", "thread_id": "volatile-thread"}

        with patch("adapters.run_native", side_effect=run):
            result = await service.infer(row, data)
        assert result["answer"] == "volatile second answer"
        assert captured["config"]["temporary_chat"] is True
        assert captured["project"]["temporary_chat"] is True
        assert "volatile first answer" in captured["prompt"]
        assert "request-first" in captured["prompt"]
        assert not list(service.root.rglob("*cursor*"))
        assert not list(service.root.rglob("*history*"))
        assert not list(service.root.rglob("native-thread.json"))
        await parent.temporary.close_all()
        parent.db.close()

    asyncio.run(scenario())


def test_preferences_drop_temporary_ids_even_after_close(tmp_path):
    cfg = config(tmp_path)
    cfg["clients"] = {"local": cfg["clients"]["a"]}
    with TestClient(create_app(cfg), headers={"Authorization": "Bearer a"}) as client:
        response = client.patch(
            "/v1/ui-state",
            json={
                "values": {
                    "conversation_activity": {
                        "tmp-expired": {"state": "completed", "unread": True}
                    },
                    "conversation_scroll": [["tmp-expired", 12], ["ordinary", 7]],
                }
            },
        )
        assert response.status_code == 200, response.text
        assert "tmp-expired" not in response.text
        for path in (tmp_path / "ui-state").rglob("*.json"):
            assert "tmp-expired" not in path.read_text()


def test_temporary_services_share_capacity_and_quotas(tmp_path):
    async def scenario():
        cfg = config(tmp_path)
        parent = ConversationService(cfg)
        identity = ("a", cfg["clients"]["a"])
        sid = await parent.temporary.open(identity)
        service = parent.temporary.get(identity, sid).service
        assert service.write_ownership is parent.write_ownership
        assert service.provider_lanes is parent.provider_lanes
        assert service.provider_slots is parent.provider_slots
        assert service.requests is parent.requests
        for i in range(10):
            add_turn(parent, str(i), "codex", model="gpt-6-astra")
        import pytest

        from agent_service.errors import APIError

        with pytest.raises(APIError, match="owner_queue_full"):
            service.submit(
                identity,
                {
                    "project_id": "p",
                    "backend": "codex",
                    "model": "gpt-6-astra",
                    "prompt": "private",
                },
            )
        from agent_service.services import capacity

        parent.wake.clear()
        service.wake.clear()
        capacity.notify_lanes(service)
        assert parent.wake.is_set() and service.wake.is_set()
        await parent.temporary.close_all()
        parent.db.close()

    asyncio.run(scenario())


def test_temporary_unsupported_backend_and_expired_session_fail_closed(tmp_path):
    with TestClient(create_app(config(tmp_path)), headers={"Authorization": "Bearer a"}) as client:
        sid = client.post("/v1/temporary").json()["id"]
        result = client.post(
            "/v1/jobs",
            headers={"X-KeepHarness-Temporary": sid},
            json={"backend": "gemini", "prompt": "private"},
        )
        assert result.status_code == 422
        assert result.json()["code"] == "temporary_backend_unsupported"
        assert (
            client.post(
                "/v1/jobs",
                headers={"X-KeepHarness-Temporary": "expired"},
                json={"prompt": "private"},
            ).status_code
            == 404
        )


def test_startup_does_not_sweep_another_live_temporary_session(tmp_path):
    async def scenario():
        cfg = config(tmp_path)
        parent = ConversationService(cfg)
        identity = ("a", cfg["clients"]["a"])
        sid = await parent.temporary.open(identity)
        root = parent.temporary.get(identity, sid).service.root
        another = ConversationService(config(tmp_path))
        assert root.exists()
        await parent.temporary.close_all()
        another.db.close()
        parent.db.close()

    asyncio.run(scenario())


def test_runtime_changes_reach_temporary_pending_jobs(tmp_path):
    from test_runtime_reload import queued
    from test_workspaces import single_owner_config

    async def scenario():
        cfg = single_owner_config(tmp_path)
        parent = ConversationService(copy.deepcopy(cfg))
        identity = ("local", cfg["clients"]["local"])
        sid = await parent.temporary.open(identity)
        service = parent.temporary.get(identity, sid).service
        queued(
            service,
            "tmp-pending",
            {"backend": "codex", "model": "gpt-6-astra", "prompt": "private", "project_id": "p"},
        )
        candidate = copy.deepcopy(cfg)
        candidate["services"]["codex"]["models"] = []
        candidate["codex_models"] = {}
        await parent.apply_runtime_config(candidate)
        assert service.conversation_repository.get("tmp-pending")["state"] == "cancelled"
        assert service.config["temporary_chat"] is True
        assert service.config["state_dir"] == str(service.root)
        assert parent.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        await parent.temporary.close_all()
        parent.db.close()

    asyncio.run(scenario())


def test_abandoned_temporary_session_expires_and_cannot_be_revived(tmp_path):
    async def scenario():
        import pytest

        from agent_service.errors import APIError

        cfg = config(tmp_path)
        parent = ConversationService(cfg)
        identity = ("a", cfg["clients"]["a"])
        sid = await parent.temporary.open(identity)
        session = parent.temporary.get(identity, sid)
        root = session.service.root
        session.deadline = 0
        with pytest.raises(APIError, match="temporary_session_not_found"):
            parent.temporary.get(identity, sid)
        await parent.temporary.expire()
        assert not root.exists()
        assert not parent.temporary.sessions
        parent.db.close()

    asyncio.run(scenario())


def test_temporary_heartbeat_is_owner_bound(tmp_path):
    with TestClient(create_app(config(tmp_path)), headers={"Authorization": "Bearer a"}) as client:
        sid = client.post("/v1/temporary").json()["id"]
        session = client.app.state.service.temporary.sessions[sid]
        before = session.deadline
        assert client.get("/v1/temporary/" + sid).status_code == 200
        assert session.deadline >= before
        assert (
            client.get("/v1/temporary/" + sid, headers={"Authorization": "Bearer b"}).status_code
            == 404
        )


def test_cancelled_close_still_finishes_artifact_cleanup(tmp_path):
    async def scenario():
        cfg = config(tmp_path)
        parent = ConversationService(cfg)
        identity = ("a", cfg["clients"]["a"])
        sid = await parent.temporary.open(identity)
        session = parent.temporary.get(identity, sid)
        entered, release = asyncio.Event(), asyncio.Event()

        async def close_effects():
            entered.set()
            await release.wait()

        session.service.effects.close = close_effects
        task = asyncio.create_task(parent.temporary.close(identity, sid))
        await entered.wait()
        task.cancel()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert not session.service.root.exists()
        assert not parent.temporary.sessions
        parent.db.close()

    asyncio.run(scenario())


def test_temporary_upload_is_never_in_backup_even_with_secrets(tmp_path):
    import tarfile

    from control import backup

    state = tmp_path / "state"
    app = create_app(config(state / "runs"))
    marker = b"temporary-backup-private-marker"
    archives = []
    with TestClient(app, headers={"Authorization": "Bearer a"}) as client:
        sid = client.post("/v1/temporary").json()["id"]
        uploaded = client.post(
            "/v1/files?project_id=p",
            headers={"X-KeepHarness-Temporary": sid, "X-Filename": "private.txt"},
            content=marker,
        )
        assert uploaded.status_code == 201, uploaded.text
        for with_secrets in (False, True):
            archive = tmp_path / f"backup-{with_secrets}.tar.gz"
            backup.create(state, archive, with_secrets=with_secrets)
            archives.append(archive)
        assert client.delete("/v1/temporary/" + sid).status_code == 200
    for archive in archives:
        with tarfile.open(archive) as saved:
            assert not any("temporary-chats" in member.name.split("/") for member in saved)
            for member in saved:
                if member.isfile():
                    assert marker not in saved.extractfile(member).read()


def test_temporary_creation_is_atomic_with_another_startup_sweep(tmp_path, monkeypatch):
    import fcntl
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from agent_service.services.temporary_chat_service import TemporaryChatService

    async def scenario(pool):
        parent = ConversationService(config(tmp_path))
        identity = ("a", parent.config["clients"]["a"])
        original_mkdir, original_flock = Path.mkdir, fcntl.flock
        sweep_reached_boundary = threading.Event()
        sweeper = None
        sweeper_thread = None

        def sweep():
            nonlocal sweeper_thread
            sweeper_thread = threading.get_ident()
            try:
                TemporaryChatService(parent)
            finally:
                sweep_reached_boundary.set()

        def flock(fd, operation):
            if threading.get_ident() == sweeper_thread and operation == fcntl.LOCK_EX:
                # The shared lock is reached before attempting per-session cleanup.
                sweep_reached_boundary.set()
            return original_flock(fd, operation)

        def mkdir(path, *args, **kwargs):
            nonlocal sweeper
            result = original_mkdir(path, *args, **kwargs)
            if sweeper is None and path.parent == parent.temporary.root and len(path.name) == 32:
                sweeper = pool.submit(sweep)
                assert sweep_reached_boundary.wait(5), "startup sweep did not start"
            return result

        monkeypatch.setattr(Path, "mkdir", mkdir)
        monkeypatch.setattr(fcntl, "flock", flock)
        try:
            sid = await parent.temporary.open(identity)
            sweeper.result(timeout=5)
            session = parent.temporary.get(identity, sid)
            assert session.service.root.is_dir()
            assert (session.service.root / ".lock").is_file()
        finally:
            await parent.temporary.close_all()
            parent.db.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        asyncio.run(scenario(pool))
