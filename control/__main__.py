import argparse
import asyncio
import json
import os
from pathlib import Path
from .discovery import scan
p=argparse.ArgumentParser(description='Tail Harness — local Codex and Claude control')
p.add_argument('--scan',action='store_true',help='Read-only inventory; no permissions or networking changes')
p.add_argument('--port',type=int,default=8094)
p.add_argument('--state',default=str(Path.home()/'.local/share/tail-harness'))
a=p.parse_args();os.umask(0o077)
if a.scan:print(json.dumps(asyncio.run(scan()),indent=2,ensure_ascii=False))
else:
    import uvicorn
    from .server import create_app
    print(f'Gestão local: http://127.0.0.1:{a.port}/',flush=True)
    uvicorn.run(create_app(a.state,a.port),host='127.0.0.1',port=a.port,proxy_headers=False,access_log=False)
