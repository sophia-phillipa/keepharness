import json
import sqlite3

from control.persistence import ControlStateRepository
from control.server import Manager


def test_settings_and_runtime_are_written_atomically_and_privately(tmp_path):
    repository = ControlStateRepository(tmp_path)
    repository.save_settings({"port": 8095})
    repository.write_runtime({"port": 8095})
    assert json.loads((tmp_path / "settings.json").read_text()) == {"port": 8095}
    assert repository.read_runtime() == {"port": 8095}
    for name in ("settings.json", "runtime.json"):
        assert (tmp_path / name).stat().st_mode & 0o777 == 0o600
        assert not (tmp_path / name).with_suffix(".tmp").exists()


def test_unreadable_runtime_reads_as_empty(tmp_path):
    repository = ControlStateRepository(tmp_path)
    assert repository.read_runtime() == {}
    (tmp_path / "runtime.json").write_text("{broken")
    assert repository.read_runtime() == {}


def test_restore_puts_back_changed_and_removes_created_files(tmp_path):
    repository = ControlStateRepository(tmp_path)
    (tmp_path / "settings.json").write_text("before")
    previous = repository.snapshot()
    (tmp_path / "settings.json").write_text("after")
    (tmp_path / "autostart").touch()
    repository.restore(previous)
    assert (tmp_path / "settings.json").read_text() == "before"
    assert not (tmp_path / "autostart").exists()
    assert not list(tmp_path.glob("*.rollback"))


def test_audit_appends_json_lines(tmp_path):
    repository = ControlStateRepository(tmp_path)
    repository.audit("first")
    repository.audit("second")
    lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    assert [json.loads(line)["action"] for line in lines] == ["first", "second"]


def test_harness_busy_reads_the_agent_queue(tmp_path):
    repository = ControlStateRepository(tmp_path)
    assert repository.harness_busy() is False
    (tmp_path / "runs").mkdir()
    with sqlite3.connect(tmp_path / "runs/jobs.sqlite3") as db:
        db.execute("CREATE TABLE jobs(id TEXT, state TEXT)")
        db.execute("INSERT INTO jobs VALUES('a','completed')")
    assert repository.harness_busy() is False
    with sqlite3.connect(tmp_path / "runs/jobs.sqlite3") as db:
        db.execute("INSERT INTO jobs VALUES('b','queued')")
    assert repository.harness_busy() is True


def test_manager_keeps_its_delegate_methods(tmp_path):
    manager = Manager(tmp_path)
    assert manager.path == manager.state_repository.settings_path
    manager._write_runtime({"x": 1})
    assert manager._previous_runtime() == {"x": 1}
    assert manager.busy() is False


def admin(tmp_path):
    manager = Manager(tmp_path)
    manager.admin_port = 8094
    return manager


def test_admin_save_keeps_capacity_and_approval_settings(tmp_path):
    """HAR-R2-5: a save that does not send these keys keeps the stored values."""
    import copy

    manager = admin(tmp_path)
    stored = copy.deepcopy(manager.settings)
    stored["services"]["claude"]["max_concurrent"] = 3
    stored["approval_timeout_seconds"] = 600
    stored["approval_max_consecutive_expirations"] = 1
    manager.save(stored)
    posted = copy.deepcopy(manager.settings)
    del posted["services"]["claude"]["max_concurrent"]
    del posted["approval_timeout_seconds"], posted["approval_max_consecutive_expirations"]
    manager.save(posted)
    saved = json.loads(manager.path.read_text())
    assert saved["services"]["claude"]["max_concurrent"] == 3
    assert "max_concurrent" not in saved["services"]["codex"]  # the D14 default applies
    assert (saved["approval_timeout_seconds"], saved["approval_max_consecutive_expirations"]) == (
        600,
        1,
    )
    posted["services"]["claude"]["max_concurrent"] = 1
    manager.save(posted)
    assert manager.settings["services"]["claude"]["max_concurrent"] == 1


def test_admin_refuses_invalid_capacity_and_approval_settings(tmp_path):
    import copy

    import pytest

    manager = admin(tmp_path)
    for key, value in (
        ("max_concurrent", 0),
        ("max_concurrent", True),
        ("max_concurrent", 99),
        ("approval_timeout_seconds", 0),
        ("approval_timeout_seconds", "1800"),
        ("approval_max_consecutive_expirations", 0),
    ):
        settings = copy.deepcopy(manager.settings)
        target = settings["services"]["codex"] if key == "max_concurrent" else settings
        target[key] = value
        with pytest.raises(ValueError):
            manager.validate(settings)


def test_runtime_config_carries_the_approval_settings(tmp_path):
    from pathlib import Path

    from control.runtime_config import base_config

    manager = admin(tmp_path)
    settings = {**manager.settings, "approval_timeout_seconds": 900}
    runtime = base_config(settings, Path(tmp_path), 8094, "http://127.0.0.1:8095/", {})
    assert runtime["approval_timeout_seconds"] == 900
    assert "approval_max_consecutive_expirations" not in runtime
