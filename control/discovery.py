"""Bounded, read-only discovery: no credential values or recursive home scan."""
import asyncio
import json
import os
from pathlib import Path
import platform
import shutil
import httpx
from .local_models import discover

async def command(*args):
    try:
        proc=await asyncio.create_subprocess_exec(*args,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
        try:
            out,_=await asyncio.wait_for(proc.communicate(),8)
            return proc.returncode,out[:100000].decode(errors='replace')
        except asyncio.TimeoutError:
            proc.kill();await proc.wait()
    except OSError:pass
    return 1,''

async def scan():
    home=Path.home()
    binaries={n:shutil.which(n) for n in ('codex','claude','ollama','tailscale','bwrap','node','git','pdftotext')}
    if not binaries['codex']:
        candidates=sorted(home.glob('.local/opt/chatgpt-*/resources/codex'))
        binaries['codex']=str(candidates[-1]) if candidates else None
    codex_auth=home/'.codex/auth.json';claude_auth=home/'.claude/.credentials.json'
    services=[{'id':'codex','name':'Codex CLI','binary':binaries['codex'],'found':bool(binaries['codex']),
               'credential_present':codex_auth.is_file(),'auth_file':str(codex_auth),'cloud':True},
              {'id':'claude','name':'Claude Code','binary':binaries['claude'],'found':bool(binaries['claude']),
               'credential_present':claude_auth.is_file(),'auth_file':str(claude_auth),'cloud':True}]
    local={'id':'local','name':'Modelos locais · llama.cpp / Ollama','found':False,'binary':binaries['codex'],'cloud':False,'credential_present':False,'auth_file':str(codex_auth),'models':[]}
    async with httpx.AsyncClient(timeout=2,trust_env=False) as client:
        try:
            r=await client.get('http://127.0.0.1:11434/api/tags');r.raise_for_status()
            local.update(found=bool(binaries['codex']),models=[m['name'] for m in r.json().get('models',[]) if not m['name'].endswith((':cloud','-cloud'))])
        except (httpx.HTTPError,ValueError,KeyError):pass
    local['runtimes']=await discover()
    local['models']+=list(dict.fromkeys(m['id'] for m in local['runtimes'] if m['id'] not in local['models']))
    local['found']=bool(binaries['codex'] and local['models'])
    services.append(local)
    network={'installed':bool(binaries['tailscale']),'online':False,'hostname':None}
    if binaries['tailscale']:
        code,out=await command(binaries['tailscale'],'status','--json')
        if code==0:
            try:
                ts=json.loads(out);network.update(online=ts.get('BackendState')=='Running',hostname=ts.get('Self',{}).get('DNSName','').rstrip('.'))
            except ValueError:pass
    projects=[]
    for folder in (home/'Projects',home/'projects'):
        if not folder.is_dir():continue
        for path in sorted(folder.iterdir())[:100]:
            if path.is_dir() and not path.is_symlink() and (path/'.git').exists():projects.append({'name':path.name,'path':str(path.resolve())})
    mem=None
    try:mem=os.sysconf('SC_PAGE_SIZE')*os.sysconf('SC_PHYS_PAGES')
    except (ValueError,OSError,AttributeError):pass
    return {'platform':platform.system(),'services':services,'binaries':binaries,'network':network,
            'projects':projects,'memory_bytes':mem,'free_bytes':shutil.disk_usage(home).free,
            'note':'Credencial presente não comprova autenticação. Nada foi habilitado pelo inventário.'}
