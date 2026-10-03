"""keepharness-install: honest success, a port it does not own, containers, unit text, rollback.

Nothing here runs systemctl for real: every call goes through a recorder, and HOME is a
temporary folder.
"""

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
