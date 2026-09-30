"""Owner-issued enrollment and browser-only approval authority.

This boundary is advisory when a worker has unrestricted shell access as the
owner: that process can read the same private state or invoke the owner CLI.
"""

import hashlib
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .errors import APIError
from .persistence.db import private_file

SESSION_COOKIE = "harness_session"
ENROLLMENT_SECONDS = 600
SESSION_SECONDS = 30 * 24 * 60 * 60


@contextmanager
def session_database(config):
    root = Path(config["state_dir"])
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    path = root / "approval_sessions.sqlite3"
    descriptor = private_file(path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW)
    try:
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)
    for suffix in ("-journal", "-wal", "-shm"):
        try:
            Path(str(path) + suffix).chmod(0o600, follow_symlinks=False)
        except FileNotFoundError:
            pass
    database = sqlite3.connect(path)
    try:
        database.executescript("""
            CREATE TABLE IF NOT EXISTS enrollments(
                digest TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions(
                digest TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL,
                approval_capable INTEGER NOT NULL
            );
        """)
        with database:
            yield database
    finally:
        database.close()


def token_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def issue_enrollment(config, owner):
    """Owner CLI only: never expose issuance through an HTTP or MCP route."""
    if owner not in config["clients"]:
        raise APIError("approval_owner_unknown", 403)
    nonce = secrets.token_urlsafe(32)
    with session_database(config) as database:
        database.execute("DELETE FROM enrollments WHERE expires <= ?", (time.time(),))
        database.execute(
            "INSERT INTO enrollments VALUES(?,?,?)",
            (token_digest(nonce), owner, time.time() + ENROLLMENT_SECONDS),
        )
    return nonce


def consume_enrollment(config, nonce):
    """Consume the nonce and issue its owner's session in one transaction."""
    if not nonce or len(nonce) > 128:
        raise APIError("approval_enrollment_invalid", 403)
    with session_database(config) as database:
        database.execute("BEGIN IMMEDIATE")
        row = database.execute(
            "SELECT owner FROM enrollments WHERE digest=? AND expires>?",
            (token_digest(nonce), time.time()),
        ).fetchone()
        if not row or row[0] not in config["clients"]:
            raise APIError("approval_enrollment_invalid", 403)
        database.execute("DELETE FROM enrollments WHERE digest=?", (token_digest(nonce),))
        token = secrets.token_urlsafe(32)
        database.execute("DELETE FROM sessions WHERE expires <= ?", (time.time(),))
        database.execute(
            "INSERT INTO sessions VALUES(?,?,?,1)",
            (token_digest(token), row[0], time.time() + SESSION_SECONDS),
        )
    return token


def session_identity(request, config):
    token = request.cookies.get(SESSION_COOKIE)
    if not token or len(token) > 128:
        return None
    with session_database(config) as database:
        row = database.execute(
            "SELECT owner FROM sessions WHERE digest=? AND expires>? AND approval_capable=1",
            (token_digest(token), time.time()),
        ).fetchone()
    return row[0] if row and row[0] in config["clients"] else None


def require_approval_session(request, config, identity):
    if session_identity(request, config) != identity[0]:
        raise APIError("approval_session_required", 403)
