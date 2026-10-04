"""keepharness-install: honest success, a port it does not own, containers, unit text, rollback.

Nothing here runs systemctl for real: every call goes through a recorder, and HOME is a
temporary folder.
"""

import http.client
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from control import install, product
from control.product import LEGACY_MARKER, PRODUCT, migrate_legacy_state

CURRENT = {"slug": "keepharness", "lineage": "keepharness"}


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def foreign_server(tmp_path):
    """Another program answering 200 on a loopback port, like a Tail Harness 0.14 left running."""
    port = free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", "--bind", "127.0.0.1", str(port)],
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1).close()
                break
            except OSError:
                time.sleep(0.05)
        yield server, port
    finally:
        server.terminate()
        server.wait(10)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A temporary HOME on the host (these tests may themselves run in a container)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CONTAINER_ID", raising=False)
    monkeypatch.setattr(install, "CONTAINER_MARKER", tmp_path / "no-container")
    return tmp_path


def recorder(calls):
    def run(command, **_):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    return run


# --------------------------------------------------------------------------- honest success


def test_a_foreign_answer_on_the_port_is_a_failure_naming_its_pid_and_command(
    foreign_server, monkeypatch
):
    server, port = foreign_server
    monkeypatch.setattr(install, "unit_pid", lambda service=install.SERVICE: server.pid + 100000)
    with pytest.raises(RuntimeError) as failed:
        install.wait_ready(port, install.SERVICE)
    assert f"pid {server.pid}" in str(failed.value) and "http.server" in str(failed.value)
    assert install.SERVICE in str(failed.value)


def test_the_service_answering_with_its_own_pid_is_success(foreign_server, monkeypatch):
    server, port = foreign_server
    monkeypatch.setattr(install, "unit_pid", lambda service=install.SERVICE: server.pid)
    install.wait_ready(port, install.SERVICE)


def test_port_holders_finds_the_listening_process():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert os.getpid() in [pid for pid, _ in product.port_holders(port)]
    assert product.port_holders(port) == []


def test_install_refuses_a_port_another_program_holds_before_writing_anything(home, monkeypatch):
    calls = []
    monkeypatch.setattr(install.subprocess, "run", recorder(calls))
    monkeypatch.setattr(install, "unit_pid", lambda service=install.SERVICE: None)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with pytest.raises(SystemExit) as refused:
            install.main(["--port", str(port)])
    assert f"127.0.0.1:{port}" in str(refused.value.code)
    assert f"pid {os.getpid()}" in str(refused.value.code)
    assert calls == [] and not (home / ".config").exists()


def test_a_service_that_never_answers_ends_with_a_message_not_a_traceback(home, monkeypatch):
    calls = []
    monkeypatch.setattr(install.subprocess, "run", recorder(calls))

    def never(port, service=None):
        raise RuntimeError("keepharness.service did not answer")

    monkeypatch.setattr(install, "wait_ready", never)
    with pytest.raises(SystemExit) as failed:
        install.main(["--port", str(free_port())])
    assert "did not answer" in str(failed.value.code)
    assert ["systemctl", "--user", "enable", "--now", install.SERVICE] in calls


# --------------------------------------------------------------------------- containers


@pytest.mark.parametrize("how", ["marker", "variable"])
def test_install_refuses_inside_a_container(home, monkeypatch, tmp_path, how):
    marker = tmp_path / "containerenv"
    if how == "marker":
        marker.write_text('engine="podman"\nname="claude-ubuntu"\n')
    else:
        monkeypatch.setenv("CONTAINER_ID", "claude-ubuntu")
    monkeypatch.setattr(install, "CONTAINER_MARKER", marker)
    calls = []
    monkeypatch.setattr(install.subprocess, "run", recorder(calls))
    for argv in (["--check-only"], []):
        with pytest.raises(SystemExit) as refused:
            install.main([*argv, "--port", str(free_port())])
        assert "host" in str(refused.value.code)
        assert "distrobox-host-exec" in str(refused.value.code)
    assert calls == []


def test_on_the_host_no_container_is_reported(tmp_path, monkeypatch):
    monkeypatch.delenv("CONTAINER_ID", raising=False)
    assert install.container_name(tmp_path / "missing") is None


# --------------------------------------------------------------------------- check-only


def legacy_state(home, port=None):
    old = home / ".local/share/tail-harness"
    (old / "runs").mkdir(parents=True)
    for folder in (old, old / "runs"):
        (folder / "harness.identity.json").write_text(json.dumps(LEGACY_MARKER))
    (old / "settings.json").write_text("{}")
    runtime = {"state_dir": str(old / "runs"), "control_state_dir": str(old), "port": port or free_port()}
    (old / "runtime.json").write_text(json.dumps(runtime))
    return old


def test_check_only_passes_while_tail_harness_state_waits_to_move(home, capsys):
    old = legacy_state(home)
    install.main(["--check-only", "--port", str(free_port())])
    assert str(old) in capsys.readouterr().out
    assert old.is_dir() and not PRODUCT.state_path(home).exists()


def test_check_only_refuses_while_the_old_state_still_answers(home):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        old = legacy_state(home, listener.getsockname()[1])
        with pytest.raises(SystemExit) as refused:
            install.main(["--check-only", "--port", str(free_port())])
    assert "still answers" in str(refused.value.code)
    assert old.is_dir() and not PRODUCT.state_path(home).exists()


@pytest.fixture
def running_tail_harness(home, monkeypatch):
    """Tail Harness 0.14 running under its own unit and holding the admin port (a listener here)."""
    unit = home / ".config/systemd/user/tail-harness.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Service]\n")
    monkeypatch.setattr(install.subprocess, "run", recorder([]))  # is-active: exit 0
    monkeypatch.setattr(install, "unit_pid", lambda service=install.SERVICE: None)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        legacy_state(home, port)
        yield port


def belongs_to(monkeypatch, unit):
    def in_unit(pid, name):
        return name == unit

    monkeypatch.setattr(product, "in_unit", in_unit)


def test_the_preflight_lets_a_running_tail_harness_unit_through_for_install_sh_to_stop(
    running_tail_harness, monkeypatch
):
    belongs_to(monkeypatch, "tail-harness.service")
    for argv in (["--check-only"], []):
        assert install.preflight(running_tail_harness) is None, argv


def test_the_preflight_still_refuses_tail_harness_running_outside_its_unit(
    running_tail_harness, monkeypatch
):
    belongs_to(monkeypatch, "something-else.service")
    refusal = install.preflight(running_tail_harness)
    assert f"127.0.0.1:{running_tail_harness}" in refusal and "stop it" in refusal


@pytest.mark.parametrize("stopping, refused", [(True, False), (False, True)])
def test_only_install_sh_stopping_the_unit_excuses_an_active_unit_and_its_ports(
    home, running_tail_harness, monkeypatch, stopping, refused
):
    belongs_to(monkeypatch, "tail-harness.service")
    reason = product.migration_refusal(home, stopping=stopping)
    assert (reason is not None) is refused
    if refused:
        assert "Stop Tail Harness" in reason


# --------------------------------------------------------------------------- unit text


def unit_text(home, **options):
    config = install.files(home, "/env/bin/python", 8100, **options)
    return next(text for path, (text, _) in config.items() if path.suffix == ".service")


def test_the_unit_uses_the_installer_path_first(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/home/linuxbrew/.linuxbrew/bin:relative/bin:/usr/bin")
    unit = unit_text(tmp_path)
    (line,) = [line for line in unit.splitlines() if line.startswith("Environment=")]
    path = json.loads(line.removeprefix("Environment="))
    assert path == (
        f"PATH=/home/linuxbrew/.linuxbrew/bin:/usr/bin:{tmp_path}/.local/bin:/usr/local/bin:/bin"
    )


def test_the_unit_stops_retrying_and_waits_for_no_system_target(tmp_path):
    unit = unit_text(tmp_path)
    assert "network-online" not in unit
    assert "StartLimitBurst=" in unit and "StartLimitIntervalSec=" in unit


def test_the_unit_signals_only_the_admin_so_that_it_can_drain_its_harness(tmp_path):
    # With the default control-group mode systemd also SIGTERMs the harness at once, which would
    # kill the running work the admin is waiting for (decision D18).
    assert "KillMode=mixed" in unit_text(tmp_path).splitlines()


def test_only_a_dev_install_records_that_it_runs_the_checkout(tmp_path):
    checkout = str(install.CHECKOUT)
    assert checkout not in unit_text(tmp_path)
    assert "--dev" in unit_text(tmp_path, dev=True) and checkout in unit_text(tmp_path, dev=True)


# --------------------------------------------------------------------------- rollback


def installed_from_tail_harness(home):
    """A Tail Harness state moved by install.sh, used by 0.15, with its service files."""
    old = legacy_state(home)
    migrate_legacy_state(home)
    new = PRODUCT.state_path(home)
    (new / "venv/bin").mkdir(parents=True)
    (new / "runs/harness-agents").mkdir()
    (new / "runs/harness-agents/a.json").write_text("{}")
    # An early pre-release rewrote the top marker; 0.14.0 refuses that value.
    (new / "harness.identity.json").write_text(json.dumps(CURRENT))
    PRODUCT.config_path(home).mkdir(parents=True)
    for path, (text, _) in install.files(home, "/env/bin/python").items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return old, new


def test_rollback_gives_the_state_back_to_0_14_and_stays_rolled_back(home, monkeypatch):
    import uvicorn

    from agent_service import log_config
    from control import cli

    old, new = installed_from_tail_harness(home)
    calls = []
    install.rollback(home, run=recorder(calls))
    assert calls == [
        ["systemctl", "--user", "disable", "--now", install.SERVICE],
        ["systemctl", "--user", "daemon-reload"],
    ]
    assert not any(path.exists() for path in install.files(home, "/env/bin/python"))
    assert old.is_dir() and not new.exists() and not (old / "venv").exists()
    for folder in (old, old / "runs"):
        assert json.loads((folder / "harness.identity.json").read_text()) == LEGACY_MARKER
    runtime = json.loads((old / "runtime.json").read_text())
    assert runtime["state_dir"] == str(old / "runs")
    assert (old / "runs/tail-agents/a.json").exists()
    assert (home / ".config/tail-harness").is_dir() and not PRODUCT.config_path(home).exists()
    # Nothing takes the state again: not install.sh's move, not a server start.
    refused = migrate_legacy_state(home)
    assert "rolled back" in refused and "./install.sh" in refused and old.is_dir()
    started = []
    monkeypatch.setattr(log_config, "configure_logging", lambda: None)
    monkeypatch.setattr(uvicorn, "run", lambda app, **_: started.append(app))
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--port", str(free_port())])
    assert "rolled back" in str(stopped.value.code)
    assert started == [] and old.is_dir() and not new.exists()
    # Deleting the record is the one explicit step that allows the upgrade again.
    (home / product.ROLLBACK_RECORD).unlink()
    assert migrate_legacy_state(home) is None and new.is_dir() and not old.exists()


def test_rollback_sets_provider_thread_markers_aside_so_0_14_replays_history(home):
    _, new = installed_from_tail_harness(home)
    session = new / "runs/sessions/c1/codex"
    session.mkdir(parents=True)
    for name in product.THREAD_MARKERS:
        (session / name).write_text('{"id": "pre-upgrade"}')
    (session / "context-recovery.json").write_text("{}")
    install.rollback(home, run=recorder([]))
    kept = home / ".local/share/tail-harness/runs/sessions/c1/codex"
    for name in product.THREAD_MARKERS:
        assert not (kept / name).exists()
        assert (kept / (name + ".before-rollback")).read_text() == '{"id": "pre-upgrade"}'
    assert (kept / "context-recovery.json").exists()


def test_rollback_never_nests_into_an_existing_tail_harness_folder(home):
    _, new = installed_from_tail_harness(home)
    (home / ".local/share/tail-harness").mkdir()
    with pytest.raises(ValueError, match="already exists"):
        install.rollback(home, run=recorder([]))
    assert (new / "venv").is_dir() and (new / "settings.json").exists()
    assert not (home / product.ROLLBACK_RECORD).exists()


def test_rollback_waits_while_the_state_is_in_use(home):
    _, new = installed_from_tail_harness(home)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        runtime = json.loads((new / "runtime.json").read_text())
        (new / "runtime.json").write_text(json.dumps({**runtime, "port": listener.getsockname()[1]}))
        with pytest.raises(ValueError, match="still answers"):
            install.rollback(home, run=recorder([]))
    assert new.is_dir() and not (home / ".local/share/tail-harness").exists()


def test_rollback_from_the_command_line_reports_a_refusal_cleanly(home, monkeypatch):
    monkeypatch.setattr(install.subprocess, "run", recorder([]))
    with pytest.raises(SystemExit) as refused:
        install.main(["--rollback-to-0.14"])
    assert "nothing to roll back" in str(refused.value.code)


@pytest.fixture
def admin_work(home, monkeypatch, request):
    """Authenticated admin in-process; no system service or network listener is touched."""
    import io
    import sqlite3
    from types import SimpleNamespace

    from starlette.testclient import TestClient
    from control.server import create_app

    state = PRODUCT.state_path(home)
    app = create_app(state, port=19876)
    if getattr(request, "param", False):
        app.router.routes[:] = [route for route in app.router.routes if route.path != "/open-admin"]
    jobs = state / "runs/jobs.sqlite3"
    jobs.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(jobs) as db:
        db.execute("CREATE TABLE jobs (state TEXT)")
    client = TestClient(app, base_url="http://127.0.0.1:19876")
    requests = []

    def open_url(url, **kwargs):
        headers = url.headers if isinstance(url, urllib.request.Request) else {}
        url = url.full_url if isinstance(url, urllib.request.Request) else url
        requests.append(url.split("?")[0])
        response = client.get(url, headers=headers)
        if response.status_code >= 400:
            raise urllib.error.HTTPError(url, response.status_code, "refused", {}, None)
        return io.BytesIO(response.content)

    monkeypatch.setattr(install.urllib.request, "build_opener", lambda *args: SimpleNamespace(open=open_url))
    monkeypatch.setattr(install, "port_conflict", lambda *args, **kwargs: None)
    monkeypatch.setattr(install, "port_holders", lambda port: [(os.getpid(), "admin")])
    registered = []
    monkeypatch.setattr(install, "register", lambda args: registered.append(args))

    def set_work(work):
        with sqlite3.connect(jobs) as db:
            db.execute("INSERT INTO jobs VALUES (?)", (work,))

    yield set_work, registered, requests
    client.close()


@pytest.mark.parametrize("work", ["queued", "running"])
@pytest.mark.parametrize("check_only", [False, True])
def test_install_refuses_admin_work_before_any_changes(admin_work, work, check_only):
    set_work, registered, requests = admin_work
    set_work(work)
    args = ["--port", "19876"] + (["--check-only"] if check_only else [])
    with pytest.raises(SystemExit) as refused:
        install.main(args)
    assert refused.value.code
    assert "queued or running" in str(refused.value)
    assert "--force" in str(refused.value)
    if check_only:
        assert "Nothing was stopped or moved" in str(refused.value)
    else:
        assert "Nothing was stopped or moved" not in str(refused.value)
        assert "package is installed" in str(refused.value)
        assert "old code until it restarts" in str(refused.value)
    assert registered == []
    assert requests[-1].endswith("/api/state")


@pytest.mark.parametrize("work", ["queued", "running"])
def test_install_force_overrides_admin_work(admin_work, work):
    set_work, registered, requests = admin_work
    set_work(work)
    install.main(["--port", "19876", "--force"])
    assert len(registered) == 1
    assert requests == []


def test_install_continues_when_admin_is_idle(admin_work):
    _, registered, requests = admin_work
    install.main(["--port", "19876"])
    assert len(registered) == 1
    assert requests[-1].endswith("/api/state")


def test_service_stop_budget_includes_full_drain_and_harness_shutdown(home):
    from control.manager import DRAIN_SECONDS, HARNESS_STOP_SECONDS

    unit = install.files(home, "/fake/python")[home / ".config/systemd/user" / install.SERVICE][0]
    assert DRAIN_SECONDS + HARNESS_STOP_SECONDS < 90
    assert "TimeoutStopSec=90\n" in unit


def test_install_refuses_when_existing_admin_status_cannot_be_verified(admin_work, monkeypatch):
    from control import local_access

    _, registered, _ = admin_work

    def unreadable_key(path):
        raise OSError("unreadable")

    monkeypatch.setattr(local_access, "read_secret", unreadable_key)
    with pytest.raises(SystemExit, match="Cannot verify queued or running work.*--force"):
        install.main(["--port", "19876"])
    assert registered == []


@pytest.mark.parametrize("existing_session", [False, True])
def test_install_check_only_does_not_create_owner_sessions(admin_work, home, existing_session):
    from control.local_access import SESSIONS_FILE, issue_session

    _, registered, requests = admin_work
    sessions = PRODUCT.state_path(home) / SESSIONS_FILE
    if existing_session:
        issue_session(PRODUCT.state_path(home))
    before = sessions.read_bytes() if sessions.exists() else None
    install.main(["--port", "19876", "--check-only"])
    after = sessions.read_bytes() if sessions.exists() else None
    assert after == before
    assert requests[-1].endswith("/api/state")
    assert registered == []


@pytest.mark.parametrize("admin_work", [True], indirect=True)
def test_install_old_admin_refuses_without_creating_owner_sessions(admin_work, home):
    from control.local_access import SESSIONS_FILE

    _, registered, _ = admin_work
    sessions = PRODUCT.state_path(home) / SESSIONS_FILE
    with pytest.raises(SystemExit, match="Cannot verify queued or running work.*--force"):
        install.main(["--port", "19876", "--check-only"])
    assert not sessions.exists()
    assert registered == []


def test_install_admin_auth_rejects_invalid_and_replayed_tickets(tmp_path):
    from starlette.testclient import TestClient
    from control.local_access import SESSIONS_FILE, open_ticket
    from control.server import create_app

    state = tmp_path / "state"
    app = create_app(state, port=19876)
    with TestClient(app, base_url="http://127.0.0.1:19876") as client:
        assert client.get("/open-admin?ticket=invalid").status_code == 403
        url = "/open-admin?ticket=" + open_ticket(app.state.manager.local_secret)
        assert client.get(url).status_code == 200
        assert client.get(url).status_code == 403
        assert not (state / SESSIONS_FILE).exists()


@pytest.mark.parametrize("error", [http.client.IncompleteRead(b"partial"), http.client.BadStatusLine("broken")])
def test_install_refuses_http_protocol_errors(admin_work, monkeypatch, error):
    from types import SimpleNamespace

    def broken_open(*args, **kwargs):
        raise error

    monkeypatch.setattr(install.urllib.request, "build_opener", lambda *args: SimpleNamespace(open=broken_open))
    with pytest.raises(SystemExit, match="Cannot verify queued or running work"):
        install.main(["--port", "19876", "--check-only"])
    assert admin_work[1] == []


def test_work_refusal_real_opener_carries_admin_cookie(home, monkeypatch):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread
    from urllib.parse import parse_qs, urlsplit
    from control.local_access import KEY_FILE

    state = PRODUCT.state_path(home)
    state.mkdir(parents=True)
    (state / KEY_FILE).write_bytes(b"test-secret")
    requests = []

    class Admin(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Cookie")))
            if self.path.startswith("/open-admin?"):
                assert parse_qs(urlsplit(self.path).query)["ticket"]
                self.send_response(302)
                self.send_header("Set-Cookie", "test-admin=authenticated; Path=/; HttpOnly")
                self.send_header("Location", "/")
                self.end_headers()
            elif self.headers.get("Cookie") == "test-admin=authenticated":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":{"busy":true}}')
            else:
                self.send_error(403)

        def log_message(self, *args):
            pass

    monkeypatch.setattr(install, "port_holders", lambda port: [(os.getpid(), "admin")])
    with HTTPServer(("127.0.0.1", 0), Admin) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            refusal = install.work_refusal(server.server_port)
        finally:
            server.shutdown()
            thread.join(timeout=5)
    assert refusal.startswith("There is queued or running work")
    assert requests[-1] == ("/api/state", "test-admin=authenticated")
    assert len(requests) == 3
