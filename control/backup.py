"""Back up and restore the KeepHarness state folder (decision D32).

``keepharness backup`` writes one owner-only archive: every SQLite database is copied with
SQLite's online backup API (consistent while the harness writes), and a manifest with the row
counts comes first. Provider logins, keys and sessions stay out unless ``--with-secrets`` asks
for them; the program's own environment (``venv``), the logs and the pre-write copies of the
owner's ``~/.claude.json`` (``backups/claude-json``) never go in.

``keepharness restore ARCHIVE`` prints the plan and changes nothing; ``--apply`` runs it. It
refuses while the state is in use, and while the folder already holds data unless ``--replace``
is given; then what was there is moved to ``<state>.before-restore-<stamp>``, never deleted.
To undo a restore, move the restored folder's contents back from that folder.
"""

import io
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from contextlib import closing
from pathlib import Path

from adapters.shared.provider_state import CLAUDE_JSON_BACKUP_PARTS
from agent_service.config import VERSION_FILE

from .product import (
    LEGACY_MARKER,
    PRODUCT,
    legacy_waiting,
    migration_lock,
    read_marker,
    state_in_use,
)

MANIFEST = "manifest.json"
FORMAT = 1
KEPT = ("venv",)  # the installed program: never backed up, never moved by a restore
# Names that can hold a credential wherever they sit: the secret vaults, the sign-in sessions,
# and a conversation's MCP config (the owner's connector settings and the effect capability).
SECRET_FILES = (
    "approval_sessions.sqlite3",
    "harness.secrets.json",
    "harness.effect_credentials.json",
    "local-sessions.json",
    "mcp.json",
    ".credentials.json",
    "oauth_creds.json",
    ".claude.json",
)
SECRET_SUFFIXES = (".key", ".pem")
VOLATILE_SUFFIXES = (".log", ".lock", ".tmp", ".partial", "-wal", "-shm", "-journal")


class BackupRefused(Exception):
    """Why a backup or restore cannot go ahead; nothing was changed."""


def is_secret(relative: Path) -> bool:
    name = relative.name
    return relative.parts[0] == "providers" or name.endswith(SECRET_SUFFIXES) or name.startswith(SECRET_FILES)


def is_temporary_chat(parts):
    """The volatile temporary-chat folders (run folders sit in the sessions folder or beside it)."""
    return (
        parts[:2] in (("runs", "temporary-chats"), ("runs", "temporary-chats.lock"))
        or parts[:3] == ("runs", "sessions", "temporary-chats")
    )


def wanted(relative: Path, with_secrets: bool) -> bool:
    if is_temporary_chat(relative.parts):
        return False  # disposable chat artifacts must never become durable backups
    if relative.parts[0] in KEPT or relative.name.endswith(VOLATILE_SUFFIXES):
        return False
    if relative.parts[: len(CLAUDE_JSON_BACKUP_PARTS)] == CLAUDE_JSON_BACKUP_PARTS:
        return False  # copies of ~/.claude.json hold the sign-in session: never exported
    return with_secrets or not is_secret(relative)


def selected(state: Path, with_secrets: bool):
    """The folders and files of ``state`` that go into a backup, as ``(path, relative)``."""
    for folder, dirs, files in os.walk(state):
        base = Path(folder).relative_to(state)
        dirs[:] = sorted(d for d in dirs if wanted(base / d, with_secrets) and not (Path(folder) / d).is_symlink())
        for name in dirs:
            yield Path(folder) / name, base / name
        for name in sorted(files):
            path = Path(folder) / name
            if wanted(base / name, with_secrets) and path.is_file() and not path.is_symlink():
                yield path, base / name


def refuse_foreign(marker: dict | None, where: str) -> None:
    if marker not in (None, LEGACY_MARKER, {"slug": PRODUCT.slug, "lineage": PRODUCT.lineage}):
        raise BackupRefused(f"{where} belongs to another product identity.")


def row_counts(db: sqlite3.Connection) -> dict[str, int]:
    tables = [n for (n,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {t: db.execute('SELECT count(*) FROM "%s"' % t.replace('"', '""')).fetchone()[0] for t in tables}


def snapshot_database(source: Path, target: Path) -> dict[str, int]:
    """Copy ``source`` with the online backup API, check the copy, return its rows per table."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source, timeout=30)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
        if (status := dst.execute("PRAGMA integrity_check").fetchone()[0]) != "ok":
            raise BackupRefused(f"The copy of {source} failed its check ({status}).")
        return row_counts(dst)


def fsync_directory(folder: Path) -> None:
    descriptor = os.open(folder, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_archive(partial: Path, manifest: dict, members: list[tuple[Path, Path]]) -> None:
    partial.unlink(missing_ok=True)
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as raw:
        with tarfile.open(fileobj=raw, mode="w:gz") as tar:
            payload = json.dumps(manifest, indent=2).encode()
            info = tarfile.TarInfo(MANIFEST)  # first, so a restore can plan without reading the rest
            info.size, info.mode, info.mtime = len(payload), 0o600, time.time()
            tar.addfile(info, io.BytesIO(payload))
            for path, relative in members:
                tar.add(path, arcname=relative.as_posix(), recursive=False)
        raw.flush()
        os.fsync(raw.fileno())


def check_source(state: Path, output: Path) -> dict | None:
    """Refuse a state that is not ours or an output that would clobber or land inside it."""
    if not state.is_dir():
        raise BackupRefused(f"No {PRODUCT.name} state at {state}; nothing to back up.")
    try:
        marker = read_marker(state)
    except (OSError, ValueError) as exc:
        raise BackupRefused(f"{state} has an unreadable identity marker.") from exc
    refuse_foreign(marker, str(state))
    if os.path.lexists(output):
        raise BackupRefused(f"{output} already exists; choose another name.")
    if output.resolve().is_relative_to(state.resolve()):
        raise BackupRefused(f"Write the backup outside {state}.")
    return marker


def stage_databases(members: list[tuple[Path, Path]], staging: Path) -> dict[str, dict[str, int]]:
    """Replace each SQLite file in ``members`` by an online copy in ``staging``; its row counts."""
    databases = {}
    for index, (path, relative) in enumerate(members):
        if relative.suffix == ".sqlite3" and path.is_file():
            members[index] = (staging / relative, relative)
            databases[relative.as_posix()] = snapshot_database(path, staging / relative)
    return databases


def create(state: Path, output: Path, *, with_secrets: bool = False) -> dict:
    """Write a backup of ``state`` to the new file ``output``; the manifest it carries."""
    state, output = Path(state), Path(output)
    marker = check_source(state, output)
    members = list(selected(state, with_secrets))
    size = sum(path.stat().st_size for path, _ in members if path.is_file())
    if shutil.disk_usage(output.parent).free < 2 * size:
        raise BackupRefused(f"Not enough free space in {output.parent} for a backup of {size} bytes.")
    staging = Path(tempfile.mkdtemp(prefix=".keepharness-backup-", dir=output.parent))
    partial = output.with_name(output.name + ".partial")
    try:
        manifest = {
            "format": FORMAT,
            "product": PRODUCT.slug,
            "version": VERSION_FILE.read_text().strip(),
            "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "identity": marker,
            "with_secrets": with_secrets,
            "files": sum(path.is_file() for path, _ in members),
            "bytes": size,
            "databases": stage_databases(members, staging),
        }
        write_archive(partial, manifest, members)
        try:
            os.link(partial, output)  # fails if the name appeared meanwhile: never overwrites
        except FileExistsError:
            raise BackupRefused(f"{output} already exists; choose another name.") from None
        fsync_directory(output.parent)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        partial.unlink(missing_ok=True)
    return manifest


def read_manifest(archive: Path) -> dict:
    try:
        with tarfile.open(archive) as tar:
            first = tar.next()
            manifest = json.load(tar.extractfile(first)) if first and first.name == MANIFEST and first.isfile() else None
    except (tarfile.TarError, OSError, ValueError, KeyError) as exc:
        raise BackupRefused(f"{archive} is not a {PRODUCT.name} backup ({exc}).") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or not isinstance(manifest.get("databases", {}), dict):
        raise BackupRefused(f"{archive} is not a {PRODUCT.name} backup this version can read.")
    return manifest


def check_target(state: Path, replace: bool) -> list[str]:
    """Refuse a state that is in use, or holds data without ``--replace``; what would be set aside."""
    if state.is_symlink() or (state.exists() and not state.is_dir()):
        raise BackupRefused(f"{state} is not a folder.")
    if busy := state_in_use(state):
        raise BackupRefused(f"{state} is still in use: {busy}. Stop {PRODUCT.name} first.")
    ignored = (*KEPT, "harness.identity.json")
    held = [p.name for p in sorted(state.iterdir()) if p.name not in ignored] if state.is_dir() else []
    if held and not replace:
        raise BackupRefused(
            f"{state} already holds data ({', '.join(held[:5])}). Run with --replace to move it aside "
            f"as {state.name}.before-restore-<time> (nothing is deleted), or move it aside yourself."
        )
    return held


def describe(archive: Path, manifest: dict, state: Path, held: list[str]) -> str:
    lines = [
        f"Restore {archive} into {state}:",
        f"  taken {manifest.get('created', '?')} by {PRODUCT.name} {manifest.get('version', '?')}, "
        + ("including secrets" if manifest.get("with_secrets") else "without secrets"),
        f"  {manifest.get('files', '?')} files, {manifest.get('bytes', '?')} bytes",
    ]
    for name, counts in manifest.get("databases", {}).items():
        lines.append(f"  database {name}: " + ", ".join(f"{t} {n}" for t, n in counts.items()))
    if held:
        lines.append(f"  move what is there ({', '.join(held[:5])}) aside first; the environment stays")
    return "\n".join(lines)


def extract(archive: Path, staging: Path) -> None:
    try:
        with tarfile.open(archive) as tar:
            for member in tar:
                parts = Path(member.name).parts  # "./venv/x" is the environment too
                if member.name != MANIFEST and parts and parts[0] not in KEPT:
                    tar.extract(member, staging, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise BackupRefused(f"{archive} cannot be restored ({exc}); nothing was changed.") from exc
    for path in [staging, *staging.rglob("*")]:
        if not path.is_symlink():
            path.chmod(path.stat().st_mode & 0o700)  # owner-only whatever the archive says


def verify(staging: Path, manifest: dict) -> None:
    """Every database in the manifest must be there, intact, with the counts it was backed up with."""
    root = staging.resolve()
    for relative, counts in manifest.get("databases", {}).items():
        path = (staging / relative).resolve()
        try:
            if not path.is_relative_to(root):
                raise sqlite3.DatabaseError("outside the backup")
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                found = db.execute("PRAGMA integrity_check").fetchone()[0], row_counts(db)
        except sqlite3.DatabaseError as exc:
            raise BackupRefused(f"{relative} does not match the manifest ({exc}); nothing was changed.") from exc
        if found != ("ok", counts):
            raise BackupRefused(f"{relative} does not match the manifest; nothing was changed.")


def swap_in(staging: Path, state: Path) -> Path | None:
    """Move the restored entries in, the old ones to a sibling folder; returns that folder."""
    if not state.exists():
        staging.rename(state)
        return None
    aside = state.with_name(f"{state.name}.before-restore-{time.strftime('%Y%m%d-%H%M%S')}")
    moved, placed = [], []
    try:
        for entry in sorted(state.iterdir()):
            if entry.name not in KEPT:
                if not moved:
                    aside.mkdir(mode=0o700)  # never into a folder that already exists
                moved.append((entry.rename(aside / entry.name), entry))
        for entry in sorted(staging.iterdir()):
            placed.append((entry.rename(state / entry.name), entry))
    except BaseException:
        # Newest first: the restored entries leave before the originals come back.
        for current, original in reversed(moved + placed):
            current.rename(original)
        raise
    return aside if moved else None


def restore(archive: Path, state: Path, *, apply: bool = False, replace: bool = False, home: Path | None = None) -> str:
    """The plan for putting ``archive`` into ``state``; with ``apply``, do it and say what happened."""
    archive, state = Path(archive), Path(state)
    home = Path(home) if home is not None else Path.home()
    manifest = read_manifest(archive)
    refuse_foreign(manifest.get("identity"), "The backup")
    # A new folder here would stop the pending move of the Tail Harness state (decision D24).
    if state == PRODUCT.state_path(home) and (waiting := legacy_waiting(home)):
        raise BackupRefused(waiting)
    plan = describe(archive, manifest, state, check_target(state, replace))
    if not apply:
        return plan + "\nDry run: nothing changed. Run again with --apply to restore."
    state.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(state.parent).free < 2 * int(manifest.get("bytes", 0)):
        raise BackupRefused(f"Not enough free space in {state.parent} to restore.")
    with migration_lock(home):
        check_target(state, replace)  # again, now that no install.sh can move a folder meanwhile
        staging = Path(tempfile.mkdtemp(prefix=f".{state.name}.restoring-", dir=state.parent))
        try:
            extract(archive, staging)
            verify(staging, manifest)
            aside = swap_in(staging, state)
            fsync_directory(state.parent)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return plan + f"\nRestored into {state}." + (f" The previous data is in {aside}." if aside else "")
