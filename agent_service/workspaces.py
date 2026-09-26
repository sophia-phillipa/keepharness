"""Bounded project transfers and source retrieval; never execute archive contents."""

import hashlib
import os
import shutil
import stat
import zipfile
from itertools import islice
from pathlib import Path, PurePosixPath

from .tools import ToolError, safe_file

MAX_BYTES = 200 * 1024 * 1024
MAX_FILES = 10000
MAX_ATTACHMENTS = 20
EXCLUDED = {
    ".git",
    ".ssh",
    ".aws",
    ".config",
    ".codex",
    ".claude",
    ".gemini",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".DS_Store",
    "__MACOSX",
}
PROJECT_EXCLUDED = EXCLUDED | {"local-ai", "local_ai"}
FOLDER_ATTACH_EXCLUDED = {
    ".ssh",
    ".aws",
    ".config",
    ".codex",
    ".claude",
    ".gemini",
    "local-ai",
    "local_ai",
}
SYSTEM_DIRECTORIES = tuple(
    Path("/" + name) for name in ("proc", "sys", "dev", "etc", "usr", "boot", "ostree", "var")
)
SYSTEM_DIRECTORY_NAMES = {directory.name for directory in SYSTEM_DIRECTORIES}
VOLUME_DIRECTORIES = {"System Volume Information", "$RECYCLE.BIN", "RECYCLE.BIN", "lost+found"}


def allowed(name):
    parts = PurePosixPath(name).parts
    return (
        bool(parts)
        and not name.startswith("/")
        and "\\" not in name
        and not any(p in (".", "..") or p in EXCLUDED or p.startswith(".env") for p in parts)
        and not any(ord(c) < 32 for c in name)
        and Path(name).suffix.lower() not in {".pem", ".key", ".gguf"}
    )


def project_roots(spec):
    extra = spec.get("additional_roots", [])
    paths = [spec.get("root"), *(extra if isinstance(extra, list) else [])]
    return [
        (("root" if index == 0 else f"additional-{index - 1}"), path)
        for index, path in enumerate(paths)
        if isinstance(path, str) and path
    ]


def project_root(spec, root_id):
    roots = dict(project_roots(spec))
    if not isinstance(root_id, str) or root_id not in roots:
        raise ToolError("project_root_denied")
    root = Path(roots[root_id])
    if any(part.is_symlink() for part in (root, *root.parents)) or not root.is_dir():
        raise ToolError("project_root_unavailable")
    return root.resolve()


def project_allowed(name):
    return allowed(name) and not any(part in PROJECT_EXCLUDED for part in PurePosixPath(name).parts)


def project_path(root, name):
    if not isinstance(name, str) or not name or not project_allowed(name):
        raise ToolError("path_not_authorized")
    root = Path(root).resolve()
    target = root / name
    if target.is_symlink() or any(part.is_symlink() for part in target.parents if part != root):
        raise ToolError("symlink_denied")
    if not target.resolve().is_relative_to(root) or not target.exists():
        raise ToolError("path_not_authorized")
    return target


def browse_project(root, path="", start=1, limit=100):
    if type(limit) is not int or not 1 <= limit <= 200 or type(start) is not int or start < 1:
        raise ToolError("invalid_range")
    root = Path(root).resolve()
    target = root if not path else project_path(root, path)
    if not target.is_dir():
        raise ToolError("directory_not_found")
    entries = []
    for child in sorted(
        target.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold())
    ):
        relative = child.relative_to(root).as_posix()
        if child.is_symlink() or not project_allowed(relative):
            continue
        if child.is_dir():
            entries.append({"path": relative, "name": child.name, "type": "directory"})
        elif child.is_file() and child.resolve().is_relative_to(root):
            entries.append(
                {
                    "path": relative,
                    "name": child.name,
                    "type": "file",
                    "bytes": child.stat().st_size,
                }
            )
    window = entries[start - 1 : start - 1 + limit]
    return {"path": path, "entries": window, "limited": start - 1 + len(window) < len(entries)}


def selected_project_files(root, names, maximum):
    if (
        not isinstance(names, list)
        or not 1 <= len(names) <= 100
        or not all(isinstance(name, str) and name not in ("", ".") for name in names)
        or type(maximum) is not int
        or not 1 <= maximum <= MAX_ATTACHMENTS
    ):
        raise ToolError("invalid_selection")
    root = Path(root).resolve()
    selected = {}
    skipped = []
    scanned = 0
    for name in dict.fromkeys(names):
        if scanned >= MAX_FILES:
            skipped.append({"path": name, "reason": "selection_scan_limit"})
            continue
        target = project_path(root, name)
        if target.is_file():
            candidates = [target]
        else:
            candidates = list(islice(target.rglob("*"), MAX_FILES - scanned + 1))
            exceeded = len(candidates) > MAX_FILES - scanned
            candidates = sorted(candidates[: MAX_FILES - scanned])
            if exceeded:
                skipped.append({"path": name, "reason": "selection_scan_limit"})
        scanned += len(candidates)
        for candidate in candidates:
            if not candidate.is_file() or candidate.is_symlink():
                continue
            relative = candidate.relative_to(root).as_posix()
            if not project_allowed(relative):
                skipped.append({"path": relative, "reason": "path_not_authorized"})
                continue
            if any(
                parent.is_symlink() for parent in candidate.parents if parent != root
            ) or not candidate.resolve().is_relative_to(root):
                raise ToolError("symlink_denied")
            if relative in selected:
                continue
            if len(selected) >= maximum:
                skipped.append({"path": relative, "reason": "file_limit"})
                continue
            selected[relative] = candidate
    return list(selected.items()), skipped


def system_roots():
    roots = [("system", Path("/")), ("home", Path.home().resolve())]
    media_user = Path("/run/media") / Path.home().name
    if media_user.is_dir():
        roots.append(("media-user", media_user.resolve()))
    return roots


def visible_system_roots():
    return [(root_id, path) for root_id, path in system_roots() if root_id != "system"]


def hidden_system_entry(path, name, root, top_level=False):
    return (
        name.startswith(".")
        or name in VOLUME_DIRECTORIES
        or (
            Path(root).resolve() == Path("/")
            and (
                (top_level and name in SYSTEM_DIRECTORY_NAMES)
                or any(
                    path == directory or directory in path.parents
                    for directory in SYSTEM_DIRECTORIES
                )
            )
        )
    )


def system_root(root_id):
    roots = dict(system_roots())
    if not isinstance(root_id, str) or root_id not in roots:
        raise ToolError("system_root_denied")
    return roots[root_id]


def open_attachment_source(path):
    """Open the selected regular file without following replaceable symlinks."""
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ToolError("path_not_authorized")
    parent = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ToolError("attachment_source_unavailable")
            return os.fdopen(fd, "rb")
        except BaseException:
            os.close(fd)
            raise
    except OSError:
        raise ToolError("attachment_source_unavailable") from None
    finally:
        os.close(parent)


def system_path(root, name=""):
    if (
        not isinstance(name, str)
        or Path(name).is_absolute()
        or ".." in Path(name).parts
        or any(ord(char) < 32 for char in name)
    ):
        raise ToolError("path_not_authorized")
    root = Path(root).resolve()
    raw = root / name
    try:
        target = raw.resolve(strict=True)
    except OSError as exc:
        raise ToolError("path_not_authorized") from exc
    if not target.is_relative_to(root):
        raise ToolError("path_not_authorized")
    return target


def browse_system(root, path="", start=1, limit=100, directories_only=False, query=""):
    if type(limit) is not int or not 1 <= limit <= 200 or type(start) is not int or start < 1:
        raise ToolError("invalid_range")
    root = Path(root).resolve()
    target = system_path(root, path)
    if not target.is_dir():
        raise ToolError("directory_not_found")
    entries = []
    for child in sorted(
        target.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold())
    ):
        try:
            canonical = child.resolve(strict=True)
        except OSError:
            continue
        if not canonical.is_relative_to(root) or hidden_system_entry(
            canonical, child.name, root, target == root
        ):
            continue
        if (
            directories_only and not canonical.is_dir()
        ) or query.casefold() not in child.name.casefold():
            continue
        relative = canonical.relative_to(root).as_posix()
        if canonical.is_dir():
            entries.append({"path": relative, "name": child.name, "type": "directory"})
        elif canonical.is_file():
            entries.append(
                {
                    "path": relative,
                    "name": child.name,
                    "type": "file",
                    "bytes": canonical.stat().st_size,
                }
            )
    window = entries[start - 1 : start - 1 + limit]
    return {
        "path": target.relative_to(root).as_posix() if target != root else "",
        "entries": window,
        "limited": start - 1 + len(window) < len(entries),
    }


def selected_system_files(root, names, maximum):
    if (
        not isinstance(names, list)
        or not 1 <= len(names) <= 100
        or not all(isinstance(name, str) and name not in ("", ".") for name in names)
        or type(maximum) is not int
        or not 1 <= maximum <= MAX_ATTACHMENTS
    ):
        raise ToolError("invalid_selection")
    root = Path(root).resolve()
    selected = {}
    skipped = []
    scanned = 0
    for name in dict.fromkeys(names):
        if scanned >= MAX_FILES:
            skipped.append({"path": name, "reason": "selection_scan_limit"})
            continue
        raw = root / name
        target = system_path(root, name)
        if raw.is_symlink() or any(parent.is_symlink() for parent in raw.parents if parent != root):
            skipped.append({"path": name, "reason": "symlink_denied"})
            continue
        if target.is_file():
            candidates = [target]
        else:
            candidates = list(islice(target.rglob("*"), MAX_FILES - scanned + 1))
            exceeded = len(candidates) > MAX_FILES - scanned
            candidates = sorted(candidates[: MAX_FILES - scanned])
            if exceeded:
                skipped.append({"path": name, "reason": "selection_scan_limit"})
        scanned += len(candidates)
        for candidate in candidates:
            if (
                candidate.is_symlink()
                or not candidate.is_file()
                or not stat.S_ISREG(candidate.stat().st_mode)
            ):
                continue
            relative = candidate.relative_to(root).as_posix()
            parts = PurePosixPath(relative).parts
            if (
                hidden_system_entry(candidate, candidate.name, root, target == root)
                or any(part in FOLDER_ATTACH_EXCLUDED or part.startswith(".env") for part in parts)
                or Path(relative).suffix.lower() in {".pem", ".key", ".gguf"}
                or (
                    not target.is_dir()
                    and any(hidden_system_entry(candidate, part, root) for part in parts[:-1])
                )
            ):
                skipped.append({"path": relative, "reason": "sensitive_file"})
                continue
            if relative in selected:
                continue
            if len(selected) >= maximum:
                skipped.append({"path": relative, "reason": "file_limit"})
                continue
            selected[relative] = candidate
    return list(selected.items()), skipped


def unpack(archive, destination):
    """Validate the whole ZIP first, then extract regular files with a hard byte cap."""
    destination = Path(destination)
    manifest = []
    try:
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            if len(entries) > MAX_FILES:
                raise ToolError("workspace_file_limit")
            names = set()
            total = 0
            for item in entries:
                name = item.filename.rstrip("/")
                mode = item.external_attr >> 16
                if (
                    not allowed(name)
                    or PurePosixPath(name).parts[0] == "_harness_sources"
                    or name in names
                    or ":" in name
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)
                ):
                    raise ToolError("unsafe_archive_entry")
                names.add(name)
                total += item.file_size
                if total > MAX_BYTES or item.flag_bits & 1:
                    raise ToolError("workspace_size_or_encryption_limit")
            destination.mkdir(parents=True, mode=0o700)
            written = 0
            for item in entries:
                target = destination / item.filename
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with bundle.open(item) as source, target.open("xb") as output:
                    while chunk := source.read(65536):
                        written += len(chunk)
                        if written > MAX_BYTES:
                            raise ToolError("workspace_size_limit")
                        output.write(chunk)
                        digest.update(chunk)
                target.chmod(0o600)
                manifest.append(
                    {
                        "path": item.filename,
                        "bytes": target.stat().st_size,
                        "sha256": digest.hexdigest(),
                    }
                )
        return manifest
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise ToolError("invalid_archive") from exc
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def files(root):
    root = Path(root).resolve()
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if (
            path.is_file()
            and allowed(name)
            and path.resolve().is_relative_to(root)
            and not any(p.is_symlink() for p in [path, *path.parents] if p != root)
        ):
            yield name, path


def inspect(root, query="", path="", start=1, limit=100):
    if type(limit) is not int or not 1 <= limit <= 200 or type(start) is not int or start < 1:
        raise ToolError("invalid_range")
    if not isinstance(query, str) or len(query) > 200:
        raise ToolError("invalid_query")
    if path:
        if not allowed(path):
            raise ToolError("path_not_authorized")
        target = safe_file(root, path)
        try:
            lines = target.read_text().splitlines()
        except UnicodeError:
            raise ToolError("binary_file_use_download")
        return {
            "path": path,
            "lines": [
                {"line": i, "text": t[:2000]}
                for i, t in enumerate(lines, 1)
                if start <= i < start + limit
            ],
            "total_lines": len(lines),
        }
    results = []
    scanned = 0
    for name, target in files(root):
        scanned += 1
        if scanned > MAX_FILES:
            break
        if not query:
            results.append({"path": name, "bytes": target.stat().st_size})
        else:
            if query.casefold() in name.casefold():
                results.append({"path": name, "match": "filename"})
            if target.stat().st_size <= 2 * 1024 * 1024:
                try:
                    for i, line in enumerate(target.read_text().splitlines(), 1):
                        if query.casefold() in line.casefold():
                            results.append({"path": name, "line": i, "text": line[:1000]})
                            if len(results) >= limit:
                                break
                except (UnicodeError, OSError):
                    pass
        if len(results) >= limit:
            break
    return {"matches": results[:limit], "limited": len(results) >= limit or scanned > MAX_FILES}


def pack(root, output):
    total = 0
    count = 0
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, path in files(root):
            total += path.stat().st_size
            count += 1
            if total > MAX_BYTES or count > MAX_FILES:
                raise ToolError("workspace_export_limit")
            bundle.write(path, name)
    return count


async def prepare_documents(root, manifest):
    """Make PDF/DOCX text searchable while keeping original uploaded bytes intact."""
    import xml.etree.ElementTree as ET

    from . import tools

    warnings = []
    generated = 0
    documents = 0
    for entry in manifest:
        path = Path(root) / entry["path"]
        if path.suffix.lower() not in {".pdf", ".docx"}:
            continue
        documents += 1
        if documents > 200:
            warnings.append(
                {"path": entry["path"], "warning": "automatic_extraction_document_limit"}
            )
            continue
        try:
            if path.suffix.lower() == ".pdf":
                pages = await tools.extract(path, path.name)
                text = "\n".join("[Page " + str(p["page"]) + "]\n" + p["text"] for p in pages)
            else:
                with zipfile.ZipFile(path) as document:
                    item = document.getinfo("word/document.xml")
                    if item.file_size > 8 * 1024 * 1024:
                        raise ToolError("document_text_limit")
                    xml = document.read(item)
                    if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
                        raise ToolError("xml_entities_denied")
                    tree = ET.fromstring(xml)
                    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
                    text = "\n".join(
                        "".join(t.text or "" for t in p.iter(ns + "t")) for p in tree.iter(ns + "p")
                    )
            generated += len(text.encode())
            if len(text.encode()) > 2 * 1024 * 1024 or generated > MAX_BYTES:
                raise ToolError("document_text_limit")
            output = Path(root) / "_harness_sources" / (entry["path"] + ".txt")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("Source: " + entry["path"] + "\n" + text)
        except (ToolError, OSError, ValueError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
            warnings.append({"path": entry["path"], "warning": str(exc)[:200]})
    return warnings
