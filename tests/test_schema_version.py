import sqlite3

from agent_service.persistence.db import MIGRATIONS, baseline, connect, migrate


def schema(db):
    return sorted(
        tuple(row)
        for row in db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        )
    )


def version(db):
    return [tuple(row) for row in db.execute("SELECT version FROM schema_version")]


def test_fresh_database_gets_the_baseline_schema_and_version(tmp_path):
    db = connect(tmp_path)
    migrate(db)
    assert version(db) == [(len(MIGRATIONS),)]
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    tables = {row[1] for row in schema(db) if row[0] == "table"}
    assert tables == {
        "jobs",
        "events",
        "deleted_conversations",
        "conversation_titles",
        "files",
        "workspaces",
        "approval_rules",
        "registered_projects",
        "deleted_project_folders",
        "schema_version",
    }
    assert "owner" in {column[1] for column in db.execute("PRAGMA table_info(files)")}


def test_pre_versioning_database_migrates_to_the_same_schema_and_keeps_rows(tmp_path):
    for name in ("fresh", "old"):
        (tmp_path / name).mkdir()
    fresh = connect(tmp_path / "fresh")
    migrate(fresh)
    old = connect(tmp_path / "old")
    # A database written before schema_version: files without owner, plus user data.
    old.execute(
        "CREATE TABLE files(id TEXT PRIMARY KEY, project TEXT, name TEXT, size INTEGER, hash TEXT, pages TEXT)"
    )
    baseline(old)
    with old:
        old.execute("INSERT INTO jobs(id,project,owner,state) VALUES('j','p','a','completed')")
    migrate(old)
    assert schema(old) == schema(fresh)
    assert version(old) == [(1,)]
    assert [tuple(row) for row in old.execute("SELECT id,state FROM jobs")] == [("j", "completed")]


def test_double_migrate_is_a_no_op(tmp_path):
    db = connect(tmp_path)
    migrate(db)
    before = schema(db)
    migrate(db)
    assert schema(db) == before
    assert version(db) == [(1,)]
    db.close()
    reopened = sqlite3.connect(tmp_path / "jobs.sqlite3")
    migrate(reopened)
    assert version(reopened) == [(1,)]
