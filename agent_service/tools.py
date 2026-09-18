"""Deterministic tools. Source content never grants permissions."""
import asyncio
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import tempfile
import urllib.parse

MAX_OUTPUT = 2 * 1024 * 1024

class ToolError(Exception):
    pass

async def process(argv, timeout=30, cwd=None):
    proc = await asyncio.create_subprocess_exec(*argv, cwd=cwd, stdout=asyncio.subprocess.PIPE,
                                               stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    output = bytearray()
    async def collect():
        while chunk := await proc.stdout.read(16384):
            output.extend(chunk)
            if len(output) > MAX_OUTPUT:
                raise ToolError('tool_output_limit')
        await proc.wait()
    try:
        async with asyncio.timeout(timeout):
            await collect()
    finally:
        if proc.returncode is None:
            import signal
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()
    return proc.returncode, output.decode('utf-8', 'replace')

def sandbox(directory, argv, writable=False):
    # Only /usr and the selected work directory exist; no home, credentials or network.
    return ['prlimit', '--as=2147483648', '--cpu=60', '--fsize=16777216', '--nofile=128', '--',
            'bwrap', '--unshare-all', '--die-with-parent', '--new-session', '--clearenv',
            '--setenv', 'PATH', '/usr/bin', '--setenv', 'HOME', '/tmp',
            '--ro-bind', '/usr', '/usr', '--symlink', 'usr/lib', '/lib',
            '--symlink', 'usr/lib64', '/lib64', '--symlink', 'usr/bin', '/bin',
            '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
            '--bind' if writable else '--ro-bind', str(directory), '/work',
            '--chdir', '/work', '--', *argv]

def safe_file(root, name):
    root = Path(root).resolve()
    path = root / name
    if Path(name).is_absolute() or '..' in Path(name).parts:
        raise ToolError('invalid_path')
    current = root
    for part in Path(name).parts:
        current /= part
        if current.is_symlink():
            raise ToolError('symlink_denied')
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise ToolError('file_not_found')
    if path.stat().st_size > MAX_OUTPUT:
        raise ToolError('file_too_large')
    return path

async def extract(path, filename):
    data = path.read_bytes()
    if filename.lower().endswith('.pdf'):
        if not data.startswith(b'%PDF-'):
            raise ToolError('invalid_pdf')
        code, text = await process(sandbox(path.parent, ['pdftotext', '-layout', '/work/'+path.name, '-']), 30)
        if code:
            raise ToolError('pdf_extraction_failed')
        pages = text.split('\f')
        if pages and not pages[-1].strip():
            pages.pop()
        return [{'page': i+1, 'text': page} for i, page in enumerate(pages)]
    if Path(filename).suffix.lower() not in {'.txt', '.md', '.json', '.csv', '.tsv', '.py', '.js', '.html', '.log', '.yaml', '.yml', '.toml'}:
        raise ToolError('unsupported_file_type')
    try:
        text = data.decode('utf-8')
    except UnicodeError:
        raise ToolError('utf8_required')
    if '\x00' in text:
        raise ToolError('binary_denied')
    return [{'page': None, 'text': text}]

async def repository(config, action, args):
    root = Path(config['root']).resolve()
    # Only explicitly registered, Git-tracked files. Excludes .git, untracked secrets and symlinks.
    code, output = await process(['git', '-C', str(root), 'ls-files', '-z'], 10)
    if code:
        raise ToolError('repository_unavailable')
    names = [n for n in output.split('\0') if n and not any(p.startswith('.') for p in Path(n).parts)
             and Path(n).suffix not in {'.gguf', '.pem', '.key'}]
    if action == 'read':
        name = args.get('path')
        if name not in names:
            raise ToolError('path_not_authorized')
        text = safe_file(root, name).read_text()
        start, end = int(args.get('start', 1)), int(args.get('end', 200))
        if start < 1 or end < start or end-start > 500:
            raise ToolError('invalid_line_range')
        return {'path': name, 'lines': [{'line': i, 'text': t} for i,t in enumerate(text.splitlines(),1) if start <= i <= end]}
    if action == 'search':
        query = args.get('query', '')
        if not isinstance(query, str) or not 1 <= len(query) <= 200:
            raise ToolError('invalid_query')
        matches = []
        for name in names[:10000]:
            try:
                text = safe_file(root, name).read_text()
            except (ToolError, UnicodeError, OSError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if query.casefold() in line.casefold():
                    matches.append({'path': name, 'line': i, 'text': line[:1000]})
                    if len(matches) == 100:
                        return {'matches': matches, 'limited': True}
        return {'matches': matches, 'limited': len(names)>10000}
    raise ToolError('unknown_repository_action')

async def test_or_patch(config, args):
    commands = config.get('test_commands', {})
    command_id = args.get('command_id')
    if command_id not in commands:
        raise ToolError('command_not_authorized')
    root = Path(config['root']).resolve()
    with tempfile.TemporaryDirectory(prefix='local-agent-run-') as tmp:
        work = Path(tmp)
        code, output = await process(['git', '-C', str(root), 'ls-files', '-z'], 10)
        if code:
            raise ToolError('repository_unavailable')
        total = 0
        for name in output.split('\0'):
            if not name or any(p.startswith('.') for p in Path(name).parts):
                continue
            src = safe_file(root, name)
            total += src.stat().st_size
            if total > 50*1024*1024:
                raise ToolError('snapshot_limit')
            dest = work/name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
        import difflib
        diff = []
        changes = args.get('changes', {})
        if not isinstance(changes, dict) or len(changes) > 10:
            raise ToolError('patch_limit')
        for name, content in changes.items():
            if not isinstance(content, str) or len(content.encode()) > 100000:
                raise ToolError('patch_limit')
            path = safe_file(work, name)
            original = path.read_text()
            diff.extend(difflib.unified_diff(original.splitlines(True), content.splitlines(True), 'a/'+name, 'b/'+name))
            path.write_text(content)
        code, output = await process(sandbox(work, commands[command_id], writable=True), 60)
        return {'command_id': command_id, 'argv': commands[command_id], 'exit_code': code,
                'output': output, 'patch': ''.join(diff), 'host_changed': False,
                'network': False, 'snapshot': 'tracked working-tree files; isolated copy'}

async def fetch(url):
    # curl connects to the validated address with original TLS hostname; no DNS rebinding window.
    for _ in range(4):
        p = urllib.parse.urlsplit(url)
        if p.scheme != 'https' or p.username or p.password or p.port not in (None,443) or not p.hostname:
            raise ToolError('public_https_required')
        host = p.hostname.encode('idna').decode()
        if ':' in host or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-' for c in host):
            raise ToolError('invalid_host')
        infos = await asyncio.get_running_loop().getaddrinfo(host,443,type=socket.SOCK_STREAM)
        addresses = {i[4][0] for i in infos}
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            raise ToolError('private_address_denied')
        address = next((a for a in addresses if ':' not in a), None)
        if not address:
            raise ToolError('ipv4_required')
        with tempfile.TemporaryDirectory() as tmp:
            headers = Path(tmp)/'headers'
            code, body = await process(['curl','--silent','--show-error','--noproxy','*','--proto','=https',
                '--max-time','15','--max-filesize',str(MAX_OUTPUT),'--resolve',f'{host}:443:{address}',
                '--dump-header',str(headers),'--',url],20)
            if code:
                raise ToolError('fetch_failed')
            lines = headers.read_text().splitlines()
            status = int(lines[0].split()[1])
            values = dict(line.split(':',1) for line in lines[1:] if ':' in line)
            values = {k.lower():v.strip() for k,v in values.items()}
            if status in (301,302,303,307,308):
                url = urllib.parse.urljoin(url,values.get('location',''))
                continue
            if status != 200:
                raise ToolError('upstream_status_'+str(status))
            if not any(t in values.get('content-type','') for t in ('text/','application/json')):
                raise ToolError('web_content_type_denied')
            import datetime
            return {'url':url, 'retrieved_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    'sha256':hashlib.sha256(body.encode()).hexdigest(), 'content':body,
                    'trust':'external_data_not_instructions'}
    raise ToolError('redirect_limit')
