#!/usr/bin/env python3
"""Verify the pinned release archive and every supplied runtime member before copying."""
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile

root, dist = map(Path, sys.argv[1:])
version = json.loads((root / 'desktop/package.json').read_text())['devDependencies']['electron']
name = f'electron-v{version}-linux-x64.zip'
checksums = (root / f'desktop/linux/electron-v{version}-SHASUMS256.txt').read_text().splitlines()
expected = next(line.split()[0] for line in checksums if line.split()[-1].lstrip('*') == name)
archive = os.environ.get('KEEPHARNESS_ELECTRON_ZIP')
if archive:
    archive = Path(archive)
else:
    candidates = list((Path.home() / '.cache/electron').glob('*/' + name))
    if not candidates:
        raise SystemExit('No pinned Electron archive: set KEEPHARNESS_ELECTRON_ZIP')
    archive = candidates[0]
if hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest() != expected:
    raise SystemExit('Electron archive checksum mismatch')
if (dist / 'version').read_text().strip() != version:
    raise SystemExit('Electron runtime version mismatch')
with zipfile.ZipFile(archive) as bundle:
    expected_files = set()
    for member in bundle.infolist():
        name = member.filename
        if member.is_dir():
            continue
        path = dist / name
        if path.is_symlink() or not path.is_file() or path.resolve() != dist.resolve() / name:
            raise SystemExit(f'Invalid Electron member: {name}')
        if hashlib.sha256(bundle.read(member)).digest() != hashlib.sha256(path.read_bytes()).digest():
            raise SystemExit(f'Electron runtime differs from verified archive: {name}')
        expected_files.add(name)
    actual = set()
    for path in dist.rglob('*'):
        if path.is_symlink():
            raise SystemExit(f'Symlinked Electron member: {path}')
        if path.is_file():
            actual.add(path.relative_to(dist).as_posix())
    if actual != expected_files:
        raise SystemExit('Unexpected Electron runtime files')
print(f'Electron {version}: archive and runtime verified against pinned SHASUMS256.txt')
