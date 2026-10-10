"""Normalized file edits of one turn: the ``turn_edit`` records stored on the turn's job (D-053)."""

import functools
import os
from pathlib import PurePath

from agent_service.secret_vault import redact_secrets

MAX_DIFF_BYTES = 64 * 1024
MAX_JOB_DIFF_BYTES = 1024 * 1024
MAX_RECORDS = 200
CLAUDE_EDIT_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")
CODEX_OPS = {"add": "created", "update": "modified", "delete": "deleted"}


@functools.lru_cache(maxsize=32)
def resolved_root(root):
    """The realpath of the trusted project root, or None when it cannot be resolved (#80, D-053)."""
    try:
        return os.path.realpath(root)
    except OSError:
        return None


def root_forms(root):
    """The stored root, then its realpath when that differs. Only the trusted root is resolved."""
    stored = PurePath(root)
    real = resolved_root(str(root))
    if real is None or PurePath(real) == stored:
        return (stored,)
    return (stored, PurePath(real))


def relative_to_root(path, root):
    """``path`` relative to the stored root or its realpath, or None when it is under neither."""
    for form in root_forms(root):
        try:
            return path.relative_to(form)
        except ValueError:
            continue
    return None


def project_path(value, root):
    """A project-relative POSIX path, or None when it is not a string or leaves the project.

    Lexical only: reported paths are never resolved, they are compared with the stored root and
    its realpath (#80). A ``..`` segment is rejected outright.
    """
    if root is None or not isinstance(value, str) or not value or "\0" in value:
        return None
    path = PurePath(value)
    if ".." in path.parts:
        return None
    if path.is_absolute():
        path = relative_to_root(path, root)
        if path is None:
            return None
    text = path.as_posix()
    return None if text in ("", ".") else text


def stored(text, state):
    """(diff, diff_state) for supplied text: binary text is dropped, secrets are redacted, over the cap is oversized."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return None, "binary"
    if "\0" in text:
        return None, "binary"
    text = redact_secrets(text)
    if len(text.encode("utf-8")) > MAX_DIFF_BYTES:
        return None, "oversized"
    return text, state


def record(tool, path, op, diff_state, diff=None, **extra):
    if path is None:
        diff, diff_state = None, "unavailable"
    return {
        "path": path,
        "op": op,
        "source": extra.pop("source"),
        "tool": tool,
        "diff": diff,
        "diff_state": diff_state,
        **extra,
    }


def normalize_codex(change, root):
    """One entry of a completed Codex ``fileChange`` item, as a record. Never raises on bad input."""
    change = change if isinstance(change, dict) else {}
    kind = change.get("kind") if isinstance(change.get("kind"), dict) else {}
    op = CODEX_OPS.get(kind.get("type"), "unknown")
    source = change.get("path")
    moved = kind.get("move_path") if op == "modified" else None
    extra = {"moved_from": project_path(source, root)} if moved else {}
    diff = change.get("diff")
    if op == "unknown" or not isinstance(diff, str) or (op == "deleted" and not diff):
        return record(
            "fileChange",
            project_path(moved or source, root),
            op,
            "unavailable",
            source="codex",
            **extra,
        )
    body, state = stored(diff, "diff")
    return record(
        "fileChange", project_path(moved or source, root), op, state, body, source="codex", **extra
    )


def pairs_text(tool, args):
    """Removed and added lines of the Edit or MultiEdit pairs, or None when a pair is malformed."""
    pairs = [args] if tool == "Edit" else args.get("edits")
    if not isinstance(pairs, list) or not pairs:
        return None
    lines = []
    for pair in pairs:
        if not isinstance(pair, dict):
            return None
        old, new = pair.get("old_string"), pair.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            return None
        lines += ["-" + line for line in old.splitlines()]
        lines += ["+" + line for line in new.splitlines()]
    return "\n".join(lines)


def normalize_claude(tool, args, root):
    """The record of one successful Claude edit tool call, as a one-item list; [] for other tools."""
    if tool not in CLAUDE_EDIT_TOOLS:
        return []
    if not isinstance(args, dict):
        return [record(tool, None, "unknown", "unavailable", source="claude")]
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    path = project_path(args.get(key), root)
    if tool == "NotebookEdit":
        return [record(tool, path, "modified", "unavailable", source="claude")]
    if tool == "Write":
        content = args.get("content")
        lines = content.splitlines() if isinstance(content, str) else None
        text = None if lines is None else "\n".join("+" + line for line in lines)
        body, state = stored(text, "partial") if text is not None else (None, "unavailable")
        return [record(tool, path, "unknown", state, body, source="claude")]
    text = pairs_text(tool, args)
    body, state = stored(text, "partial") if text is not None else (None, "unavailable")
    return [record(tool, path, "modified", state, body, source="claude")]


class Budget:
    """The caps of one job: 200 records and 1 MiB of diff text, written in order as ``turn_edit`` events."""

    def __init__(self):
        self.count = 0
        self.size = 0
        self.closed = False

    def emit(self, event, records):
        for item in records:
            if self.closed:
                return
            if self.count == MAX_RECORDS:
                event("turn_edit", {"truncated": True})
                self.closed = True
                return
            self.count += 1
            diff = item.get("diff")
            if diff is not None:
                if self.size + len(diff.encode("utf-8")) > MAX_JOB_DIFF_BYTES:
                    item = {**item, "diff": None, "diff_state": "oversized"}
                else:
                    self.size += len(diff.encode("utf-8"))
            event("turn_edit", item)


DIFF_STATES_WITH_TEXT = frozenset({"diff", "partial"})


def changes_view(job, job_state, stored, ran_shell):
    """One job's file changes as the review endpoint shows them (D-053, section 2.5); no disk access."""
    marker = any("truncated" in item for item in stored)
    files = group_by_path([item for item in stored if "truncated" not in item])
    return {
        "job": job,
        "job_state": job_state,
        "state": "captured" if files else "none",
        "truncated": marker,
        "shell_unattributed": ran_shell,
        "files": files,
    }


def group_by_path(stored):
    """Edits grouped by path in order of first appearance; a file is deleted if its last edit deleted it."""
    grouped = {}
    for item in stored:
        grouped.setdefault(item.get("path"), []).append(shown_edit(item))
    return [{"path": path, "op": file_op(edits), "edits": edits} for path, edits in grouped.items()]


def shown_edit(item):
    diff_state = item.get("diff_state")
    return {
        "op": item.get("op"),
        "diff_state": diff_state,
        "diff": item.get("diff") if diff_state in DIFF_STATES_WITH_TEXT else None,
        "tool": item.get("tool"),
        "source": item.get("source"),
        **({"moved_from": item["moved_from"]} if item.get("moved_from") else {}),
    }


def file_op(edits):
    if edits[-1]["op"] == "deleted":
        return "deleted"
    if edits[0]["op"] == "deleted":
        return "modified"
    return edits[0]["op"]
