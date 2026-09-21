"""Local administration. Provider access and tailnet exposure require explicit choices."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from tail_ui import asset_response
from .dashboard import DashboardReader, execution as dashboard_execution
from pathlib import Path
import platform
import re
import secrets
import shutil
import socket
import sqlite3
import sys
import time
import uuid
from urllib.parse import urlparse
import httpx
from Adapters.claude.auth import cli_login_environment
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse,JSONResponse
from starlette.routing import Route
from .discovery import scan,command
from Adapters.deepseek import account as deepseek
from Adapters.gemini import account as gemini
from .integrations import inventory
from .integration_catalog import catalog as integration_catalog, installed_plugins
from .operations import Operations,operation
from .local_models import load_profile,load_profiles,save_profile,launch_command,validate_profile,runtime_permissions,runtime_roots,runtime_details
import ipaddress
from Adapters.codex.rpc import metadata

ROOT=Path(__file__).resolve().parents[1]
LOCAL_AI=Path(os.environ.get('TAIL_HARNESS_ROOT',str(ROOT)))/'local-ai'
PERMISSIONS=('read','write','upload','tests','internet','shell','hooks')
ADMIN_BODY_LIMIT=64000
ADMIN_BODY_TIMEOUT=10
ADMIN_OPERATION_LIMIT=4

class Manager:
    def __init__(self,state):
        self.state=Path(state);self.state.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path=self.state/'settings.json';self.cookie=secrets.token_urlsafe(32)
        self.admin_port=8094
        self.dashboard=DashboardReader(self.state)
        self.inventory=None;self.plugin_catalog=None;self.proc=None;self.lock=asyncio.Lock()
        self.settings=json.loads(self.path.read_text()) if self.path.exists() else {
            'services':{p:{'enabled':False,'models':[],'projects':['sem-projeto'],'mode':'native' if p in ('local','deepseek','gemini') else 'scoped','integrations':[],'permissions':{k:False for k in PERMISSIONS}} for p in ('codex','claude','gemini','local','deepseek')},
            'projects':[],'uploads_enabled':False,'port':8095,'tailnet_port':8095,'logins':[]}
        self.settings['services'].setdefault('deepseek',{'enabled':False,'added':False,'models':[],'projects':['sem-projeto'],'mode':'native','integrations':[],'permissions':{k:False for k in PERMISSIONS}})
        self.settings['services'].setdefault('gemini',{'enabled':False,'added':False,'models':[],'projects':['sem-projeto'],'mode':'native','integrations':[],'permissions':{k:False for k in PERMISSIONS}})
        self.provider_models={};self.auth={};self.applied=None;self.operations=Operations();self.startup_error=None;self.provider_revisions={}

    def audit(self,action):
        with (self.state/'audit.jsonl').open('a') as out:out.write(json.dumps({'time':time.time(),'action':action})+'\n')

    async def refresh(self):
        self.inventory=await scan()
        binary=self.inventory.get('binaries',{}).get('codex')
        if binary:
            plugins=await installed_plugins(binary)
            if plugins is not None:self.plugin_catalog=plugins
            else:self.inventory['integration_warnings']=['Não foi possível confirmar os plugins instalados; mantido o último inventário conhecido.']
        return self.inventory

    def integrations(self):
        result=inventory()
        if self.plugin_catalog is not None:
            result['codex']=[item for item in result.get('codex',[]) if item.get('kind')!='plugin']+self.plugin_catalog
            result['local']=result['deepseek']=result['codex']
        return result

    def busy(self):
        db=self.state/'runs/jobs.sqlite3'
        if not db.exists():return False
        with sqlite3.connect(db) as c:return bool(c.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone())

    def running(self):return self.proc is not None and self.proc.returncode is None

    async def claude_login_completed(self):
        # No token is copied or stored here. Claude owns the renewed credential.
        marker=self.state/'claude-cli-login'
        marker.touch(mode=0o600)
        runtime=self._previous_runtime()
        if 'claude' in runtime:
            runtime['claude']['use_cli_login']=True
            self._write_runtime(runtime)
        self.auth['claude']=False
        self.audit('claude_login_completed')

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
        out['uploads_enabled']=data.get('uploads_enabled') is True or any(data.get('services',{}).get(p,{}).get('enabled') is True for p in ('codex','claude','gemini','deepseek'))
        out['maestro_enabled']=data.get('maestro_enabled',True) is True
        policy=data.get('maestro_instructions','')
        if not isinstance(policy,str) or len(policy)>12000:raise ValueError('Instruções do Maestro: máximo de 12 mil caracteres.')
        out['maestro_instructions']=policy
        default=data.get('default_backend','')
        if default not in ('','codex','claude','gemini','local','deepseek'):raise ValueError('Executor padrão inválido.')
        out['default_backend']=default
        projects=[];ids=set()
        for project in data.get('projects',[]):
            pid=project.get('id','');label=project.get('label','');raw=Path(project.get('root','')).expanduser()
            if not re.fullmatch('[a-z0-9_-]{1,64}',pid) or pid=='sem-projeto' or pid in ids:raise ValueError('Identificador de projeto inválido ou repetido.')
            if not raw.is_absolute() or not raw.is_dir():raise ValueError('Escolha uma pasta existente e absoluta.')
            root=raw.resolve();home=Path.home().resolve()
            forbidden=[Path('/'),home,home/'.ssh',home/'.codex',home/'.claude',home/'.gemini',home/'.config',self.state.resolve()]
            if root in forbidden or any(root.is_relative_to(x) or x.is_relative_to(root) for x in forbidden[2:]):raise ValueError('Pasta ampla ou de credenciais não pode ser compartilhada.')
            if len(label)>100:raise ValueError('Nome de projeto muito longo.')
            units=project.get('service_units',[])
            if not isinstance(units,list) or len(units)>20 or any(not isinstance(u,str) or not re.fullmatch(r'[A-Za-z0-9_@.][A-Za-z0-9_@.-]*\.service',u) for u in units):raise ValueError('Serviços devem ser nomes de unidades .service do usuário.')
            overrides=project.get('permissions',{})
            if not isinstance(overrides,dict) or set(overrides)-set(PERMISSIONS) or any(type(value) is not bool for value in overrides.values()):raise ValueError('Permissões do projeto devem ser valores booleanos conhecidos; omita para herdar.')
            projects.append({'id':pid,'label':label or pid,'root':str(root),'service_units':list(dict.fromkeys(units)),'permissions':dict(overrides)});ids.add(pid)
        out['projects']=projects
        out['services']={}
        for provider in ('codex','claude','gemini','local','deepseek'):
            spec=data.get('services',{}).get(provider,{})
            models=spec.get('models',[]);allowed_projects=spec.get('projects',[])
            if not isinstance(models,list) or len(models)>50 or any(not isinstance(x,str) or not re.fullmatch('[a-zA-Z0-9_./:-]{1,160}',x) for x in models):raise ValueError('Lista de modelos inválida.')
            if not isinstance(allowed_projects,list) or any(p not in ids|{'sem-projeto'} for p in allowed_projects):raise ValueError('Projeto não cadastrado.')
            if not isinstance(spec.get('permissions',{}),dict):raise ValueError('Permissões inválidas.')
            perms={k:(provider!='local' or spec.get('permissions',{}).get(k) is True) for k in PERMISSIONS}
            if perms['write'] and not perms['read']:raise ValueError('Para permitir alterações, habilite também leitura.')
            if provider=='local' and perms['upload'] and not out['uploads_enabled']:raise ValueError('Habilite uploads globais antes de permitir anexos no serviço.')
            mode='native'
            selected=spec.get('integrations',[])
            available={x['id'] for x in self.integrations().get(provider,[])}
            if not isinstance(selected,list) or any(x not in available for x in selected):raise ValueError('Integração não encontrada. Atualize o inventário.')
            if selected and (mode!='native' or not perms['internet']):raise ValueError('Conectores exigem modo nativo e internet nesta versão.')
            enabled=spec.get('enabled') is True
            allowed_projects=['sem-projeto',*[p['id'] for p in projects]]
            if enabled and not models:raise ValueError('Selecione modelos para o serviço habilitado.')
            out['services'][provider]={'added':spec.get('added') is True or enabled or bool(models),'mode':mode,'integrations':selected,'enabled':enabled,'models':list(dict.fromkeys(models)),'projects':allowed_projects,'permissions':perms}
        logins=data.get('logins',[])
        if not isinstance(logins,list) or len(logins)>50 or any(not isinstance(x,str) or not re.fullmatch('[A-Za-z0-9_.+@-]{1,160}',x) for x in logins):raise ValueError('Identidades Tailscale inválidas.')
        defaults=data.get('mcp_defaults') or {}
        if not isinstance(defaults,dict) or set(defaults)-{'backend','model','effort'}:raise ValueError('Padrões MCP inválidos.')
        if defaults:
            backend=defaults.get('backend');model=defaults.get('model');effort=defaults.get('effort','')
            if not isinstance(backend,str) or backend not in out['services']:raise ValueError('Escolha um provedor MCP válido.')
            spec=out['services'][backend]
            if not spec['enabled'] or model not in spec['models']:raise ValueError('O modelo padrão MCP precisa estar habilitado.')
            if effort not in ('','configured','none','minimal','low','medium','high','xhigh','max','ultra'):raise ValueError('Esforço padrão MCP inválido.')
            supported=self.provider_models.get(backend,{}).get(model)
            if supported and effort and effort not in supported:raise ValueError('Esforço não disponível para o modelo padrão.')
            defaults={'backend':backend,'model':model,'effort':effort}
        out['mcp_defaults']=defaults
        out['logins']=list(dict.fromkeys(logins))
        return out

    def save(self,data):
        settings=self.validate(data)
        if (self.state/'tailnet.json').exists() and any(settings.get(k)!=self.settings.get(k) for k in ('port','tailnet_port','vpn_bind')):raise ValueError('Retire a rota Tailscale antes de mudar as portas ou o IP.')
        tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(settings,indent=2));tmp.chmod(0o600);tmp.replace(self.path)
        self.settings=settings;self.audit('settings_saved')

    def _previous_runtime(self):
        path = self.state / "runtime.json"
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {}

    def _write_runtime(self, config):
        path = self.state / "runtime.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(config))
        tmp.chmod(0o600)
        tmp.replace(path)

    def browser_url(self, settings):
        host = (self.inventory or {}).get('network', {}).get('hostname')
        if host and settings.get('logins') and (self.state / 'tailnet.json').exists():
            return f'http://{host}:{settings["tailnet_port"]}/'
        return None

    async def build_runtime_config(self, settings, allow_empty=False):
        """Build the process-owned configuration without starting or restarting it."""
        if self.inventory is None:
            await self.refresh()
        settings = self.validate(settings)
        cfg = {
            "browser_url": self.browser_url(settings),
            "state_dir": str(self.state / "runs"),
            "projects": {"sem-projeto": {"label": "Sem projeto"}},
            "clients": {},
            "services": json.loads(json.dumps(settings["services"])),
            "origins": [],
            "uploads_enabled": settings["uploads_enabled"],
            "mcp_defaults": settings.get("mcp_defaults", {}),
            "default_backend": settings.get("default_backend", ""),
            "maestro_enabled": settings.get("maestro_enabled", True),
            "maestro_instructions": settings.get("maestro_instructions", ""),
            "shared_projects": True,
            "control_state_dir": str(self.state),
            "admin_url": f"http://127.0.0.1:{self.admin_port}/",
            "local_access": settings.get("vpn_bind", "127.0.0.1") == "127.0.0.1",
            "bind": settings.get("vpn_bind", "127.0.0.1"),
            "port": settings["port"],
            "config_revision": str(uuid.uuid4()),
            "provider_revisions": getattr(self, "provider_revisions", {}),
        }
        for project in settings["projects"]:
            cfg["projects"][project["id"]] = {
                **project,
                "test_commands": {},
                "additional_roots": [],
                "node_binary": shutil.which("node") or "node",
            }
        enabled = 0
        for provider, spec in settings["services"].items():
            if not spec["enabled"]:
                continue
            enabled += 1
            checked = await self.check(provider)
            info = next(s for s in self.inventory["services"] if s["id"] == provider)
            if provider == "deepseek":
                if any(m not in checked["models"] for m in spec["models"]):
                    raise ValueError("Modelo DeepSeek não disponível na conta.")
                cfg[provider] = {
                    "binary": info["binary"],
                    "api_provider": {
                        "url": deepseek.API,
                        "key_file": str(deepseek.key_file(self.state)),
                    },
                    "integrations": spec.get("integrations", []),
                }
                cfg["deepseek_models"] = {m: checked["models"][m] for m in spec["models"]}
                continue
            # A pending native Claude login is an account condition; it must
            # not prevent the UI and other configured providers from starting.
            pending_claude_login = provider == 'claude' and spec.get('mode') == 'native'
            if (not checked["authenticated"] and not pending_claude_login) or (
                spec.get("mode") == "scoped" and not Path(info["auth_file"]).is_file()
            ):
                raise ValueError(
                    "Faça login em "
                    + provider
                    + " e use autenticação por arquivo local. Keychain não é suportado pelo sandbox atual."
                )
            binary = Path(info["binary"]).resolve()
            with binary.open("rb") as stream:
                native = stream.read(4) == b"\x7fELF"
            if not native and spec.get("mode") == "scoped":
                raise ValueError(
                    "Use o binário Linux nativo de "
                    + provider
                    + "; wrappers de shell/npm não são montados no sandbox."
                )
            if provider in ("codex", "gemini") and any(
                m not in checked["models"] for m in spec["models"]
            ):
                raise ValueError("Modelo não retornado pelo catálogo do provedor atual.")
            cfg[provider] = {
                "binary": str(binary),
                "auth_file": info["auth_file"],
                "python": sys.executable,
                "integrations": spec.get("integrations", []),
            }
            if provider == 'claude' and (self.state/'claude-cli-login').exists():
                cfg[provider]['use_cli_login'] = True
            if provider == "local":
                if any(m not in checked["models"] for m in spec["models"]):
                    raise ValueError(
                        "Modelo local não está disponível. Atualize a descoberta."
                    )
                cfg[provider]["local_provider"] = "ollama"
                cfg[provider]["local_models"] = {
                    m["id"]: m for m in info.get("runtimes", [])
                }
                cfg["services"][provider]["model_permissions"] = runtime_permissions(
                    self.state, info.get("runtimes", []), spec["models"]
                )
                cfg[provider]["model_roots"] = runtime_roots(
                    self.state, info.get("runtimes", []), spec["models"]
                )
                if any(
                    permissions.get("upload")
                    for permissions in cfg["services"][provider][
                        "model_permissions"
                    ].values()
                ):
                    cfg["uploads_enabled"] = True
            cfg[provider + "_models"] = (
                {m: checked["models"][m] for m in spec["models"]}
                if provider == "codex"
                else spec["models"]
            )
        if not enabled and not allow_empty:
            raise ValueError("Habilite pelo menos um serviço.")
        defaults = cfg.get("mcp_defaults") or {}
        if defaults:
            backend = defaults["backend"]
            model = defaults["model"]
            catalog = cfg.get(backend + "_models", {})
            supported = (
                catalog.get(model, []) if isinstance(catalog, dict) else ["configured"]
            )
            if defaults.get("effort") and defaults["effort"] not in supported:
                raise ValueError("Verifique o esforço padrão MCP antes de iniciar.")
        for provider in ("codex", "claude", "deepseek"):
            if provider in cfg:
                cfg[provider]["unrestricted"] = True
                if provider in ('codex','deepseek'):
                    cfg[provider]['plugin_inventory']=[item['id'] for item in self.integrations().get(provider,[]) if item.get('kind')=='plugin']
        previous = self._previous_runtime()
        all_projects = list(cfg["projects"])
        vpnkey = self.state / "vpn.key"
        if not vpnkey.exists():
            vpnkey.write_text(secrets.token_urlsafe(48))
            vpnkey.chmod(0o600)
        clients = previous.get("clients", {})
        cfg["clients"]["vpn"] = {
            "sha256": clients.get("vpn", {}).get(
                "sha256", hashlib.sha256(vpnkey.read_text().encode()).hexdigest()
            ),
            "projects": all_projects,
        }
        cfg["clients"]["local"] = {
            "sha256": clients.get("local", {}).get(
                "sha256", hashlib.sha256(secrets.token_bytes(48)).hexdigest()
            ),
            "projects": all_projects,
        }
        cfg["tailscale_logins"] = {}
        for login in settings["logins"]:
            client = "tailnet-" + hashlib.sha256(login.encode()).hexdigest()[:16]
            cfg["clients"][client] = {
                "sha256": clients.get(client, {}).get(
                    "sha256", hashlib.sha256(secrets.token_bytes(48)).hexdigest()
                ),
                "projects": all_projects,
            }
            cfg["tailscale_logins"][login] = client
        port = settings["port"]
        host = self.inventory["network"].get("hostname")
        remote = settings["tailnet_port"]
        bind = settings.get("vpn_bind", "127.0.0.1")
        cfg["origins"] = [
            f"http://{bind}:{port}",
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
        ] + ([f"http://{host}:{remote}"] if host else [])
        return cfg

    async def apply_settings(self, data):
        settings = self.validate(data)
        # The new configuration may repair integrations removed from the CLI.
        current = {'port':self.settings.get('port',8095),
                   'vpn_bind':self.settings.get('vpn_bind','127.0.0.1')}
        if self.running() and any(
            settings.get(key) != current.get(key) for key in ("port", "vpn_bind")
        ):
            raise ValueError("Para mudar endereço ou porta, reinicie o harness.")
        if self.running():
            runtime = await self.build_runtime_config(settings, allow_empty=True)
            self.save(settings)
            self._write_runtime(runtime)
        else:
            self.save(settings)
            if any(spec.get("enabled") for spec in settings["services"].values()):
                await self.start()

    async def check(self,provider):
        if provider not in ('codex','claude','gemini','local','deepseek'):raise ValueError('Serviço desconhecido.')
        if self.inventory is None:await self.refresh()
        info=next(s for s in self.inventory['services'] if s['id']==provider)
        if not info['found']:raise ValueError('CLI não encontrado. Instale e entre pela ferramenta oficial.')
        if provider=='deepseek':
            self.auth[provider]=False
            result=await deepseek.check(self.state);self.provider_models[provider]=result['models'];self.auth[provider]=True;return result
        if provider=='local':
            self.provider_models[provider]={m:['configured'] for m in info.get('models',[])}
            self.auth[provider]=bool(info['found'])
            return {'authenticated':bool(info['found']),'models':self.provider_models[provider],'model_source':'Servidores locais detectados (llama.cpp / Ollama)'}
        if provider=='gemini':
            self.auth[provider]=False
            result=await gemini.check(info['binary'])
            self.provider_models[provider]=result.get('models',{})
            self.auth[provider]=result.get('authenticated') is True
            self.audit('provider_check:gemini')
            return result
        if provider=='codex':
            code,_=await command(info['binary'],'login','status');authenticated=code==0
            if authenticated:
                listing=await metadata(info['binary'],'model/list')
                self.provider_models[provider]={m['id']:[e['reasoningEffort'] for e in m.get('supportedReasoningEfforts',[])] or ['low'] for m in listing.get('data',[])}
        else:
            options={'env':cli_login_environment()} if (self.state/'claude-cli-login').exists() else {}
            code,raw=await command(info['binary'],'auth','status','--json',**options)
            try:authenticated=code==0 and json.loads(raw).get('loggedIn') is True
            except ValueError:authenticated=False
            # Official CLI aliases; entitlement is checked by the provider at execution.
            self.provider_models[provider]={m:['configured'] for m in ('sonnet','opus','haiku')}
        self.auth[provider]=authenticated;self.audit('provider_check:'+provider)
        return {'authenticated':authenticated,'models':self.provider_models.get(provider,{}),'model_source':'CLI model/list' if provider=='codex' else 'Aliases oficiais; disponibilidade depende da conta'}

    async def start(self):
        if self.running():return
        await self.refresh()
        self.settings=self.validate(self.settings)
        if any(s.get('enabled') and s.get('mode')=='scoped' for s in self.settings['services'].values()) and (platform.system()!='Linux' or not self.inventory['binaries']['bwrap']):raise ValueError('Modo isolado requer Linux e bubblewrap. Use o modo nativo em outra plataforma.')
        cfg=await self.build_runtime_config(self.settings)
        port=self.settings['port'];bind=self.settings.get('vpn_bind','127.0.0.1')
        with socket.socket() as check:
            check.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            try:check.bind((bind,port))
            except OSError:raise ValueError('Porta em uso. Escolha outra; nenhum serviço existente foi encerrado.')
        path=self.state/'runtime.json';self._write_runtime(cfg)
        log=(self.state/'harness.log').open('ab')
        self.proc=await asyncio.create_subprocess_exec(sys.executable,'-m','agent_service.app',cwd=ROOT,env={**os.environ,'LOCAL_AGENT_CONFIG':str(path)},stdout=log,stderr=log)
        log.close()
        async with httpx.AsyncClient(trust_env=False,timeout=1) as client:
            for _ in range(40):
                if self.proc.returncode is not None:raise ValueError('O serviço encerrou ao iniciar. Consulte o log local.')
                try:
                    # The browser entry may redirect to Tailscale; readiness is local.
                    if (await client.get(f'http://{bind}:{port}/ui.css')).status_code==200:break
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
        runtime = self._previous_runtime()
        if runtime:
            runtime['browser_url'] = self.browser_url(self.settings)
            self._write_runtime(runtime)

    def status(self):
        host=(self.inventory or {}).get('network',{}).get('hostname');port=self.settings['port']
        return {'running':self.running(),'busy':self.busy(),'applied_at':self.applied,
                'local_url':f'http://{self.settings.get("vpn_bind","127.0.0.1")}:{port}/','remote_url':f'http://{host}:{self.settings["tailnet_port"]}/' if host else None,
                'shared':(self.state/'tailnet.json').exists(),'version':'0.5.0','startup_error':self.startup_error}

def create_app(state,port=8094):
    manager=Manager(state);manager.admin_port=port
    @asynccontextmanager
    async def lifespan(app):
        await manager.refresh()
        if any(spec.get('enabled') for spec in manager.settings['services'].values()):
            try:await manager.start()
            except (ValueError,RuntimeError,OSError) as exc:manager.startup_error=str(exc)
        yield
        await manager.stop(force=True)
        await manager.operations.close()
    async def endpoint(request:Request):
        host=request.headers.get('host','')
        allowed=(f'127.0.0.1:{port}',f'localhost:{port}')
        if host not in allowed or (request.client is None or request.client.host not in ('127.0.0.1','::1','testclient')) or request.headers.get('tailscale-user-login'):
            return JSONResponse({'error':'Gestão disponível somente nesta máquina.'},403)
        origin=request.headers.get('origin')
        path=request.url.path
        # A clicked harness link may cross sites; only allow the initial document.
        navigation=(request.method=='GET' and path=='/'
                    and request.headers.get('sec-fetch-mode')=='navigate'
                    and request.headers.get('sec-fetch-dest')=='document'
                    and request.headers.get('sec-fetch-user')=='?1')
        if (origin and origin not in ['http://'+h for h in allowed]) or (request.headers.get('sec-fetch-site')=='cross-site' and not navigation):return JSONResponse({'error':'Origem não autorizada.'},403)
        if path=='/':
            r=FileResponse(ROOT/'control/index.html');r.set_cookie('admin',manager.cookie,httponly=True,samesite='strict');return r
        if path.startswith('/assets/'):return asset_response(path)
        if path in ('/admin.js','/admin.css'):return FileResponse(ROOT/'control'/path[1:])
        if not secrets.compare_digest(request.cookies.get('admin','').encode('utf-8'),manager.cookie.encode('utf-8')):return JSONResponse({'error':'Abra a gestão nesta máquina primeiro.'},401)
        try:
            if request.method=='GET':
                if path=='/api/folders':
                    def folders():
                        folder=Path(request.query_params.get('path') or Path.home()).expanduser()
                        if not folder.is_absolute():raise ValueError('Informe uma pasta com caminho absoluto neste servidor.')
                        try:
                            folder=folder.resolve(strict=True)
                            directories=[]
                            with os.scandir(folder) as entries:
                                for entry in entries:
                                    try:is_directory=entry.is_dir()
                                    except OSError:continue
                                    if is_directory:
                                        directories.append({'name':entry.name,'path':str(folder/entry.name)})
                                        if len(directories)>200:break
                        except PermissionError:raise ValueError('Não há permissão para abrir esta pasta. Escolha outra pasta.') from None
                        except (FileNotFoundError,NotADirectoryError):raise ValueError('A pasta não foi encontrada neste servidor. Escolha outra pasta.') from None
                        return {'path':str(folder),'parent':str(folder.parent) if folder.parent!=folder else None,'directories':sorted(directories[:200],key=lambda d:d['name'].casefold()),'truncated':len(directories)>200}
                    return JSONResponse(await asyncio.to_thread(folders))
                if path=='/api/dashboard':
                    job=request.query_params.get('job')
                    return JSONResponse(await asyncio.to_thread(dashboard_execution,manager.state,job) if job else await manager.dashboard.read())
                if path=='/api/state':return JSONResponse({'settings':manager.settings,'inventory':manager.inventory,'status':manager.status(),'authentication':manager.auth,'models':manager.provider_models,'integrations':manager.integrations(),'operations':list(manager.operations.jobs.values()),'local_profile':load_profile(manager.state),'local_profiles':load_profiles(manager.state),'credentials':{'deepseek':deepseek.key_file(manager.state).exists()}})
                return JSONResponse({'error':'Não encontrado'},404)
            if request.headers.get('x-harness-admin')!='1':raise ValueError('Cabeçalho administrativo obrigatório.')
            if manager.lock.locked():return JSONResponse({'error':'Outra operação administrativa está em andamento. Aguarde e tente novamente.'},429,headers={'Retry-After':'1'})
            async with manager.lock:
                length=request.headers.get('content-length')
                if length is not None:
                    if not length.isdecimal():raise ValueError('Tamanho do pedido inválido.')
                    if int(length)>ADMIN_BODY_LIMIT:return JSONResponse({'error':'Pedido muito grande.'},413)
                raw=bytearray()
                async with asyncio.timeout(ADMIN_BODY_TIMEOUT):
                    async for chunk in request.stream():
                        if len(raw)+len(chunk)>ADMIN_BODY_LIMIT:return JSONResponse({'error':'Pedido muito grande.'},413)
                        raw.extend(chunk)
                data=json.loads(raw or '{}')
                if not isinstance(data,dict):raise ValueError('O pedido deve ser um objeto JSON.')
                if path in ('/api/provider-login','/api/integration','/api/model-install','/api/local-start') and sum(j.get('state')=='running' for j in manager.operations.jobs.values())>=ADMIN_OPERATION_LIMIT:
                    return JSONResponse({'error':'Há operações em andamento. Aguarde uma conclusão antes de iniciar outra.'},429,headers={'Retry-After':'5'})
                if path=='/api/folders/create':
                    name=data.get('name','');parent=data.get('parent','')
                    if not isinstance(name,str) or not name.strip() or name!=name.strip() or name in ('.','..') or any(c in name for c in ('/','\\','\x00')) or len(name)>120:raise ValueError('Use um nome de pasta simples, sem barras, com até 120 caracteres.')
                    if not isinstance(parent,str) or not Path(parent).is_absolute():raise ValueError('Selecione a pasta onde criar o projeto.')
                    folder=Path(parent).resolve(strict=True)/name
                    try:await asyncio.to_thread(folder.mkdir)
                    except FileExistsError:raise ValueError('Já existe um item com esse nome. Escolha outro nome.') from None
                    except PermissionError:raise ValueError('Não há permissão para criar uma pasta neste local.') from None
                    result={'path':str(folder),'created':True}
                elif path=='/api/scan':result=await manager.refresh()
                elif path=='/api/check':result=await manager.check(data.get('provider'))
                elif path=='/api/provider-token':
                    if data.get('provider')!='deepseek':raise ValueError('Provedor API desconhecido.')
                    deepseek.store_key(manager.state,data.get('token'));manager.provider_revisions['deepseek']=str(uuid.uuid4());manager.auth['deepseek']=False;manager.provider_models.pop('deepseek',None)
                    if manager.running():await manager.apply_settings(manager.settings)
                    manager.audit('deepseek_key_saved');result={'saved':True}
                elif path=='/api/provider-delete':
                    provider=data.get('provider')
                    if provider not in manager.settings['services']:raise ValueError('Provedor desconhecido.')
                    import copy
                    draft=copy.deepcopy(manager.settings);draft['services'][provider].update(added=False,enabled=False,models=[],integrations=[],projects=['sem-projeto'],permissions={k:False for k in PERMISSIONS})
                    if draft.get('mcp_defaults',{}).get('backend')==provider:draft['mcp_defaults']={}
                    await manager.apply_settings(draft)
                    if provider=='deepseek':deepseek.key_file(manager.state).unlink(missing_ok=True)
                    manager.audit('provider_removed:'+provider);result={'removed':True}
                elif path=='/api/settings-export':
                    result={'format':'tail-harness-settings','version':1,'settings':manager.settings,'local_profile':load_profile(manager.state),'local_profiles':load_profiles(manager.state)}
                elif path=='/api/settings-import':
                    bundle=data.get('bundle',{})
                    if not isinstance(bundle,dict) or bundle.get('format')!='tail-harness-settings' or bundle.get('version')!=1:raise ValueError('Formato de configuração incompatível.')
                    imported=manager.validate(bundle.get('settings'))
                    profile=validate_profile(bundle.get('local_profile',{}))
                    profiles=bundle.get('local_profiles',{})
                    if not isinstance(profiles,dict):raise ValueError('Catálogo de perfis locais inválido.')
                    validated={}
                    for key,value in profiles.items():
                        item=validate_profile(value)
                        if not item or str(Path(key).expanduser().resolve())!=item['model_file']:raise ValueError('O perfil não corresponde ao arquivo de pesos informado.')
                        validated[item['model_file']]=item
                    if profile:validated.setdefault(profile['model_file'],profile)
                    if data.get('apply') is True:
                        for item in validated.values():save_profile(manager.state,item)
                        if profile:save_profile(manager.state,validated[profile['model_file']])
                        await manager.apply_settings(imported)
                        manager.audit('settings_imported')
                    result={'valid':True,'applied':data.get('apply') is True,'services':[p for p,s in imported['services'].items() if s['enabled']],'projects':len(imported['projects']),'local_profile':bool(validated),'local_profiles':len(validated)}
                elif path=='/api/settings':await manager.apply_settings(data);result={'saved':True}
                elif path=='/api/start':await manager.start();result=manager.status()
                elif path=='/api/stop':await manager.stop();result=manager.status()
                elif path=='/api/cancel-operation':
                    manager.operations.cancel(data.get('id'));result={'cancelled':True}
                elif path=='/api/provider-login':
                    provider=data.get('provider');binary=manager.inventory['binaries'].get(provider) if provider in ('codex','claude','gemini') else None
                    if not binary:raise ValueError('CLI não encontrado.')
                    command=[sys.executable,'-m','Adapters.gemini.account','--binary',binary] if provider=='gemini' else ([binary,'login','--device-auth'] if provider=='codex' else [binary,'auth','login'])
                    existing=next((j for j in manager.operations.jobs.values() if j.get('provider')==provider and j.get('kind')=='provider-login' and j['state']=='running'),None)
                    if existing:
                        result=existing
                    else:
                        options={'env':cli_login_environment(),'on_success':manager.claude_login_completed} if provider=='claude' else {}
                        result=manager.operations.launch(command,**options)
                        result.update(provider=provider,kind='provider-login')
                elif path=='/api/integration-catalog':
                    provider=data.get('provider')
                    if provider not in ('codex','claude'):raise ValueError('Provedor inválido.')
                    binary=manager.inventory['binaries'].get(provider)
                    if not binary:raise ValueError('CLI não instalado.')
                    result=await integration_catalog(provider,binary,inventory())
                elif path=='/api/integration':
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
                    LOCAL_AI.mkdir(parents=True,exist_ok=True)
                    if shutil.disk_usage(LOCAL_AI).free<required:raise ValueError('Espaço livre insuficiente para o modelo.')
                    args=[binary,'pull',model] if data.get('runtime')=='ollama' and binary else [sys.executable,'-m','control.download_model',data['model'],str(LOCAL_AI/'models')]
                    result=manager.operations.launch(args,timeout=7200);manager.audit('model_install:'+data['model'])
                elif path=='/api/local-import':
                    from .local_models import processes
                    active=processes()
                    if data.get('file'):
                        selected=str(Path(data['file']).expanduser().resolve())
                        active=[server for server in active if server.get('model_file') and str(Path(server['model_file']).expanduser().resolve())==selected]
                    if len(active)!=1:raise ValueError('É necessário um único servidor local ativo para importar o perfil.')
                    selected={k:active[0][k] for k in ('binary','model_file','mmproj_file','flags','performance') if k in active[0] and active[0][k]}
                    result=save_profile(manager.state,validate_profile(selected))
                    if manager.running():await manager.apply_settings(manager.settings)
                    manager.audit('local_profile_imported')
                elif path=='/api/local-profile':
                    profile=validate_profile(data)
                    if not profile:raise ValueError('Informe o modelo e o executável deste perfil.')
                    result=save_profile(manager.state,profile)
                    if manager.running():await manager.apply_settings(manager.settings)
                    manager.audit('local_profile_saved')
                elif path=='/api/local-devices':
                    result=await runtime_details(data.get('binary',''))
                elif path=='/api/local-files':
                    from .local_models import processes
                    roots={Path(m['model_file']).parent for m in processes() if m.get('model_file')}
                    roots.add(LOCAL_AI/'models')
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
                    model=Path(data.get('file','')).expanduser().resolve()
                    profile=load_profile(manager.state,model) if data.get('use_profile') else {}
                    if data.get('use_profile') and not profile:raise ValueError('Este modelo ainda não tem um perfil salvo. Configure-o antes de iniciar.')
                    if profile:profile=validate_profile(profile)
                    cpu_only=bool(profile) and profile.get('performance',{}).get('n-gpu-layers')=='0'
                    if active and not cpu_only:raise ValueError('Outro servidor local está ativo. Para preservá-lo, só é possível iniciar outro modelo com perfil explícito de CPU (0 camadas GPU).')
                    binary=data.get('binary') or profile.get('binary') or (str(LOCAL_AI/'runtime/llama-b11003/llama-server') if (LOCAL_AI/'runtime/llama-b11003/llama-server').is_file() else shutil.which('llama-server'))
                    if not binary or not Path(binary).is_file() or Path(binary).name!='llama-server':raise ValueError('Informe o executável llama-server instalado.')
                    if not model.is_file() or model.suffix!='.gguf':raise ValueError('Selecione um arquivo GGUF existente.')
                    multigpu=set(profile.get('performance',{}))&{'main-gpu','split-mode','tensor-split'}
                    if multigpu:
                        details=await runtime_details(binary)
                        unsupported=multigpu-set(details['supported_flags'])
                        if unsupported:raise ValueError('Este runtime não confirmou suporte a: '+', '.join(sorted(unsupported)))
                    layers=int(data.get('gpu_layers',0))
                    if not 0<=layers<=999:raise ValueError('Camadas GPU inválidas.')
                    from .start_local import ensure_key
                    key=ensure_key(LOCAL_AI/'config/api-key')
                    with socket.socket() as probe:
                        probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                        try:probe.bind(('127.0.0.1',8096))
                        except OSError:raise ValueError('Porta 8096 ocupada; o processo existente foi preservado.')
                    launch_profile={**profile,'binary':str(binary)} if profile else {'binary':str(binary),'model_file':str(model),'performance':{'ctx-size':'65536','parallel':'1','n-gpu-layers':str(layers)}}
                    result=manager.operations.launch(launch_command(launch_profile,key_file=key),timeout=None)
                    manager.audit('local_model_started')
                elif path=='/api/vpn-key':
                    key=manager.state/'vpn.key'
                    if not key.exists():raise ValueError('Inicie o harness primeiro para gerar a chave.')
                    result={'token':key.read_text()};manager.audit('vpn_key_revealed')
                elif path=='/api/tailnet':await manager.tailnet(data.get('enabled') is True);result=manager.status()
                else:return JSONResponse({'error':'Não encontrado'},404)
            return JSONResponse(result)
        except TimeoutError:return JSONResponse({'error':'O envio do pedido demorou demais. Tente novamente.'},408)
        except (TypeError,AttributeError,RecursionError):return JSONResponse({'error':'Estrutura do pedido inválida. Confira os campos enviados.'},400)
        except (ValueError,OSError,RuntimeError,KeyError) as exc:return JSONResponse({'error':str(exc)},400)
    app=Starlette(routes=[Route('/',endpoint),Route('/admin.js',endpoint),Route('/admin.css',endpoint),Route('/assets/{path:path}',endpoint),Route('/api/{path:path}',endpoint,methods=['GET','POST'])],lifespan=lifespan)
    @app.middleware('http')
    async def security(request,call_next):
        response=await call_next(request)
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"})
        return response
    app.state.manager=manager
    return app
