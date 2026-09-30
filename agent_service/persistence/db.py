"""Connection, JSON encoding and versioned schema migrations for ``jobs.sqlite3``."""

import json
import os
import sqlite3


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def private_file(path, flags):
    """``open`` opener: state files hold requests and ids, so they are owner-only."""
    return os.open(path, flags, 0o600)


def connect(root):
    path = root / "jobs.sqlite3"
    # SQLite gives its -wal/-shm files the database file's mode.
    os.close(private_file(path, os.O_CREAT | os.O_WRONLY))
    db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


def baseline(db):
    """The schema created before versioning existed; idempotent on any older database."""
    db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, project TEXT, owner TEXT, state TEXT,
          created REAL, payload TEXT, result TEXT, idem TEXT, digest TEXT, UNIQUE(owner,project,idem));
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT, time REAL, type TEXT, data TEXT);
        CREATE TABLE IF NOT EXISTS deleted_conversations(id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS conversation_titles(id TEXT PRIMARY KEY, title TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS events_job ON events(job,id);
        CREATE INDEX IF NOT EXISTS events_job_terminal ON events(job,type,time);
        CREATE INDEX IF NOT EXISTS events_terminal_time ON events(type,time,job);
        CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created);
        CREATE INDEX IF NOT EXISTS jobs_state_created ON jobs(state,created);
        CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY, project TEXT, name TEXT, size INTEGER, hash TEXT, pages TEXT);
        """)
    if "owner" not in {column[1] for column in db.execute("PRAGMA table_info(files)")}:
        # Older attachments have no reliable owner; keep them private until re-uploaded.
        db.execute("ALTER TABLE files ADD COLUMN owner TEXT")
    db.execute("CREATE INDEX IF NOT EXISTS files_owner_project ON files(owner,project)")
    db.execute(
        "CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY, project TEXT, owner TEXT, name TEXT, created REAL, manifest TEXT)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS approval_rules(owner TEXT, conversation TEXT, backend TEXT, model TEXT, fingerprint TEXT, PRIMARY KEY(owner,conversation,backend,model,fingerprint))"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS registered_projects(id TEXT PRIMARY KEY, spec TEXT NOT NULL)"
    )
    db.execute("CREATE TABLE IF NOT EXISTS deleted_project_folders(id TEXT PRIMARY KEY)")


def reset_legacy_approval_rules(db):
    """Pre-0.7.0 rules may have been approved by workers rather than humans."""
    db.execute("DELETE FROM approval_rules")


# Append-only: migration N upgrades a database from version N-1 to N.
def add_gates(db):
    db.execute(
        "CREATE TABLE gates(gate_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, "
        "state TEXT NOT NULL, spec TEXT NOT NULL, choice TEXT, resolved_by TEXT, resolved_at REAL)"
    )


def add_work_item(db):
    db.execute("ALTER TABLE jobs ADD COLUMN work_item TEXT")
    db.execute("CREATE INDEX jobs_project_work_item ON jobs(project,work_item)")


def add_effects(db):
    db.execute("""CREATE TABLE effects(
        effect_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, gate_id TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL, request TEXT NOT NULL, artifact TEXT NOT NULL,
        binding TEXT NOT NULL, contract TEXT NOT NULL, execution_id TEXT NOT NULL,
        enforcement TEXT NOT NULL, approved_by TEXT, receipt TEXT,
        reconcile_attempts INTEGER NOT NULL DEFAULT 0, next_reconcile_at REAL NOT NULL DEFAULT 0
    )""")
    db.execute("CREATE INDEX effects_job ON effects(job_id)")


MIGRATIONS = (baseline, reset_legacy_approval_rules, add_gates, add_work_item, add_effects)


def migrate(db):
    """Apply pending migrations and record the version; older code ignores the table."""
    db.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL)")
    row = db.execute("SELECT version FROM schema_version").fetchone()
    version = row[0] if row else 0
    for number, migration in enumerate(MIGRATIONS[version:], start=version + 1):
        migration(db)
        with db:
            db.execute("DELETE FROM schema_version")
            db.execute("INSERT INTO schema_version VALUES(?)", (number,))
