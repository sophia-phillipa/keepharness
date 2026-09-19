"""Bounded project transfers and source retrieval; never execute archive contents."""
import hashlib
import json
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath
from .tools import ToolError, safe_file

MAX_BYTES = 200 * 1024 * 1024
MAX_FILES = 10000
EXCLUDED = {'.git', '.ssh', '.aws', '.config', '.codex', '.claude', '.venv', 'venv',
            'node_modules', '__pycache__', '.DS_Store', '__MACOSX'}


def allowed(name):
    parts = PurePosixPath(name).parts
    return (bool(parts) and not name.startswith('/') and '\\' not in name
            and not any(p in ('.', '..') or p in EXCLUDED or p.startswith('.env') for p in parts)
            and not any(ord(c) < 32 for c in name)
            and Path(name).suffix.lower() not in {'.pem', '.key', '.gguf'})


def unpack(archive, destination):
    """Validate the whole ZIP first, then extract regular files with a hard byte cap."""
    destination = Path(destination)
    manifest = []
    try:
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            if len(entries) > MAX_FILES:
                raise ToolError('workspace_file_limit')
            names = set(); total = 0
            for item in entries:
                name = item.filename.rstrip('/')
                mode = item.external_attr >> 16
                if (not allowed(name) or PurePosixPath(name).parts[0]=='_harness_sources' or name in names or ':' in name
                        or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                    raise ToolError('unsafe_archive_entry')
                names.add(name)
                total += item.file_size
                if total > MAX_BYTES or item.flag_bits & 1:
                    raise ToolError('workspace_size_or_encryption_limit')
            destination.mkdir(parents=True, mode=0o700)
            written = 0
            for item in entries:
                target = destination / item.filename
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with bundle.open(item) as source, target.open('xb') as output:
                    while chunk := source.read(65536):
                        written += len(chunk)
                        if written > MAX_BYTES:
                            raise ToolError('workspace_size_limit')
                        output.write(chunk); digest.update(chunk)
                target.chmod(0o600)
                manifest.append({'path': item.filename, 'bytes': target.stat().st_size, 'sha256': digest.hexdigest()})
        return manifest
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise ToolError('invalid_archive') from exc
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def files(root):
    root = Path(root).resolve()
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        if (path.is_file() and allowed(name) and path.resolve().is_relative_to(root)
                and not any(p.is_symlink() for p in [path, *path.parents] if p != root)):
            yield name, path


def inspect(root, query='', path='', start=1, limit=100):
    if type(limit) is not int or not 1 <= limit <= 200 or type(start) is not int or start < 1:
        raise ToolError('invalid_range')
    if not isinstance(query, str) or len(query) > 200:
        raise ToolError('invalid_query')
    if path:
        if not allowed(path):raise ToolError('path_not_authorized')
        target = safe_file(root, path)
        try:lines = target.read_text().splitlines()
        except UnicodeError:raise ToolError('binary_file_use_download')
        return {'path': path, 'lines': [{'line': i, 'text': t[:2000]} for i, t in enumerate(lines, 1) if start <= i < start + limit], 'total_lines': len(lines)}
    results = []; scanned = 0
    for name, target in files(root):
        scanned += 1
        if scanned > MAX_FILES:break
        if not query:
            results.append({'path': name, 'bytes': target.stat().st_size})
        else:
            if query.casefold() in name.casefold():results.append({'path': name, 'match': 'filename'})
            if target.stat().st_size <= 2 * 1024 * 1024:
                try:
                    for i, line in enumerate(target.read_text().splitlines(), 1):
                        if query.casefold() in line.casefold():
                            results.append({'path': name, 'line': i, 'text': line[:1000]})
                            if len(results) >= limit:break
                except (UnicodeError, OSError):pass
        if len(results) >= limit:break
    return {'matches': results[:limit], 'limited': len(results) >= limit or scanned > MAX_FILES}


def pack(root, output):
    total = 0; count = 0
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for name, path in files(root):
            total += path.stat().st_size; count += 1
            if total > MAX_BYTES or count > MAX_FILES:raise ToolError('workspace_export_limit')
            bundle.write(path, name)
    return count


async def prepare_documents(root, manifest):
    """Make PDF/DOCX text searchable while keeping original uploaded bytes intact."""
    from . import tools
    import xml.etree.ElementTree as ET
    warnings = []; generated = 0; documents = 0
    for entry in manifest:
        path = Path(root) / entry['path']
        if path.suffix.lower() not in {'.pdf', '.docx'}:continue
        documents += 1
        if documents > 200:
            warnings.append({'path':entry['path'],'warning':'automatic_extraction_document_limit'});continue
        try:
            if path.suffix.lower() == '.pdf':
                pages = await tools.extract(path, path.name)
                text = '\n'.join('[Página '+str(p['page'])+']\n'+p['text'] for p in pages)
            else:
                with zipfile.ZipFile(path) as document:
                    item = document.getinfo('word/document.xml')
                    if item.file_size > 8*1024*1024:raise ToolError('document_text_limit')
                    xml = document.read(item)
                    if b'<!DOCTYPE' in xml or b'<!ENTITY' in xml:raise ToolError('xml_entities_denied')
                    tree = ET.fromstring(xml)
                    ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
                    text = '\n'.join(''.join(t.text or '' for t in p.iter(ns+'t')) for p in tree.iter(ns+'p'))
            generated += len(text.encode())
            if len(text.encode()) > 2*1024*1024 or generated > MAX_BYTES:raise ToolError('document_text_limit')
            output = Path(root)/'_harness_sources'/(entry['path']+'.txt')
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text('Fonte: '+entry['path']+'\n'+text)
        except (ToolError, OSError, ValueError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
            warnings.append({'path':entry['path'],'warning':str(exc)[:200]})
    return warnings
