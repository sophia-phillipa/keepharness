"""Install a per-user systemd service and desktop shortcut, without root."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import shlex
import time
import urllib.request

SERVICE='tail-harness.service'
def wait_ready(port):
    for _ in range(120):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/',timeout=1) as response:
                if response.status==200:return
        except OSError:pass
        time.sleep(.25)
    raise RuntimeError('O serviço não ficou disponível; consulte journalctl --user -u tail-harness.')

def quoted(value):
    return json.dumps(str(value).replace('%','%%').replace('$','$$'))

def files(home,python,port=8094):
    home=Path(home);state=home/'.local/share/tail-harness'
    unit=f'''[Unit]
Description=Tail Harness local administration
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={quoted(python)} -m control --port {port} --state {quoted(state)}
Environment="PATH={home}/.local/bin:/usr/local/bin:/usr/bin:/bin"
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
UMask=0077

[Install]
WantedBy=default.target
'''
    # Desktop launcher starts the registered service before opening the browser.
    launcher=f'''#!/bin/sh
set -eu
systemctl --user start {SERVICE}
{shlex.quote(str(python))} -c {shlex.quote('from control.install import wait_ready; wait_ready('+str(port)+')')}
exec xdg-open http://127.0.0.1:{port}/
'''
    desktop=f'''[Desktop Entry]
Type=Application
Name=Tail Harness
Comment=Gerenciar serviços de IA locais
Exec="{home}/.local/bin/tail-harness-open"
Icon=utilities-terminal
Terminal=false
Categories=Development;
StartupNotify=false
'''
    return {home/'.config/systemd/user'/SERVICE:(unit,0o600),home/'.local/bin/tail-harness-open':(launcher,0o700),home/'.local/share/applications/tail-harness.desktop':(desktop,0o644)}

def main(argv=None):
    parser=argparse.ArgumentParser(description='Instala serviço e atalho para este usuário Linux')
    parser.add_argument('--port',type=int,default=8094)
    parser.add_argument('--boot',action='store_true',help='Habilitar linger para iniciar antes do login')
    args=parser.parse_args(argv)
    if not sys.platform.startswith('linux'):parser.error('Instalação de serviço requer Linux/systemd; use tail-harness para execução manual.')
    if not 1024<=args.port<=65535:parser.error('Porta inválida')
    os.umask(0o077)
    for path,(content,mode) in files(Path.home(),sys.executable,args.port).items():
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content);path.chmod(mode)
    subprocess.run(['systemctl','--user','daemon-reload'],check=True)
    subprocess.run(['systemctl','--user','enable','--now',SERVICE],check=True)
    subprocess.run(['systemctl','--user','restart',SERVICE],check=True)
    if args.boot:subprocess.run(['loginctl','enable-linger',str(os.getuid())],check=True)
    subprocess.run(['systemctl','--user','is-active',SERVICE],check=True)
    wait_ready(args.port)
    print(f'Serviço instalado. Abra Tail Harness no menu ou http://127.0.0.1:{args.port}/')

if __name__=='__main__':main()
