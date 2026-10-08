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


# --- Supervision and drain (OPS-R3-3, OPS-R1-3, OPS-R4-2): the admin watches its harness. ---


class _Exited:
    """A harness process that has already exited with ``code``."""

    def __init__(self, code):
        self.returncode = code

    async def wait(self):
        return self.returncode


def _supervised(tmp_path, monkeypatch, **limits):
    import asyncio
    from unittest.mock import AsyncMock

    from control import manager as manager_module
    from control.server import Manager

    for name, value in limits.items():
        monkeypatch.setattr(manager_module, name, value)
    delays = []

    async def sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(manager_module.asyncio, "sleep", sleep)
    manager = Manager(tmp_path)
    manager.proc = _Exited(1)
    manager.started_at = manager_module.time.monotonic()

    def start(supervised=False):  # a new process that exits at once, as a real start would record
        manager.proc, manager.started_at = _Exited(1), manager_module.time.monotonic()

    manager.start = AsyncMock(side_effect=start)
    return manager, delays, asyncio


def test_three_quick_crashes_stop_the_restarts_and_say_why(tmp_path, monkeypatch):
    manager, delays, asyncio = _supervised(tmp_path, monkeypatch)

    asyncio.run(manager.supervise())

    assert manager.start.await_count == 2  # crash 1 and 2 restart; crash 3 gives up
    assert not manager.running()
    assert manager.last_exit["code"] == 1
    assert "3 times in a row" in manager.startup_error
    assert manager.status()["last_exit"]["code"] == 1
    assert delays == [2, 4]


def test_restart_delays_grow_but_stay_capped(tmp_path, monkeypatch):
    manager, delays, asyncio = _supervised(
        tmp_path, monkeypatch, MAX_QUICK_CRASHES=6, RESTART_DELAY=2, RESTART_DELAY_CAP=5
    )

    asyncio.run(manager.supervise())

    assert delays == [2, 4, 5, 5, 5]


def test_a_stop_during_the_backoff_cancels_the_restart(tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    from control.server import Manager

    manager = Manager(tmp_path)
    manager.proc = _Exited(1)
    manager.start = AsyncMock()

    async def scenario():
        manager.watch()
        await asyncio.sleep(0.05)  # the watcher saw the exit and waits out its backoff
        assert manager.last_exit["code"] == 1
        await manager.stop(force=True)
        await asyncio.sleep(0.05)

    asyncio.run(scenario())

    manager.start.assert_not_awaited()
    assert manager.watcher is None


def test_killed_harness_is_restarted_and_last_exit_recorded(tmp_path, monkeypatch):
    """A real harness killed with SIGKILL comes back on its own and the admin says how it ended."""
    import asyncio
    import socket
    from unittest.mock import AsyncMock

    from control import manager as manager_module
    from control.server import Manager

    monkeypatch.setattr(manager_module, "RESTART_DELAY", 0.05)

    async def scenario():
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        manager = Manager(tmp_path)
        manager.settings["port"] = port
        manager.refresh = AsyncMock()
        manager.build_runtime_config = AsyncMock(
            return_value={
                "state_dir": str(tmp_path / "runs"),
                "bind": "127.0.0.1",
                "port": port,
                "clients": {"local": {"sha256": "0" * 64, "projects": []}},
                "projects": {},
                "services": {},
                "origins": [],
            }
        )
        try:
            await manager.start()
            first = manager.proc
            os.kill(first.pid, signal.SIGKILL)
            for _ in range(200):
                await asyncio.sleep(0.1)
                if manager.proc is not first and manager.running() and manager.startup_error is None:
                    break
            assert manager.proc is not first and manager.running()
            assert manager.last_exit["code"] == -signal.SIGKILL
            assert manager.status()["last_exit"]["code"] == -signal.SIGKILL
        finally:
            await manager.stop(force=True)
        assert not manager.running()

    asyncio.run(scenario())


def _draining(tmp_path, monkeypatch, busy_answers):
    """An admin app whose harness is a fake that records when it is told to stop."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from control import manager as manager_module
    from control.server import create_app as create_control_app

    monkeypatch.setattr(manager_module, "DRAIN_POLL", 0.01)
    app = create_control_app(tmp_path / "state")
    manager = app.state.manager
    manager.refresh = AsyncMock()
    events = []

    def busy():
        events.append("busy")
        return busy_answers() if callable(busy_answers) else busy_answers.pop(0)

    manager.busy = busy
    manager.proc = SimpleNamespace(
        returncode=None,
        terminate=Mock(side_effect=lambda: events.append("terminate")),
        wait=AsyncMock(),
    )
    return app, events


def test_shutdown_waits_for_running_work_before_stopping_the_harness(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    app, events = _draining(tmp_path, monkeypatch, [True, True, True, False, False])

    with TestClient(app, base_url="http://127.0.0.1:8094"):
        pass

    assert events[-1] == "terminate"
    assert events.count("busy") >= 4  # it polled until the work was gone
    assert "terminate" not in events[:-1]


def test_shutdown_stops_a_harness_whose_work_never_ends_after_the_drain_limit(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from control import manager as manager_module

    monkeypatch.setattr(manager_module, "DRAIN_SECONDS", 0.2)
    app, events = _draining(tmp_path, monkeypatch, lambda: True)

    with TestClient(app, base_url="http://127.0.0.1:8094"):
        pass

    assert events[-1] == "terminate"
    assert 3 <= events.count("busy") < 100


def test_a_supervisor_that_breaks_says_so_instead_of_dying_silently(tmp_path):
    import asyncio

    from control.server import Manager

    manager = Manager(tmp_path)
    manager.proc = object()  # no wait(): supervision itself fails

    asyncio.run(manager.supervise())

    assert "no longer watched" in manager.startup_error


class _Alive:
    """A harness process that runs until it is told to stop."""

    def __init__(self):
        self.returncode = None
        self.gone = None

    async def wait(self):
        import asyncio

        self.gone = self.gone or asyncio.Event()
        if self.returncode is None:
            await self.gone.wait()
        return self.returncode

    def terminate(self):
        self.returncode = -15
        if self.gone:
            self.gone.set()


def test_a_start_during_the_backoff_and_the_restart_never_run_together(tmp_path, monkeypatch):
    import asyncio

    from control import manager as manager_module
    from control.server import Manager

    monkeypatch.setattr(manager_module, "RESTART_DELAY", 0.05)
    manager = Manager(tmp_path)
    manager.proc = _Exited(1)
    active, peak, spawned = 0, 0, []

    async def slow_start(supervised=False):
        nonlocal active, peak
        if manager.running():
            return
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.1)
        manager.proc = _Alive()
        spawned.append(manager.proc)
        active -= 1

    manager.start = slow_start

    async def scenario():
        manager.watch()
        await asyncio.sleep(0.08)  # the restart is under way
        async with manager.lock:  # the owner clicks Start: the route holds this lock
            await manager.start()
        await asyncio.sleep(0.05)
        await manager.stop(force=True)

    asyncio.run(scenario())

    assert peak == 1 and len(spawned) == 1
    assert manager.last_exit["code"] == 1  # the crash only; the stop is not recorded as one


def test_a_stop_is_not_recorded_as_an_exit_of_its_own(tmp_path):
    import asyncio

    from control.server import Manager

    manager = Manager(tmp_path)
    manager.proc = _Alive()
    manager.audit = lambda action: None

    async def scenario():
        manager.watch()
        await asyncio.sleep(0.02)
        await manager.stop(force=True)
        await asyncio.sleep(0.02)

    asyncio.run(scenario())

    assert manager.last_exit is None


def test_crashes_after_a_stable_run_start_a_fresh_count(tmp_path, monkeypatch):
    manager, delays, asyncio = _supervised(tmp_path, monkeypatch, STABLE_SECONDS=60)
    manager.started_at -= 120  # the first process ran for two minutes

    asyncio.run(manager.supervise())

    # The long run is not a quick crash: three quick ones follow it before the restarts stop.
    assert manager.start.await_count == 3
    assert "3 times in a row" in manager.startup_error
    assert delays == [2, 2, 4]


def test_a_start_that_fails_before_spawning_keeps_the_exit_that_was_reported(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    manager, delays, asyncio = _supervised(tmp_path, monkeypatch)
    manager.start = AsyncMock(side_effect=ValueError("Port in use."))
    actions = []
    manager.audit = actions.append

    asyncio.run(manager.supervise())

    assert manager.start.await_count == 2
    assert actions == ["harness_exited:1"]  # the same dead process is not reported again
    assert manager.last_exit["code"] == 1
    assert "3 times in a row" in manager.startup_error


def test_shutdown_drain_allows_sixty_seconds_before_stopping(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from control import manager as manager_module

    app, events = _draining(tmp_path, monkeypatch, lambda: True)
    elapsed = 0.0

    async def sleep(seconds):
        nonlocal elapsed
        elapsed += seconds

    monkeypatch.setattr(manager_module, "time", SimpleNamespace(monotonic=lambda: elapsed))
    monkeypatch.setattr(manager_module, "asyncio", SimpleNamespace(sleep=sleep, to_thread=asyncio.to_thread))
    monkeypatch.setattr(manager_module, "DRAIN_POLL", 0.5)
    asyncio.run(app.state.manager.drain())
    assert elapsed == 60
    assert events.count("busy") == 120
    assert "terminate" not in events
