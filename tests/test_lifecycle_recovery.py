"""Startup recovery and abrupt-shutdown behaviour: P5-07, P5-08, P5-09, P5-10.

Two styles are used deliberately:

* P5-07 drives ``ConversationService``/``create_app`` in process, mirroring the existing
  ``tests/test_context_recovery.py`` technique of inserting rows directly and calling
  internal methods, to reach the exact branch that prefixes a continuation prompt after
  an interrupted native session without spawning a real provider CLI.
* P5-08/P5-09 spawn the real ``python -m agent_service.app`` subprocess (like
  ``scripts/test-ui.sh`` does) with a fake ``codex`` app-server CLI, so a real ``kill -9``
  / ``SIGTERM`` exercises the actual OS process and the on-disk SQLite WAL, not just
  Python object recreation.
"""

import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from agent_service.app import create_app
from tests.owner_session import sign_in

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable


def _codex_config(tmp_path):
    return {
        "state_dir": str(tmp_path),
        "projects": {"p": {}},
        "clients": {
            "a": {"sha256": __import__("hashlib").sha256(b"a").hexdigest(), "projects": ["p"]}
        },
        "services": {
            "codex": {
                "enabled": True,
                "models": ["gpt-6-astra"],
                "projects": ["p"],
                "permissions": {"read": True},
            }
        },
        "codex_models": {"gpt-6-astra": ["low", "high"]},
        "codex": {},
        "origins": [],
    }


def _insert_job(db, job_id, project, owner, state, created, payload, result=None):
    db.execute(
        "INSERT INTO jobs(id,project,owner,state,created,payload,result) VALUES(?,?,?,?,?,?,?)",
        (
            job_id,
            project,
            owner,
            state,
            created,
            json.dumps(payload),
            json.dumps(result) if result is not None else None,
        ),
    )
    db.commit()


def test_restart_marks_orphaned_running_jobs_interrupted_and_keeps_them_listed(tmp_path):
    """P5-07 (part 1): a job left ``running`` when the process died is recovered on restart."""
    cfg = _codex_config(tmp_path)
    app = create_app(cfg)
    service = app.state.service
    identity = ("a", cfg["clients"]["a"])

    submission = service.submit(
        identity,
        {
            "project_id": "p",
            "backend": "codex",
            "model": "gpt-6-astra",
            "effort": "low",
            "prompt": "root turn",
        },
    )
    job_id = submission["job_id"]
    with service.db:
        service.db.execute("UPDATE jobs SET state='running' WHERE id=?", (job_id,))
    service.db.close()

    # Simulate the process dying and a fresh one taking over the same state dir.
    app2 = create_app(cfg)
    service2 = app2.state.service

    row = service2.job(identity, job_id)
    assert row["state"] == "interrupted"
    assert json.loads(row["result"])["error"] == "service_restarted"

    last_event = service2.message_repository.last_event(job_id)
    assert last_event["type"] == "interrupted"

    assert service2.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    conversations = service2.conversation_rows(identity)
    assert any(r["id"] == job_id for r in conversations)


def test_continuation_after_interrupted_native_session_gets_interrupted_preface(tmp_path):
    """P5-07 (part 2): a continuation of an interrupted native codex turn is warned in-prompt."""
    cfg = _codex_config(tmp_path)
    app = create_app(cfg)
    service = app.state.service
    identity = ("a", cfg["clients"]["a"])
    owner = "a"

    root_payload = {
        "project_id": "p",
        "backend": "codex",
        "model": "gpt-6-astra",
        "effort": "low",
        "execution_mode": "native",
        "prompt": "root turn",
    }
    root_id = uuid.uuid4().hex
    _insert_job(service.db, root_id, "p", owner, "completed", 1.0, root_payload, {"answer": "ok"})

    interrupted_payload = {
        **root_payload,
        "parent_job_id": root_id,
        "prompt": "continue after crash",
    }
    interrupted_id = uuid.uuid4().hex
    _insert_job(
        service.db,
        interrupted_id,
        "p",
        owner,
        "interrupted",
        2.0,
        interrupted_payload,
        {"error": "service_restarted", "metrics": None},
    )

    # A native codex session the previous (now-interrupted) turn left behind.
    native_session = service.root / "sessions" / root_id / "codex"
    native_session.mkdir(parents=True, exist_ok=True)
    (native_session / "native-thread.json").write_text(json.dumps({"id": "thread-1"}))
    (native_session / "harness-context.json").write_text(
        json.dumps(
            {
                "markers": {"native-thread.json": "thread-1"},
                "mode": "native",
                "job_id": interrupted_id,
                "started": True,
            }
        )
    )

    continuation_payload = {
        **root_payload,
        "parent_job_id": interrupted_id,
        "prompt": "please resume",
    }
    continuation_id = uuid.uuid4().hex
    _insert_job(service.db, continuation_id, "p", owner, "queued", 3.0, continuation_payload)

    row_c = service.job(identity, continuation_id)

    import asyncio

    plan = asyncio.run(service._prepare_inference(row_c, continuation_payload))
    assert plan.persisted_session is True
    assert "The previous execution was interrupted" in plan.prompt


def test_start_reports_port_in_use_without_touching_other_state(tmp_path, monkeypatch):
    """P5-10: ``Manager.start`` refuses to start when its configured port is already bound."""
    import copy
    from unittest.mock import AsyncMock

    from starlette.testclient import TestClient

    from control.server import Manager
    from control.server import create_app as create_control_app

    inventory = {
        "platform": "Linux",
        "services": [],
        "binaries": {},
        "projects": [],
        "network": {"online": False, "hostname": None},
    }
    monkeypatch.setattr("control.discovery.scan", AsyncMock(return_value=copy.deepcopy(inventory)))
    monkeypatch.setattr(Manager, "integrations", lambda self: {})

    admin_app = create_control_app(tmp_path / "state")
    manager = admin_app.state.manager
    monkeypatch.setattr(manager, "build_runtime_config", AsyncMock(return_value={"services": {}}))

    holder = socket.socket()
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        manager.settings["port"] = port
        manager.settings["services"]["local"]["enabled"] = True
        manager.settings["services"]["local"]["models"] = ["installed-model"]

        with TestClient(admin_app, base_url="http://127.0.0.1:8094") as client:
            sign_in(client).get("/")
            response = client.post("/api/start", json={}, headers={"X-Harness-Admin": "1"})

        assert response.status_code == 400
        assert (
            "Port in use. Choose another; no existing service was stopped."
            in response.json()["error"]
        )
        assert manager.proc is None

        # The holder is untouched: still listening, still accepting connections.
        probe = socket.create_connection(("127.0.0.1", port), timeout=1)
        probe.close()
    finally:
        holder.close()


# --- Real-process lifecycle: a fake codex app-server CLI, spawned by a real
# ``python -m agent_service.app`` subprocess, killed for real. ---------------


def _free_port():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def _write_fake_codex_cli(path, pidfile):
    """A codex app-server that answers the handshake, starts a turn, then hangs."""
    path.write_text(
        "#!"
        + PYTHON
        + "\n"
        + "import sys, json, time, os\n"
        + "open("
        + repr(str(pidfile))
        + ", 'w').write(str(os.getpid()))\n"
        + "def emit(x):\n"
        + "    print(json.dumps(x), flush=True)\n"
        + "for line in sys.stdin:\n"
        + "    x = json.loads(line)\n"
        + "    method = x.get('method')\n"
        + "    if method == 'initialize':\n"
        + "        emit({'id': x['id'], 'result': {}})\n"
        + "    elif method in ('thread/start', 'thread/resume'):\n"
        + "        emit({'id': x['id'], 'result': {'thread': {'id': 'session-1'}}})\n"
        + "    elif method == 'thread/name/set':\n"
        + "        emit({'id': x['id'], 'result': {}})\n"
        + "    elif method == 'turn/start':\n"
        + "        emit({'method': 'turn/started', 'params': {'threadId': 'session-1',"
        + " 'turn': {'id': 'turn-1', 'status': 'inProgress'}}})\n"
        + "        time.sleep(60)\n"
        + "        break\n"
    )
    path.chmod(0o700)


def _wait_for_http(base_url, timeout=20):
    import httpx

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(base_url + "/ui.css", timeout=1).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    return False


def _spawn_harness(config_path, log_path):
    env = {**os.environ, "KEEPHARNESS_AGENT_CONFIG": str(config_path)}
    log = open(log_path, "ab")
    proc = subprocess.Popen(
        [PYTHON, "-m", "agent_service.app"], cwd=REPOSITORY_ROOT, env=env, stdout=log, stderr=log
    )
    log.close()
    return proc


@pytest.fixture
def hanging_codex_harness(tmp_path):
    """A real harness subprocess with one codex job stuck ``running`` on a hung fake CLI."""
    state = tmp_path / "state"
    state.mkdir()
    exe = tmp_path / "fake-codex"
    pidfile = tmp_path / "fake.pid"
    _write_fake_codex_cli(exe, pidfile)
    port = _free_port()
    config = {
        "state_dir": str(state),
        "bind": "127.0.0.1",
        "port": port,
        "local_access": True,
        "clients": {"local": {"sha256": "0" * 64, "projects": ["p"]}},
        "projects": {"p": {}},
        "services": {
            "codex": {
                "enabled": True,
                "models": ["gpt-6-astra"],
                "projects": ["p"],
                "permissions": {},
            }
        },
        "codex_models": {"gpt-6-astra": ["low", "high"]},
        "codex": {"binary": str(exe)},
        "origins": [f"http://127.0.0.1:{port}"],
    }
    config_path = tmp_path / "agent.json"
    config_path.write_text(json.dumps(config))
    base_url = f"http://127.0.0.1:{port}"

    proc = _spawn_harness(config_path, tmp_path / "harness.log")
    assert _wait_for_http(base_url), (tmp_path / "harness.log").read_text()

    import httpx

    response = httpx.post(
        base_url + "/v1/jobs",
        json={
            "project_id": "p",
            "backend": "codex",
            "model": "gpt-6-astra",
            "effort": "low",
            "execution_mode": "native",
            "prompt": "Count slowly from 1 to 300",
        },
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]

    deadline = time.monotonic() + 10
    state_now = None
    while time.monotonic() < deadline:
        info = httpx.get(base_url + f"/v1/jobs/{job_id}").json()
        state_now = info["state"]
        if state_now == "running":
            break
        time.sleep(0.1)
    assert state_now == "running", state_now

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not pidfile.exists():
        time.sleep(0.1)

    try:
        yield {
            "proc": proc,
            "base_url": base_url,
            "job_id": job_id,
            "state": state,
            "config_path": config_path,
            "pidfile": pidfile,
            "tmp_path": tmp_path,
        }
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except (OSError, ValueError):
                pass


def test_sigkill_of_harness_recovers_as_interrupted_on_restart(hanging_codex_harness):
    """P5-08: a real ``kill -9`` of the harness, then a real restart on the same state."""
    context = hanging_codex_harness
    proc = context["proc"]

    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=10)
    assert proc.returncode is not None

    proc2 = _spawn_harness(context["config_path"], context["tmp_path"] / "harness-restart.log")
    context["proc"] = proc2
    try:
        assert _wait_for_http(context["base_url"]), (
            context["tmp_path"] / "harness-restart.log"
        ).read_text()

        import httpx

        info = httpx.get(context["base_url"] + f"/v1/jobs/{context['job_id']}").json()
        assert info["state"] == "interrupted"
        assert info["result"]["error"] == "service_restarted"

        db = sqlite3.connect(str(context["state"] / "jobs.sqlite3"))
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        db.close()
    finally:
        if proc2.poll() is None:
            proc2.kill()
            proc2.wait(timeout=10)


def test_sigterm_during_native_codex_job_exits_promptly(hanging_codex_harness):
    """P5-09 / F-30: uvicorn's SIGTERM gives a graceful shutdown within a bounded time.

    The shutdown cancel used to wait up to 25 s in the worker's post-job Codex quota
    refresh (the hung fake CLI never answers it).
    """
    context = hanging_codex_harness
    proc = context["proc"]

    proc.send_signal(signal.SIGTERM)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.2)

    # The job must never come back to life as ``running``.
    db = sqlite3.connect(str(context["state"] / "jobs.sqlite3"))
    state = db.execute("SELECT state FROM jobs WHERE id=?", (context["job_id"],)).fetchone()[0]
    db.close()
    assert state in ("cancelled", "interrupted")

    assert proc.poll() is not None, "harness did not exit within 10s of SIGTERM"
