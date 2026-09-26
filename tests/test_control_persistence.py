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
