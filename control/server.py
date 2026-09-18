"""Local administration. Provider access and tailnet exposure require explicit choices."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import socket
import sqlite3
import sys
import time
from urllib.parse import urlparse
import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse,JSONResponse
from starlette.routing import Route
from .discovery import scan,command
from .integrations import inventory
from .operations import Operations,operation
import ipaddress
from agent_service.codex_rpc import metadata

ROOT=Path(__file__).resolve().parents[1]
PERMISSIONS=('read','write','upload','tests','internet','shell','hooks')

class Manager:
    def __init__(self,state):
        self.state=Path(state);self.state.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path=self.state/'settings.json';self.cookie=secrets.token_urlsafe(32)
        self.admin_port=8094
        self.inventory=None;self.proc=None;self.lock=asyncio.Lock()
        self.settings=json.loads(self.path.read_text()) if self.path.exists() else {
            'services':{p:{'enabled':False,'models':[],'projects':['sem-projeto'],'mode':'scoped' if p!='local' else 'native','integrations':[],'permissions':{k:False for k in PERMISSIONS}} for p in ('codex','claude','local')},
            'projects':[],'uploads_enabled':False,'port':8095,'tailnet_port':8095,'logins':[]}
        self.provider_models={};self.auth={};self.applied=None;self.operations=Operations();self.startup_error=None

    def audit(self,action):
        with (self.state/'audit.jsonl').open('a') as out:out.write(json.dumps({'time':time.time(),'action':action})+'\n')

    async def refresh(self):
        self.inventory=await scan()
        return self.inventory

    def busy(self):
        db=self.state/'runs/jobs.sqlite3'
        if not db.exists():return False
        with sqlite3.connect(db) as c:return bool(c.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone())

    def running(self):return self.proc is not None and self.proc.returncode is None

    def validate(self,data):
        if not isinstance(data,dict):raise ValueError('Configuração inválida.')
        out={}
        for key in ('port','tailnet_port'):
            value=data.get(key,8095)
            if type(value)!=int or not 1024<=value<=65535 or value==self.admin_port:raise ValueError('Porta inválida ou reservada pela gestão.')
            out[key]=value
        bind=data.get('vpn_bind','127.0.0.1');address=ipaddress.ip_address(bind)
        if address.version!=4 or not (address.is_loopback or address.is_private) or address.is_unspecified or address.is_multicast:raise ValueError('Use somente o IP privado específico da interface VPN, nunca 0.0.0.0.')
        out['vpn_bind']=bind
        out['uploads_enabled']=data.get('uploads_enabled') is True
        projects=[];ids=set()
        for project in data.get('projects',[]):
            pid=project.get('id','');label=project.get('label','');raw=Path(project.get('root','')).expanduser()
            if not re.fullmatch('[a-z0-9_-]{1,64}',pid) or pid=='sem-projeto' or pid in ids:raise ValueError('Identificador de projeto inválido ou repetido.')
            if not raw.is_absolute() or not raw.is_dir():raise ValueError('Escolha uma pasta existente e absoluta.')
            root=raw.resolve();home=Path.home().resolve()
            forbidden=[Path('/'),home,home/'.ssh',home/'.codex',home/'.claude',home/'.config',self.state.resolve()]
            if root in forbidden or any(root.is_relative_to(x) for x in forbidden[2:]):raise ValueError('Pasta ampla ou de credenciais não pode ser compartilhada.')
            if len(label)>100:raise ValueError('Nome de projeto muito longo.')
            projects.append({'id':pid,'label':label or pid,'root':str(root)});ids.add(pid)
        out['projects']=projects
        out['services']={}
        for provider in ('codex','claude','local'):
            spec=data.get('services',{}).get(provider,{})
            models=spec.get('models',[]);allowed_projects=spec.get('projects',[])
            if not isinstance(models,list) or len(models)>50 or any(not isinstance(x,str) or not re.fullmatch('[a-zA-Z0-9_./:-]{1,160}',x) for x in models):raise ValueError('Lista de modelos inválida.')
            if not isinstance(allowed_projects,list) or any(p not in ids|{'sem-projeto'} for p in allowed_projects):raise ValueError('Projeto não cadastrado.')
            perms={k:spec.get('permissions',{}).get(k) is True for k in PERMISSIONS}
            if perms['write'] and not perms['read']:raise ValueError('Para permitir alterações, habilite também leitura.')
            if perms['upload'] and not out['uploads_enabled']:raise ValueError('Habilite uploads globais antes de permitir anexos no serviço.')
            mode=spec.get('mode','scoped')
            if mode not in ('scoped','native') or (provider=='local' and mode!='native'):raise ValueError('Modelo local usa o agente Codex nativo.')
            if mode=='scoped' and (perms['shell'] or perms['internet']):raise ValueError('Internet e terminal exigem modo nativo.')
            selected=spec.get('integrations',[])
            available={x['id'] for x in inventory().get(provider,[])}
            if not isinstance(selected,list) or any(x not in available for x in selected):raise ValueError('Integração não encontrada. Atualize o inventário.')
            if selected and (mode!='native' or not perms['internet']):raise ValueError('Conectores exigem modo nativo e internet nesta versão.')
            enabled=spec.get('enabled') is True
            if enabled and (not models or not allowed_projects):raise ValueError('Selecione modelos e projetos para o serviço habilitado.')
            out['services'][provider]={'mode':mode,'integrations':selected,'enabled':enabled,'models':list(dict.fromkeys(models)),'projects':allowed_projects,'permissions':perms}
        logins=data.get('logins',[])
        if not isinstance(logins,list) or len(logins)>50 or any(not isinstance(x,str) or not re.fullmatch('[A-Za-z0-9_.+@-]{1,160}',x) for x in logins):raise ValueError('Identidades Tailscale inválidas.')
        out['logins']=list(dict.fromkeys(logins))
        return out

    def save(self,data):
        settings=self.validate(data)
        if self.running():raise ValueError('Pare o harness antes de alterar permissões. Conversas em andamento são preservadas.')
        if (self.state/'tailnet.json').exists() and any(settings.get(k)!=self.settings.get(k) for k in ('port','tailnet_port','vpn_bind')):raise ValueError('Retire a rota Tailscale antes de mudar as portas ou o IP.')
        tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(settings,indent=2));tmp.chmod(0o600);tmp.replace(self.path)
        self.settings=settings;self.audit('settings_saved')

    async def check(self,provider):
        if provider not in ('codex','claude','local'):raise ValueError('Serviço desconhecido.')
        if self.inventory is None:await self.refresh()
        info=next(s for s in self.inventory['services'] if s['id']==provider)
        if not info['found']:raise ValueError('CLI não encontrado. Instale e entre pela ferramenta oficial.')
        if provider=='local':
            self.provider_models[provider]={m:['configured'] for m in info.get('models',[])}
            self.auth[provider]=bool(info['found'])
            return {'authenticated':bool(info['found']),'models':self.provider_models[provider],'model_source':'Servidores locais detectados (llama.cpp / Ollama)'}
        if provider=='codex':
            code,_=await command(info['binary'],'login','status');authenticated=code==0
            if authenticated:
                listing=await metadata(info['binary'],'model/list')
                self.provider_models[provider]={m['id']:[e['reasoningEffort'] for e in m.get('supportedReasoningEfforts',[])] or ['low'] for m in listing.get('data',[])}
        else:
            code,raw=await command(info['binary'],'auth','status','--json')
            try:authenticated=code==0 and json.loads(raw).get('loggedIn') is True
            except ValueError:authenticated=False
            # Official CLI aliases; entitlement is checked by the provider at execution.
            self.provider_models[provider]={m:['configured'] for m in ('sonnet','opus','haiku')}
        self.auth[provider]=authenticated;self.audit('provider_check:'+provider)
        return {'authenticated':authenticated,'models':self.provider_models.get(provider,{}),'model_source':'CLI model/list' if provider=='codex' else 'Aliases oficiais; disponibilidade depende da conta'}

    async def start(self):
        if self.running():return
        await self.refresh()
        if any(s.get('enabled') and s.get('mode')=='scoped' for s in self.settings['services'].values()) and (platform.system()!='Linux' or not self.inventory['binaries']['bwrap']):raise ValueError('Modo isolado requer Linux e bubblewrap. Use o modo nativo em outra plataforma.')
        cfg={'state_dir':str(self.state/'runs'),'projects':{'sem-projeto':{'label':'Sem projeto'}},'clients':{},
             'services':self.settings['services'],'origins':[],'uploads_enabled':self.settings['uploads_enabled'],
             'local_access':self.settings.get('vpn_bind','127.0.0.1')=='127.0.0.1','bind':self.settings.get('vpn_bind','127.0.0.1'),'port':self.settings['port']}
        for project in self.settings['projects']:cfg['projects'][project['id']]={**project,'test_commands':{},'additional_roots':[],'node_binary':shutil.which('node') or 'node'}
        enabled=0
        for provider,spec in self.settings['services'].items():
            if not spec['enabled']:continue
            enabled+=1
            checked=await self.check(provider)
            info=next(s for s in self.inventory['services'] if s['id']==provider)
            if not checked['authenticated'] or (spec.get('mode')=='scoped' and not Path(info['auth_file']).is_file()):raise ValueError('Faça login em '+provider+' e use autenticação por arquivo local. Keychain não é suportado pelo sandbox atual.')
            binary=Path(info['binary']).resolve()
            with binary.open('rb') as stream:native=stream.read(4)==b'\x7fELF'
            if not native and spec.get('mode')=='scoped':raise ValueError('Use o binário Linux nativo de '+provider+'; wrappers de shell/npm não são montados no sandbox.')
            if provider=='codex' and any(m not in checked['models'] for m in spec['models']):raise ValueError('Modelo Codex não retornado pelo CLI atual.')
            cfg[provider]={'binary':str(binary),'auth_file':info['auth_file'],'python':sys.executable,'integrations':spec.get('integrations',[])}
            if provider=='local':
                if any(m not in checked['models'] for m in spec['models']):raise ValueError('Modelo local não está disponível. Atualize a descoberta.')
                cfg[provider]['local_provider']='ollama'
                cfg[provider]['local_models']={m['id']:m for m in info.get('runtimes',[])}
            cfg[provider+'_models']={m:checked['models'][m] for m in spec['models']} if provider=='codex' else spec['models']
        if not enabled:raise ValueError('Habilite pelo menos um serviço.')
        all_projects=list(cfg['projects'])
        vpnkey=self.state/'vpn.key'
        if not vpnkey.exists():vpnkey.write_text(secrets.token_urlsafe(48));vpnkey.chmod(0o600)
        cfg['clients']['vpn']={'sha256':hashlib.sha256(vpnkey.read_text().encode()).hexdigest(),'projects':all_projects}
        cfg['clients']['local']={'sha256':hashlib.sha256(secrets.token_bytes(48)).hexdigest(),'projects':all_projects}
        cfg['tailscale_logins']={}
        for login in self.settings['logins']:
            client='tailnet-'+hashlib.sha256(login.encode()).hexdigest()[:16]
            cfg['clients'][client]={'sha256':hashlib.sha256(secrets.token_bytes(48)).hexdigest(),'projects':all_projects}
            cfg['tailscale_logins'][login]=client
        port=self.settings['port'];host=self.inventory['network'].get('hostname');remote=self.settings['tailnet_port']
        bind=self.settings.get('vpn_bind','127.0.0.1')
        cfg['origins']=[f'http://{bind}:{port}',f'http://127.0.0.1:{port}',f'http://localhost:{port}']+([f'http://{host}:{remote}'] if host else [])
        with socket.socket() as check:
            try:check.bind((bind,port))
            except OSError:raise ValueError('Porta em uso. Escolha outra; nenhum serviço existente foi encerrado.')
        path=self.state/'runtime.json';path.write_text(json.dumps(cfg));path.chmod(0o600)
        log=(self.state/'harness.log').open('ab')
        self.proc=await asyncio.create_subprocess_exec(sys.executable,'-m','agent_service.app',cwd=ROOT,env={**os.environ,'LOCAL_AGENT_CONFIG':str(path)},stdout=log,stderr=log)
        log.close()
        async with httpx.AsyncClient(trust_env=False,timeout=1) as client:
            for _ in range(40):
                if self.proc.returncode is not None:raise ValueError('O serviço encerrou ao iniciar. Consulte o log local.')
                try:
                    if (await client.get(f'http://{bind}:{port}/')).status_code==200:break
                except httpx.HTTPError:pass
                await asyncio.sleep(.25)
            else:
                await self.stop(force=True);raise ValueError('O serviço não ficou pronto a tempo.')
        self.applied=time.time();(self.state/'autostart').touch(mode=0o600);self.startup_error=None;self.audit('harness_started')

    async def stop(self,force=False):
        if not force and self.busy():raise ValueError('Há tarefas na fila ou em execução. Cancele ou aguarde antes de parar.')
        if self.running():
            self.proc.terminate()
            try:await asyncio.wait_for(self.proc.wait(),15)
            except asyncio.TimeoutError:self.proc.kill();await self.proc.wait()
        if not force:(self.state/'autostart').unlink(missing_ok=True)
        self.audit('harness_stopped')

    async def tailnet(self,enabled):
        if not self.inventory:await self.refresh()
        binary=self.inventory['binaries']['tailscale'];port=self.settings['tailnet_port']
        if not binary or not self.inventory['network']['online']:raise ValueError('Conecte o Tailscale primeiro.')
        receipt=self.state/'tailnet.json'
        if enabled:
            if self.settings.get('vpn_bind','127.0.0.1')!='127.0.0.1':raise ValueError('Para Tailscale Serve, use o endereço local 127.0.0.1; para outra VPN, use diretamente o IP configurado.')
            if not self.running() or not self.settings['logins']:raise ValueError('Inicie o harness e cadastre identidades permitidas antes de compartilhar.')
            code,out=await command(binary,'serve','status','--json')
            if code:raise ValueError('Não foi possível conferir as rotas existentes.')
            # Never overwrite a pre-existing route; only a route recorded as ours can be updated.
            status=json.loads(out or '{}')
            if str(port) in status.get('TCP',{}) and not receipt.exists():raise ValueError('Essa porta Tailscale já pertence a outra rota. Escolha outra.')
            code,_=await command(binary,'serve','--bg','--http='+str(port),'http://127.0.0.1:'+str(self.settings['port']))
            if code:raise ValueError('Tailscale recusou a configuração. Confira permissões do operador no terminal; não executamos sudo.')
            receipt.write_text(json.dumps({'port':port}));self.audit('tailnet_enabled')
        else:
            if not receipt.exists():raise ValueError('Nenhuma rota deste projeto para retirar.')
            port=json.loads(receipt.read_text())['port']
            code,_=await command(binary,'serve','--http='+str(port),'off')
            if code:raise ValueError('Não foi possível retirar a rota.')
            receipt.unlink();self.audit('tailnet_disabled')

    def status(self):
        host=(self.inventory or {}).get('network',{}).get('hostname');port=self.settings['port']
        return {'running':self.running(),'busy':self.busy(),'applied_at':self.applied,
                'local_url':f'http://{self.settings.get("vpn_bind","127.0.0.1")}:{port}/','remote_url':f'http://{host}:{self.settings["tailnet_port"]}/' if host else None,
                'shared':(self.state/'tailnet.json').exists(),'version':'0.2.0','startup_error':self.startup_error}

def create_app(state,port=8094):
    manager=Manager(state);manager.admin_port=port
    @asynccontextmanager
    async def lifespan(app):
        await manager.refresh()
        if (manager.state/'autostart').exists():
            try:await manager.start()
            except (ValueError,RuntimeError,OSError) as exc:manager.startup_error=str(exc)
        yield
        await manager.stop(force=True)
        await manager.operations.close()
    async def endpoint(request:Request):
        host=request.headers.get('host','')
        allowed=(f'127.0.0.1:{port}',f'localhost:{port}')
        if host not in allowed or request.client.host not in ('127.0.0.1','::1','testclient') or request.headers.get('tailscale-user-login'):
            return JSONResponse({'error':'Gestão disponível somente nesta máquina.'},403)
        origin=request.headers.get('origin')
        if (origin and origin not in ['http://'+h for h in allowed]) or request.headers.get('sec-fetch-site')=='cross-site':return JSONResponse({'error':'Origem não autorizada.'},403)
        path=request.url.path
        if path=='/':
            r=FileResponse(ROOT/'control/index.html');r.set_cookie('admin',manager.cookie,httponly=True,samesite='strict');return r
        if path in ('/admin.js','/admin.css'):return FileResponse(ROOT/'control'/path[1:])
        if not secrets.compare_digest(request.cookies.get('admin',''),manager.cookie):return JSONResponse({'error':'Abra a gestão nesta máquina primeiro.'},401)
        try:
            if request.method=='GET':
                if path=='/api/state':return JSONResponse({'settings':manager.settings,'inventory':manager.inventory,'status':manager.status(),'authentication':manager.auth,'models':manager.provider_models,'integrations':inventory(),'operations':list(manager.operations.jobs.values())})
                return JSONResponse({'error':'Não encontrado'},404)
            if request.headers.get('x-harness-admin')!='1':raise ValueError('Cabeçalho administrativo obrigatório.')
            raw=await request.body()
            if len(raw)>64000:raise ValueError('Pedido muito grande.')
            data=json.loads(raw or '{}')
            async with manager.lock:
                if path=='/api/scan':result=await manager.refresh()
                elif path=='/api/check':result=await manager.check(data.get('provider'))
                elif path=='/api/settings':manager.save(data);result={'saved':True}
                elif path=='/api/start':await manager.start();result=manager.status()
                elif path=='/api/stop':await manager.stop();result=manager.status()
                elif path=='/api/cancel-operation':
                    manager.operations.cancel(data.get('id'));result={'cancelled':True}
                elif path=='/api/provider-login':
                    provider=data.get('provider');binary=manager.inventory['binaries'].get(provider) if provider in ('codex','claude') else None
                    if not binary:raise ValueError('CLI não encontrado.')
                    result=manager.operations.launch([binary,'login','--device-auth'] if provider=='codex' else [binary,'auth','login'])
                elif path=='/api/integration':
                    if manager.running():raise ValueError('Pare o harness antes de modificar integrações.')
                    provider=data.get('provider')
                    if provider not in ('codex','claude'):raise ValueError('Provedor inválido.')
                    binary=manager.inventory['binaries'][provider]
                    if not binary:raise ValueError('CLI não instalado.')
                    result=manager.operations.launch(operation(binary,provider,data));manager.audit('integration:'+data.get('action',''))
                elif path=='/api/model-install':
                    catalog={'gemma4':'hf.co/unsloth/gemma-4-E4B-it-GGUF:Q4_K_M','qwen36':'hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q3_K_M'}
                    model=catalog.get(data.get('model'));binary=manager.inventory['binaries'].get('ollama')
                    if not model:raise ValueError('Modelo desconhecido.')
                    if not data.get('accepted'):raise ValueError('Confirme licença e tamanho do download.')
                    required={'gemma4':6*1024**3,'qwen36':18*1024**3}[data['model']]
                    if shutil.disk_usage(Path.home()).free<required:raise ValueError('Espaço livre insuficiente para o modelo.')
                    args=[binary,'pull',model] if data.get('runtime')=='ollama' and binary else [sys.executable,'-m','control.download_model',data['model'],str(manager.state/'models')]
                    result=manager.operations.launch(args,timeout=7200);manager.audit('model_install:'+data['model'])
                elif path=='/api/local-files':
                    from .local_models import processes
                    roots={Path(m['model_file']).parent for m in processes() if m.get('model_file')}
                    roots.add(manager.state/'models')
                    if data.get('folder'):
                        root=Path(data['folder']).expanduser().resolve()
                        if not root.is_dir() or root==Path('/'):raise ValueError('Escolha uma pasta de modelos existente.')
                        roots.add(root)
                    files=[]
                    for root in roots:
                        for file in list(root.glob('*.gguf'))[:200]:
                            if file.is_file() and not file.is_symlink():files.append({'path':str(file.resolve()),'name':file.name,'bytes':file.stat().st_size})
                    result={'files':files,'servers':processes()}
                elif path=='/api/local-start':
                    from .local_models import processes
                    active=processes()
                    if active:raise ValueError('Já existe um llama.cpp ativo. Ele será reutilizado; não iniciaremos outro modelo em paralelo.')
                    binary=data.get('binary') or shutil.which('llama-server')
                    model=Path(data.get('file','')).expanduser().resolve()
                    if not binary or not Path(binary).is_file() or Path(binary).name!='llama-server':raise ValueError('Informe o executável llama-server instalado.')
                    if not model.is_file() or model.suffix!='.gguf':raise ValueError('Selecione um arquivo GGUF existente.')
                    layers=int(data.get('gpu_layers',0))
                    if not 0<=layers<=999:raise ValueError('Camadas GPU inválidas.')
                    key=manager.state/'llama.key'
                    if not key.exists():key.write_text(secrets.token_urlsafe(32));key.chmod(0o600)
                    with socket.socket() as probe:
                        try:probe.bind(('127.0.0.1',8096))
                        except OSError:raise ValueError('Porta 8096 ocupada; o processo existente foi preservado.')
                    result=manager.operations.launch([str(binary),'--model',str(model),'--alias','managed-local','--host','127.0.0.1','--port','8096','--api-key-file',str(key),'--ctx-size','65536','--parallel','1','--jinja','--n-gpu-layers',str(layers)],timeout=None)
                    manager.audit('local_model_started')
                elif path=='/api/vpn-key':
                    key=manager.state/'vpn.key'
                    if not key.exists():raise ValueError('Inicie o harness primeiro para gerar a chave.')
                    result={'token':key.read_text()};manager.audit('vpn_key_revealed')
                elif path=='/api/tailnet':await manager.tailnet(data.get('enabled') is True);result=manager.status()
                else:return JSONResponse({'error':'Não encontrado'},404)
            return JSONResponse(result)
        except (ValueError,OSError,RuntimeError,KeyError) as exc:return JSONResponse({'error':str(exc)},400)
    app=Starlette(routes=[Route('/',endpoint),Route('/admin.js',endpoint),Route('/admin.css',endpoint),Route('/api/{path:path}',endpoint,methods=['GET','POST'])],lifespan=lifespan)
    @app.middleware('http')
    async def security(request,call_next):
        response=await call_next(request)
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"})
        return response
    app.state.manager=manager
    return app
