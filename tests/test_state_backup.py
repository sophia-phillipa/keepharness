"""`keepharness backup` and `keepharness restore` (OPS-R3-2, decision D32)."""

import io
import json
import socket
import sqlite3
import tarfile
import threading
from pathlib import Path

import pytest

from control import backup, cli
from control.backup import BackupRefused
from control.product import PRODUCT

IDENTITY = {"slug": PRODUCT.slug, "lineage": PRODUCT.lineage}


def make_state(root: Path) -> Path:
    state = root / "state"
    (state / "runs").mkdir(parents=True)
    (state / "venv/bin").mkdir(parents=True)
    (state / "providers/codex").mkdir(parents=True)
    (state / "venv/bin/python").write_text("environment")
    (state / "providers/codex/auth.json").write_text("provider login")
    (state / "local.key").write_text("local secret")
    (state / "vpn.key").write_text("vpn secret")
    (state / "harness.secrets.json").write_text("{}")
    (state / "harness.effect_credentials.json").write_text("{}")
    (state / "runs/sessions/o/c").mkdir(parents=True)
    (state / "runs/sessions/o/c/mcp.json").write_text('{"mcpServers": {"x": {"env": {"TOKEN": "t"}}}}')
    (state / "local-sessions.json").write_text("{}")
    (state / "harness.log").write_text("noise")
    (state / "harness.identity.json").write_text(json.dumps(IDENTITY))
    (state / "settings.json").write_text('{"port": 8095}')
    (state / "runs/notes.txt").write_text("a conversation attachment")
    with sqlite3.connect(state / "runs/jobs.sqlite3") as db:
        db.execute("CREATE TABLE jobs(id INTEGER PRIMARY KEY, state TEXT)")
        db.executemany("INSERT INTO jobs(state) VALUES(?)", [("done",)] * 5)
    with sqlite3.connect(state / "runs/approval_sessions.sqlite3") as db:
        db.execute("CREATE TABLE sessions(token TEXT)")
        db.execute("INSERT INTO sessions VALUES('s3cret')")
    return state


def names(archive: Path) -> set[str]:
    with tarfile.open(archive) as tar:
        return set(tar.getnames())


def job_count(database: Path) -> int:
    with sqlite3.connect(database) as db:
        return db.execute("SELECT count(*) FROM jobs").fetchone()[0]


def test_backup_leaves_out_secrets_the_environment_and_logs_by_default(tmp_path):
    state = make_state(tmp_path)
    archive = tmp_path / "out.tar.gz"

    backup.create(state, archive)

    found = names(archive)
    assert {"manifest.json", "settings.json", "runs/jobs.sqlite3", "runs/notes.txt"} <= found
    assert not {n for n in found if n.endswith(".key") or n.startswith(("providers", "venv"))}
    assert not found & {
        "harness.secrets.json", "harness.effect_credentials.json", "runs/sessions/o/c/mcp.json",
        "local-sessions.json", "harness.log", "runs/approval_sessions.sqlite3",
    }
    assert archive.stat().st_mode & 0o777 == 0o600
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".keepharness-backup")]


def test_backup_with_secrets_adds_them_but_still_not_the_environment(tmp_path):
    state = make_state(tmp_path)
    archive = tmp_path / "out.tar.gz"

    manifest = backup.create(state, archive, with_secrets=True)

    found = names(archive)
    assert {
        "local.key", "vpn.key", "providers/codex/auth.json", "runs/approval_sessions.sqlite3",
        "harness.effect_credentials.json", "runs/sessions/o/c/mcp.json",
    } <= found
    assert not any(n.startswith("venv") for n in found)
    assert manifest["with_secrets"] is True


def test_backup_taken_while_a_writer_inserts_restores_the_counts_of_that_moment(tmp_path):
    state = make_state(tmp_path)
    stop, writing = threading.Event(), threading.Event()

    def writer():
        with sqlite3.connect(state / "runs/jobs.sqlite3", timeout=30) as db:
            for written in range(1_000_000):
                db.execute("INSERT INTO jobs(state) VALUES('queued')")
                db.commit()
                if written == 50:
                    writing.set()
                if stop.is_set():
                    break

    thread = threading.Thread(target=writer)
    thread.start()
    assert writing.wait(30)
    try:
        manifest = backup.create(state, tmp_path / "out.tar.gz")
    finally:
        stop.set()
        thread.join()

    target = tmp_path / "restored"
    backup.restore(tmp_path / "out.tar.gz", target, apply=True, home=tmp_path)

    counted = manifest["databases"]["runs/jobs.sqlite3"]["jobs"]
    assert counted >= 55  # the writer was already at work when the backup began
    assert job_count(target / "runs/jobs.sqlite3") == counted
    with sqlite3.connect(target / "runs/jobs.sqlite3") as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_backup_refuses_to_overwrite_and_a_foreign_identity(tmp_path):
    state = make_state(tmp_path)
    archive = tmp_path / "out.tar.gz"
    archive.write_text("precious")

    with pytest.raises(BackupRefused, match="already exists"):
        backup.create(state, archive)
    assert archive.read_text() == "precious"

    (state / "harness.identity.json").write_text(json.dumps({"slug": "other", "lineage": "other"}))
    with pytest.raises(BackupRefused, match="another product"):
        backup.create(state, tmp_path / "other.tar.gz")


def test_restore_gives_back_the_files_and_keeps_a_dry_run_harmless(tmp_path):
    state = make_state(tmp_path)
    backup.create(state, tmp_path / "out.tar.gz")
    target = tmp_path / "new-home"

    plan = backup.restore(tmp_path / "out.tar.gz", target, apply=False, home=tmp_path)
    assert not target.exists()
    assert "Dry run" in plan

    backup.restore(tmp_path / "out.tar.gz", target, apply=True, home=tmp_path)
    assert (target / "settings.json").read_text() == '{"port": 8095}'
    assert (target / "runs/notes.txt").read_text() == "a conversation attachment"
    assert job_count(target / "runs/jobs.sqlite3") == 5
    assert not (target / "local.key").exists()
    assert (target.stat().st_mode & 0o777) == 0o700
    assert not [p for p in tmp_path.iterdir() if ".restoring-" in p.name]


def test_restore_refuses_while_the_service_still_answers(tmp_path):
    state = make_state(tmp_path)
    backup.create(state, tmp_path / "out.tar.gz")
    target = tmp_path / "running"
    (target / "runs").mkdir(parents=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        (target / "runtime.json").write_text(json.dumps({"port": port}))

        with pytest.raises(BackupRefused, match="still in use"):
            backup.restore(tmp_path / "out.tar.gz", target, apply=True, replace=True, home=tmp_path)

    assert (target / "runtime.json").exists()
    assert sorted(p.name for p in target.iterdir()) == ["runs", "runtime.json"]


def test_restore_refuses_state_that_holds_data_unless_replace_and_then_sets_it_aside(tmp_path):
    state = make_state(tmp_path)
    backup.create(state, tmp_path / "out.tar.gz")
    target = tmp_path / "existing"
    (target / "venv").mkdir(parents=True)
    (target / "venv/keep").write_text("environment")
    (target / "settings.json").write_text("newer settings")

    with pytest.raises(BackupRefused, match="--replace"):
        backup.restore(tmp_path / "out.tar.gz", target, apply=True, home=tmp_path)
    assert (target / "settings.json").read_text() == "newer settings"

    backup.restore(tmp_path / "out.tar.gz", target, apply=True, replace=True, home=tmp_path)

    assert (target / "settings.json").read_text() == '{"port": 8095}'
    assert (target / "venv/keep").read_text() == "environment"
    asides = [p for p in tmp_path.iterdir() if p.name.startswith("existing.before-restore-")]
    assert len(asides) == 1
    assert (asides[0] / "settings.json").read_text() == "newer settings"


def test_restore_into_the_default_state_waits_for_the_tail_harness_move(tmp_path):
    state = make_state(tmp_path)
    backup.create(state, tmp_path / "out.tar.gz")
    home = tmp_path / "home"
    (home / ".local/share/tail-harness").mkdir(parents=True)

    with pytest.raises(BackupRefused, match="have not moved"):
        backup.restore(tmp_path / "out.tar.gz", PRODUCT.state_path(home), apply=True, home=home)

    assert not PRODUCT.state_path(home).exists()


def test_restore_refuses_a_backup_of_another_product_and_an_unsafe_archive(tmp_path):
    state = make_state(tmp_path)
    backup.create(state, tmp_path / "out.tar.gz")
    foreign = tmp_path / "foreign.tar.gz"
    with tarfile.open(tmp_path / "out.tar.gz") as source, tarfile.open(foreign, "w:gz") as tar:
        for member in source.getmembers():
            data = source.extractfile(member) if member.isfile() else None
            if member.name == "manifest.json":
                manifest = json.loads(data.read())
                manifest["identity"] = {"slug": "other", "lineage": "other"}
                payload = json.dumps(manifest).encode()
                member.size = len(payload)
                data = io.BytesIO(payload)
            tar.addfile(member, data)

    with pytest.raises(BackupRefused, match="another product"):
        backup.restore(foreign, tmp_path / "t1", apply=True, home=tmp_path)

    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        manifest = json.dumps({"format": 1, "identity": IDENTITY, "databases": {}, "bytes": 1}).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))
        info = tarfile.TarInfo("../escape.txt")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))

    with pytest.raises(BackupRefused, match="unsafe|cannot be restored"):
        backup.restore(evil, tmp_path / "t2", apply=True, home=tmp_path)
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "t2").exists()


def test_restore_rejects_a_database_that_does_not_match_its_manifest(tmp_path):
    state = make_state(tmp_path)
    manifest = backup.create(state, tmp_path / "out.tar.gz")
    tampered = tmp_path / "tampered.tar.gz"
    manifest["databases"]["runs/jobs.sqlite3"]["jobs"] += 1
    with tarfile.open(tmp_path / "out.tar.gz") as source, tarfile.open(tampered, "w:gz") as tar:
        for member in source.getmembers():
            data = source.extractfile(member) if member.isfile() else None
            if member.name == "manifest.json":
                payload = json.dumps(manifest).encode()
                member.size = len(payload)
                data = io.BytesIO(payload)
            tar.addfile(member, data)

    with pytest.raises(BackupRefused, match="does not match"):
        backup.restore(tampered, tmp_path / "t", apply=True, home=tmp_path)
    assert not (tmp_path / "t").exists()
    assert not [p for p in tmp_path.iterdir() if ".restoring-" in p.name]


def test_the_command_line_backs_up_and_restores(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state = make_state(tmp_path)
    archive = tmp_path / "cli.tar.gz"

    cli.main(["--state", str(state), "backup", "--output", str(archive)])
    assert archive.is_file()
    assert "without secrets" in capsys.readouterr().out

    target = tmp_path / "restored"
    cli.main(["--state", str(target), "restore", str(archive)])
    assert not target.exists()
    cli.main(["--state", str(target), "restore", str(archive), "--apply"])
    assert (target / "settings.json").is_file()

    with pytest.raises(SystemExit, match="still holds data|--replace"):
        cli.main(["--state", str(target), "restore", str(archive), "--apply"])


def existing_state(root: Path) -> Path:
    target = root / "existing"
    (target / "venv").mkdir(parents=True)
    (target / "venv/keep").write_text("environment")
    (target / "runs").mkdir()
    (target / "runs/own.txt").write_text("own")
    (target / "settings.json").write_text("newer settings")
    (target / "zz-extra.txt").write_text("extra")
    return target


def test_a_swap_that_fails_midway_puts_every_original_back(tmp_path, monkeypatch):
    state = make_state(tmp_path)
    backup.create(state, tmp_path / "out.tar.gz")
    target = existing_state(tmp_path)
    real_rename = Path.rename

    def failing(self, destination):
        if self.name == "settings.json" and ".restoring-" in str(self.parent):
            raise OSError("disk went away")
        return real_rename(self, destination)

    monkeypatch.setattr(Path, "rename", failing)
    with pytest.raises(OSError, match="disk went away"):
        backup.restore(tmp_path / "out.tar.gz", target, apply=True, replace=True, home=tmp_path)
    monkeypatch.undo()

    assert (target / "settings.json").read_text() == "newer settings"
    assert (target / "runs/own.txt").read_text() == "own"
    assert (target / "zz-extra.txt").read_text() == "extra"
    assert (target / "venv/keep").read_text() == "environment"
    assert not (target / "runs/notes.txt").exists()  # nothing of the backup was left half in


def test_restore_never_lets_a_dot_prefixed_environment_member_touch_the_venv(tmp_path):
    state = make_state(tmp_path)
    backup.create(state, tmp_path / "out.tar.gz")

    with tarfile.open(tmp_path / "out.tar.gz") as old, tarfile.open(tmp_path / "dot.tar.gz", "w:gz") as new:
        for member in old.getmembers():
            new.addfile(member, old.extractfile(member) if member.isfile() else None)
        extra = tarfile.TarInfo("./venv/x")
        extra.size = 1
        new.addfile(extra, io.BytesIO(b"x"))
    target = existing_state(tmp_path)

    backup.restore(tmp_path / "dot.tar.gz", target, apply=True, replace=True, home=tmp_path)

    assert (target / "settings.json").read_text() == '{"port": 8095}'
    assert (target / "venv/keep").read_text() == "environment"
    assert not (target / "venv/x").exists()
    aside = next(p for p in tmp_path.iterdir() if p.name.startswith("existing.before-restore-"))
    assert (aside / "settings.json").read_text() == "newer settings"


def test_a_manifest_that_is_a_folder_is_refused_cleanly(tmp_path):
    archive = tmp_path / "odd.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        folder = tarfile.TarInfo("manifest.json")
        folder.type = tarfile.DIRTYPE
        tar.addfile(folder)

    with pytest.raises(BackupRefused, match="not a KeepHarness backup"):
        backup.restore(archive, tmp_path / "t", home=tmp_path)
