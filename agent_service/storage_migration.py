"""Store identical existing uploads once and report storage use (decision D33).

Usage:
    python -m agent_service.storage_migration --state-dir <state folder>          # dry run
    python -m agent_service.storage_migration --state-dir <state folder> --apply

The dry run (the default) writes nothing: per project it prints the kept runs, the uploaded
bytes counted against the cap (each sha256 once), the archived conversations and how many
bytes linking identical uploads would free. ``--apply`` replaces each duplicate source with a
hard link to the first upload of the same content, after checking both hash to the recorded
sha256, and appends one line per file to ``storage-migration.jsonl`` in the state folder.
The database is only read.

Rerunning is safe: a source already sharing the first upload's inode is skipped, so a run cut
short resumes where it stopped. Rollback: linking never changes content; to give a file its
own copy again, ``cp -p <source> <source>.copy && mv <source>.copy <source>`` for each
``linked`` line of the log.
"""

import argparse
import hashlib
import os
import sqlite3
import sys
import time
from pathlib import Path

from .config import MAX_PROJECT_RUNS, MAX_PROJECT_UPLOAD_BYTES
from .persistence.db import encoded, private_file
from .persistence.repositories import ConversationRepository, MessageRepository

LOG_FILE = "storage-migration.jsonl"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def regular(path):
    return not path.is_symlink() and path.is_file()


def duplicates(db, root):
    """(project, first source, duplicate row) for each upload repeating earlier content."""
    first = {}
    for row in db.execute(
        "SELECT id,project,hash,size FROM files WHERE hash IS NOT NULL ORDER BY project,hash,id"
    ):
        path = root / "files" / row["project"] / row["id"] / "source"
        if not regular(path):
            continue
        key = (row["project"], row["hash"])
        if key not in first:
            first[key] = path
            continue
        if path.stat().st_ino != first[key].stat().st_ino:
            yield row, first[key], path


def link(original, path, expected):
    """Swap ``path`` for a hard link to ``original`` once both hold the expected bytes."""
    if sha256(original) != expected or sha256(path) != expected:
        return "hash_mismatch"
    temporary = path.with_name("source.link")
    try:
        os.link(original, temporary)
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    folder = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)
    return "linked"


def report(db):
    conversations, messages = ConversationRepository(db), MessageRepository(db)
    archived = conversations.archived()
    for (project,) in db.execute(
        "SELECT DISTINCT project FROM jobs UNION SELECT project FROM files"
    ):
        roots = {
            row[0]
            for row in db.execute(
                "SELECT id FROM jobs WHERE project=? "
                "AND json_extract(payload,'$.parent_job_id') IS NULL",
                (project,),
            )
        }
        print(
            f"{project}: runs {conversations.count_for_project(project)}/{MAX_PROJECT_RUNS}, "
            f"uploads {messages.project_bytes(project)}/{MAX_PROJECT_UPLOAD_BYTES} bytes, "
            f"archived conversations {len(roots & archived)}"
        )


def run(root, apply):
    db = sqlite3.connect(f"file:{root / 'jobs.sqlite3'}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        report(db)
        found = list(duplicates(db, root))
    finally:
        db.close()
    reclaimable = sum(row["size"] for row, _, _ in found)
    print(f"duplicate uploads: {len(found)}, bytes to free: {reclaimable}")
    if not apply:
        print("dry run: nothing changed; run again with --apply")
        return 0
    failed = 0
    with open(root / LOG_FILE, "a", encoding="utf-8", opener=private_file) as log:
        for row, original, path in found:
            try:
                status = link(original, path, row["hash"])
            except OSError as exc:
                status = "error: " + exc.__class__.__name__
            failed += status != "linked"
            line = {"time": time.time(), "file_id": row["id"], "project": row["project"]}
            log.write(encoded({**line, "linked_to": original.parent.name, "status": status}) + "\n")
            log.flush()
    print(f"linked: {len(found) - failed}, failed: {failed}")
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="link duplicates (default: dry run)")
    args = parser.parse_args(argv)
    return run(args.state_dir, args.apply)


if __name__ == "__main__":
    sys.exit(main())
