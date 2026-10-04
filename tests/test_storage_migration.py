"""Decision D33 migration: identical existing uploads are linked once, dry run by default."""

import hashlib
import json

from agent_service.persistence.db import connect, migrate
from agent_service.storage_migration import main


def seed(root, uploads):
    """A state folder whose uploads were each stored as a separate copy (before D33)."""
    db = connect(root)
    migrate(db)
    with db:
        for file_id, data in uploads.items():
            folder = root / "files" / "p" / file_id
            folder.mkdir(parents=True)
            (folder / "source").write_bytes(data)
            db.execute(
                "INSERT INTO files(id,project,name,size,hash,pages,owner) VALUES(?,?,?,?,?,?,?)",
                (file_id, "p", file_id, len(data), hashlib.sha256(data).hexdigest(), "[]", "a"),
            )
        db.execute(
            "INSERT INTO jobs(id,project,owner,state,created,payload) VALUES('j','p','a','completed',1,'{}')"
        )
        db.execute("INSERT INTO deleted_conversations VALUES('j')")
    db.close()


def inode(root, file_id):
    return (root / "files" / "p" / file_id / "source").stat().st_ino


def test_dry_run_reports_and_changes_nothing(tmp_path, capsys):
    seed(tmp_path, {"a": b"same", "b": b"same", "c": b"other"})
    before = {name: inode(tmp_path, name) for name in "abc"}

    assert main(["--state-dir", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    assert "p: runs 1/1000, uploads 9/2147483648 bytes, archived conversations 1" in out
    assert "duplicate uploads: 1, bytes to free: 4" in out
    assert {name: inode(tmp_path, name) for name in "abc"} == before
    assert not (tmp_path / "storage-migration.jsonl").exists()


def test_apply_links_duplicates_and_reruns_as_a_no_op(tmp_path, capsys):
    seed(tmp_path, {"a": b"same", "b": b"same", "c": b"other"})

    assert main(["--state-dir", str(tmp_path), "--apply"]) == 0

    assert inode(tmp_path, "a") == inode(tmp_path, "b") != inode(tmp_path, "c")
    assert (tmp_path / "files" / "p" / "b" / "source").read_bytes() == b"same"
    log = [
        json.loads(line) for line in (tmp_path / "storage-migration.jsonl").read_text().splitlines()
    ]
    assert [(line["file_id"], line["linked_to"], line["status"]) for line in log] == [
        ("b", "a", "linked")
    ]
    capsys.readouterr()
    assert main(["--state-dir", str(tmp_path), "--apply"]) == 0
    assert "duplicate uploads: 0" in capsys.readouterr().out


def test_apply_never_links_bytes_that_no_longer_match_their_hash(tmp_path, capsys):
    seed(tmp_path, {"a": b"same", "b": b"same"})
    (tmp_path / "files" / "p" / "b" / "source").write_bytes(b"edit")

    assert main(["--state-dir", str(tmp_path), "--apply"]) == 1

    assert (tmp_path / "files" / "p" / "b" / "source").read_bytes() == b"edit"
    assert "hash_mismatch" in (tmp_path / "storage-migration.jsonl").read_text()
