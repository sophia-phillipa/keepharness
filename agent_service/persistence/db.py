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


def bind_effect_endpoints(db):
    """Retain duplicate protection across upgrades using the approved endpoint."""
    from ..integrations import endpoint_identity

    for row in db.execute("SELECT effect_id,binding,contract FROM effects").fetchall():
        binding = json.loads(row["binding"])
        binding["endpoint"] = endpoint_identity(json.loads(row["contract"])["endpoint"])
        db.execute(
            "UPDATE effects SET binding=? WHERE effect_id=?",
            (
                json.dumps(binding, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                row["effect_id"],
            ),
        )


def add_effect_public_content(db):
    """Legacy artifacts have no durable sanitation provenance; keep their bytes private."""
    if "public_content" not in {row[1] for row in db.execute("PRAGMA table_info(effects)")}:
        db.execute("ALTER TABLE effects ADD COLUMN public_content TEXT")
    bind_effect_endpoints(db)


def add_gate_public_spec(db):
    """Recover legacy public text only from events sanitized when they were emitted."""
    if "public_spec" not in {row[1] for row in db.execute("PRAGMA table_info(gates)")}:
        db.execute("ALTER TABLE gates ADD COLUMN public_spec TEXT")
    for gate in db.execute("SELECT gate_id,job_id FROM gates WHERE public_spec IS NULL").fetchall():
        public = None
        for event in db.execute(
            "SELECT type,data FROM events WHERE job=? AND type IN ('gate_required','gate_resolved') ORDER BY id",
            (gate["job_id"],),
        ):
            try:
                data = json.loads(event["data"])
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict) or data.get("gate_id") != gate["gate_id"]:
                continue
            if event["type"] == "gate_required" and public is None:
                public = {
                    key: value
                    for key, value in data.items()
                    if key
                    not in ("schema_version", "execution_id", "attempt", "parent_execution_id")
                }
                public["choice"] = None
            elif event["type"] == "gate_resolved" and public is not None:
                for key in ("choice", "plan"):
                    if key in data:
                        public[key] = data[key]
        if public is None:
            public = {
                "gate_id": gate["gate_id"],
                "question": "Historical question content unavailable.",
                "options": [],
                "choice": None,
            }
        db.execute(
            "UPDATE gates SET public_spec=? WHERE gate_id=?", (encoded(public), gate["gate_id"])
        )


MIGRATIONS = (
    baseline,
    reset_legacy_approval_rules,
    add_gates,
    add_work_item,
    add_effects,
    bind_effect_endpoints,
    add_effect_public_content,
    bind_effect_endpoints,
    add_gate_public_spec,
)


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
