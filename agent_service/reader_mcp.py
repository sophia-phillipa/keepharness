"""Read-only file tools over the authorized roots, for native runs without a shell (decision D12).

The provider CLI starts this module as a stdio MCP server (``server_spec``). It never writes,
runs commands, opens the network or uploads. Every path must stay inside one of the roots
given on the command line and pass the project browser's name policy plus the broader credential
list (``workspaces.credential_path``), so credential folders, ``.env`` files, key files and
symbolic links are refused. Files are opened one folder at a time without following links.
"""

import os
import sys
from collections.abc import Iterator, Sequence
from itertools import islice
from pathlib import Path
from typing import Any

from agent_service.tools import ToolError, read_contained
from agent_service.workspaces import (
    MAX_FILES,
    browse_project,
    credential_path,
    project_allowed,
    project_path,
)

SERVER_NAME = "harness_reader"
TOOLS = ("read_file", "list_directory", "search_files")
MAX_LINES = 500
MAX_MATCHES = 100
MAX_QUERY = 200
MAX_MATCH_TEXT = 1000


class Reader:
    """The three read tools, bound to a fixed list of resolved roots."""

    def __init__(self, roots: Sequence[str]) -> None:
        self.roots = [Path(root).resolve() for root in roots]

    def locate(self, path: str) -> tuple[Path, str]:
        """The root that holds ``path`` and the allowed path relative to it ("" is the root).

        A relative ``path`` is relative to the first root. Containment is checked here on the
        names and again when the file is opened (``read_contained`` / ``project_path``).
        """
        if not isinstance(path, str) or "\x00" in path or not self.roots:
            raise ToolError("path_not_authorized")
        candidate = Path(path) if Path(path).is_absolute() else self.roots[0] / path
        for root in self.roots:
            if candidate == root:
                return root, ""
            if candidate.is_relative_to(root):
                relative = candidate.relative_to(root).as_posix()
                if readable(relative):
                    return root, relative
        raise ToolError("path_not_authorized")

    def read_file(self, path: str, start: int = 1, end: int = 200) -> dict[str, Any]:
        """Read lines ``start``..``end`` (at most 500) of a text file inside the authorized folders."""
        if start < 1 or end < start or end - start >= MAX_LINES:
            raise ToolError("invalid_range")
        root, relative = self.locate(path)
        if not relative:
            raise ToolError("file_not_found")
        lines = read_contained(root, relative, errors="replace").splitlines()
        return {
            "path": str(root / relative),
            "total_lines": len(lines),
            "lines": [
                {"line": number, "text": lines[number - 1]}
                for number in range(start, min(end, len(lines)) + 1)
            ],
        }

    def list_directory(self, path: str = "", start: int = 1, limit: int = 100) -> dict[str, Any]:
        """List a folder inside the authorized folders; an empty path lists the first one."""
        root, relative = self.locate(path)
        listing = browse_project(root, relative, start, limit)
        listing["entries"] = [e for e in listing["entries"] if not credential_path(e["path"])]
        for entry in listing["entries"]:
            entry["path"] = str(root / entry["path"])
        return {**listing, "path": str(root / relative), "roots": [str(r) for r in self.roots]}

    def search_files(self, query: str, path: str = "") -> list[dict[str, Any]]:
        """Find up to 100 lines containing ``query`` (case-insensitive) in the authorized folders."""
        if not isinstance(query, str) or not 1 <= len(query) <= MAX_QUERY:
            raise ToolError("invalid_query")
        scopes = [self.locate(path)] if path else [(root, "") for root in self.roots]
        files = (
            (root, name) for root, relative in scopes for name in allowed_files(root, relative)
        )
        matches = []
        for root, name in islice(files, MAX_FILES):
            matches += matching_lines(root, name, query.casefold())
            if len(matches) >= MAX_MATCHES:
                break
        return matches[:MAX_MATCHES]


def matching_lines(root: Path, name: str, needle: str) -> list[dict[str, Any]]:
    """The lines of one allowed text file that contain ``needle``; unreadable files have none."""
    try:
        text = read_contained(root, name)
    except (OSError, UnicodeError, ToolError):
        return []
    return [
        {"path": str(root / name), "line": number, "text": line[:MAX_MATCH_TEXT]}
        for number, line in enumerate(text.splitlines(), 1)
        if needle in line.casefold()
    ]


def readable(relative: str) -> bool:
    """The project browser's name policy plus the broader credential list."""
    return project_allowed(relative) and not credential_path(relative)


def allowed_files(root: Path, relative: str) -> Iterator[str]:
    """Allowed regular files under ``root/relative``, relative to ``root``; links are never followed."""
    base = project_path(root, relative) if relative else root
    for current, folders, names in os.walk(base):
        here = Path(current).relative_to(root)
        folders[:] = sorted(
            folder
            for folder in folders
            if not Path(current, folder).is_symlink() and readable((here / folder).as_posix())
        )
        for name in sorted(names):
            candidate = (here / name).as_posix()
            if readable(candidate) and not Path(current, name).is_symlink():
                yield candidate


def server_spec(roots: Sequence[str]) -> dict[str, Any]:
    """The provider's MCP entry for this reader: only the read tools, approved without a card."""
    return {
        "command": sys.executable,
        "args": ["-m", "agent_service.reader_mcp", *roots],
        # Lets ``-m`` find the package from a source checkout as well as from an install.
        "cwd": str(Path(__file__).resolve().parents[1]),
        "enabled": True,
        "enabled_tools": list(TOOLS),
        "default_tools_approval_mode": "approve",
    }


def main() -> None:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    reader = Reader(sys.argv[1:])
    server = FastMCP(
        SERVER_NAME,
        instructions="Read-only access to these folders: "
        + ", ".join(str(root) for root in reader.roots),
    )
    for name in TOOLS:
        server.add_tool(
            getattr(reader, name),
            name=name,
            annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
        )
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
