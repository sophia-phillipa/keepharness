'use strict';
const $=id=>document.getElementById(id);
let state,settings,working=false;
function say(text,error=false){$('feedback').hidden=false;$('feedback').textContent=text;$('feedback').classList.toggle('error',error);}
async function request(path,data){const r=await fetch('/api/'+path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Harness-Admin':'1'},body:JSON.stringify(data)});const value=await r.json();if(!r.ok)throw Error(value.error||'Falha na operação');return value;}
function dirty(){$('dirty').textContent='Alterações ainda não salvas';}
function element(tag,text,cls){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;}
function toggle(text,checked,change,detail){const label=element('label',undefined,'toggle-row');const input=element('input');input.type='checkbox';input.checked=checked;input.onchange=()=>{change(input.checked);dirty();};const span=element('span');span.append(element('strong',text));if(detail)span.append(element('small',detail));label.append(input,span);return label;}
function providerCard(info){
 const id=info.id,spec=settings.services[id],card=element('article',undefined,'provider-card');card.dataset.provider=id;
 const top=element('div',undefined,'provider-top');top.append(element('span',id==='claude'?'✳':id==='local'?'◈':'⌘','provider-logo'));
 const title=element('div',undefined,'provider-title');title.append(element('h3',info.name),element('small',id==='local'?'Inferência local · agente Codex':'CLI local · inferência na nuvem'));top.append(title,element('span',info.found?'Encontrado':'Não instalado','pill'+(info.found?' good':'')));card.append(top);
 const body=element('div',undefined,'provider-body'),meta=element('div',undefined,'provider-meta');meta.append(element('span',state.authentication[id]?(id==='local'?'● Servidor disponível':'● Autenticado'):info.credential_present?'Credencial encontrada · verificar':'Login não verificado'));
 const check=element('button',id==='local'?'Verificar modelos':'Verificar conta','button secondary');check.disabled=!info.found;check.onclick=()=>action(async()=>{const data=await request('check',{provider:id});state.authentication[id]=data.authenticated;state.models[id]=data.models;renderProviders();say(data.authenticated?'Conta verificada. Escolha os modelos que deseja disponibilizar.':'Use Entrar para abrir a autorização pelo painel e depois verifique novamente.');});meta.append(check);if(id!=='local'){const login=element('button','Entrar','button secondary');login.disabled=!info.found;login.onclick=()=>action(async()=>{await request('provider-login',{provider:id});say('Login iniciado. Acompanhe a autorização na seção de operações.');pollOperations();});meta.append(login);}body.append(meta);
 const label=element('label','Modelos disponibilizados','field-label');body.append(label);
 const choices=element('div',undefined,'model-list');const models=Object.keys(state.models[id]||{});for(const m of new Set([...models,...spec.models]))choices.append(toggle(m,spec.models.includes(m),yes=>{spec.models=yes?[...spec.models,m]:spec.models.filter(x=>x!==m);}));
 if(!choices.children.length)choices.append(element('p','Verifique a conta para listar os modelos.','hint'));body.append(choices);
 if(id==='claude')body.append(element('p','Sonnet, Opus e Haiku são aliases do CLI. O acesso real depende da sua conta.','hint'));
 const permissions=element('div',undefined,'permissions');for(const [key,name] of [['read','Ler pastas'],['write','Alterar arquivos'],['upload','Receber anexos'],['internet','Internet'],['shell','Terminal'],['hooks','Hooks do CLI']])permissions.append(toggle(name,!!spec.permissions[key],yes=>spec.permissions[key]=yes));body.append(permissions);
 const mode=element('label','Modo de execução'),select=element('select');for(const [v,label] of [['scoped','Isolado · ferramentas de projeto'],['native','CLI nativo · ferramentas e conectores']]){const o=element('option',label);o.value=v;select.append(o);}select.value=spec.mode||'scoped';select.onchange=()=>{spec.mode=select.value;dirty();};mode.append(select);body.append(mode);
 body.append(element('p','Modo nativo usa as permissões do CLI e os acessos próprios dos conectores. A leitura não fica limitada pelas pastas do modo isolado. Terminal, hooks e plugins podem ampliar leitura, escrita e rede conforme a política do CLI. Internet controla ferramentas web/rede do agente, não é um firewall para conectores.','hint'));
 const connectors=element('label','Conectores / plugins selecionados'),connectorList=element('div',undefined,'model-list');for(const c of state.integrations?.[id]||[]){connectorList.append(toggle(c.name,(spec.integrations||[]).includes(c.id),yes=>{spec.integrations=spec.integrations||[];spec.integrations=yes?[...spec.integrations,c.id]:spec.integrations.filter(x=>x!==c.id);}));}if(!connectorList.children.length)connectorList.append(element('p','Nenhuma integração cadastrada. Use a seção de conectores.','hint'));connectors.append(connectorList);body.append(connectors);
 card.append(body);const bottom=element('div',undefined,'provider-enabled');bottom.append(toggle('Disponibilizar este serviço',spec.enabled,yes=>spec.enabled=yes));card.append(bottom);return card;
}
function renderProviders(){$('provider-cards').replaceChildren(...state.inventory.services.map(providerCard));}
function renderProjects(){
 const projects=new Map();for(const p of state.inventory.projects)projects.set(p.path,{root:p.path,label:p.name,id:p.name.toLowerCase().replace(/[^a-z0-9_-]/g,'-')});for(const p of settings.projects)projects.set(p.root,p);
 const rows=[];const none=element('div',undefined,'project-row');none.append(element('span','◌'));const noneLabel=element('div');noneLabel.append(element('strong','Conversas sem projeto'),element('small','Sem pasta de projeto vinculada; modo nativo segue a política do CLI'));none.append(noneLabel);rows.push(none);
 for(const p of projects.values()){
  const row=element('div',undefined,'project-row'),selected=settings.projects.some(x=>x.root===p.root);
  const choice=toggle(p.label,selected,yes=>{if(yes){if(settings.projects.some(x=>x.id===p.id))p.id+='-'+Math.random().toString(36).slice(2,6);settings.projects.push(p);}else{settings.projects=settings.projects.filter(x=>x.root!==p.root);for(const s of Object.values(settings.services))s.projects=s.projects.filter(x=>x!==p.id);}renderProjects();},p.root);row.append(choice);
  const badges=element('div',undefined,'project-badges');if(selected)for(const [id,s] of Object.entries(settings.services))badges.append(toggle(id,s.projects.includes(p.id),yes=>s.projects=yes?[...s.projects,p.id]:s.projects.filter(x=>x!==p.id)));row.append(badges);rows.push(row);
 }
 $('project-list').replaceChildren(...rows);$('project-count').textContent=settings.projects.length;
}
function renderStatus(){const s=state.status;$('runtime-badge').textContent=s.running?'● Harness ativo':'● Harness parado';$('harness-state').textContent=s.running?'Ativo':'Parado';$('runtime-badge').classList.toggle('good',s.running);$('network-state').textContent=s.shared?'Compartilhado':'Local';$('network-detail').textContent=s.shared?'Apenas identidades autorizadas':'Compartilhamento desativado';$('tailscale-badge').textContent=state.inventory.network.online?'Conectado':'Desconectado';$('share').textContent=s.shared?'Retirar da Tailscale':'Habilitar na Tailscale';$('harness-link').textContent=s.shared?s.remote_url:s.local_url;$('harness-link').href=s.shared?s.remote_url:s.local_url;$('start').disabled=s.running;$('stop').disabled=!s.running;$('save').disabled=s.running;$('share').disabled=!state.inventory.network.online||(!s.running&&!s.shared);}
function render(){renderProviders();renderProjects();renderStatus();$('found-count').textContent=state.inventory.services.filter(x=>x.found).length;$('uploads').checked=settings.uploads_enabled;$('vpn-bind').value=settings.vpn_bind||'127.0.0.1';$('port').value=settings.port;$('tailnet-port').value=settings.tailnet_port;$('logins').value=settings.logins.join('\n');$('platform-note').textContent='Plataforma detectada: '+state.inventory.platform+'. Modo isolado requer Linux e bubblewrap. O modo nativo usa os mecanismos do CLI instalado.';}
async function load(){state=await request('state');settings=structuredClone(state.settings);render();}
async function action(fn){if(working)return;working=true;document.body.setAttribute('aria-busy','true');try{await fn();}catch(e){say(e.message,true);}finally{working=false;document.body.removeAttribute('aria-busy');}}
function collect(){settings.vpn_bind=$('vpn-bind').value.trim();settings.uploads_enabled=$('uploads').checked;settings.port=Number($('port').value);settings.tailnet_port=Number($('tailnet-port').value);settings.logins=$('logins').value.split('\n').map(x=>x.trim()).filter(Boolean);return settings;}
$('scan').onclick=()=>action(async()=>{state.inventory=await request('scan',{});renderProviders();renderProjects();renderStatus();say('Verificação concluída. Nenhuma permissão foi habilitada.');});
$('save').onclick=()=>action(async()=>{await request('settings',collect());$('dirty').textContent='Escolhas salvas';say('Configuração salva. Inicie o harness para aplicar.');});
$('start').onclick=()=>action(async()=>{await request('settings',collect());say('Verificando as contas e iniciando o harness…');await request('start',{});await load();say('Harness pronto. Abra o endereço de acesso.');});
$('stop').onclick=()=>action(async()=>{await request('stop',{});await load();say('Harness parado. O histórico foi preservado.');});
$('share').onclick=()=>action(async()=>{await request('tailnet',{enabled:!state.status.shared});await load();say(state.status.shared?'Acesso Tailscale habilitado.':'Rota deste harness retirada.');});
$('copy-link').onclick=()=>action(async()=>{await navigator.clipboard.writeText($('harness-link').href);say('Endereço copiado.');});
$('uploads').onchange=()=>{settings.uploads_enabled=$('uploads').checked;if(!settings.uploads_enabled)for(const s of Object.values(settings.services))s.permissions.upload=false;renderProviders();dirty();};
for(const id of ['port','tailnet-port','logins','vpn-bind'])$(id).oninput=dirty;
$('add-project').onclick=()=>$('folder-dialog').showModal();$('close-dialog').onclick=()=>$('folder-dialog').close();
$('folder-form').onsubmit=e=>{e.preventDefault();const label=$('folder-name').value.trim(),root=$('folder-path').value.trim(),id=label.toLowerCase().replace(/[^a-z0-9_-]/g,'-');if(settings.projects.some(p=>p.id===id||p.root===root)){say('Esse nome ou caminho já foi adicionado.',true);return;}settings.projects.push({id,label,root});renderProjects();dirty();$('folder-dialog').close();};
$('theme').onclick=()=>{document.documentElement.classList.toggle('light');localStorage.setItem('theme',document.documentElement.classList.contains('light')?'light':'dark');};if(localStorage.getItem('theme')==='light')document.documentElement.classList.add('light');
$('integration-run').onclick=()=>action(async()=>{
 const transport=$('integration-transport').value,source=$('integration-source').value.trim();
 const data={provider:$('integration-provider').value,action:$('integration-action').value,name:$('integration-name').value.trim(),transport,url:source};
 if(transport==='stdio'&&data.action==='connector_add')data.command=JSON.parse(source);
 await request('integration',data);say('Operação iniciada. Acompanhe o resultado abaixo.');pollOperations();
});
$('integration-refresh').onclick=()=>action(async()=>{const fresh=await request('state');state.integrations=fresh.integrations;renderProviders();say('Integrações atualizadas. Selecione-as no cartão do serviço.');});
$('install-model-button').onclick=()=>action(async()=>{await request('model-install',{model:$('install-model').value,accepted:$('model-consent').checked,runtime:$('install-runtime').value});say('Download iniciado. Acompanhe o progresso em Operações.');pollOperations();});
$('vpn-key').onclick=()=>action(async()=>{const value=await request('vpn-key',{});$('vpn-token').textContent=value.token;$('vpn-token').hidden=false;setTimeout(()=>{$('vpn-token').textContent='';$('vpn-token').hidden=true;},30000);});
let operationTimer;
async function pollOperations(){try{
 const fresh=await request('state');const jobs=fresh.operations||[];
 $('operations').replaceChildren(...jobs.map(j=>{const d=element('details');d.open=j.state==='running';d.append(element('summary','Operação '+j.id.slice(0,6)+' · '+j.state));const pre=element('pre',j.output||'Aguardando o CLI…');d.append(pre);
 for(const match of (j.output||'').matchAll(/https:\/\/[^\s<>"']+/g)){try{const url=new URL(match[0]);const link=element('a','Abrir autenticação ↗');link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';d.append(link);}catch{}}
 if(j.state==='running'){const cancel=element('button','Cancelar','button secondary');cancel.onclick=()=>action(async()=>{await request('cancel-operation',{id:j.id});pollOperations();});d.append(cancel);}return d;}));
 clearTimeout(operationTimer);if(jobs.some(j=>j.state==='running'))operationTimer=setTimeout(pollOperations,2000);
}catch(e){say(e.message,true);}}
action(async()=>{await load();pollOperations();});

$('local-files').onclick=()=>action(async()=>{const result=await request('local-files',{folder:$('model-folder').value});$('local-file').replaceChildren(...result.files.map(f=>{const o=element('option',f.name+' · '+(f.bytes/1024**3).toFixed(1)+' GiB');o.value=f.path;return o;}));if(result.servers[0])$('llama-binary').value=result.servers[0].binary;say(result.files.length+' arquivos encontrados. Servidores existentes: '+result.servers.length+'.');});
$('local-start').onclick=()=>action(async()=>{await request('local-start',{file:$('local-file').value,binary:$('llama-binary').value,gpu_layers:Number($('gpu-layers').value)});say('Servidor iniciando. Acompanhe Operações e atualize o inventário após o carregamento.');pollOperations();});
