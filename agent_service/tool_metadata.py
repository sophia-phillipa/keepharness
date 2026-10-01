"""Safe, additive metadata for timeline tool events."""

import os
import re
import shlex

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
