"""Bounded, read-only hook and instruction metadata. Never evaluates commands or rules."""

import hashlib
import json
import re
from pathlib import Path

from adapters.shared.provider_state import StateItem
from agent_service.log_config import redact

MAX_BYTES = 256 * 1024
PREVIEW_CHARS = 8000
_SECRET = re.compile(r"token|secret|password|passwd|cookie|api.?key|authorization|credential", re.I)
_ASSIGNMENT = re.compile(
    r"""(?i)((?:[\w-]*(?:token|secret|password|passwd|api[_-]?key|credential)[\w-]*)["']?\s*(?:[:=]|\s)\s*)("[^"\n]*"|'[^'\n]*'|[^\s,;]+)"""
)
_BEARER = re.compile(r'(?i)\b(Bearer|Basic)\s+[^\s"\'<>]+')
_URL_AUTH = re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@", re.I)


def safe_text(value):
    text = _URL_AUTH.sub(r"\1[REDACTED]@", str(value))
    text = _ASSIGNMENT.sub(r"\1[REDACTED]", text)
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
            with path.open("rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
        except (FileNotFoundError, NotADirectoryError):
            self.parts.append((str(path), "missing"))
            return None
        except OSError:
            self.warnings.append(f"{self.shown(path)}: source unreadable.")
            self.parts.append((str(path), "unreadable"))
            return None
        self.parts.append((str(path), hashlib.sha256(raw).hexdigest()))
        if len(raw) > MAX_BYTES:
            self.warnings.append(f"{self.shown(path)}: source exceeds the preview limit.")
            return None
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
            "size_bytes": len(raw),
            "preview": safe_text(text),
            "preview_truncated": len(text) > PREVIEW_CHARS,
            "status": status,
            "content_sha256": hashlib.sha256(raw).hexdigest(),
        }
        if imported_from:
            details["imported_from"] = imported_from
        self.items.append(
            StateItem(
                "instructions:" + hashlib.sha256(identity.encode()).hexdigest()[:20],
                "instructions",
                safe_text(title),
                scope,
                not status.startswith("shadowed"),
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
                if inside:
                    self.document(
                        target_path,
                        scope,
                        imports=True,
                        depth=depth + 1,
                        imported_from=self.shown(path),
                    )
                else:
                    self.warnings.append(
                        f"{self.shown(path)}: external import pending CLI approval; not previewed."
                    )

    def rules(self, folder, scope):
        folder = Path(folder)
        self.paths.append(folder)
        for path in sorted(folder.rglob("*.md"))[:200]:
            self.document(path, scope)

    def digest(self):
        return hashlib.sha256(json.dumps(self.parts).encode()).hexdigest()


def ancestors(project):
    if project is None:
        return []
    path = Path(project)
    return list(reversed((path, *path.parents)))


def claude_instructions(home, config, project, managed, *, read_content=True):
    reader = InstructionReader(home, project, read_content=read_content)
    reader.document(Path(managed) / "CLAUDE.md", "managed", imports=True)
    reader.rules(Path(managed) / "rules", "managed")
    reader.document(Path(config) / "CLAUDE.md", "user", imports=True)
    reader.rules(Path(config) / "rules", "user")
    for folder in ancestors(project):
        reader.document(folder / "CLAUDE.md", "project", imports=True)
        reader.document(folder / ".claude/CLAUDE.md", "project", imports=True)
        reader.document(folder / "CLAUDE.local.md", "local", imports=True)
        reader.rules(folder / ".claude/rules", "project")
    return reader


def codex_instructions(home, config, project, fallback_names=(), *, read_content=True):
    reader = InstructionReader(home, project, read_content=read_content)
    for scope, folder in [("user", Path(config)), *(("project", p) for p in ancestors(project))]:
        override = folder / "AGENTS.override.md"
        standard = folder / "AGENTS.md"
        reader.document(override, scope)
        reader.document(
            standard,
            scope,
            status="shadowed by AGENTS.override.md" if override.is_file() else "configured",
        )
        chosen = override.is_file() or standard.is_file()
        for name in fallback_names:
            if isinstance(name, str) and Path(name).name == name:
                reader.document(
                    folder / name,
                    scope,
                    status="shadowed by higher priority instructions" if chosen else "configured",
                )
                chosen = chosen or (folder / name).is_file()
        rules = folder / "rules" if scope == "user" else folder / ".codex/rules"
        reader.paths.append(rules)
        for path in sorted(rules.glob("*.rules"))[:200]:
            reader.document(path, scope)
    return reader
