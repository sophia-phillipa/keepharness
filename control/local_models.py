"""Discover local llama.cpp processes without exporting credential contents."""
from pathlib import Path
import httpx
import os

def processes():
    found=[]
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            if proc.stat().st_uid!=os.getuid():continue
            args=(proc/'cmdline').read_bytes().decode().split('\0')
            if Path(args[0]).name!='llama-server':continue
            def option(*names,default=''):
                return next((args[args.index(n)+1] for n in names if n in args),default)
            host=option('--host',default='127.0.0.1')
            if host not in ('127.0.0.1','localhost','0.0.0.0'):continue
            found.append({'url':'http://127.0.0.1:'+option('--port',default='8080'),
                          'key_file':option('--api-key-file'),'binary':args[0],
                          'model_file':option('--model','-m'),'performance':performance(args)})
        except (OSError,ValueError,IndexError,UnicodeError):continue
    return found

async def discover():
    results=[]
    async with httpx.AsyncClient(timeout=3,trust_env=False) as client:
        for server in processes():
            try:
                headers={}
                if server['key_file']:headers['Authorization']='Bearer '+Path(server['key_file']).read_text().strip()
                r=await client.get(server['url']+'/v1/models',headers=headers);r.raise_for_status()
                for model in r.json().get('data',[]):
                    results.append({**server,'id':model['id'],'runtime':'llama.cpp','context':model.get('meta',{}).get('n_ctx')})
            except (OSError,httpx.HTTPError,ValueError,KeyError):continue
    return results

# Only performance options: never import credentials or arbitrary CLI arguments.
PERFORMANCE_FLAGS=('device','n-gpu-layers','ctx-size','parallel','flash-attn','cache-type-k','cache-type-v','cache-ram','reasoning','reasoning-format','reasoning-budget','threads','n-cpu-moe','threads-batch','load-mode','cpu-range','cpu-strict','cpu-range-batch','cpu-strict-batch')
def performance(args):
    return {name:args[args.index('--'+name)+1] for name in PERFORMANCE_FLAGS if '--'+name in args and args.index('--'+name)+1<len(args)}

def save_profile(state,server):
    import json
    profile={k:server[k] for k in ('binary','model_file','performance') if k in server}
    target=Path(state)/'local-profile.json';temp=target.with_suffix('.tmp')
    temp.write_text(json.dumps(profile,indent=2));temp.chmod(0o600);temp.replace(target)
    return profile

def load_profile(state):
    import json
    try:return json.loads((Path(state)/'local-profile.json').read_text())
    except FileNotFoundError:return {}

def launch_options(profile):
    values=profile.get('performance',{})
    return [value for name in PERFORMANCE_FLAGS if name in values for value in ('--'+name,str(values[name]))]

def validate_profile(profile):
    if not profile:return {}
    if not isinstance(profile,dict) or set(profile)-{'binary','model_file','performance'}:raise ValueError('Perfil local contém campos não permitidos.')
    binary=Path(profile.get('binary',''));model=Path(profile.get('model_file',''))
    if not binary.is_absolute() or binary.name!='llama-server' or not binary.is_file():raise ValueError('Executável do perfil não encontrado nesta máquina.')
    if not model.is_absolute() or model.suffix!='.gguf' or not model.is_file():raise ValueError('Modelo do perfil não encontrado nesta máquina.')
    values=profile.get('performance',{})
    if not isinstance(values,dict) or set(values)-set(PERFORMANCE_FLAGS):raise ValueError('Opções de desempenho não permitidas.')
    if any(not isinstance(v,str) or not v or len(v)>100 or v.startswith('--') or any(ord(c)<32 for c in v) for v in values.values()):raise ValueError('Valor de desempenho inválido.')
    return {'binary':str(binary),'model_file':str(model),'performance':dict(values)}
