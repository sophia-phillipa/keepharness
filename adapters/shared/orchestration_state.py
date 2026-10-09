"""Bounded, read-only hook and instruction metadata. Never evaluates commands or rules."""

import hashlib
import json
import os
import re
import string
from dataclasses import dataclass
from pathlib import Path

from adapters.shared.command_mask import (
    PLACEHOLDER,
    mask_argv,
    mask_command,
    mask_named_values,
    mask_url,
)
from adapters.shared.private_files import scoped_home_open_read
from adapters.shared.provider_state import StateItem
from agent_service.log_config import redact
from agent_service.tools import ToolError

MAX_BYTES = 256 * 1024
PREVIEW_CHARS = 8000
_SECRET = re.compile(r"token|secret|password|passwd|cookie|api.?key|authorization|credential", re.I)
_BEARER = re.compile(r'(?i)\b(Bearer|Basic)\s+[^\s"\'<>]+')
_URL_AUTH = re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@", re.I)
_NAME_CHARS = frozenset(string.ascii_letters + string.digits + "-_.")
_REDACTED = "[REDACTED]"
_QUOTES = "\"'"
_COMMAND_KEYS = frozenset(
    {"command", "commandline", "cmd", "args", "argv", "run", "script", "exec"}
)
_NAMED_VALUE_KEYS = frozenset(
    {"env", "environment", "envvars", "env_vars", "headers", "httpheaders", "http_headers"}
)
_URL_KEYS = frozenset({"url", "uri", "endpoint"})


class _UnbalancedQuote(Exception):
    """Raised by the quote-aware scanner; the caller falls back to literal words."""


@dataclass(frozen=True, slots=True)
class _Word:
    start: int
    end: int
    value: str
    newline_before: bool


@dataclass(frozen=True, slots=True)
class _Pending:
    expect_value: bool = False
    rest_of_line: bool = False


def _read_quoted(text: str, start: int, chars: list[str]) -> int:
    """Append the decoded body of the quote at ``start``; return the index after it."""
    quote = text[start]
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == quote:
            return index + 1
        if char == "\\" and quote == '"' and index + 1 < len(text):
            index += 1
            char = text[index]
        chars.append(char)
        index += 1
    raise _UnbalancedQuote


def _read_word(text: str, start: int, *, quoted: bool) -> tuple[int, str]:
    """Return the end index and decoded value of the word starting at ``start``."""
    index, chars = start, []
    while index < len(text) and not text[index].isspace():
        char = text[index]
        if char in _QUOTES:
            index = _read_quoted(text, index, chars) if quoted else index + 1
        elif quoted and char == "\\" and index + 1 < len(text):
            chars.append(text[index + 1])
            index += 2
        else:
            chars.append(char)
            index += 1
    return index, "".join(chars)


def _scan_words(text: str, *, quoted: bool) -> list[_Word]:
    """Split on whitespace in one linear pass; quotes group, or are dropped when literal."""
    words: list[_Word] = []
    index, newline = 0, False
    while index < len(text):
        if text[index].isspace():
            newline = newline or text[index] in "\r\n"
            index += 1
            continue
        end, value = _read_word(text, index, quoted=quoted)
        words.append(_Word(index, end, value, newline))
        index, newline = end, False
    return words


def _is_secret_name(name: str) -> bool:
    return bool(name) and set(name) <= _NAME_CHARS and bool(_SECRET.search(name))


def _is_credential_flag(value: str) -> bool:
    return value.startswith("-") and "=" not in value and _is_secret_name(value.lstrip("-"))


def _header_name(value: str) -> str | None:
    name, colon, _ = value.partition(":")
    return name if colon and _is_secret_name(name) else None


def _redact_word(raw: str, value: str, pending: _Pending, fallback: bool) -> tuple[str, _Pending]:
    """Return the display form of one word and the state for the next word."""
    if pending.rest_of_line:
        return _REDACTED, pending
    if pending.expect_value and not _is_credential_flag(value):
        # A flag-looking value keeps the expectation so a secret after it is still hidden.
        return _REDACTED, _Pending(expect_value=value.startswith("-"))
    key, equals, rest = value.partition("=")
    if equals and _is_secret_name(key.lstrip("-")):
        if rest:
            return f"{key}={_REDACTED}", _Pending()
        return raw, _Pending(expect_value=True)
    header = _header_name(value)
    if header is not None:
        # A credential header can contain spaces and arbitrary authentication schemes.
        return f"{header}: {_REDACTED}", _Pending(rest_of_line=True)
    if _is_credential_flag(value):
        # Quotes do not hide an option from the program that receives the words.
        return raw, _Pending(expect_value=not fallback, rest_of_line=fallback)
    return raw, _Pending()


def _redact_tokens(text: str) -> str:
    try:
        words, fallback = _scan_words(text, quoted=True), False
    except _UnbalancedQuote:
        words, fallback = _scan_words(text, quoted=False), True
    pieces, cursor, pending = [], 0, _Pending()
    for word in words:
        if word.newline_before and pending.rest_of_line:
            pending = _Pending(expect_value=pending.expect_value)
        pieces.append(text[cursor : word.start])
        replacement, pending = _redact_word(
            text[word.start : word.end], word.value, pending, fallback
        )
        pieces.append(replacement)
        cursor = word.end
    pieces.append(text[cursor:])
    return "".join(pieces)


def safe_text(value):
    text = str(value)[: PREVIEW_CHARS * 2]
    text = _URL_AUTH.sub(r"\1[REDACTED]@", text)
    text = _redact_tokens(text)
    return redact(_BEARER.sub(r"\1 [REDACTED]", text))[:PREVIEW_CHARS]


def _mask_known_field(value, key):
    """Fail-closed masking (D-048) for hook command, env, header and URL fields."""
    if value is None:
        return None
    if key in _COMMAND_KEYS:
        if isinstance(value, str):
            return mask_command(value)
        return mask_argv(value) if isinstance(value, (list, tuple)) else PLACEHOLDER
    if key in _NAMED_VALUE_KEYS:
        return mask_named_values(value)
    if key in _URL_KEYS:
        return mask_url(value) if isinstance(value, str) else PLACEHOLDER
    return None


def safe_details(value, key="", depth=0):
    if depth > 6:
        return "[truncated]"
    if _SECRET.search(key):
        return "[REDACTED]"
    masked = _mask_known_field(value, key.lower())
    if masked is not None:
        return masked
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
