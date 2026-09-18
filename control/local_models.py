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
                          'model_file':option('--model','-m')})
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
