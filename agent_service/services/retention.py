"""Archive, permanent deletion and storage use of conversations (decisions D31 and D33).

Archive hides a conversation and Unarchive brings it back whole. Delete permanently purges it:
turns, events, gates, effects, uploads no other turn attaches, session folders, Maestro plans,
workspace answer copies and the provider sessions kept in the harness-owned provider homes.
The audit keeps one line per turn with ids, times, model and tokens, never content.
"""

import json
import logging
import os
import shutil
import time

from ..config import MAX_PROJECT_RUNS, MAX_PROJECT_UPLOAD_BYTES, STORAGE_WARNING_RATIO, TERMINAL
from ..errors import APIError
from ..persistence.db import encoded, private_file

logger = logging.getLogger(__name__)

AUDIT_FILE = "purged-turns.jsonl"


def set_archived(service, identity, cid, archived):
    """Archive or unarchive the caller's conversation; a running one cannot be archived."""
    rows = service.conversation(identity, cid, archived=True)
    if archived and any(row["state"] not in TERMINAL for row in rows):
        raise APIError("conversation_busy", 409)
    with service.db:
        if archived:
            service.conversation_repository.archive(cid)
        else:
            service.conversation_repository.unarchive(cid)
    return {"id": cid, "archived": archived}


def begin_purge(service, identity, cid):
    """Hide the conversation so no turn can join it; its turns and the uploads only it uses.

    Disk goes before rows: a purge cut short leaves an archived conversation whose permanent
    deletion can simply run again.
    """
    with service.db:
        # The shared connection may already be inside a transaction; the writes then join it.
        if not service.db.in_transaction:
            service.db.execute("BEGIN IMMEDIATE")
        rows = service.conversation(identity, cid, archived=True)
        if any(row["state"] not in TERMINAL for row in rows):
            raise APIError("conversation_busy", 409)
        service.conversation_repository.archive(cid)
    files = [dict(f) for f in service.message_repository.files_only_in([r["id"] for r in rows])]
    return rows, files


def purge_paths(service, cid, rows, files):
    """(path, base) pairs: what the conversation left on disk and the root it must stay in."""
    sessions = service.sessions_root()
    paths = [
        (service.root / "files" / f["project"] / f["id"], service.root / "files") for f in files
    ]
    paths.append((sessions / cid, sessions))
    for row in rows:
        paths += [
            (sessions / row["id"], sessions),
            (service.root / "maestro" / row["id"], service.root / "maestro"),
        ]
        workspace = json.loads(row["payload"]).get("workspace_id")
        if workspace:
            results = service.root / "workspaces" / workspace / "work" / "_harness_results"
            paths.append((results / row["id"], service.root / "workspaces"))
    return paths


def result_of(row):
    try:
        result = json.loads(row["result"] or "{}")
    except ValueError:
        return {}
    return result if isinstance(result, dict) else {}


def remove_paths(paths):
    """Delete each path that stays inside its base; a symlink loses only the link."""
    for path, base in paths:
        if path.is_symlink():
            path.unlink()
            continue
        if not path.exists():
            continue
        resolved = path.resolve()
        if resolved == base.resolve() or not resolved.is_relative_to(base.resolve()):
            logger.warning("Purge skipped a path outside its folder")
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def finish_purge(service, identity, cid, files):
    """Drop the rows and append the content-free audit; repeats find nothing left."""
    with service.db:
        if not service.db.in_transaction:
            service.db.execute("BEGIN IMMEDIATE")
        rows = service.conversation(identity, cid, archived=True)
        if any(row["state"] not in TERMINAL for row in rows):
            raise APIError("conversation_busy", 409)
        service.message_repository.delete_files([f["id"] for f in files])
        service.conversation_repository.purge(identity[0], cid, [row["id"] for row in rows])
    now = time.time()
    with open(service.root / AUDIT_FILE, "a", encoding="utf-8", opener=private_file) as log:
        for row in rows:
            log.write(encoded(audit_line(cid, row, now)) + "\n")
    return len(rows)


def audit_line(cid, row, purged):
    payload = json.loads(row["payload"])
    metrics = result_of(row).get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    return {
        "job_id": row["id"],
        "conversation_id": cid,
        "project": row["project"],
        "owner": row["owner"],
        "created": row["created"],
        "purged": purged,
        "backend": payload.get("backend"),
        "model": payload.get("model"),
        **{key: metrics.get(key) for key in ("input_tokens", "output_tokens")},
    }


def admit_upload(service, project, used, size, digest, dest):
    """Refuse an upload past the project cap; identical content is stored and counted once.

    A kept upload with the same sha256 already counts, so ``dest`` becomes a hard link to its
    source (a failed link keeps the copy). Returns the bytes the upload adds to ``used``.
    """
    kept = service.message_repository.same_content(project, digest)
    if not kept:
        if used + size > MAX_PROJECT_UPLOAD_BYTES:
            raise APIError("upload_limit", 413)
        return size
    source = service.root / "files" / project / kept["id"] / "source"
    link = dest.with_name("source.link")
    try:
        if not source.is_symlink() and source.is_file():
            os.link(source, link)
            os.replace(link, dest)
    except OSError:
        link.unlink(missing_ok=True)
    return 0


def storage(service, identity, project):
    """A project's use of its run and upload caps, for the Settings Storage line."""
    service.project(identity, project)
    runs = service.conversation_repository.count_for_project(project)
    used = service.message_repository.project_bytes(project)
    return {
        "project_id": project,
        "runs": {"used": runs, "limit": MAX_PROJECT_RUNS},
        "bytes": {"used": used, "limit": MAX_PROJECT_UPLOAD_BYTES},
        "warning": runs >= STORAGE_WARNING_RATIO * MAX_PROJECT_RUNS
        or used >= STORAGE_WARNING_RATIO * MAX_PROJECT_UPLOAD_BYTES,
    }
