"""Bounded, read-only hook and instruction metadata. Never evaluates commands or rules."""

import hashlib
import json
import os
import re
from pathlib import Path

from adapters.shared.private_files import scoped_home_open_read
from adapters.shared.provider_state import StateItem
from agent_service.log_config import redact
from agent_service.tools import ToolError

MAX_BYTES = 256 * 1024
PREVIEW_CHARS = 8000
_SECRET = re.compile(r"token|secret|password|passwd|cookie|api.?key|authorization|credential", re.I)
_FIELD = re.compile(r"""(?<![\w-])(?:--)?([\w-]+)(["']?\s*[:=]\s*|["']?[ \t]+)""")
_BEARER = re.compile(r'(?i)\b(Bearer|Basic)\s+[^\s"\'<>]+')
_URL_AUTH = re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@", re.I)


def _redact_fields(text):
    pieces, cursor = [], 0
    while match := _FIELD.search(text, cursor):
        if not _SECRET.search(match.group(1)):
            pieces.append(text[cursor : match.end()])
            cursor = match.end()
            continue
        start = end = match.end()
        if ":" in match.group(2):
            # A credential header can contain spaces and arbitrary authentication schemes.
            while end < len(text) and text[end] not in "\r\n":
                end += 1
        else:
            quote = None
            while end < len(text):
                character = text[end]
                if character == "\\":
                    end += 2
                    continue
                if quote:
                    if character == quote:
                        quote = None
                elif character in ('"', "'"):
                    quote = character
                elif character.isspace() or character in ",;|&()":
                    break
                end += 1
        pieces.extend((text[cursor:start], "[REDACTED]"))
        cursor = max(end, start)
    pieces.append(text[cursor:])
    return "".join(pieces)


def safe_text(value):
    text = str(value)[: PREVIEW_CHARS * 2]
    text = _URL_AUTH.sub(r"\1[REDACTED]@", text)
    text = _redact_fields(text)
    return redact(_BEARER.sub(r"\1 [REDACTED]", text))[:PREVIEW_CHARS]


def safe_details(value, key="", depth=0):
    if depth > 6:
        return "[truncated]"
    if _SECRET.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            safe_text(k): safe_details(v, str(k), depth + 1) for k, v in list(value.items())[:80]
        }
    if isinstance(value, list):
        result, hide = [], False
        for part in value[:100]:
            result.append("[REDACTED]" if hide else safe_details(part, depth=depth + 1))
            hide = (
                isinstance(part, str)
                and part.startswith("-")
                and bool(_SECRET.search(part))
                and "=" not in part
            )
        return result
    if isinstance(value, str):
        return safe_text(value)
    return value if value is None or isinstance(value, (bool, int, float)) else None


def hook_items(document, source, scope, *, enabled=True, status="configured", extra=None):
    hooks = document.get("hooks", {})
    if not isinstance(hooks, dict):
        return []
    result = []
    for event, groups in hooks.items():
        for group_index, group in enumerate(groups if isinstance(groups, list) else []):
            if not isinstance(group, dict):
                continue
            for index, hook in enumerate(
                group.get("hooks", []) if isinstance(group.get("hooks"), list) else []
            ):
                if not isinstance(hook, dict):
                    continue
                details = safe_details(
                    {
                        **hook,
                        "event": event,
                        "matcher": group.get("matcher", ""),
                        "description": document.get("description", ""),
                        **(extra or {}),
                    }
                )
                details["status"] = status
                details["content_sha256"] = hashlib.sha256(
                    json.dumps(hook, sort_keys=True).encode()
                ).hexdigest()
                ident = hashlib.sha256(
                    f"{source}:{event}:{group_index}:{index}".encode()
                ).hexdigest()[:20]
                result.append(
                    StateItem(
                        f"hook:{ident}",
                        "hook",
                        safe_text(event),
                        scope,
                        enabled,
                        source,
                        False,
                        "Read-only; manage hooks in the CLI.",
                        details=details,
                    )
                )
    return result


class InstructionReader:
    """Tracks bytes and paths for snapshot fingerprints and outside-change invalidation."""

    def __init__(self, home, project=None, *, read_content=True):
        self.read_content = read_content
        self.home = Path(home)
        self.project = Path(project) if project else None
        self.parts = []
        self.paths = []
        self.warnings = []
        self.items = []
        self.visited = set()
        self.sizes = {}
        self.versions = {}

    def shown(self, path):
        path = Path(path)
        for root, prefix in ((self.project, ""), (self.home, "~/")):
            if root:
                try:
                    return prefix + str(path.relative_to(root))
                except ValueError:
                    pass
        return str(path)

    def read(self, path):
        path = Path(path)
        self.paths.append(path)
        if not self.read_content:
            return None
        # Symlinked documents can redirect a benign-looking instruction path to credentials.
        if any(part.is_symlink() for part in (path, *path.parents)):
            self.warnings.append(f"{self.shown(path)}: linked source not previewed.")
            self.parts.append((str(path), "symlink"))
            return None
        try:
            with scoped_home_open_read(path.parent, path.name) as stream:
                if stream is None:
                    raise FileNotFoundError
                raw = stream.read(MAX_BYTES + 1)
                info = os.fstat(stream.fileno())
        except (FileNotFoundError, NotADirectoryError):
            self.parts.append((str(path), "missing"))
            return None
        except (OSError, ToolError):
            self.warnings.append(
                f"{self.shown(path)}: source unreadable or not a private regular file."
            )
            self.parts.append((str(path), "unreadable"))
            return None
        self.sizes[path] = info.st_size
        version = hashlib.sha256(raw).hexdigest()
        if len(raw) > MAX_BYTES:
            self.warnings.append(f"{self.shown(path)}: source exceeds the preview limit.")
            # For a bounded prefix, include stat metadata to detect changes beyond the preview.
            version = hashlib.sha256(
                f"{version}:{info.st_size}:{info.st_mtime_ns}".encode()
            ).hexdigest()
        self.versions[path] = version
        self.parts.append((str(path), version))
        return raw

    def document(
        self, path, scope, *, status="configured", imports=False, depth=0, imported_from=""
    ):
        path = Path(path)
        identity = str(path.absolute())
        if identity in self.visited:
            return
        self.visited.add(identity)
        raw = self.read(path)
        if raw is None:
            return
        text = raw.decode("utf-8", errors="replace")
        title = next(
            (line.lstrip("#").strip() for line in text.splitlines() if line.startswith("#")),
            path.name,
        )
        details = {
            "title": safe_text(title),
            "size_bytes": self.sizes[path],
            "preview": safe_text(text),
            "preview_truncated": len(text) > PREVIEW_CHARS,
            "status": status,
            "content_sha256": self.versions[path],
        }
        if imported_from:
            details["imported_from"] = imported_from
        self.items.append(
            StateItem(
                "instructions:" + hashlib.sha256(identity.encode()).hexdigest()[:20],
                "instructions",
                safe_text(title),
                scope,
                status == "configured",
                self.shown(path),
                False,
                "Read-only; edit this source in your editor.",
                details=details,
            )
        )
        if imports and depth < 4:
            without_code = re.sub(r"(?ms)^```.*?^```[^\n]*|`[^`]*`", "", text)
            for match in re.finditer(r'(?<!\S)@((?:\\ |[^\s"\'`])+)', without_code):
                target = match.group(1).replace("\\ ", " ")
                target_path = (
                    self.home / target[2:] if target.startswith("~/") else path.parent / target
                )
                try:
                    inside = target_path.resolve().is_relative_to(
                        (
                            self.project
                            if scope in ("project", "local") and self.project
                            else path.parent
                        ).resolve()
                    )
                except (OSError, RuntimeError):
                    inside = False
                if inside and any(
                    part.is_symlink() for part in (target_path, *target_path.parents)
                ):
                    self.warnings.append(f"{self.shown(path)}: linked import not previewed.")
                    continue
                if inside:
                    # Normalize safe parent traversal only after checking the original path:
                    # resolving first could hide a symlink that was traversed before '..'.
                    self.document(
                        Path(os.path.abspath(target_path)),
                        scope,
                        imports=True,
                        depth=depth + 1,
                        imported_from=self.shown(path),
                        status=status,
                    )
                else:
                    self.warnings.append(
                        f"{self.shown(path)}: external import pending CLI approval; not previewed."
                    )

    def rules(self, folder, scope, *, status="configured"):
        folder = Path(folder)
        self.paths.append(folder)
        for path in sorted(folder.rglob("*.md"))[:200]:
            self.document(path, scope, status=status)

    def digest(self):
        return hashlib.sha256(json.dumps(self.parts).encode()).hexdigest()


def ancestors(project):
    if project is None:
        return []
    path = Path(project)
    return list(reversed((path, *path.parents)))


def claude_instructions(
    home, config, project, managed, *, read_content=True, instruction_mode="claude-md"
):
    reader = InstructionReader(home, project, read_content=read_content)
    status = (
        "disabled by instructionFiles=managed-only"
        if instruction_mode == "managed-only"
        else "configured"
    )
    reader.document(Path(managed) / "CLAUDE.md", "managed", imports=True)
    reader.rules(Path(managed) / "rules", "managed", status=status)
    reader.document(Path(config) / "CLAUDE.md", "user", imports=True, status=status)
    reader.rules(Path(config) / "rules", "user", status=status)
    for folder in ancestors(project):
        reader.document(folder / "CLAUDE.md", "project", imports=True, status=status)
        reader.document(folder / ".claude/CLAUDE.md", "project", imports=True, status=status)
        reader.document(folder / "CLAUDE.local.md", "local", imports=True, status=status)
        reader.rules(folder / ".claude/rules", "project", status=status)
    project_memory = any(
        (folder / name).is_file()
        for folder in ancestors(project)
        for name in ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md")
    )
    if instruction_mode == "and" or (instruction_mode == "or" and not project_memory):
        for folder in ancestors(project):
            reader.document(folder / "AGENTS.md", "project", imports=True)
            reader.document(folder / ".claude/AGENTS.md", "project", imports=True)
    return reader


def codex_instructions(home, config, project, fallback_names=(), *, read_content=True):
    reader = InstructionReader(home, project, read_content=read_content)
    chain = ancestors(project)
    git_root = next(
        (folder for folder in reversed(chain) if (folder / ".git").exists()),
        Path(project) if project else None,
    )

    def has_content(path):
        try:
            return path.is_file() and path.stat().st_size > 0
        except OSError:
            return False

    for scope, folder in [("user", Path(config)), *(("project", p) for p in chain)]:
        outside = (
            scope == "project" and git_root is not None and not folder.is_relative_to(git_root)
        )
        chosen = None
        names = ["AGENTS.override.md", "AGENTS.md"]
        names.extend(
            name for name in fallback_names if isinstance(name, str) and Path(name).name == name
        )
        for name in dict.fromkeys(names):
            path = folder / name
            present = has_content(path)
            status = (
                "outside native instruction chain"
                if outside
                else "empty instruction file"
                if not present
                else "shadowed by " + chosen
                if chosen
                else "configured"
            )
            reader.document(path, scope, status=status)
            if present and chosen is None:
                chosen = name
        rules = folder / "rules" if scope == "user" else folder / ".codex/rules"
        reader.paths.append(rules)
        for path in sorted(rules.glob("*.rules"))[:200]:
            reader.document(path, scope)
    return reader
