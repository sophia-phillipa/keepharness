"""Safe, additive metadata for timeline tool events."""

import os
import re
import shlex
from pathlib import PurePath

from .log_config import redact

TARGET_LIMIT = 160
SHELL_TOOLS = {"Bash", "commandExecution", "exec_command", "execute"}
FILE_TOOLS = {
    "Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Glob", "Grep",
    "read_file", "list_directory", "list_dir", "search_files", "fileChange",
    "read", "edit", "delete", "move", "search",
}  # fmt: skip
SEARCH_TOOLS = {"Glob", "Grep", "search_files", "search"}
_SHELLS = {"sh", "bash", "zsh"}
_NAME = r"(?<![\w-])(?:[A-Za-z0-9]+_)*(?:TOKEN|SECRET|KEY|PASSWORD|PASSWD)(?:_[A-Za-z0-9]+)*"
_TARGET_REDACTIONS = tuple(
    (re.compile(pattern, re.IGNORECASE), repl)
    for pattern, repl in (
        (_NAME + r"=(?:([\'\"]).*?\1|\S+)", lambda m: m.group(0).split("=", 1)[0] + "=[redacted]"),
        (r"(://[^/\s:@]+:)[^@\s]+@", r"\1[redacted]@"),
        (r"((?<![\w-])--?(?:token|password|passwd|secret|api[-_]?key)[= ])\S+", r"\1[redacted]"),
        (r"(\b(?:mysql|mariadb)\b[^|;&]*?\s-p)\S+", r"\1[redacted]"),
        (r"(Authorization:\s*(?:Bearer|token|Basic)\s+)[^\s\'\",;]+", r"\1[redacted]"),
        (r"\b(?:ghp_\w+|github_pat_\w+|xox[bp]-[\w-]+|AKIA[0-9A-Z]{16})", "[redacted]"),
        (r"(aws_secret_access_key\s+)\S+", r"\1[redacted]"),
    )
)
_ASSIGNMENT = re.compile(
    r"\b([A-Za-z0-9_]*(?:TOKEN|SECRET|KEY|PASSWORD|PASSWD)[A-Za-z0-9_]*)=\S+", re.IGNORECASE
)
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*\Z")
_SHELL_SYNTAX = re.compile(r"[\n\r;|&<>`]|\$\(")
_EXECUTABLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")


def command_name(command):
    """Return only an executable basename; never return command arguments."""
    if isinstance(command, str):
        if not command or _SHELL_SYNTAX.search(command):
            return None
        try:
            parts = shlex.split(command)
        except ValueError:
            return None
    elif isinstance(command, list) and all(isinstance(part, str) for part in command):
        parts = command
    else:
        return None
    while parts and _ASSIGNMENT.fullmatch(parts[0]):
        parts = parts[1:]
    if not parts or not parts[0] or any(char.isspace() for char in parts[0]):
        return None
    name = os.path.basename(parts[0])
    return name if _EXECUTABLE.fullmatch(name) else None


def event_metadata(item, *, command=None):
    """Keep raw event fields separate from optional display-only metadata."""
    metadata = {}
    if isinstance(item, dict):
        tool_id = item.get("id") or item.get("toolCallId") or item.get("callId")
        if isinstance(tool_id, str) and tool_id:
            metadata["tool_id"] = tool_id
            metadata["tool_call_id"] = tool_id
    name = command_name(command)
    if name:
        metadata["command_name"] = name
    return metadata


def _command_line(command):
    """The command as a terminal shows it: a ``bash -lc '<cmd>'`` wrapper is dropped."""
    if isinstance(command, list) and all(isinstance(part, str) for part in command):
        parts = command
    elif isinstance(command, str):
        try:
            parts = shlex.split(command)
        except ValueError:
            return command
    else:
        return None
    if len(parts) == 3 and os.path.basename(parts[0]) in _SHELLS and parts[1] in ("-c", "-lc"):
        return parts[2]
    return shlex.join(parts) if isinstance(command, list) else command


def _relative(path, root):
    if not (root and isinstance(path, str) and os.path.isabs(path)):
        return path
    try:
        return str(PurePath(path).relative_to(root)) or "."
    except ValueError:
        return path


def _path_value(tool, args, root):
    if isinstance(args.get("changes"), list):
        paths = [c.get("path") for c in args["changes"] if isinstance(c, dict)]
        return ", ".join(_relative(p, root) for p in paths if isinstance(p, str)) or None
    keys = ("pattern", "query") if tool in SEARCH_TOOLS else ()
    for key in (*keys, "path", "file_path", "filePath", "notebook_path", "directory", "dir"):
        if isinstance(args.get(key), str) and args[key]:
            return _relative(args[key], root)
    return None


def tool_target(tool, args, root=None):
    """A short display line for what a tool acts on: the command, or the file path or pattern.

    Shell and file tools only; everything else has no target. The line is redacted,
    single-line and at most ``TARGET_LIMIT`` characters.
    """
    if not isinstance(tool, str) or not isinstance(args, dict):
        return None
    name = tool.split("__")[-1]
    if name in SHELL_TOOLS:
        value = _command_line(args.get("command", args.get("cmd")))
    elif name in FILE_TOOLS:
        value = _path_value(name, args, root and str(root))
    else:
        return None
    if not isinstance(value, str):
        return None
    for pattern, repl in _TARGET_REDACTIONS:
        value = pattern.sub(repl, value)
    line = " ".join(redact(value).split())
    if not line:
        return None
    return line if len(line) <= TARGET_LIMIT else line[: TARGET_LIMIT - 1] + "…"


def item_target(item, root=None):
    """The target of a Codex ``item/started`` payload."""
    kind = item.get("type")
    if kind == "mcpToolCall":
        return tool_target(item.get("tool"), item.get("arguments"), root)
    if kind == "commandExecution":
        return tool_target(kind, {"command": item.get("command")}, root)
    if kind == "fileChange":
        return tool_target(kind, {"changes": item.get("changes")}, root)
    return None
