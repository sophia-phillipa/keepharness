"""Smoke-test a fresh installation with isolated state, outside the checkout."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import httpx

def main():
    with tempfile.TemporaryDirectory(prefix='tail-install-check-') as folder:
        with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        with (Path(folder)/'server.log').open('w+') as log:
            proc=subprocess.Popen([sys.executable,'-m','control','--port',str(port),'--state',folder],cwd=folder,env={k:v for k,v in os.environ.items() if k!='PYTHONPATH'},stdout=log,stderr=log)
            try:
                with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=1) as client:
                    for _ in range(120):
                        if proc.poll() is not None:raise RuntimeError('Installed server exited before startup')
                        try:
                            if client.get('/').status_code==200:break
                        except httpx.HTTPError:pass
                        time.sleep(.25)
                    else:raise RuntimeError('Installed server startup timeout')
                    for path in ('/admin.js','/admin.css'):assert client.get(path).status_code==200
                    state=client.get('/api/state').json()
                    assert not state['status']['running']
                    assert not any(s['enabled'] for s in state['settings']['services'].values())
                    assert state['local_profile']=={}
                    print('PASS: installed package outside checkout, fresh private state, HTTP/assets/API, no services enabled.')
            finally:
                proc.terminate()
                try:proc.wait(timeout=15)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
if __name__=='__main__':main()
