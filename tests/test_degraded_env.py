"""P5 §7: how the control panel and the harness behave in a degraded environment.

No network access is required; discovery is bounded with local fakes only.
"""

import asyncio
import hashlib
import sqlite3
import time
from unittest.mock import AsyncMock

import pytest

import control.discovery as discovery
import control.local_models as local_models
from tests.owner_session import sign_in


def test_scan_with_no_provider_binaries_and_a_fresh_home_returns_quickly(tmp_path, monkeypatch):
    """P5-23: an empty PATH/HOME still yields a well-shaped, prompt scan."""
    python_only = tmp_path / "path"
    python_only.mkdir()
    monkeypatch.setenv("PATH", str(python_only))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()

    started = time.monotonic()
    result = asyncio.run(discovery.scan())
    elapsed = time.monotonic() - started

    assert elapsed < 5
    assert result["network"] == {"installed": False, "online": False, "hostname": None}


async def _drip_server(reader, writer, byte_delay):
    try:
        await reader.read(4096)
    except (ConnectionError, OSError):
        pass
    body = b"{}"
    payload = f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body
    try:
        for byte in payload:
            writer.write(bytes([byte]))
            await writer.drain()
            await asyncio.sleep(byte_delay)
    except (ConnectionError, OSError):
        pass


def test_scan_bounds_a_trickling_ollama_response(monkeypatch):
    """P5-24: a server that drips one byte every 1.5s must not hold ``scan()`` open.

    The suspected bug (F-08 in the P5 findings log) was that ``httpx``'s per-read
    timeout, with no overall cap, could let such a server hold discovery forever.
    Both ``control/discovery.py:scan`` and ``control/local_models.py:discover`` already
    wrap their probes in ``asyncio.wait_for(..., PROBE_SECONDS)``, so this test currently
    passes: the drip is bounded at the probe's own timeout, not the byte-count.
    """

    async def scenario():
        server = await asyncio.start_server(lambda r, w: _drip_server(r, w, 1.5), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(discovery, "OLLAMA_TAGS_URL", f"http://127.0.0.1:{port}/api/tags")
                started = time.monotonic()
                await discovery.scan()
                return time.monotonic() - started
        finally:
            server.close()
            await server.wait_closed()

    elapsed = asyncio.run(scenario())
    assert elapsed <= 8


def test_local_models_discover_bounds_a_trickling_llama_server(monkeypatch, tmp_path):
    """P5-24 (companion): the per-process llama.cpp probe is bounded the same way."""

    async def scenario():
        server = await asyncio.start_server(lambda r, w: _drip_server(r, w, 1.5), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            fake_process = [
                {
                    "url": f"http://127.0.0.1:{port}",
                    "key_file": "",
                    "binary": "llama-server",
                    "model_file": "",
                    "mmproj_file": "",
                    "flags": [],
                    "performance": {},
                }
            ]
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(local_models, "processes", lambda: fake_process)
                started = time.monotonic()
                await local_models.discover()
                return time.monotonic() - started
        finally:
            server.close()
            await server.wait_closed()

    elapsed = asyncio.run(scenario())
    assert elapsed <= 8


def test_a_hanging_tailscale_status_call_is_capped_and_reports_offline(tmp_path, monkeypatch):
    """P5-25: a fake ``tailscale`` that sleeps 30s must still return within ``command``'s cap."""
    fake_tailscale = tmp_path / "bin" / "tailscale"
    fake_tailscale.parent.mkdir()
    fake_tailscale.write_text("#!/bin/sh\nsleep 30\n")
    fake_tailscale.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()

    started = time.monotonic()
    result = asyncio.run(discovery.scan())
    elapsed = time.monotonic() - started

    assert elapsed <= 9
    assert result["network"]["installed"] is True
    assert result["network"]["online"] is False


@pytest.fixture
def admin_manager(tmp_path, monkeypatch):
    import copy

    from control.server import Manager, create_app

    inventory = {
        "platform": "Linux",
        "services": [],
        "binaries": {},
        "projects": [],
        "network": {"online": False, "hostname": None},
    }
    monkeypatch.setattr("control.discovery.scan", AsyncMock(return_value=copy.deepcopy(inventory)))
    monkeypatch.setattr(Manager, "integrations", lambda self: {})
    app = create_app(tmp_path / "state")
    return app.state.manager


def test_manager_save_raises_a_plain_oserror_when_the_state_dir_is_read_only(admin_manager):
    """P5-26 (unit level): the read-only directory surfaces as an ``OSError``, not a crash."""
    manager = admin_manager
    manager.state.chmod(0o500)
    try:
        with pytest.raises(OSError):
            manager.save(manager.settings)
    finally:
        manager.state.chmod(0o700)


def test_settings_save_over_http_reports_400_and_state_endpoint_still_works(tmp_path, monkeypatch):
    import copy

    from starlette.testclient import TestClient

    from control.server import Manager, create_app

    inventory = {
        "platform": "Linux",
        "services": [],
        "binaries": {},
        "projects": [],
        "network": {"online": False, "hostname": None},
    }
    monkeypatch.setattr("control.discovery.scan", AsyncMock(return_value=copy.deepcopy(inventory)))
    monkeypatch.setattr(Manager, "integrations", lambda self: {})
    app = create_app(tmp_path / "state")
    manager = app.state.manager

    manager.state.chmod(0o500)
    try:
        with TestClient(app, base_url="http://127.0.0.1:8094") as client:
            sign_in(client).get("/")
            response = client.post(
                "/api/settings", json=manager.settings, headers={"X-Harness-Admin": "1"}
            )
            assert response.status_code == 400, response.text
            assert response.json()["error"]

            state_response = client.get("/api/state")
            assert state_response.status_code == 200
            # Restore before the client's context manager triggers lifespan shutdown,
            # which audits the shutdown and needs to append to audit.jsonl.
            manager.state.chmod(0o700)
    finally:
        manager.state.chmod(0o700)


def test_settings_save_leaves_no_tmp_file_and_keeps_the_old_settings_on_enospc(
    admin_manager, monkeypatch
):
    """P5-27: a disk-full write must not corrupt or leave a stray ``settings.tmp``."""
    manager = admin_manager
    manager.save(manager.settings)
    before = manager.path.read_bytes()

    from pathlib import Path as PathType

    real_write_text = PathType.write_text

    def failing_write_text(self, *args, **kwargs):
        if self.name == "settings.tmp":
            raise OSError(28, "No space left on device")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(PathType, "write_text", failing_write_text)

    changed = dict(manager.settings)
    changed["maestro_instructions"] = "changed while the disk is full"
    with pytest.raises(OSError):
        manager.save(changed)

    monkeypatch.undo()
    assert manager.path.read_bytes() == before
    assert not manager.path.with_suffix(".tmp").exists()


def _local_only_config(state_dir, project="p"):
    return {
        "state_dir": str(state_dir),
        "projects": {project: {}},
        "clients": {"a": {"sha256": hashlib.sha256(b"a").hexdigest(), "projects": [project]}},
        "services": {
            "local": {
                "enabled": True,
                "models": ["installed-model"],
                "projects": [project],
                "permissions": {"read": True, "upload": True},
            }
        },
        "local": {},
        "uploads_enabled": True,
        "origins": [],
    }


def test_worker_survives_a_disk_full_error_writing_a_result_and_runs_the_next_job(tmp_path):
    """P5-28 / F-31: an ``OperationalError`` while persisting one job's result must not wedge
    the queue."""
    from agent_service.app import create_app
    from agent_service.persistence.repositories import ConversationRepository

    cfg = _local_only_config(tmp_path)
    app = create_app(cfg)
    service = app.state.service
    identity = ("a", cfg["clients"]["a"])

    calls = {"count": 0}
    real_set_result = ConversationRepository.set_result

    def flaky_set_result(self, job, state, result):
        calls["count"] += 1
        if calls["count"] == 1:
            raise sqlite3.OperationalError("database or disk is full")
        return real_set_result(self, job, state, result)

    ConversationRepository.set_result = flaky_set_result
    try:
        first = service.submit(
            identity,
            {"project_id": "p", "backend": "local", "model": "installed-model", "prompt": "one"},
        )["job_id"]
        second = service.submit(
            identity,
            {"project_id": "p", "backend": "local", "model": "installed-model", "prompt": "two"},
        )["job_id"]

        async def run_worker_until_both_settle():
            worker = asyncio.create_task(service.worker())
            deadline = time.monotonic() + 3
            try:
                while time.monotonic() < deadline:
                    first_state = service.conversation_repository.state(first)[0]
                    second_state = service.conversation_repository.state(second)[0]
                    if first_state in ("completed", "failed") and second_state in (
                        "completed",
                        "failed",
                    ):
                        return first_state, second_state
                    await asyncio.sleep(0.05)
                return (
                    service.conversation_repository.state(first)[0],
                    service.conversation_repository.state(second)[0],
                )
            finally:
                worker.cancel()
                # A worker that died on the patched OperationalError would re-raise it here.
                try:
                    await worker
                except asyncio.CancelledError:
                    pass

        first_state, second_state = asyncio.run(run_worker_until_both_settle())
    finally:
        ConversationRepository.set_result = real_set_result

    # The first job's own persistence attempt failed once; a resilient worker would
    # retry finish() with a "failed" result and keep serving the second job.
    assert first_state == "failed"
    assert second_state in ("completed", "failed")
    assert calls["count"] >= 2


def test_project_folder_and_uploaded_file_with_unicode_and_punctuation_round_trip(
    tmp_path, monkeypatch
):
    """P5-29: a project folder/file with accents, a cedilla and spaces works end to end."""
    import copy

    from control.server import Manager
    from control.server import create_app as create_control_app

    # conventions: allow-pt (deliberately non-English test data, per the P5 design doc)
    unicode_label = "Projetos ção"  # conventions: allow-pt
    root = tmp_path / unicode_label / "ü name"
    root.mkdir(parents=True)

    inventory = {
        "platform": "Linux",
        "services": [],
        "binaries": {},
        "projects": [],
        "network": {"online": False, "hostname": None},
    }
    monkeypatch.setattr("control.discovery.scan", AsyncMock(return_value=copy.deepcopy(inventory)))
    monkeypatch.setattr(Manager, "integrations", lambda self: {})
    control_app = create_control_app(tmp_path / "control-state")
    manager = control_app.state.manager
    settings = dict(manager.settings)
    settings["projects"] = [{"id": "proj1", "label": unicode_label, "root": str(root)}]
    manager.save(settings)
    assert manager.settings["projects"][0]["root"] == str(root)

    from starlette.testclient import TestClient

    with TestClient(control_app, base_url="http://127.0.0.1:8094") as client:
        sign_in(client).get("/")
        listing = client.get("/api/folders", params={"path": str(root.parent)})
        assert listing.status_code == 200
        names = [entry["name"] for entry in listing.json()["directories"]]
        assert "ü name" in names

    from agent_service.app import create_app as create_agent_app

    agent_cfg = {
        "state_dir": str(tmp_path / "agent-state"),
        "projects": {"proj1": {"root": str(root)}},
        "clients": {"a": {"sha256": hashlib.sha256(b"a").hexdigest(), "projects": ["proj1"]}},
        "services": {
            "local": {
                "enabled": True,
                "models": ["installed-model"],
                "projects": ["proj1"],
                "permissions": {"read": True, "upload": True},
            }
        },
        "uploads_enabled": True,
        "origins": [],
    }
    app = create_agent_app(agent_cfg)
    service = app.state.service
    identity = ("a", agent_cfg["clients"]["a"])

    upload_source = tmp_path / "relatório.txt"
    upload_source.write_text("evidência\n")
    result = asyncio.run(
        service.attach_project_files(
            identity, "proj1", [("relatório.txt", upload_source)], [], "local", "installed-model"
        )
    )
    assert result["attachments"], result
    file_id = result["attachments"][0]["file_id"]
    stored_name = service.db.execute("SELECT name FROM files WHERE id=?", (file_id,)).fetchone()[0]
    assert stored_name == "relatório.txt"

    submission = service.submit(
        identity,
        {
            "project_id": "proj1",
            "backend": "local",
            "model": "installed-model",
            "prompt": "look at the attached file",
            "file_ids": [file_id],
        },
    )
    assert submission["job_id"]
    assert service.config["projects"]["proj1"]["root"] == str(root)
