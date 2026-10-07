"""Heal a Tail Harness state that an earlier pre-release split between two folders.

``./install.sh --merge-legacy`` prints the plan and changes nothing; ``--apply`` runs it
(OPS round 5, runbook Stage A): a tar backup of both folders (environments excluded), an
online SQLite copy of the conversation databases from keepharness/ into tail-harness/, then a
copy of everything else tail-harness/ lacks, refusing when one conversation's sessions or
uploads exist in both. tail-harness/ keeps its own (newer) settings, keys and runtime.json, and
keepharness/ is retired as a dated folder, never deleted. The merged folder opens in Tail
Harness 0.14, and ``./install.sh`` then moves it to KeepHarness.

To undo an applied merge: move ~/.local/share/tail-harness aside, rename the retired
keepharness.split-<stamp> folder back to keepharness, or extract the backup tar it printed.
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from adapters.shared.provider_state import CLAUDE_JSON_BACKUP_PARTS

from .product import (
    LEGACY_FOLDERS,
    LEGACY_MARKER,
    PRODUCT,
    legacy_in_use,
    migration_lock,
    read_marker,
    state_in_use,
    write_private,
)

DATABASES = ("jobs.sqlite3", "approval_sessions.sqlite3")
# One conversation's provider sessions and one upload: copied whole, never combined.
UNITS = {("runs", "sessions"): 3, ("runs", "files"): 4}
SECRET_WORDS = ("key", "token", "secret", "password")
SHOWN = 20


class MergeRefused(Exception):
    """Why the split cannot be merged now; nothing was changed."""


@dataclass
class Plan:
    old: Path
    new: Path
    copies: list = field(default_factory=list)
    kept: list = field(default_factory=list)
    collisions: list = field(default_factory=list)


def skipped(relative):
    """What the plan handles by itself, or never copies (the environment, a client bridge)."""
    parts, name = relative.parts, relative.name
    if len(parts) == 1:
        return name in ("venv", "harness.identity.json", "audit.jsonl") or name.startswith("mcp_bridge.")
    return parts[0] == "runs" and len(parts) == 2 and (
        name == "harness.identity.json" or name.split("-")[0] in DATABASES
    )


def walk(plan, relative=Path()):
    for entry in sorted((plan.new / relative).iterdir()):
        path = relative / entry.name
        target = plan.old / path
        if skipped(path):
            continue
        if path == Path("runs"):
            walk(plan, path)  # never copied whole: its databases go through the backup API
        elif not os.path.lexists(target):
            plan.copies.append(path)
        elif UNITS.get(path.parts[:2]) == len(path.parts):
            plan.collisions.append(path)
        elif is_folder(entry) and is_folder(target):
            walk(plan, path)
        else:
            plan.kept.append(path)


def make_plan(home):
    old, new = home / LEGACY_FOLDERS[0], PRODUCT.state_path(home)
    check_split(home, old, new)
    plan = Plan(old, new)
    walk(plan)
    if plan.collisions:
        raise MergeRefused(
            "These conversations have files in both folders: "
            + ", ".join(map(str, plan.collisions)) + ". Nothing was merged."
        )
    if not shutil.disk_usage(old.parent).free > 2 * (size(old) + size(new)):
        raise MergeRefused(f"Not enough free space in {old.parent} for the backup and the copies.")
    return plan


def check_split(home, old, new):
    """Refuse anything but the split this merges: one database, our lineage, nothing running."""
    if not (is_folder(old) and is_folder(new)):
        raise MergeRefused(f"{old} and {new} do not both exist: nothing to merge.")
    if not (new / "runs/jobs.sqlite3").is_file():
        raise MergeRefused(f"{new} has no conversation database: it is not the split this merges.")
    if os.path.lexists(old / "runs") and not is_folder(old / "runs"):
        raise MergeRefused(f"{old / 'runs'} is not a folder.")
    if any((old / "runs").glob("*.sqlite3*")):
        raise MergeRefused(f"Both folders hold a conversation database ({old / 'runs'}): merge by hand.")
    for folder in (old, old / "runs", new, new / "runs"):
        check_marker(folder)
    if busy := legacy_in_use(home) or state_in_use(new):
        raise MergeRefused(f"The state is still in use: {busy}. Stop Tail Harness and KeepHarness first.")


def is_folder(path):
    return path.is_dir() and not path.is_symlink()


def check_marker(folder):
    try:
        marker = read_marker(folder) if folder.is_dir() else None
    except (OSError, ValueError) as exc:
        raise MergeRefused(f"{folder} has an unreadable identity marker.") from exc
    if marker not in (None, LEGACY_MARKER, {"slug": PRODUCT.slug, "lineage": PRODUCT.lineage}):
        raise MergeRefused(f"{folder} belongs to another product identity.")


def size(folder):
    return sum(p.lstat().st_size for p in folder.rglob("*") if "venv" not in p.relative_to(folder).parts[:1])


def settings_changes(plan):
    """Settings that differ between the two folders; tail-harness/'s values are kept."""
    try:
        older, newer = (json.loads((folder / "settings.json").read_text()) for folder in (plan.new, plan.old))
    except (OSError, ValueError):
        return []
    lines = []
    for key in sorted(set(older) | set(newer)):
        if older.get(key) != newer.get(key):
            secret = any(word in key.lower() for word in SECRET_WORDS)
            show = (lambda value: "***") if secret else (lambda value: str(value)[:60])
            lines.append(f"    {key}: keepharness {show(older.get(key))} | tail-harness {show(newer.get(key))}")
    return lines


def listed(paths):
    names = [str(path) for path in paths[:SHOWN]]
    return ", ".join(names) + (f" and {len(paths) - SHOWN} more" if len(paths) > SHOWN else "")


def describe_plan(plan, stamp):
    share = plan.old.parent
    lines = [
        f"Merge {plan.new} into {plan.old}:",
        f"  back up both folders (environments excluded) to {share}/keepharness-merge-{stamp}.tar",
        "  copy the databases with SQLite's online backup: "
        + ", ".join(name for name in DATABASES if (plan.new / "runs" / name).is_file()),
    ]
    if plan.copies:
        lines.append(f"  copy what tail-harness lacks ({len(plan.copies)}): {listed(plan.copies)}")
    if plan.kept:
        lines.append(f"  keep the tail-harness copy of: {listed(plan.kept)}")
    if changes := settings_changes(plan):
        lines += ["  settings that differ (tail-harness's are kept; change them in Settings later):", *changes]
    lines += [
        "  put keepharness/audit.jsonl before tail-harness/audit.jsonl",
        "  mark tail-harness/runs as Tail Harness state",
        f"  retire {plan.new} as {share}/keepharness.split-{stamp}",
    ]
    return "\n".join(lines)


def backup(plan, archive):
    def without_environment(member):
        parts = Path(member.name).parts
        if len(parts) > 1 and parts[1] == "venv":
            return None
        # copies of ~/.claude.json hold the sign-in session: never archived
        return None if parts[1:1 + len(CLAUDE_JSON_BACKUP_PARTS)] == CLAUDE_JSON_BACKUP_PARTS else member

    with tarfile.open(archive, "x") as tar:
        for folder in (plan.old, plan.new):
            tar.add(folder, arcname=folder.name, filter=without_environment)


def copy_database(source, target):
    """Online-backup ``source`` into a new ``target``; checked, never overwriting."""
    temporary = target.with_name(target.name + ".merging")
    temporary.unlink(missing_ok=True)
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(temporary)) as dst:
        src.backup(dst)
        status = dst.execute("PRAGMA integrity_check").fetchone()[0]
        counts = []
        if source.name == "jobs.sqlite3":
            counts = [db.execute("SELECT count(*) FROM jobs").fetchone()[0] for db in (src, dst)]
    if status != "ok" or len(set(counts)) > 1:
        temporary.unlink()
        raise MergeRefused(f"The copy of {source} failed its check ({status}, counts {counts}).")
    os.link(temporary, target)  # fails if target appeared meanwhile: nothing is overwritten
    temporary.unlink()
    return f"{source.name}: integrity ok" + (f", {counts[0]} jobs" if counts else "")


def apply(plan, stamp):
    share = plan.old.parent
    archive = share / f"keepharness-merge-{stamp}.tar"
    backup(plan, archive)
    print(f"Backed up both folders to {archive}")
    try:
        merge(plan)
    except BaseException:
        print(
            f"The merge stopped part-way; {plan.new} is unchanged. To undo it, move {plan.old} "
            f"aside and extract {archive} into {share}.",
            file=sys.stderr,
        )
        raise
    retired = share / f"keepharness.split-{stamp}"
    plan.new.rename(retired)
    print(f"Merged into {plan.old}; {plan.new} is retired as {retired}.")


def merge(plan):
    (plan.old / "runs").mkdir(mode=0o700, exist_ok=True)
    for name in DATABASES:
        if (plan.new / "runs" / name).is_file():
            print(copy_database(plan.new / "runs" / name, plan.old / "runs" / name))
    for relative in plan.copies:
        source, target = plan.new / relative, plan.old / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if is_folder(source):
            shutil.copytree(source, target, symlinks=True)
        else:
            shutil.copy2(source, target, follow_symlinks=False)
    if read_marker(plan.old / "runs") is None:
        write_private(plan.old / "runs/harness.identity.json", json.dumps(LEGACY_MARKER))
    # A log cut off mid-write may lack its final newline; without one the next record would glue on.
    audit = "".join(
        text if text.endswith("\n") else text + "\n"
        for folder in (plan.new, plan.old)
        if (folder / "audit.jsonl").is_file()
        if (text := (folder / "audit.jsonl").read_text("utf-8"))
    )
    write_private(plan.old / "audit.jsonl", audit)


def main(argv=None, home=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="Merge (default: print the plan only)")
    args = parser.parse_args(argv)
    home = Path(home if home is not None else Path.home())
    os.umask(0o077)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    try:
        plan = make_plan(home)  # a refusal comes before anything is created, the lock included
        print(describe_plan(plan, stamp))
        if not args.apply:
            print("Dry run: nothing changed. Run ./install.sh --merge-legacy --apply to merge.")
            return
        with migration_lock(home) as lock:
            plan = make_plan(home)  # again, now that no install.sh can move a folder meanwhile
            apply(plan, stamp)
            lock.unlink(missing_ok=True)
    except MergeRefused as exc:
        raise SystemExit(f"Merge refused: {exc}") from None
    print("Next: run ./install.sh to move it to KeepHarness, or start Tail Harness 0.14 on it.")


if __name__ == "__main__":
    main()
