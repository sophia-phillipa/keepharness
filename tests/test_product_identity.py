"""One identity controls runtime names; state cannot cross product lineages."""
import errno
import fcntl
import json
import logging
import os
import socket
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from control import product
from control.product import PRODUCT, ensure_lineage, migrate_legacy_state

# KeepHarness was named Tail Harness before 0.15.0; its state is adopted once.
LEGACY = {"slug": "tail-harness", "lineage": "tail-harness"}
CURRENT = {"slug": "keepharness", "lineage": "keepharness"}


def test_current_identity_and_paths(tmp_path):
    assert (PRODUCT.name, PRODUCT.slug, PRODUCT.env_prefix) == (
        "KeepHarness", "keepharness", "KEEPHARNESS"
    )
    assert PRODUCT.state_path(tmp_path) == tmp_path / ".local/share/keepharness"
    assert PRODUCT.config_path(tmp_path) == tmp_path / ".config/keepharness"
    assert PRODUCT.mcp_name == "keepharness"


def test_lineage_rejects_cross_identity_before_mutation(tmp_path):
    ensure_lineage(tmp_path)
    before = (tmp_path / "harness.identity.json").read_bytes()
    with pytest.raises(ValueError, match="identity"):
        ensure_lineage(tmp_path, replace(PRODUCT, slug="synthetic-harness", lineage="synthetic"))
    assert (tmp_path / "harness.identity.json").read_bytes() == before


def test_only_original_identity_adopts_unmarked_existing_state(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"projects": []}))
    with pytest.raises(ValueError, match="unmarked"):
        ensure_lineage(tmp_path, replace(PRODUCT, slug="synthetic-harness", lineage="synthetic"))
    ensure_lineage(tmp_path)


def test_lineage_does_not_follow_symlink(tmp_path):
    other = tmp_path / "other"
    other.write_text('{}')
    (tmp_path / "harness.identity.json").symlink_to(other)
    with pytest.raises(ValueError, match="identity"):
        ensure_lineage(tmp_path)
    assert other.read_text() == '{}'


def test_changed_slug_cannot_adopt_unmarked_upstream(tmp_path):
    (tmp_path / 'settings.json').write_text('{}')
    with pytest.raises(ValueError, match='unmarked'):
        ensure_lineage(tmp_path, replace(PRODUCT, slug='synthetic-harness'))


def test_owner_enrollment_rejects_cross_identity(tmp_path, monkeypatch):
    from control import cli
    ensure_lineage(tmp_path)
    (tmp_path / 'runtime.json').write_text(json.dumps({'clients': {'local': {}}, 'state_dir': str(tmp_path / 'runs'), 'port': 8095, 'origins': ['http://127.0.0.1:8095']}))
    monkeypatch.setattr(cli, 'PRODUCT', replace(PRODUCT, slug='synthetic-harness', lineage='synthetic'))
    with pytest.raises(SystemExit):
        cli.main(['--state', str(tmp_path), 'approve-device', '--owner', 'local', '--yes'])
    assert not (tmp_path / 'runs').exists()


def closed_port():
    """A local port nothing listens on (the host's real services must not count as in use)."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def legacy_state(home, port=None):
    old = home / ".local/share/tail-harness"
    (old / "runs").mkdir(parents=True)
    for folder in (old, old / "runs"):
        (folder / "harness.identity.json").write_text(json.dumps(LEGACY))
    (old / "settings.json").write_text("{}")
    runtime = {
        "state_dir": str(old / "runs"),
        "control_state_dir": str(old),
        "port": port or closed_port(),
    }
    (old / "runtime.json").write_text(json.dumps(runtime))
    return old


def test_tail_harness_state_and_config_move_to_keepharness_once(tmp_path):
    old = legacy_state(tmp_path)
    (tmp_path / ".config/tail-harness").mkdir(parents=True)
    (tmp_path / ".config/tail-harness/client.json").write_text('{"url": "http://vpn:8095"}')
    port = json.loads((old / "runtime.json").read_text())["port"]
    assert migrate_legacy_state(tmp_path) is None
    new = PRODUCT.state_path(tmp_path)
    assert not old.exists() and (new / "settings.json").read_text() == "{}"
    # The Tail Harness marker stays, so 0.14.0 can still open the state after a rollback.
    assert json.loads((new / "harness.identity.json").read_text()) == LEGACY
    assert json.loads((new / "runtime.json").read_text()) == {
        "state_dir": str(new / "runs"), "control_state_dir": str(new), "port": port
    }
    assert (new / "runtime.json").stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / ".config/tail-harness").exists()
    assert (PRODUCT.config_path(tmp_path) / "client.json").read_text() == '{"url": "http://vpn:8095"}'
    ensure_lineage(new / "runs")
    assert json.loads((new / "runs/harness.identity.json").read_text()) == LEGACY
    migrate_legacy_state(tmp_path)
    assert sorted(entry.name for entry in new.iterdir()) == [
        "harness.identity.json", "runs", "runtime.json", "settings.json"
    ]


def test_unmarked_tail_harness_state_moves_like_marked_state(tmp_path):
    old = legacy_state(tmp_path)
    (old / "harness.identity.json").unlink()
    migrate_legacy_state(tmp_path)
    assert json.loads((PRODUCT.state_path(tmp_path) / "harness.identity.json").read_text()) == LEGACY


def test_migration_never_merges_into_an_existing_keepharness_folder(tmp_path, caplog):
    old = legacy_state(tmp_path)
    new = PRODUCT.state_path(tmp_path)
    new.mkdir(parents=True)
    (tmp_path / ".config/tail-harness").mkdir(parents=True)
    PRODUCT.config_path(tmp_path).mkdir(parents=True)
    with caplog.at_level(logging.WARNING, logger="control.product"):
        assert migrate_legacy_state(tmp_path) is None  # the new folder is already in use
    assert (old / "settings.json").exists() and list(new.iterdir()) == []
    assert (tmp_path / ".config/tail-harness").exists()
    # Not silent: each pair is named, with the way to recover the older data.
    for folder in (old, new, tmp_path / ".config/tail-harness", PRODUCT.config_path(tmp_path)):
        assert str(folder) in caplog.text
    assert "./install.sh" in caplog.text


def test_migration_leaves_another_identity_state_alone(tmp_path):
    old = legacy_state(tmp_path)
    other = {"slug": "synthetic-harness", "lineage": "synthetic"}
    (old / "harness.identity.json").write_text(json.dumps(other))
    migrate_legacy_state(tmp_path)
    assert json.loads((old / "harness.identity.json").read_text()) == other
    assert not PRODUCT.state_path(tmp_path).exists()


def test_a_fork_never_takes_tail_harness_state(tmp_path):
    old = legacy_state(tmp_path)
    fork = replace(PRODUCT, slug="synthetic-harness", lineage="synthetic", state_dir=".local/share/synthetic-harness", config_dir=".config/synthetic-harness")
    migrate_legacy_state(tmp_path, fork)
    assert old.exists() and not fork.state_path(tmp_path).exists()
    with pytest.raises(ValueError, match="identity"):
        ensure_lineage(old, fork)
    assert json.loads((old / "harness.identity.json").read_text()) == LEGACY


def test_keepharness_opens_a_tail_harness_marker_without_rewriting_it(tmp_path):
    (tmp_path / "harness.identity.json").write_text(json.dumps(LEGACY))
    ensure_lineage(tmp_path)
    assert json.loads((tmp_path / "harness.identity.json").read_text()) == LEGACY
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["harness.identity.json"]


def test_only_install_sh_moves_the_default_state(tmp_path, monkeypatch):
    import uvicorn

    from agent_service import log_config
    from control import cli, server

    monkeypatch.setenv("HOME", str(tmp_path))
    old = legacy_state(tmp_path)
    with pytest.raises(SystemExit):
        cli.main(["--state", str(old), "approve-device", "--owner", "local", "--yes"])
    assert json.loads((old / "harness.identity.json").read_text()) == LEGACY
    with pytest.raises(SystemExit):
        cli.main(["approve-device", "--owner", "local", "--yes"])

    async def inventory():
        return {}

    monkeypatch.setattr(cli, "scan", inventory)
    cli.main(["--scan"])
    assert old.is_dir() and not PRODUCT.state_path(tmp_path).exists()
    started = []
    monkeypatch.setattr(log_config, "configure_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "create_app", lambda state, port: (state, port))
    monkeypatch.setattr(uvicorn, "run", lambda app, **_: started.append(app))
    # A server start (boot, login, the desktop client) never moves it: it names install.sh.
    with pytest.raises(SystemExit) as refused:
        cli.main(["--port", "18999"])
    assert "./install.sh" in str(refused.value.code) and str(old) in str(refused.value.code)
    assert started == [] and old.is_dir() and not PRODUCT.state_path(tmp_path).exists()
    monkeypatch.setattr(sys, "argv", ["product.py", "--migrate-state"])
    with pytest.raises(SystemExit) as moved:
        product.main()  # what install.sh runs, after its preflight
    assert moved.value.code is None
    cli.main(["--port", "18999"])
    assert not old.exists() and (PRODUCT.state_path(tmp_path) / "runs").is_dir()
    assert started == [(str(PRODUCT.state_path(tmp_path)), 18999)]


def test_the_server_does_not_start_while_the_old_state_is_in_use(tmp_path, monkeypatch, capsys):
    import uvicorn

    from agent_service import log_config
    from control import cli

    monkeypatch.setenv("HOME", str(tmp_path))
    old = legacy_state(tmp_path)
    started = []
    monkeypatch.setattr(log_config, "configure_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(uvicorn, "run", lambda app, **_: started.append(app))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        runtime = json.loads((old / "runtime.json").read_text())
        (old / "runtime.json").write_text(json.dumps({**runtime, "port": port}))
        with pytest.raises(SystemExit) as refused:
            cli.main(["--port", "18999"])
    # The reason reaches stderr (the desktop client shows it); nothing created the new folder.
    assert f"127.0.0.1:{port} still answers" in str(refused.value.code)
    assert "./install.sh" in str(refused.value.code)
    assert started == [] and old.is_dir() and not PRODUCT.state_path(tmp_path).exists()


# --------------------------------------------------------------------------- state in use


def systemctl(active, calls):
    def run(command, **_):
        calls.append(command)
        if active is None:
            raise FileNotFoundError("systemctl")
        return subprocess.CompletedProcess(command, 0 if active else 3)

    return run


@pytest.mark.parametrize("active", [True, False, None])
def test_migration_waits_while_the_tail_harness_service_is_active(tmp_path, monkeypatch, active):
    old = legacy_state(tmp_path)
    unit = tmp_path / ".config/systemd/user/tail-harness.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Service]\n")
    calls = []
    monkeypatch.setattr(product.subprocess, "run", systemctl(active, calls))
    refused = migrate_legacy_state(tmp_path)
    assert calls and calls[0][:3] == ["systemctl", "--user", "is-active"]
    assert "tail-harness.service" in calls[0]
    assert old.is_dir() is bool(active)  # inactive, or no systemctl at all: the state moves
    if active:
        assert not PRODUCT.state_path(tmp_path).exists()
        assert "tail-harness.service is active" in refused and "./install.sh" in refused
    else:
        assert refused is None


@pytest.mark.parametrize("key", ["port", "admin_url"])
def test_migration_waits_while_an_old_port_answers(tmp_path, key):
    old = legacy_state(tmp_path)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        runtime = json.loads((old / "runtime.json").read_text())
        runtime[key] = port if key == "port" else f"http://127.0.0.1:{port}/"
        (old / "runtime.json").write_text(json.dumps(runtime))
        refused = migrate_legacy_state(tmp_path)
    assert old.is_dir() and not PRODUCT.state_path(tmp_path).exists()
    assert f"127.0.0.1:{port} still answers" in refused and "./install.sh" in refused


def test_the_in_use_check_also_runs_when_both_folders_exist(tmp_path):
    old = legacy_state(tmp_path)
    new = PRODUCT.state_path(tmp_path)
    (new / "runs").mkdir(parents=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        runtime = json.loads((old / "runtime.json").read_text())
        (old / "runtime.json").write_text(json.dumps({**runtime, "port": port}))
        refused = migrate_legacy_state(tmp_path)
    # Installing now would report success while the old instance keeps the user.
    assert f"127.0.0.1:{port} still answers" in refused and "./install.sh" in refused
    assert f"pid {os.getpid()}" in refused  # who holds it, not only that something does
    assert (old / "settings.json").exists() and list(new.iterdir()) == [new / "runs"]


def test_the_both_folders_warning_points_to_the_merge(tmp_path, caplog):
    legacy_state(tmp_path)
    PRODUCT.state_path(tmp_path).mkdir(parents=True)
    with caplog.at_level(logging.WARNING, logger="control.product"):
        migrate_legacy_state(tmp_path)
    assert "./install.sh --merge-legacy" in caplog.text


def test_migration_waits_while_this_program_runs_from_the_old_folder(tmp_path, monkeypatch):
    old = legacy_state(tmp_path)
    monkeypatch.setattr(sys, "prefix", str(old / "venv"))
    assert "this program runs from it" in migrate_legacy_state(tmp_path)
    assert old.is_dir() and not PRODUCT.state_path(tmp_path).exists()


@pytest.mark.parametrize("code", [errno.EXDEV, errno.EBUSY], ids=["EXDEV", "EBUSY"])
def test_a_move_the_system_refuses_is_a_reason_not_a_crash(tmp_path, monkeypatch, code):
    old = legacy_state(tmp_path)

    def refuse(self, target):
        raise OSError(code, "refused")

    monkeypatch.setattr(Path, "rename", refuse)
    refused = migrate_legacy_state(tmp_path)
    assert old.is_dir() and str(old) in refused and "./install.sh" in refused


def test_a_second_migration_waits_for_the_first(tmp_path):
    old = legacy_state(tmp_path)
    lock = tmp_path / ".local/share/tail-harness.migration.lock"
    with open(lock, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        waiting = threading.Thread(target=migrate_legacy_state, args=(tmp_path,))
        waiting.start()
        waiting.join(0.3)
        assert waiting.is_alive() and old.is_dir()
    waiting.join(10)
    assert not waiting.is_alive() and not old.exists()
    assert (PRODUCT.state_path(tmp_path) / "settings.json").exists()
    assert not lock.exists()  # nothing is left to move: the lock file goes too


def client_bridge(home, extra=None):
    """What ``setup-mcp.sh`` before 0.15.0 left in the state folder of an MCP client."""
    new = PRODUCT.state_path(home)
    (new / "venv/bin").mkdir(parents=True)
    (new / "venv/bin/python").write_text("#!/bin/sh\n")
    (new / "mcp_bridge.py").write_text("# bridge\n")
    for name in extra or ():
        (new / name).write_text("{}")
    return new


def test_a_target_holding_only_a_client_bridge_is_set_aside_not_adopted(tmp_path, caplog):
    old = legacy_state(tmp_path)
    new = client_bridge(tmp_path)
    assert product.legacy_waiting(tmp_path) is not None  # install --check-only still refuses
    with caplog.at_level(logging.WARNING, logger="control.product"):
        assert migrate_legacy_state(tmp_path) is None
    assert not old.exists() and (new / "settings.json").read_text() == "{}"
    assert json.loads((new / "harness.identity.json").read_text()) == LEGACY
    assert not (new / "mcp_bridge.py").exists() and not (new / "venv").exists()
    # Nothing is deleted: the bridge install is set aside whole, and the log names where.
    (aside,) = [p for p in new.parent.iterdir() if p.name.startswith("keepharness.bridge-")]
    assert (aside / "mcp_bridge.py").read_text() == "# bridge\n"
    assert (aside / "venv/bin/python").exists() and str(aside) in caplog.text
    assert product.legacy_waiting(tmp_path) is None


def test_a_target_with_state_next_to_a_bridge_is_never_displaced(tmp_path):
    old = legacy_state(tmp_path)
    new = client_bridge(tmp_path, extra=["settings.json"])
    assert migrate_legacy_state(tmp_path) is None
    assert (old / "settings.json").exists()
    assert sorted(entry.name for entry in new.iterdir()) == ["mcp_bridge.py", "settings.json", "venv"]
    assert [p.name for p in new.parent.iterdir() if "bridge-" in p.name] == []


def test_a_bridge_target_is_not_displaced_while_the_old_state_is_in_use(tmp_path):
    old = legacy_state(tmp_path)
    new = client_bridge(tmp_path)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        runtime = json.loads((old / "runtime.json").read_text())
        runtime["port"] = listener.getsockname()[1]
        (old / "runtime.json").write_text(json.dumps(runtime))
        refused = migrate_legacy_state(tmp_path)
    assert "still answers" in refused and (old / "settings.json").exists()
    assert (new / "mcp_bridge.py").read_text() == "# bridge\n"


def test_a_bridge_target_set_aside_failure_is_a_reason_not_a_crash(tmp_path, monkeypatch):
    old = legacy_state(tmp_path)
    new = client_bridge(tmp_path)

    def refuse(self, target):
        raise OSError(errno.EBUSY, "refused")

    monkeypatch.setattr(Path, "rename", refuse)
    refused = migrate_legacy_state(tmp_path)
    assert "./install.sh" in refused and (old / "settings.json").exists()
    assert (new / "mcp_bridge.py").exists()


def test_the_generated_installer_keeps_the_bridge_out_of_the_state_folder(tmp_path):
    root = Path(product.__file__).resolve().parents[1]
    for relative in ("pyproject.toml", "agent_service/mcp_bridge.py", "agent_service/index.html", "agent_service/ui.js", "agent_service/tour.js", "agent_service/setup-mcp.sh", "control/index.html", "control/admin.js", "harness_ui/assets/theme.js"):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text((root / relative).read_text())
    script = tmp_path / "agent_service/setup-mcp.sh"
    product.generate(tmp_path, PRODUCT)
    assert script.read_text() == (root / "agent_service/setup-mcp.sh").read_text()
    fork = replace(PRODUCT, slug="synthetic-harness", lineage="synthetic", state_dir=".local/share/synthetic-harness", config_dir=".config/synthetic-harness")
    product.generate(tmp_path, fork)
    assert "TH_PRODUCT_BRIDGE=.local/share/synthetic-harness-mcp\n" in script.read_text()
    assert "TH_PRODUCT_STATE" not in script.read_text()


def test_generating_unchanged_assets_writes_nothing(tmp_path):
    """A read-only checkout still builds: the build backend regenerates only what differs."""
    root = Path(product.__file__).resolve().parents[1]
    names = ("pyproject.toml", "agent_service/mcp_bridge.py", "agent_service/index.html", "agent_service/ui.js", "agent_service/tour.js", "agent_service/setup-mcp.sh", "control/index.html", "control/admin.js", "harness_ui/assets/theme.js")
    for relative in names:
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text((root / relative).read_text())
        (tmp_path / relative).chmod(0o444)
    product.generate(tmp_path, PRODUCT)
    for relative in names:
        assert (tmp_path / relative).read_text() == (root / relative).read_text()


def test_the_admin_refuses_a_held_port_before_any_startup_work(tmp_path, monkeypatch):
    import uvicorn

    from agent_service import log_config
    from control import cli, server

    created, started = [], []
    monkeypatch.setattr(log_config, "configure_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "create_app", lambda state, port: created.append(state))
    monkeypatch.setattr(uvicorn, "run", lambda app, **_: started.append(app))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with pytest.raises(SystemExit) as refused:
            cli.main(["--port", str(port), "--state", str(tmp_path / "state")])
    assert f"127.0.0.1:{port}" in str(refused.value.code)
    assert f"pid {os.getpid()}" in str(refused.value.code)
    assert created == [] and started == [] and not (tmp_path / "state").exists()
