"""./install.sh --merge-legacy: heal a state that a pre-release split between two folders.

The fixture copies the shape of the real split (OPS round 5): keepharness/ holds the
conversation database, an older settings file and a few sessions; tail-harness/ holds the
newer settings, keys and most sessions and uploads, and no database.
"""

import json
import socket
import sqlite3
import tarfile
from contextlib import closing

import pytest

from control import state_merge
from control.product import LEGACY_MARKER, PRODUCT, ensure_lineage, migrate_legacy_state

CURRENT = {"slug": "keepharness", "lineage": "keepharness"}
JOBS = 18


def closed_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def database(path, rows):
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE schema_version(version INTEGER NOT NULL)")
        db.execute("INSERT INTO schema_version VALUES (9)")
        db.execute("CREATE TABLE jobs(id TEXT PRIMARY KEY)")
        db.executemany("INSERT INTO jobs VALUES (?)", [(f"job-{n}",) for n in range(rows)])
        db.commit()


@pytest.fixture
def split(tmp_path):
    share = tmp_path / ".local/share"
    old, new = share / "tail-harness", share / "keepharness"
    # keepharness/: the database side, with older settings.
    write(new / "harness.identity.json", json.dumps(CURRENT))
    write(new / "runs/harness.identity.json", json.dumps(LEGACY_MARKER))
    database(new / "runs/jobs.sqlite3", JOBS)
    with closing(sqlite3.connect(new / "runs/approval_sessions.sqlite3")) as db:
        db.execute("CREATE TABLE sessions(id TEXT)")
        db.commit()
    write(new / "runs/sessions/c3/codex/native-thread.json", '{"id": "t3"}')
    write(new / "runs/files/sem-projeto/f3/image.png", "png-3")
    write(new / "settings.json", json.dumps({"default_backend": "codex", "api_key": "old-secret"}))
    write(new / "vpn.key", "old-key")
    write(new / "runtime.json", json.dumps({"state_dir": str(new / "runs"), "port": closed_port()}))
    write(new / "audit.jsonl", '{"event": "older"}\n')
    write(new / "autostart", "")
    write(new / "harness.log", "log\n")
    write(new / "venv/bin/python", "")
    # tail-harness/: the newer settings, keys, sessions and uploads; no database, no top marker.
    write(old / "settings.json", json.dumps({"default_backend": "claude", "api_key": "new-secret"}))
    write(old / "vpn.key", "new-key")
    write(old / "runtime.json", json.dumps({"state_dir": str(old / "runs"), "port": closed_port()}))
    write(old / "audit.jsonl", '{"event": "newer"}\n')
    write(old / "runs/sessions/a1/claude/claude-session.json", "{}")
    write(old / "runs/sessions/b2/deepseek/native-thread.json", '{"id": "t2"}')
    write(old / "runs/files/sem-projeto/f1/doc.pdf", "pdf-1")
    write(old / "runs/files/sem-projeto/f2/image.png", "png-2")
    return tmp_path, old, new


def snapshot(root):
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }


def test_the_dry_run_lists_every_action_and_changes_nothing(split, capsys):
    home, old, new = split
    before = snapshot(home)
    state_merge.main([], home=home)
    out = capsys.readouterr().out
    assert snapshot(home) == before
    for expected in (
        "jobs.sqlite3", "approval_sessions.sqlite3", "runs/sessions/c3",
        "runs/files/sem-projeto/f3", "autostart", "harness.log", "settings.json", "vpn.key",
        "default_backend", "audit.jsonl", "keepharness.split-", "--apply",
    ):
        assert expected in out
    assert "secret" not in out  # changed secret settings are masked
    assert "venv" not in out


def test_apply_never_glues_two_audit_records_when_the_first_lacks_a_newline(split, capsys):
    home, old, new = split
    (new / "audit.jsonl").write_text('{"event": "older"}')
    state_merge.main(["--apply"], home=home)
    capsys.readouterr()
    lines = (old / "audit.jsonl").read_text().splitlines()
    assert [json.loads(line)["event"] for line in lines] == ["older", "newer"]


def test_apply_merges_into_tail_harness_and_retires_the_split_folder(split, capsys):
    home, old, new = split
    state_merge.main(["--apply"], home=home)
    capsys.readouterr()
    with closing(sqlite3.connect(old / "runs/jobs.sqlite3")) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == JOBS
    assert (old / "runs/approval_sessions.sqlite3").exists()
    assert sorted(p.name for p in (old / "runs/sessions").iterdir()) == ["a1", "b2", "c3"]
    assert sorted(p.name for p in (old / "runs/files/sem-projeto").iterdir()) == ["f1", "f2", "f3"]
    assert (old / "runs/files/sem-projeto/f3/image.png").read_text() == "png-3"
    # tail-harness/ keeps its newer settings and keys; the older ones stay in the retired folder.
    assert json.loads((old / "settings.json").read_text())["default_backend"] == "claude"
    assert (old / "vpn.key").read_text() == "new-key"
    assert (old / "autostart").exists() and (old / "harness.log").read_text() == "log\n"
    assert (old / "audit.jsonl").read_text() == '{"event": "older"}\n{"event": "newer"}\n'
    assert not (old / "venv").exists()
    # 0.14.0 opens it: runs/ carries the Tail Harness marker, the top stays unmarked.
    assert json.loads((old / "runs/harness.identity.json").read_text()) == LEGACY_MARKER
    assert not (old / "harness.identity.json").exists()
    assert not new.exists()
    (retired,) = (home / ".local/share").glob("keepharness.split-*")
    assert (retired / "settings.json").exists() and (retired / "runs/jobs.sqlite3").exists()
    (backup,) = (home / ".local/share").glob("keepharness-merge-*.tar")
    assert backup.stat().st_mode & 0o777 == 0o600
    with tarfile.open(backup) as archive:
        names = archive.getnames()
    assert "keepharness/runs/jobs.sqlite3" in names and "tail-harness/vpn.key" in names
    assert not any("/venv" in name for name in names)
    # Then the usual single-folder move takes the merged state to KeepHarness.
    assert migrate_legacy_state(home) is None
    moved = PRODUCT.state_path(home)
    ensure_lineage(moved)
    assert json.loads((moved / "harness.identity.json").read_text()) == LEGACY_MARKER


def test_the_rollback_archive_leaves_the_claude_json_copies_out(split):
    home, old, new = split
    for folder in (old, new):
        write(folder / "backups/claude-json/00000000000000000001.json", '{"oauthAccount": {}}')
        write(folder / "backups/other.txt", "not a copy of ~/.claude.json")

    state_merge.main(["--apply"], home=home)

    (backup,) = (home / ".local/share").glob("keepharness-merge-*.tar")
    with tarfile.open(backup) as archive:
        names = archive.getnames()
    assert "tail-harness/backups/other.txt" in names and "keepharness/backups/other.txt" in names
    assert not any("claude-json" in name for name in names)


def test_a_conversation_present_in_both_folders_refuses_the_merge(split):
    home, old, new = split
    write(old / "runs/sessions/c3/claude/claude-session.json", "{}")
    before = snapshot(home)
    with pytest.raises(SystemExit) as refused:
        state_merge.main(["--apply"], home=home)
    assert "runs/sessions/c3" in str(refused.value.code)
    assert snapshot(home) == before


def test_two_databases_refuse_the_merge(split):
    home, old, new = split
    database(old / "runs/jobs.sqlite3", 1)
    with pytest.raises(SystemExit) as refused:
        state_merge.main(["--apply"], home=home)
    assert "database" in str(refused.value.code)
    assert new.exists() and not list((home / ".local/share").glob("keepharness-merge-*"))


def test_the_merge_waits_while_either_folder_is_in_use(split):
    home, old, new = split
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        runtime = json.loads((new / "runtime.json").read_text())
        (new / "runtime.json").write_text(json.dumps({**runtime, "port": listener.getsockname()[1]}))
        with pytest.raises(SystemExit) as refused:
            state_merge.main(["--apply"], home=home)
    assert "still answers" in str(refused.value.code)
    assert new.exists() and not (old / "runs/jobs.sqlite3").exists()


def test_without_a_split_there_is_nothing_to_merge(tmp_path):
    with pytest.raises(SystemExit) as refused:
        state_merge.main([], home=tmp_path)
    assert "nothing to merge" in str(refused.value.code)
