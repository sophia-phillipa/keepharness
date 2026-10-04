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
# Absolute from creation: use never extends it and there is no idle timeout.
SESSION_SECONDS = 7 * 24 * 60 * 60
LEGACY_SESSION_SECONDS = 30 * 24 * 60 * 60


def initialize_session_database(config):
    """Repair private storage and create the schema at startup or from the owner CLI."""
    root = Path(config["state_dir"])
    path = root / "approval_sessions.sqlite3"
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fchmod(descriptor, 0o700)
        finally:
            os.close(descriptor)
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                descriptor = private_file(
                    Path(str(path) + suffix),
                    os.O_WRONLY | os.O_NOFOLLOW | (os.O_CREAT if not suffix else 0),
                )
            except FileNotFoundError:
                if suffix:
                    continue
                raise
            try:
                os.fchmod(descriptor, 0o600)
            finally:
                os.close(descriptor)
    except (OSError, NotImplementedError) as exc:
        raise APIError("approval_storage_unsafe", 503) from exc
    with session_database(config) as database:
        database.executescript("""
            CREATE TABLE IF NOT EXISTS enrollments(
                digest TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions(
                digest TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL,
                approval_capable INTEGER NOT NULL, created REAL
            );
        """)
        columns = {row[1] for row in database.execute("PRAGMA table_info(sessions)")}
        if "created" not in columns:
            # Legacy rows kept only their 30-day expiry; the creation time follows from it.
            database.execute("ALTER TABLE sessions ADD COLUMN created REAL")
            database.execute(
                "UPDATE sessions SET created = expires - ?", (LEGACY_SESSION_SECONDS,)
            )
        database.execute(
            "UPDATE sessions SET expires = created + ? WHERE expires > created + ?",
            (SESSION_SECONDS, SESSION_SECONDS),
        )


@contextmanager
def session_database(config, *, readonly=False):
    """Open existing storage without schema or permission changes on requests."""
    path = Path(config["state_dir"]) / "approval_sessions.sqlite3"
    mode = "ro" if readonly else "rw"
    database = sqlite3.connect(path.resolve().as_uri() + f"?mode={mode}", uri=True)
    try:
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
    initialize_session_database(config)
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
        now = time.time()
        database.execute("DELETE FROM sessions WHERE expires <= ?", (now,))
        database.execute(
            "INSERT INTO sessions(digest, owner, expires, approval_capable, created)"
            " VALUES(?,?,?,1,?)",
            (token_digest(token), row[0], now + SESSION_SECONDS, now),
        )
    return token


def revoke_sessions(config, owner=None):
    """Owner CLI: revoke existing sessions and links that could recreate them."""
    if owner is not None and owner not in config["clients"]:
        raise APIError("approval_owner_unknown", 403)
    initialize_session_database(config)
    with session_database(config) as database:
        for table in ("sessions", "enrollments"):
            if owner is None:
                database.execute(f"DELETE FROM {table}")
            else:
                database.execute(f"DELETE FROM {table} WHERE owner=?", (owner,))


def revoke_session(config, token):
    """Remove only the current browser's session; other devices remain enrolled."""
    with session_database(config) as database:
        database.execute("DELETE FROM sessions WHERE digest=?", (token_digest(token),))


def session_identity(request, config):
    token = request.cookies.get(SESSION_COOKIE)
    if not token or len(token) > 128:
        return None
    with session_database(config, readonly=True) as database:
        row = database.execute(
            "SELECT owner FROM sessions WHERE digest=? AND expires>? AND approval_capable=1",
            (token_digest(token), time.time()),
        ).fetchone()
    return row[0] if row and row[0] in config["clients"] else None


def session_expired(request, config, owner):
    """True when the browser's cookie names this owner's session that has already expired."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token or len(token) > 128:
        return False
    with session_database(config, readonly=True) as database:
        row = database.execute(
            "SELECT 1 FROM sessions WHERE digest=? AND owner=? AND expires<=? AND approval_capable=1",
            (token_digest(token), owner, time.time()),
        ).fetchone()
    return row is not None


def require_approval_session(request, config, identity, *, revalidate=False):
    owner = (
        session_identity(request, config)
        if revalidate
        else getattr(request.state, "approval_session_owner", None)
    )
    if owner != identity[0]:
        login = next(
            (
                name
                for name, client in config.get("tailscale_logins", {}).items()
                if client == identity[0]
            ),
            None,
        )
        code = (
            "approval_session_expired"
            if session_expired(request, config, identity[0])
            else "approval_session_required"
        )
        raise APIError(code, 403, owner=identity[0], login=login)
