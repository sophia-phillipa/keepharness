'use strict';
const $=id=>document.getElementById(id);
let state,settings,working=false,wizard=false,editing=null,unsaved=false;
let profileModel="",profileDirty=false,discoveredModelFiles=[];
function visibleProviders(){return state.inventory.services.filter(info=>info.id!=='gemini');}
function say(text,error=false){TailUI.notice($('feedback'),text,{error,persistent:!error&&/…$/.test(text)});}
let activeOperation=null,operationOpener=null;
async function request(path,data){
 const starts=['provider-login','integration','model-install','local-start'].includes(path)&&data!==undefined;
 if(starts){$('operation-dialog').showModal();$('operation-message').textContent='Iniciando operação…';$('operation-retry').hidden=true;activeOperation=null;$('operations').replaceChildren();$('operations-panel').hidden=true;}
 try{const result=await requestRaw(path,data);if(starts){activeOperation={path,data,id:result.id};$('operation-message').textContent='Operação iniciada. Acompanhe o progresso abaixo.';pollOperations();}return result;}
 catch(error){if(starts){$('operation-message').textContent=error.message;pollOperations();}throw error;}
}
async function requestRaw(path,data){
 let r;try{r=await fetch('/api/'+path,{...(data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Harness-Admin':'1'},body:JSON.stringify(data)}),signal:AbortSignal.timeout(30000)});}catch(e){throw Error(e.name==='TimeoutError'?'O servidor demorou a responder. A operação pode continuar no servidor; verifique o estado antes de tentar novamente.':'Não foi possível conectar ao servidor. Verifique se ele está ligado e tente novamente.');}
 let value;try{value=await r.json();}catch{throw Error('O servidor retornou uma resposta inesperada ('+r.status+'). Tente novamente após verificar o servidor.');}
 if(!r.ok)throw Error(({model_not_available:'O modelo selecionado não está disponível. Verifique os modelos do provedor e escolha novamente.',authentication_required:'Autentique sua conta e verifique novamente.',permission_denied:'Esta ação não tem permissão. Revise as permissões do provedor.'})[value.error]||value.error||'Não foi possível concluir. Verifique os dados e tente novamente.');return value;
}
function dirty(){unsaved=true;$('dirty').textContent='Alterações ainda não salvas';}
function element(tag,text,cls){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;if(cls?.includes('button')){el.classList.add('btn');if(cls.split(' ').includes('primary'))el.classList.add('btn-primary');}if(cls?.split(' ').includes('panel')||cls==='provider-card')el.classList.add('card');return el;}
function toggle(text,checked,change,detail){const label=element('label',undefined,'toggle-row');const input=element('input');input.type='checkbox';input.setAttribute('aria-label',text);if(detail)input.setAttribute('aria-description',detail);input.checked=checked;input.onchange=()=>{change(input.checked);dirty();};const span=element('span');span.append(element('strong',text));if(detail)span.append(element('small',detail));label.append(input,span);return label;}
function fieldHelp(input,text){
 const help=element('small',text,'field-help');help.id=input.id+'-help';
 input.setAttribute('aria-describedby',help.id);input.insertAdjacentElement('afterend',help);
}
function localModelLabel(id,info=visibleProviders().find(service=>service.id==='local')){
 const runtime=info?.runtimes?.find(item=>item.id===id);
 return runtime?.model_file?.split(/[\\/]/).pop()||runtime?.name||id;
}
function pendingAuthentication(id){return ['codex','claude','gemini','deepseek'].includes(id)&&state.authentication[id]===false;}
function providerIcon(id){if(id==='deepseek'){const mark=element('span','🐋','provider-mark');mark.setAttribute('aria-hidden','true');return mark;}return icon(({local:'layers',codex:'brand-openai',claude:'brand-claude',gemini:'brand-gemini'})[id]||'message');}
function providerName(info){return ({local:'Modelo Local via Codex',deepseek:'DeepSeek via Codex'})[info.id]||info.name;}
function providerModelName(id){return ({'deepseek-flash':'DeepSeek V4.1 Flash','deepseek-v4-pro':'DeepSeek V4 Pro'})[id]||id;}

function providerLoginButton(info){
 const button=element('button','Entrar / Renovar acesso','button secondary');
 button.setAttribute('aria-label','Entrar ou renovar acesso — '+providerName(info));
 button.disabled=!info.found;
 button.onclick=()=>action(async()=>{await request('provider-login',{provider:info.id});say('Conclua o login no navegador. Depois, clique em Verificar conta.');});
 return button;
}
function providerCheckButton(info){
 const button=element('button','Verificar conta','button secondary');
 button.setAttribute('aria-label','Verificar conta — '+providerName(info));button.disabled=!info.found;
 button.onclick=()=>action(async()=>{const data=await request('check',{provider:info.id});state.authentication[info.id]=data.authenticated;state.models[info.id]=data.models;renderProviders();say(data.authenticated?'Conta verificada.':'Seu acesso precisa ser renovado. Clique em Entrar / Renovar acesso.');});
 return button;
}

const effortLabels={configured:'Configuração do provedor',low:'Baixo',medium:'Médio',high:'Alto',xhigh:'Muito alto',max:'Máximo',ultra:'Ultra',none:'Sem raciocínio'};
function modelDescription(provider,model){
 const descriptions={
  'gpt-6-astra':'Modelo para tarefas complexas que exigem maior capacidade de análise.',
  'gpt-5.6-sol':'Modelo de uso geral para programação e trabalho cotidiano.',
  'gpt-5.6-terra':'Modelo equilibrado para tarefas de programação do dia a dia.',
  'gpt-5.6-luna':'Modelo voltado à rapidez e à economia em tarefas de programação.',
  'gpt-5.5':'Modelo da geração anterior para programação e tarefas gerais.'
 };
 const efforts=state.models[provider]?.[model]||[];
 return (descriptions[model]||'Modelo oferecido pela sua conta. O provedor não informa uma descrição neste painel.')+
 (efforts.filter(e=>e!=='configured').length?' Esforços disponíveis: '+efforts.filter(e=>e!=='configured').map(e=>effortLabels[e]||e).join(', ')+'.':'');
}
function providerCard(info){
 const id=info.id,spec=settings.services[id],card=element('article',undefined,'provider-card');card.dataset.provider=id;
 const top=element('div',undefined,'provider-top');const logo=element('span',undefined,'provider-logo');logo.append(providerIcon(id));top.append(logo);
 const title=element('div',undefined,'provider-title');title.append(element('h3',providerName(info)),element('small',id==='local'?'Inferência local · agente Codex':id==='deepseek'?'Sua chave · créditos DeepSeek · agente Codex':'CLI local · inferência na nuvem'));top.append(title,element('span',info.api?'API · chave própria':info.found?'Encontrado nesta máquina':'Não encontrado','pill'+(info.found?' good':'')));card.append(top);
 const body=element('div',undefined,'provider-body'),meta=element('div',undefined,'provider-meta');meta.append(element('span',state.authentication[id]?(id==='local'?'● Servidor disponível':'● Autenticado'):info.credential_present?'Credencial encontrada · verificar':id==='local'?'Servidor ainda não verificado':'Login não verificado'));
 const check=element('button',id==='local'?'Verificar modelos':'Verificar conta','button secondary');check.disabled=!info.found;check.hidden=id==='deepseek'&&!state.credentials?.deepseek;check.onclick=()=>action(async()=>{const data=await request('check',{provider:id});state.authentication[id]=data.authenticated;state.models[id]=data.models;renderProviders();renderProfile();say(data.authenticated?(id==='local'?'Servidor verificado. Escolha os modelos que deseja disponibilizar.':'Conta verificada. Escolha os modelos que deseja disponibilizar.'):(id==='local'?'O servidor local não respondeu. Inicie o modelo abaixo e clique em Verificar modelos.':'Primeiro, clique em Entrar e conclua o login no navegador. Depois, volte ao painel e clique em Verificar conta.'));});meta.append(check);if(['codex','claude','gemini'].includes(id))meta.insertBefore(providerLoginButton(info),check);body.append(meta);
 if(id==='local'||id==='deepseek'){const inference=id==='local'?'Quem responde é o modelo no seu servidor local.':'Quem responde é o modelo da API DeepSeek, usando seus créditos DeepSeek.';body.append(element('p',inference+' O Codex CLI é o motor que executa ferramentas e mantém a sessão; isso não usa um modelo OpenAI. Modelo e motor são escolhas distintas. Nesta integração, o motor disponível é o Codex. Outros motores, como Claude Code, dependem de uma integração compatível e ainda não estão disponíveis para esta opção.','hint'));}
 if(id==='local'&&(info.runtimes||[]).length){const servers=element('div',undefined,'detected-servers');servers.append(element('strong','Servidores encontrados'));for(const runtime of info.runtimes){const row=element('p',runtime.runtime+' · '+runtime.url+' · '+localModelLabel(runtime.id,info));servers.append(row);}body.append(servers);}
 if(id==='deepseek'){const tokenLabel=element('label','Sua chave API DeepSeek (BYOK)'),token=element('input');token.type='password';token.autocomplete='off';token.placeholder=state.credentials?.deepseek?'Chave salva · preencha somente para substituir':'Cole sua chave da plataforma DeepSeek';tokenLabel.append(token);const saveToken=element('button','Salvar chave e verificar','button primary');saveToken.onclick=()=>action(async()=>{if(!token.value.trim()&&!state.credentials?.deepseek)throw Error('Cole sua chave API DeepSeek antes de verificar.');if(token.value.trim()){await request('provider-token',{provider:id,token:token.value.trim()});state.credentials={...state.credentials,deepseek:true};token.value='';}const result=await request('check',{provider:id});state.models[id]=result.models||{};state.authentication[id]=result.authenticated===true;if(!result.authenticated)throw Error('A chave foi salva, mas a autenticação não foi confirmada. Confira a chave e tente verificar novamente.');state.credentials={...state.credentials,deepseek:true};renderProviders();say('DeepSeek verificado. '+(result.balance?.balance_infos||[]).map(b=>b.currency+' '+b.total_balance).join(' · '));});body.append(tokenLabel,saveToken,element('p','Salvar chave aplica a credencial imediatamente nesta máquina; Cancelar o cadastro não a remove. Conversas, arquivos e ferramentas enviados ao modelo consomem créditos DeepSeek.','hint'));}

 const label=element('p','Modelos disponibilizados','field-label');body.append(label,element('p','O provedor conecta sua conta; o modelo é a IA que responde. Marque os modelos disponíveis nas conversas. O esforço de raciocínio ajusta quanto o modelo analisa antes de responder e pode aumentar o tempo e o consumo.','hint'));
 const choices=element('div',undefined,'model-list');const models=id==='local'?(info.models||Object.keys(state.models[id]||{})):Object.keys(state.models[id]||{});for(const m of new Set([...models,...spec.models].filter(m=>TailUI.selectableModel(id,m))))choices.append(toggle(id==='local'?localModelLabel(m,info):providerModelName(m),spec.models.includes(m),yes=>{spec.models=yes?[...spec.models,m]:spec.models.filter(x=>x!==m);if(id==='local')renderProfile();},id==='local'?(localModelLabel(m,info)!==m?'Identificador do servidor: '+m:'Nome completo e quantização não informados pelo servidor.'):(state.authentication[id]===true&&!models.includes(m)?'Indisponível no catálogo atual. Desmarque e escolha outro modelo.':modelDescription(id,m))));
 if(!choices.children.length)choices.append(element('p',id==='local'?'Nenhum modelo disponível. Busque um arquivo local ou instale um modelo abaixo.':!info.found?'Instale o CLI deste provedor nesta máquina e clique em Verificar ambiente.':'Clique em Verificar conta para listar os modelos.','hint'));for(const row of choices.querySelectorAll('.toggle-row strong'))row.prepend(providerIcon(id));body.append(choices);
 if(id==='gemini')body.append(element('p','O Google encerrou o acesso deste CLI para contas individuais e assinatura AI Pro/Ultra. É necessária a migração para Antigravity; esse motor ainda não está integrado ao painel.','hint'));
 if(id==='claude')body.append(element('p','Sonnet, Opus e Haiku são aliases do CLI. O acesso real depende da sua conta.','hint'));
 if(id!=='local')body.append(toggle('Provedor preferencial',settings.default_backend===id,yes=>{settings.default_backend=yes?id:'';renderProviders();},'Usado na seleção automática quando não houver coordenação disponível. A escolha explícita de um modelo tem prioridade.'));
 const advanced=element('details',undefined,'advanced');advanced.append(element('summary','Acesso do provedor'),element('p','Codex, Claude e DeepSeek executam diretamente neste computador, sem sandbox, com acesso a pastas, arquivos, terminal e rede. Os controles de permissões são exclusivos dos modelos locais.','hint'));body.append(advanced);

 card.append(body);const bottom=element('div',undefined,'provider-enabled');bottom.append(toggle('Disponibilizar este serviço',spec.enabled,yes=>spec.enabled=yes,'Torna os modelos selecionados disponíveis nas conversas. Desativar mantém o cadastro e a conta conectada.'));if(!state.settings.services[id]?.added)card.append(bottom);return card;
}
function renderProviders(){
 $('provider-options').replaceChildren(...visibleProviders().filter(info=>!state.settings.services[info.id]?.added&&!state.settings.services[info.id]?.enabled&&!state.settings.services[info.id]?.models?.length).map(info=>{const button=element('button',undefined,'button secondary discovery-option');button.dataset.provider=info.id;const status=info.api?'API / BYOK':info.found?'Encontrado nesta máquina':'Não instalado';button.append(providerIcon(info.id));const copy=element('span');copy.append(element('strong',providerName(info)),element('small',status,'pill'+(info.found&&!info.api?' good':'')));button.append(copy);button.onclick=()=>{editing=info.id;renderProviders();showStep(0);$('wizard-back').focus();};return button;}));
 if(!$('provider-options').children.length)$('provider-options').append(element('p','Todos os provedores disponíveis já foram adicionados. Você pode editá-los na lista.','hint'));
 $('provider-options').hidden=!!editing;
 $('provider-discovery-heading').hidden=!!editing;
 const info=visibleProviders().find(x=>x.id===editing);$('provider-cards').replaceChildren(...(info?[providerCard(info)]:[]));$('permission-editor').replaceChildren();
 if(info){const advanced=$('provider-cards').querySelector('.advanced');advanced.open=true;if(info.id==='local'){advanced.hidden=true;$('permission-editor').append(element('p','As permissões de operações pertencem ao modelo selecionado acima. Os projetos são cadastrados na interface do Tail Harness e ficam disponíveis para todos os modelos.','hint'));}else $('permission-editor').append(advanced);
 renderIntegrationSelection();}
 renderDashboard();TailUI.decorate($('provider-dialog'));
}
function renderIntegrationSelection(){
 const host=$('integration-selection');host.replaceChildren();
 if(!['codex','claude'].includes(editing))return;
 const spec=settings.services[editing],available=state.integrations?.[editing]||[];
 for(const [kind,title,description] of [
  ['mcp','Conectores','Um conector MCP dá acesso às ferramentas de um serviço ou processo. Cada conexão pode exigir sua própria autorização.'],
  ['plugin','Plugins',editing==='claude'?'No Claude Code, plugins são pacotes que podem reunir skills, agentes, hooks e servidores MCP.':'No Codex, plugins são pacotes que podem reunir skills, aplicativos e servidores MCP.']]){
  const section=element('section',undefined,'provider-connectors panel');
  section.append(element('h3',title),element('p',description+' A seleção abaixo define o que o Tail Harness solicita ao CLI deste provedor nas conversas. Salve para aplicar.','hint'));
  const list=element('div',undefined,'model-list');
  for(const item of available.filter(item=>item.kind===kind)){
   const row=toggle(connectorLabel(item),(spec.integrations||[]).includes(item.id),yes=>{spec.integrations=yes?[...new Set([...(spec.integrations||[]),item.id])]:(spec.integrations||[]).filter(id=>id!==item.id);},kind==='plugin'?'Plugin instalado · carregar nas conversas':'Conector configurado · disponibilizar ferramentas');
   row.querySelector('strong').prepend(connectorIcon(item));list.append(row);
  }
  if(!list.children.length)list.append(element('p',kind==='plugin'?'Nenhum plugin instalado neste provedor.':'Nenhum conector configurado neste provedor.','hint'));
  section.append(list);host.append(section);
 }
}
const connectorIdentity={
 'node_repl':['Terminal JavaScript','⌨️'],
 'qwen-local-agent':['Agente local Qwen','🧠'],
 'codex-app-tools':['Ferramentas do Codex','codex'],
 sites:['Sites','🌐'],browser:['Navegador','🧭'],chrome:['Chrome','🌐'],
 'unified-computer-use':['Controle do computador','🖥️'],visualize:['Visualizações','📈'],
 documents:['Documentos','📝'],pdf:['PDF','📄'],spreadsheets:['Planilhas','📊'],
 presentations:['Apresentações','📽️'],'template-creator':['Criador de modelos','📐'],
 drive:['Google Drive','📁'],github:['GitHub','📦'],linear:['Linear','☑️']
};
function connectorKey(item){return String(item.name||item.id||'').replace(/^(?:mcp|plugin):/,'').split('@')[0];}
function connectorLabel(item){
 if(item.display_name||item.title)return item.display_name||item.title;
 const key=connectorKey(item);
 return connectorIdentity[key]?.[0]||key.replace(/[-_]+/g,' ').replace(/\b\w/g,char=>char.toUpperCase())||'Integração';
}
function connectorIcon(item){
 const mark=element('span',undefined,'connector-icon');mark.setAttribute('aria-hidden','true');
 const symbol=connectorIdentity[connectorKey(item)]?.[1]||(item.kind==='plugin'?'🧩':'🔌');
 if(symbol==='codex')mark.append(providerIcon('codex'));else mark.textContent=symbol;
 return mark;
}
function renderDashboard(){const configured=visibleProviders().filter(i=>{const s=state.settings.services[i.id];return s&&(s.added||s.enabled||s.models.length);});$('configured-providers').replaceChildren(...configured.map(info=>{const s=state.settings.services[info.id],card=element('article',undefined,'panel configured-card');card.classList.toggle('selected',editing===info.id);card.dataset.configuredProvider=info.id;const heading=element('div',undefined,'configured-heading'),mark=element('span',undefined,'provider-logo');mark.append(providerIcon(info.id));heading.append(mark,element('h3',providerName(info)));card.append(heading,element('span',pendingAuthentication(info.id)?'Autorização pendente':s.enabled?'Habilitado':'Desativado','pill'+(s.enabled&&!pendingAuthentication(info.id)?' good':'')),element('p',s.models.map(m=>info.id==='local'?localModelLabel(m,info):providerModelName(m)).join(' · ')||'Nenhum modelo selecionado','configured-models'));const actions=element('div',undefined,'card-actions');const edit=element('button','Editar','button secondary');edit.setAttribute('aria-label','Editar '+providerName(info));edit.onclick=()=>openWizard(info.id);const remove=element('button','Excluir','button secondary');remove.onclick=()=>action(async()=>{if(!confirm('Remover '+providerName(info)+' do painel? A conta e o CLI não serão desinstalados. A chave BYOK deste provedor será apagada.'))return;await request('provider-delete',{provider:info.id});await load();say('Provedor removido.');});edit.prepend(icon('edit'));remove.prepend(icon('trash'));actions.append(edit,remove);if(['codex','claude','gemini'].includes(info.id))actions.prepend(providerLoginButton(info),providerCheckButton(info));card.append(actions);
 const availability=toggle('Disponibilizar este serviço',s.enabled,()=>{},'Ative para usar os modelos nas conversas. Desative para pausar o uso sem apagar a configuração.');
 const checkbox=availability.querySelector('input');checkbox.setAttribute('aria-label','Disponibilizar '+providerName(info));
 checkbox.onchange=()=>{const enabled=checkbox.checked;if(working){checkbox.checked=s.enabled;return;}action(async()=>{checkbox.disabled=true;try{const saved=structuredClone(state.settings);saved.services[info.id].enabled=enabled;await request('settings',saved);await load({select:false});say(enabled?'Serviço ativado. Seus modelos estão disponíveis nas conversas.':'Serviço desativado. Sua configuração foi mantida.');}finally{checkbox.checked=s.enabled;checkbox.disabled=false;}});};
 card.append(availability);return card;}));if(!configured.length)$('configured-providers').append(element('p','Seu espaço está pronto. Adicione um provedor para escolher modelos e permissões.','empty-state'));}
function openWizard(provider=null){if((unsaved||profileDirty)&&!confirm('Descartar alterações não salvas, incluindo o perfil de CPU e GPU, e abrir outro provedor?'))return;settings=structuredClone(state.settings);unsaved=false;profileDirty=false;$('feedback').hidden=true;wizard=true;editing=provider;if(provider&&['codex','claude'].includes(provider)){const current=state.settings.services[provider];if(!current?.added&&!current?.enabled&&!current?.models?.length)settings.services[provider].integrations=(state.integrations?.[provider]||[]).map(item=>item.id);}step=0;renderProviders();renderProjects();showStep(0);$('wizard-title').textContent=provider?'Editar '+providerName(visibleProviders().find(i=>i.id===provider)):'Adicionar provedor';$('provider-dialog').hidden=false;renderProfile();$('wizard-feedback').append($('feedback'));TailUI.decorate();if(!provider)$('provider-options').querySelector('button')?.focus();}
function dashboard(){if((unsaved||profileDirty)&&!confirm('Descartar alterações não salvas, incluindo o perfil de CPU e GPU?'))return;profileDirty=false;wizard=false;editing=null;showStep(0);renderDashboard();}

function renderProjects(){
 $('project-list').replaceChildren(element('p','Adicione projetos na barra lateral do Tail Harness. Todos os modelos habilitados ficam disponíveis nos projetos cadastrados.','hint'));$('project-count').textContent=settings.projects.length;
}

function renderStatus(){const s=state.status;$('add-provider').disabled=false;$('wizard-content-lock').disabled=false;$('wizard-next').disabled=false;if(s.startup_error)say("Não foi possível retomar o harness: "+s.startup_error,true);$('runtime-badge').textContent=s.running?'● Harness ativo':'● Harness parado';$('runtime-badge').classList.toggle('good',s.running);$('open-harness').href=(s.shared?s.remote_url:s.local_url)||'#';$('save').disabled=false;}
function render(){renderProviders();renderProjects();renderStatus();renderProfile();renderMcpDefaults();showStep(step);$('found-count').textContent=visibleProviders().filter(x=>x.found).length;$('uploads').checked=settings.uploads_enabled;$('uploads').closest('.panel-bottom').hidden=true;$('platform-note').textContent='Plataforma detectada: '+state.inventory.platform+'. Modo isolado requer Linux e bubblewrap. O modo nativo usa os mecanismos do CLI instalado.';}
async function load({select=true}={}){state=await request('state');if(state.authentication.claude===false){try{const checked=await request('check',{provider:'claude'});state.authentication.claude=checked.authenticated;state.models.claude=checked.models;}catch{state.authentication.claude=null;}}settings=structuredClone(state.settings);unsaved=false;if(select){const available=visibleProviders().filter(i=>{const s=settings.services[i.id];return s&&(s.added||s.enabled||s.models.length);});editing=available.some(i=>i.id===editing)?editing:null;wizard=!!editing;}render();if(wizard)$('wizard-title').textContent='Editar '+(visibleProviders().find(i=>i.id===editing)?.name||'provedor');TailUI.decorate();}
async function action(fn){if(working)return;working=true;const trigger=document.activeElement?.closest('button');if(trigger&&!$('operation-dialog').open)operationOpener=trigger;const disabled=trigger?.disabled;const label=trigger?.textContent?.trim()||'Carregar painel';if(trigger)trigger.disabled=true;
 document.body.setAttribute('aria-busy','true');const busy=$('busy-status');busy.hidden=false;busy.replaceChildren(icon('refresh'),document.createTextNode(label+' · processando…'));if(trigger){trigger.setAttribute('aria-busy','true');trigger.classList.add('is-processing');}
 try{await fn();}catch(e){say(e.message,true);}finally{working=false;busy.hidden=true;document.body.removeAttribute('aria-busy');if(trigger?.isConnected){trigger.disabled=disabled;trigger.removeAttribute('aria-busy');trigger.classList.remove('is-processing');if(trigger===operationOpener&&!$('operation-dialog').open&&document.activeElement===document.body)trigger.focus();}if(state)renderStatus();}}

function collect(){settings.uploads_enabled=$('uploads').checked;return settings;}
$('scan').onclick=()=>action(async()=>{state.inventory=await request('scan',{});renderProviders();renderProjects();renderStatus();renderProfile();say('Verificação concluída. Nenhuma permissão foi habilitada.');});
$('save').onclick=()=>action(async()=>{
 if(profileDirty)throw Error('O perfil de CPU e GPU tem alterações pendentes. Use Salvar alterações deste perfil antes de salvar o provedor.');
 if(!editing)throw Error('Escolha um provedor.');
 const spec=settings.services[editing],catalog=Object.keys(state.models[editing]||{});
 if(spec.enabled&&pendingAuthentication(editing))throw Error('Autorização pendente. Use Entrar e Verificar conta, ou desative Disponibilizar este serviço para salvar apenas o cadastro.');
 if(spec.enabled&&editing!=='local'&&state.authentication[editing]===true&&spec.models.some(model=>!catalog.includes(model)))throw Error('Há modelos indisponíveis no catálogo atual. Desmarque-os e escolha um modelo disponível.');
 settings.services[editing].added=true;
 try{await request('settings',collect());}catch(e){TailUI.toast('Não foi possível salvar: '+e.message,{error:true});throw e;}
 unsaved=false;$('dirty').textContent='Configurações salvas';
 TailUI.toast(state.status.running?'Configurações salvas. Atualizando o harness ativo.':'Configurações salvas. O harness inicia automaticamente com um modelo habilitado.');
 try{await load();$('dirty').textContent='Configurações salvas';}catch(e){throw Error('As configurações foram salvas, mas não foi possível atualizar o painel. Recarregue a página.');}
});
$('uploads').onchange=()=>{settings.uploads_enabled=$('uploads').checked;if(!settings.uploads_enabled)for(const s of Object.values(settings.services))s.permissions.upload=false;renderProviders();dirty();};
let folderPickerTarget=null,folderPickerCurrent=null,folderPickerSequence=0;
async function browseFolders(path=''){
 const sequence=++folderPickerSequence;folderPickerCurrent=null;
 $('folder-picker-use').disabled=true;$('folder-picker-parent').disabled=true;
 $('folder-picker-error').hidden=true;$('folder-picker-list').replaceChildren();$('folder-picker-breadcrumb').replaceChildren();
 $('folder-picker-status').textContent='Carregando pastas do servidor…';$('folder-picker-list').setAttribute('aria-busy','true');
 try{
  const data=await request('folders'+(path?'?path='+encodeURIComponent(path):''));if(sequence!==folderPickerSequence)return;
  folderPickerCurrent=data;$('folder-picker-use').disabled=false;$('folder-picker-parent').disabled=!data.parent;
  const crumb=$('folder-picker-breadcrumb');let accumulated='';
  for(const [index,name] of ['/',...data.path.split('/').filter(Boolean)].entries()){
   accumulated=index?accumulated+'/'+name:'';const destination=accumulated||'/';
   const button=element('button',name,'button secondary');button.type='button';button.onclick=()=>browseFolders(destination);crumb.append(button);
  }
  for(const folder of data.directories){const button=element('button',undefined,'button secondary folder-picker-entry');button.type='button';button.append(icon('stack-2'),document.createTextNode(folder.name));button.title=folder.path;button.onclick=()=>browseFolders(folder.path);$('folder-picker-list').append(button);}
  $('folder-picker-status').textContent=data.truncated?'Mostrando as primeiras 200 subpastas. Abra uma pasta ou informe um caminho mais específico.':data.directories.length+' subpasta(s). '+(!data.directories.length?'Esta pasta não contém subpastas. Você pode escolhê-la.':'Abra uma subpasta ou escolha a pasta atual.');
 }catch(e){if(sequence!==folderPickerSequence)return;$('folder-picker-status').textContent='Não foi possível listar esta pasta.';$('folder-picker-error').textContent=e.message;$('folder-picker-error').hidden=false;}
 finally{if(sequence===folderPickerSequence)$('folder-picker-list').removeAttribute('aria-busy');}
}
function chooseFolder(target){folderPickerTarget=target;$('folder-picker').showModal();browseFolders(typeof target==='function'?'':$(target).value.trim());}
$('choose-model-folder').onclick=()=>chooseFolder('model-folder');
$('folder-picker-create').onclick=async()=>{if(!folderPickerCurrent)return;const button=$('folder-picker-create'),sequence=folderPickerSequence;button.disabled=true;$('folder-picker-error').hidden=true;try{const result=await request('folders/create',{parent:folderPickerCurrent.path,name:$('folder-picker-new-name').value.trim()});if(sequence!==folderPickerSequence)return;$('folder-picker-new-name').value='';await browseFolders(result.path);}catch(e){if(sequence!==folderPickerSequence)return;$('folder-picker-error').textContent=e.message;$('folder-picker-error').hidden=false;}finally{button.disabled=false;}};
$('folder-picker-home').onclick=()=>browseFolders();$('folder-picker-parent').onclick=()=>{if(folderPickerCurrent?.parent)browseFolders(folderPickerCurrent.parent);};
$('folder-picker-close').onclick=()=>$('folder-picker').close();$('folder-picker').addEventListener('close',()=>{folderPickerSequence++;});
$('folder-picker-use').onclick=()=>{if(!folderPickerCurrent||!folderPickerTarget)return;if(typeof folderPickerTarget==='function'){folderPickerTarget(folderPickerCurrent.path);$('folder-picker').close();return;}$(folderPickerTarget).value=folderPickerCurrent.path;$('folder-picker').close();$(folderPickerTarget).focus();};
$('theme').onclick=()=>$('appearance-dialog').showModal();$('appearance-close').onclick=()=>$('appearance-dialog').close();
$('integration-run').onclick=()=>action(async()=>{
 const transport=$('integration-transport').value,source=$('integration-source').value.trim();
 const data={provider:$('integration-provider').value,action:$('integration-action').value,name:$('integration-name').value.trim(),transport,url:source};
 if(transport==='stdio'&&data.action==='connector_add')data.command=JSON.parse(source);
 await request('integration',data);say('Operação iniciada. Acompanhe o resultado abaixo.');pollOperations();
});
$('integration-refresh').onclick=()=>action(async()=>{const fresh=await request('state');state.integrations=fresh.integrations;renderProviders();say('Integrações atualizadas. Selecione-as na área de conectores e plugins.');});
$('install-model-button').onclick=()=>action(async()=>{await request('model-install',{model:$('install-model').value,accepted:$('model-consent').checked,runtime:$('install-runtime').value});say('Download iniciado. Acompanhe o progresso em Operações.');pollOperations();});
let operationTimer,operationRequest=0;
async function pollOperations(){const sequence=++operationRequest;try{
 const fresh=await request('state');if(sequence!==operationRequest)return;$('operations-status').hidden=true;if(state&&JSON.stringify(state.authentication)!==JSON.stringify(fresh.authentication)){state.authentication=fresh.authentication;state.models=fresh.models;renderDashboard();}const allJobs=fresh.operations||[];const jobs=activeOperation?.id?allJobs.filter(j=>j.id===activeOperation.id):allJobs.filter(j=>j.state==='running');$('operations-panel').hidden=!jobs.length;const names={running:'Em andamento',completed:'Concluída',failed:'Falhou',cancelled:'Cancelada'};
 $('operations').replaceChildren(...jobs.map(j=>{const d=element('details');d.open=j.id===activeOperation?.id||['running','failed'].includes(j.state);d.className='operation-'+j.state;d.append(element('summary','Operação '+j.id.slice(0,6)+' · '+(names[j.state]||j.state)));const output=/Traceback \(most recent call last\)/.test(j.output||'')?'Não foi possível concluir a operação. Tente novamente; se persistir, verifique o serviço.':j.output;const pre=element('pre',output?.replace('[gemini_client_retired] ','')||'Aguardando o CLI…');d.append(pre);
 for(const match of (j.output||'').matchAll(/https:\/\/[^\s<>"']+/g)){try{const url=new URL(match[0]);const link=element('a',url.hostname==='antigravity.google'?'Abrir guia de migração em nova aba ↗':'Abrir autorização em nova aba ↗');link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';d.append(link);}catch{}}
 if(j.state==='running'){const cancel=element('button','Cancelar','button secondary');cancel.onclick=()=>action(async()=>{await request('cancel-operation',{id:j.id});pollOperations();});d.append(cancel);}return d;}));
 const current=jobs.find(j=>j.id===activeOperation?.id);if(current){$('operation-message').textContent=/gemini_client_retired/.test(current.output||'')?'Migração necessária: este cliente foi descontinuado pelo Google.':current.state==='completed'&&activeOperation?.path==='provider-login'?(activeOperation.data.provider==='claude'?'Login concluído. O estado da conta foi atualizado no painel.':'Login concluído. Feche esta janela e clique em Verificar conta.'):'Operação '+(names[current.state]||current.state).toLowerCase()+'.';$('operation-retry').hidden=!['failed','cancelled'].includes(current.state)||/gemini_client_retired/.test(current.output||'');}
 clearTimeout(operationTimer);if(jobs.some(j=>j.state==='running'))operationTimer=setTimeout(pollOperations,2000);
}catch(e){if(sequence!==operationRequest)return;$('operations-panel').hidden=false;TailUI.notice($('operations-status'),'Não foi possível atualizar as operações. Tentaremos novamente em 5 segundos.',{error:true});clearTimeout(operationTimer);operationTimer=setTimeout(pollOperations,5000);}}


$('local-files').onclick=()=>action(async()=>{const result=await request('local-files',{folder:$('model-folder').value});discoveredModelFiles=result.files;renderProfile();$('local-files-status').textContent=result.files.length+' arquivo(s) encontrado(s). Selecione um arquivo para configurar.';});
$('add-local-model').onclick=()=>{$('local-add').hidden=false;$('source-download').focus();};
$('close-local-add').onclick=()=>{$('local-add').hidden=true;$('add-local-model').focus();};
for(const mode of ['download','file'])$('source-'+mode).onclick=()=>{for(const choice of ['download','file'])$('source-'+choice).setAttribute('aria-pressed',String(mode===choice));$('local-download').hidden=mode!=='download';$('local-existing').hidden=mode!=='file';};
$('use-local-file').onclick=()=>{const file=$('local-file').value;if(!file){say('Busque e selecione um arquivo GGUF nesta máquina.',true);return;}if(profileDirty&&!confirm('Descartar alterações não salvas deste perfil?'))return;profileModel=file;profileDirty=false;renderProfile();$('local-add').hidden=true;editor.open=true;binaryInput.focus();};
$('local-start').onclick=()=>action(async()=>{if(profileDirty)throw Error('Salve o perfil deste modelo antes de iniciar.');await request('local-start',{file:profileModel,use_profile:true});say('Servidor iniciando. Acompanhe Operações e atualize o inventário após o carregamento.');pollOperations();});

const providerDialog=element('section');providerDialog.id='provider-dialog';providerDialog.setAttribute('aria-labelledby','wizard-title');
const workspace=element('div',undefined,'provider-workspace');$('configured-providers').before(workspace);const list=element('div',undefined,'provider-list');const listHeading=element('div',undefined,'provider-list-heading');listHeading.append(element('h2','Provedores'),$('add-provider'));list.append(listHeading,$('configured-providers'));workspace.append(list,providerDialog);const emptyInspector=element('section',undefined,'dashboard-live');emptyInspector.setAttribute('aria-label','Atividade e desempenho');emptyInspector.id='inspector-empty';workspace.append(emptyInspector);emptyInspector.innerHTML='<div class=section-heading><div><h2>Operação em tempo real</h2><p>Últimas 24 horas · <span id=dashboard-updated>consultando</span></p></div></div><div id=dashboard-metrics class=dashboard-metrics></div><section class=card><div class=panel-header><h3>Servidor local</h3><small>Uso global da máquina</small></div><div id=server-resources class=server-resources></div></section><section class=card><div class=panel-header><h3>Execuções · somente leitura</h3><small id=recent-count></small></div><div id=recent-runs></div></section><p class=hint>Saída por segundo: média da execução, incluindo ferramentas e espera pelo provedor. Não representa velocidade bruta da GPU. Tokens são apenas os reportados pelos serviços. Execuções encerradas saem desta lista após 30 minutos; o histórico é preservado.</p>';
const busyStatus=element('div');busyStatus.id='busy-status';busyStatus.hidden=true;busyStatus.setAttribute('role','status');$('main').prepend(busyStatus);
document.addEventListener('click',e=>{if(working&&e.target.closest('button')&&!e.target.closest('#feedback,#appearance-dialog')){e.preventDefault();e.stopImmediatePropagation();busyStatus.textContent='Aguarde a operação em andamento. As demais ações serão liberadas ao terminar.';}},true);
const wizardContent=element('fieldset',undefined,'wizard-content');wizardContent.id='wizard-content-lock';wizardContent.style.cssText='border:0;margin:0;min-width:0';
const inspectorTabs=element('div',undefined,'inspector-tabs');inspectorTabs.id='inspector-tabs';for(const [index,label] of ['Modelo e hardware','Permissões','Conectores e plugins'].entries()){const button=element('button',label,'button secondary');button.onclick=()=>showStep(index===2?3:index);inspectorTabs.append(button);}providerDialog.append($('wizard-heading'),inspectorTabs,wizardContent);
const wizardFeedback=element('div');wizardFeedback.id='wizard-feedback';wizardContent.append(wizardFeedback);
for(const id of ['providers','local-models','projects','integrations','review'])wizardContent.append($(id));
const wizardFooter=element('div',undefined,'wizard-footer');wizardFooter.append($('wizard-back'),$('wizard-progress'),$('wizard-next'),$('save'));providerDialog.append(wizardFooter);
const operationsPanel=element('section',undefined,'panel operations-panel');operationsPanel.id='operations-panel';operationsPanel.hidden=true;const operationStatus=element('div');operationStatus.id='operations-status';operationStatus.hidden=true;operationsPanel.append(element('h3','Operações em andamento e resultados'),operationStatus,$('operations'));const operationDialog=element('dialog');operationDialog.id='operation-dialog';operationDialog.setAttribute('aria-labelledby','operation-title');const title=element('h2','Operação');title.id='operation-title';const message=element('p');message.id='operation-message';message.setAttribute('role','status');const close=element('button','Fechar','button secondary');close.onclick=()=>operationDialog.close();const retry=element('button','Tentar novamente','button primary');retry.id='operation-retry';retry.hidden=true;retry.onclick=()=>{const previous=activeOperation;if(previous)action(()=>request(previous.path,previous.data));};operationDialog.append(title,message,operationsPanel,retry,close);document.body.append(operationDialog);const reopen=element('button','Operações em andamento e resultados','button secondary');reopen.onclick=()=>{operationOpener=reopen;operationDialog.showModal();pollOperations();};$('main').append(reopen);
operationDialog.addEventListener('close',()=>operationOpener?.focus());

function icon(name){return TailUI.icon(({layers:'stack-2',edit:'pencil'})[name]||name);}
for(const [id,name,text] of [['add-provider','plus','Adicionar provedor'],['scan','scan','Verificar ambiente']]){$(id).replaceChildren(icon(name),document.createTextNode(text));}
let step=0;
const steps=['providers','projects','network'];
function showStepBase(value){if(value!==step)$('feedback').hidden=true;step=Math.max(0,Math.min(3,value));$('dashboard').hidden=false;if(!wizard){$('provider-dialog').hidden=true;$('main').insertBefore($('feedback'),$('overview'));}$('wizard-heading').hidden=!wizard;$('inspector-empty').hidden=wizard;const existing=!!(editing&&(state.settings.services[editing]?.added||state.settings.services[editing]?.enabled));$('inspector-tabs').hidden=!existing;$('inspector-tabs').lastElementChild.hidden=!['codex','claude'].includes(editing);if(editing&&['codex','claude'].includes(editing)){$('integration-provider').value=editing;$('integration-provider').disabled=true;}[...$('inspector-tabs').children].forEach((b,i)=>b.setAttribute('aria-pressed',String((i===2?3:i)===step)));$('provider-dialog').hidden=!wizard;$('provider-dialog').classList.toggle('local-inspector',editing==='local');$('network').hidden=true;document.querySelectorAll('[data-step]').forEach(el=>el.hidden=!wizard||Number(el.dataset.step)!==step||(el.id==='local-models'&&editing!=='local'));if(editing==='local'){$('local-models').hidden=!wizard||![0,1].includes(step);$('local-profile-summary').hidden=step!==0;$('hardware-editor-details').hidden=step!==0||!profileModel;$('local-model-permissions').hidden=step!==1;$('local-launch').hidden=step!==0;$('local-import').hidden=step!==0||!(visibleProviders().find(i=>i.id==='local')?.runtimes||[]).some(r=>r.model_file===profileModel);$('add-local-model').hidden=step!==0;if(step!==0)$('local-add').hidden=true;}$('wizard-progress').textContent=wizard?'Etapa '+(step+1)+' de 3 · '+['Escolher serviço e modelos','Permissões','Revisar e concluir'][step]:'';$('wizard-back').hidden=!wizard||(step===0&&(!editing||existing));$('wizard-back').textContent=step===0?'Escolher outro provedor':'Voltar';$('wizard-next').hidden=!wizard||existing||step===2;$('save').hidden=!wizard||(!existing&&step!==2);$('save').textContent=existing?'Salvar alterações':'Concluir e salvar';$('wizard-progress').hidden=existing;$('save').title=state.status.running?'Aplicar alterações sem parar o harness.':'';if(editing){$('provider-review').textContent='Ao concluir, '+(visibleProviders().find(i=>i.id===editing)?.name||editing)+' será adicionado ao painel com os modelos selecionados.';}}
$('wizard-back').onclick=()=>{if(step===0){openWizard();$('provider-options').querySelector('button')?.focus();}else showStep(step===3?1:step-1);};$('wizard-next').onclick=()=>{if(!editing){say('Escolha um provedor para continuar.',true);return;}if(step===0&&!settings.services[editing].models.length){const info=visibleProviders().find(i=>i.id===editing);say(editing==='local'?'Busque ou instale um modelo local abaixo; depois verifique e selecione o modelo.':!info?.found&&!info?.api?'Instale o CLI nesta máquina e use Verificar ambiente antes de continuar.':'Verifique a conta e selecione pelo menos um modelo.',true);return;}showStep(step+1);};
$('add-provider').onclick=()=>action(async()=>{if((unsaved||profileDirty)&&!confirm('Descartar alterações não salvas, incluindo o perfil de CPU e GPU, para adicionar um provedor?'))return;unsaved=false;profileDirty=false;const button=$('add-provider');button.disabled=true;const label=button.textContent;button.textContent='Verificando…';try{state.inventory=await request('scan',{});openWizard();}finally{button.disabled=false;button.replaceChildren(icon('plus'),document.createTextNode(label));}});$('nav-dashboard').onclick=e=>{e.preventDefault();action(async()=>{if((unsaved||profileDirty)&&!confirm('Descartar alterações não salvas, incluindo o perfil de CPU e GPU?'))return;profileDirty=false;wizard=false;editing=null;await load({select:false});});};$('wizard-cancel').onclick=()=>action(async()=>{if((unsaved||profileDirty)&&!confirm('Descartar alterações não salvas, incluindo o perfil de CPU e GPU?'))return;profileDirty=false;wizard=false;editing=null;await load({select:false});say('Edição descartada.');});$('manage-network').onclick=()=>{$('network').hidden=!$('network').hidden;};
const hardwareFields=[['n-gpu-layers','Camadas na GPU'],['n-cpu-moe','Camadas MoE na CPU'],['cpu-range','CPUs lógicas (ex.: 2-7)'],['cpu-range-batch','CPUs no lote (ex.: 0-7)'],['threads','Threads'],['threads-batch','Threads no lote'],['ctx-size','Tamanho do contexto'],['device','GPU(s) do runtime (ex.: Vulkan0 ou CUDA0,CUDA1)'],['main-gpu','GPU principal (índice)'],['split-mode','Distribuição entre GPUs (none, layer ou row)'],['tensor-split','Proporção entre GPUs (ex.: 1,1)'],['parallel','Slots de processamento'],['flash-attn','Flash Attention (on, off, auto)'],['cache-type-k','Tipo do cache K'],['cache-type-v','Tipo do cache V'],['cache-ram','Cache RAM (MiB)'],['reasoning','Raciocínio (on, off, auto)'],['reasoning-format','Formato do raciocínio'],['reasoning-budget','Orçamento de raciocínio'],['load-mode','Modo de carregamento'],['cpu-strict','Afinidade estrita de CPU (0 ou 1)'],['cpu-strict-batch','Afinidade estrita no lote (0 ou 1)'],['temp','Temperatura'],['top-k','Top K'],['top-p','Top P'],['min-p','Min P'],['repeat-penalty','Penalidade de repetição'],['seed','Semente aleatória']];
const profilePanel=$('local-profile-summary').parentElement;
const profilePicker=element('select');profilePicker.id='hardware-model';profilePicker.className='form-select';
const profileLabel=element('label','Modelo deste perfil');profileLabel.htmlFor='hardware-model';profileLabel.append(profilePicker);
profilePanel.insertBefore(profileLabel,$('local-profile-summary'));
const editor=element('details',undefined,'advanced');editor.append(element('summary','Avançadas · execução deste modelo'));
const editorGrid=element('div',undefined,'hardware-editor');
const binaryLabel=element('label','Executável llama-server');const binaryInput=element('input');binaryInput.id='profile-binary';binaryInput.className='form-control';binaryLabel.append(binaryInput);editor.append(binaryLabel);
const descriptionLabel=element('label','Descrição deste perfil (opcional)'),descriptionInput=element('textarea');descriptionInput.id='profile-description';descriptionInput.className='form-control';descriptionInput.rows=4;descriptionInput.maxLength=2000;descriptionInput.placeholder='Anote a finalidade e as escolhas deste perfil.';descriptionLabel.append(descriptionInput);editor.append(descriptionLabel);
const hardwareHelp={"n-gpu-layers": "Número de camadas do modelo carregadas na GPU; 0 usa apenas CPU.", "n-cpu-moe": "Camadas de especialistas (MoE) mantidas na CPU para reduzir o uso de memória da GPU.", "cpu-range": "CPUs lógicas usadas na geração da resposta. Informe índices ou intervalos.", "cpu-range-batch": "CPUs lógicas usadas para processar a entrada em lote.", "threads": "Quantidade de threads da CPU usadas durante a geração.", "threads-batch": "Quantidade de threads da CPU usadas no processamento da entrada.", "ctx-size": "Limite de tokens de contexto: instruções, histórico e resposta compartilham esse espaço. Valores maiores exigem mais memória.", "device": "Identificadores das GPUs que o runtime usará. Consulte Detectar GPUs antes de preencher.", "main-gpu": "Índice da GPU principal entre os dispositivos selecionados.", "split-mode": "none usa uma GPU; layer distribui camadas; row distribui operações. O suporte depende do runtime.", "tensor-split": "Proporção de distribuição entre GPUs: 1,1 divide igualmente; 1,2 atribui mais à segunda.", "parallel": "Quantidade de slots de execução simultânea. Mais slots podem exigir mais memória.", "flash-attn": "Implementação otimizada da atenção. auto deixa o runtime decidir conforme o suporte.", "cache-type-k": "Formato de armazenamento das chaves da atenção. A quantização pode reduzir memória e afetar a qualidade.", "cache-type-v": "Formato de armazenamento dos valores da atenção. Depende do modelo e do runtime.", "cache-ram": "Limite do cache de contexto em RAM, em MiB. O comportamento depende do runtime.", "reasoning": "Ativa, desativa ou deixa automática a geração de raciocínio, quando suportada.", "reasoning-format": "Formato usado pelo runtime para separar raciocínio e resposta.", "reasoning-budget": "Orçamento de tokens de raciocínio. A interpretação de valores especiais depende do runtime.", "load-mode": "Modo de carregamento dos pesos oferecido por esta versão do runtime.", "cpu-strict": "1 restringe a execução às CPUs selecionadas; 0 permite o comportamento padrão do escalonador.", "cpu-strict-batch": "Aplica a restrição de CPU também ao processamento em lote.", "temp": "Controla a variação da resposta. Valores menores tendem a produzir escolhas mais previsíveis.", "top-k": "Limita a amostragem aos K tokens mais prováveis.", "top-p": "Seleciona candidatos até atingir esta probabilidade acumulada.", "min-p": "Descarta tokens cuja probabilidade é baixa em relação ao candidato mais provável.", "repeat-penalty": "Reduz a tendência de repetir tokens. 1 não aplica penalidade.", "seed": "Semente usada na amostragem; ajuda a reproduzir resultados sob as mesmas condições."};
for(const [key,label] of hardwareFields){const wrap=element('label',label),input=element('input');input.id='profile-'+key;input.className='form-control';input.placeholder='Padrão do runtime';wrap.append(input);fieldHelp(input,hardwareHelp[key]+' Em branco: padrão do runtime.');editorGrid.append(wrap);}
const detectDevices=element('button','Detectar GPUs e recursos do runtime','button secondary');detectDevices.id='profile-detect-devices';
const deviceStatus=element('div');deviceStatus.id='profile-device-status';deviceStatus.setAttribute('role','status');
const deviceOptions=element('datalist');deviceOptions.id='profile-device-options';editorGrid.querySelector('#profile-device').setAttribute('list',deviceOptions.id);
detectDevices.onclick=()=>action(async()=>{deviceStatus.textContent='Consultando dispositivos sem carregar pesos…';try{const result=await request('local-devices',{binary:binaryInput.value.trim()});deviceOptions.replaceChildren(...result.devices.map(d=>new Option(d.name,d.id)));deviceStatus.replaceChildren(element('p',result.devices.length?'Dispositivos anunciados pelo runtime:':'Nenhuma GPU anunciada pelo runtime. Use 0 camadas para CPU.'));for(const d of result.devices)deviceStatus.append(element('p',d.id+' · '+d.name));if(!result.capabilities_verified)deviceStatus.append(element('p','Não foi possível verificar todas as opções deste executável.','hint'));}catch(e){deviceStatus.textContent=e.message;throw e;}});
editor.append(detectDevices,deviceStatus,deviceOptions,element('p','Escolha os identificadores anunciados pelo runtime. Em múltiplas GPUs, separe dispositivos por vírgula; a proporção 1,1 divide igualmente, 1,2 destina duas partes à segunda GPU. O suporte depende do runtime. Trocar de placa exige revisar este perfil antes de iniciar.','hint'));
editor.append(editorGrid,element('p','Salvar altera apenas o perfil deste arquivo de pesos. Não modifica o modelo em execução nem as configurações dos demais modelos. Campos vazios usam o padrão do runtime.','hint'));
const profileSave=element('button','Salvar perfil deste modelo','button primary');profileSave.id='profile-save';profilePanel.append(editor,profileSave);
function markProfileDirty(){profileDirty=true;profileSave.textContent='Salvar alterações deste perfil';}
editor.addEventListener('input',markProfileDirty);
const localPermissions=$('local-model-permissions');
localPermissions.append(element('h3','Permissões deste modelo'),element('p','Estas escolhas pertencem ao arquivo selecionado. Não são herdadas por outras quantizações ou modelos.','hint'));
const toolsLabel=element('label',undefined,'toggle-row'),toolsInput=element('input');toolsInput.type='checkbox';toolsInput.id='profile-tools';toolsLabel.append(toolsInput,element('span','Permitir ferramentas · compatibilidade informada pelo administrador'));localPermissions.append(toolsLabel,element('p','O modelo e o runtime precisam aceitar chamadas de ferramentas. Esta escolha não é uma validação automática. Sem ela, internet, terminal e acesso às pastas ficam bloqueados. Anexos de texto podem ser usados como contexto.','hint'));
const modelPermissionFields=[['read','Ler pastas'],['write','Alterar arquivos'],['upload','Receber anexos'],['internet','Acessar internet'],['shell','Executar comandos'],['hooks','Hooks do CLI']];
const permissionHelp={"read": "Permite ler arquivos nas pastas autorizadas.", "write": "Permite criar e modificar arquivos nas pastas autorizadas.", "upload": "Permite usar arquivos enviados na conversa como contexto.", "internet": "Permite acesso à rede pelas ferramentas disponíveis.", "shell": "Permite executar comandos nas condições de isolamento do modelo.", "hooks": "Permite os hooks configurados no CLI, quando suportados."};
const permissionGrid=element('div',undefined,'permissions');
for(const [key,label] of modelPermissionFields){const row=element('label',undefined,'toggle-row'),input=element('input');input.type='checkbox';input.id='profile-permission-'+key;const copy=element('span');copy.append(element('strong',label),element('small',permissionHelp[key]));row.append(input,copy);permissionGrid.append(row);}
localPermissions.append(permissionGrid,element('p','Internet autoriza recursos de rede do agente; não oferece busca web por si só. Para modelos locais, o acesso depende de ferramentas disponíveis, como terminal ou conectores autorizados. As alterações serão aplicadas ao iniciar novamente o harness.','hint'));
function refreshPermissionAvailability(){for(const [key] of modelPermissionFields)$('profile-permission-'+key).disabled=key!=='upload'&&!toolsInput.checked;}
localPermissions.addEventListener('change',()=>{markProfileDirty();refreshPermissionAvailability();});
let modelRoots=[];
const modelRootsSection=element('section',undefined,'model-roots');modelRootsSection.append(element('h3','Pastas deste modelo · conversas sem projeto'),element('p','Sem projeto, o modelo usa somente as pastas adicionadas aqui, conforme as permissões escolhidas. Dentro de um projeto, a pasta e as permissões do projeto se somam às deste modelo.','hint'));
const modelRootsList=element('div');modelRootsList.id='model-roots-list';const modelRootsAdd=element('button','Adicionar pasta','button secondary');modelRootsAdd.id='model-roots-add';modelRootsAdd.type='button';
function renderModelRoots(){modelRootsList.replaceChildren();for(const root of modelRoots){const row=element('div',undefined,'model-root-row'),remove=element('button','Remover','button secondary');remove.type='button';remove.setAttribute('aria-label','Remover pasta '+root);remove.onclick=()=>{modelRoots=modelRoots.filter(path=>path!==root);renderModelRoots();markProfileDirty();};row.append(element('span',root),remove);modelRootsList.append(row);}if(!modelRoots.length)modelRootsList.append(element('p','Nenhuma pasta autorizada para conversas sem projeto.','hint'));}
modelRootsAdd.onclick=()=>chooseFolder(path=>{if(!modelRoots.includes(path)){modelRoots.push(path);renderModelRoots();markProfileDirty();}modelRootsAdd.focus();});modelRootsSection.append(modelRootsList,modelRootsAdd);localPermissions.append(modelRootsSection);
function profiles(){return state.local_profiles||(state.local_profile?.model_file?{[state.local_profile.model_file]:state.local_profile}:{});}

profilePicker.onchange=()=>{if(profileDirty&&!confirm('Descartar alterações não salvas deste perfil?')){profilePicker.value=profileModel;return;}profileModel=profilePicker.value;profileDirty=false;renderProfile();};
function renderProfile(){
 const saved=profiles(),runtimes=visibleProviders().find(x=>x.id==='local')?.runtimes||[];
 const paths=[...new Set([...runtimes.map(r=>r.model_file),...Object.keys(saved),...discoveredModelFiles.map(f=>f.path)].filter(Boolean))];
 if(!paths.includes(profileModel))profileModel=paths[0]||'';
 profilePicker.replaceChildren(...paths.map(path=>new Option(path.split('/').pop()+(saved[path]?' · Perfil salvo':' · Sem perfil salvo'),path)));
 if(!paths.length)profilePicker.append(new Option('Encontre um arquivo GGUF para configurar',''));
 profilePicker.value=profileModel;profilePicker.disabled=!paths.length;deviceStatus.replaceChildren();
 const profile=saved[profileModel]||{},p=profile.performance||{},active=runtimes.find(r=>r.model_file===profileModel);
 const summary=$('local-profile-summary');summary.replaceChildren();
 $('local-add').hidden=paths.length?$('local-add').hidden:false;
 function section(title,rows){const card=element('section',undefined,'hardware-card');card.append(element('h3',title));for(const [name,value] of rows){const row=element('div',undefined,'hardware-row');row.append(element('span',name),element('strong',value??'Não informado'));card.append(row);}return card;}
 summary.append(section('Modelo e pesos',[
  ['Modelo deste perfil',profileModel?profileModel.split('/').pop():'Nenhum arquivo selecionado'],
  ['Perfil salvo',profile.model_file?'Sim · somente para este modelo':'Nenhum · não herda de outro modelo'],
  ['Servidor verificado',active?active.runtime+' · '+active.id:'Este arquivo não está em execução']
 ]));
 if(profile.description){const description=element('section',undefined,'hardware-card');description.append(element('h3','Descrição do perfil'),element('p',profile.description));summary.append(description);}
 const grid=element('div',undefined,'hardware-grid');
 grid.append(section('GPU · perfil salvo',[['Dispositivo',p.device],['Camadas solicitadas',p['n-gpu-layers']],['Camadas efetivamente carregadas','Não aferido'],['VRAM em uso','Sem telemetria']]),section('CPU · perfil salvo',[['Camadas MoE na CPU',p['n-cpu-moe']],['CPUs lógicas',p['cpu-range']],['CPUs no lote',p['cpu-range-batch']],['Threads / lote',[p.threads??'—',p['threads-batch']??'—'].join(' / ')]]));if(profileModel)summary.append(grid);
 if(active){const perf=active.performance||{},running=element('details',undefined,'advanced');running.append(element('summary','Parâmetros do processo em execução'),section('Argumentos detectados no processo',[['GPU · camadas solicitadas',perf['n-gpu-layers']],['CPU · camadas MoE',perf['n-cpu-moe']],['CPUs lógicas',perf['cpu-range']],['Contexto configurado',perf['ctx-size']]]));summary.append(running);}
 summary.append(element('p','Perfil salvo e argumentos detectados não comprovam alocação efetiva. O perfil será aplicado na próxima inicialização deste modelo.','hint'));
 if(profileModel){const details=element('details');details.append(element('summary','Caminho completo dos pesos'),element('p',profileModel,'hint'));summary.append(details);}
 $('dashboard-profile').textContent=Object.keys(saved).length+' perfil(is) privado(s) · configurações por arquivo de pesos';
 $('local-import').hidden=!active;$('local-import').textContent='Copiar configuração em execução deste modelo';
 editor.hidden=!profileModel||step!==0;profileSave.disabled=!profileModel;localPermissions.hidden=step!==1;profilePanel.querySelector('h3').textContent=profileModel?'Modelo · '+profileModel.split('/').pop():'Nenhum modelo configurado';
 if(!profileDirty){modelRoots=[...(profile.allowed_roots||[])];renderModelRoots();toolsInput.checked=profile.capabilities?.tools===true;for(const [key] of modelPermissionFields)$('profile-permission-'+key).checked=profile.permissions?.[key]===true;refreshPermissionAvailability();binaryInput.value=profile.binary||active?.binary||runtimes.find(r=>r.binary)?.binary||'';descriptionInput.value=profile.description||'';for(const [key] of hardwareFields)$('profile-'+key).value=p[key]||'';profileSave.textContent='Salvar perfil deste modelo';}
 const launchChoice=$('local-file').value;$('local-file').replaceChildren(...paths.map(path=>new Option(path.split('/').pop(),path)));if(paths.includes(launchChoice))$('local-file').value=launchChoice;else $('local-file').value=profileModel;
 const cpuOnly=profile.performance?.['n-gpu-layers']==='0';$('local-start').disabled=!profile.model_file||!!active||(!!runtimes.length&&!cpuOnly);
 $('local-launch-status').textContent=active?'Este modelo já está em execução.':runtimes.length&&!cpuOnly?'Outro modelo está em execução. Para preservá-lo, use um perfil explícito de CPU (0 camadas GPU), ou encerre o servidor atual antes de carregar outro na GPU.':profile.model_file?'Pronto para iniciar usando apenas o perfil deste arquivo.':'Configure e salve o perfil deste arquivo antes de iniciar.';
}
editor.id='hardware-editor-details';
profileSave.onclick=()=>action(async()=>{
 const performance={...(profiles()[profileModel]?.performance||{})};
 for(const [key] of hardwareFields){const value=$('profile-'+key).value.trim();if(value)performance[key]=value;else delete performance[key];}
 const permissions={};for(const [key] of modelPermissionFields)permissions[key]=$('profile-permission-'+key).checked;
 const previous=profiles()[profileModel]||{};
 const profile=await request('local-profile',{...previous,model_file:profileModel,binary:binaryInput.value.trim(),description:descriptionInput.value,performance,permissions:{...(previous.permissions||{}),...permissions},allowed_roots:[...modelRoots],capabilities:{...(previous.capabilities||{}),tools:toolsInput.checked}});
 state.local_profiles={...profiles(),[profile.model_file]:profile};profileDirty=false;renderProfile();TailUI.toast('Perfil salvo para '+profile.model_file.split('/').pop()+'. O processo atual foi preservado.');
});
$('local-import').onclick=()=>action(async()=>{if(profileDirty&&!confirm('Substituir as alterações deste perfil pela configuração em execução?'))return;const profile=await request('local-import',{file:profileModel});state.local_profiles={...profiles(),[profile.model_file]:profile};profileDirty=false;renderProfile();TailUI.toast('Configuração copiada somente para este modelo.');});
action(async()=>{await load();pollOperations();});

let importBundle=null;
$('export-settings').onclick=()=>action(async()=>{const bundle=await request('settings-export',{});const url=URL.createObjectURL(new Blob([JSON.stringify(bundle,null,2)],{type:'application/json'}));const link=element('a');link.href=url;link.download='tail-harness-settings.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);say('Configuração salva exportada sem credenciais. Alterações ainda não salvas não entram no arquivo.');});
$('import-settings').onchange=()=>action(async()=>{importBundle=null;$('import-preview').hidden=true;const file=$('import-settings').files[0];if(!file)return;if(file.size>60000)throw Error('Arquivo de configuração muito grande.');const candidate=JSON.parse(await file.text());const preview=await request('settings-import',{bundle:candidate,apply:false});importBundle=candidate;$('import-summary').textContent='Substituir escolhas salvas: '+preview.services.join(', ')+' · '+preview.projects+' projetos'+(preview.local_profile?' · inclui perfil local':' · mantém o perfil local atual')+'. A importação não inicia serviços.';$('import-preview').hidden=false;});
$('apply-import').onclick=()=>action(async()=>{if(!importBundle)return;await request('settings-import',{bundle:importBundle,apply:true});importBundle=null;$('import-preview').hidden=true;await load();say('Configuração importada. Revise as etapas antes de iniciar.');});
$('cancel-import').onclick=()=>{importBundle=null;$('import-preview').hidden=true;$('import-settings').value='';};


function renderMcpDefaults(){
 const select=$('mcp-default-model'),saved=state.settings.mcp_defaults||{};
 select.replaceChildren(new Option('Automático · configuração atual',''));
 for(const [backend,spec] of Object.entries(state.settings.services))if(spec.enabled)for(const model of spec.models.filter(m=>TailUI.selectableModel(backend,m))){select.append(new Option(backend+' · '+model,JSON.stringify([backend,model])));}
 select.value=saved.model?JSON.stringify([saved.backend,saved.model]):'';
 renderMcpEfforts(saved.effort||'');
 $('save-mcp').disabled=false;
 $('mcp-save-note').textContent=state.status.running?'As alterações serão aplicadas sem parar o harness.':'Ao salvar um modelo habilitado, o harness inicia automaticamente. Conversas existentes não são apagadas.';
}
function renderMcpEfforts(preferred=''){
 const select=$('mcp-default-effort');select.replaceChildren(new Option('Automático · esforço compatível',''));
 const value=$('mcp-default-model').value;if(!value){select.disabled=true;return;}select.disabled=false;
 const [backend,model]=JSON.parse(value);const efforts=state.models[backend]?.[model]||(['local','claude','gemini'].includes(backend)?['configured']:[]);
 for(const effort of efforts)select.append(new Option(effortLabels[effort]||effort,effort));
 if(preferred&&!efforts.includes(preferred))select.append(new Option(preferred+' · salvo, verificar conta',preferred));select.value=preferred;
}
$('mcp-default-model').onchange=()=>renderMcpEfforts();
$('save-mcp').onclick=()=>action(async()=>{try{const draft=structuredClone(state.settings),value=$('mcp-default-model').value;if(value){const [backend,model]=JSON.parse(value);draft.mcp_defaults={backend,model,effort:$('mcp-default-effort').value};}else draft.mcp_defaults={};await request('settings',draft);state.settings=draft;settings.mcp_defaults=draft.mcp_defaults;TailUI.notice($('mcp-feedback'),'Padrão MCP salvo. Inicie o harness para aplicar.');}catch(e){TailUI.notice($('mcp-feedback'),e.message,{error:true});}});
document.querySelectorAll('[data-config-tab]').forEach(button=>button.onclick=()=>{for(const name of ['appearance','mcp'])$('config-'+name).hidden=name!==button.dataset.configTab;document.querySelectorAll('[data-config-tab]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));});

let dashboardLoading=false;
async function refreshDashboard(){
 if(document.hidden||wizard||dashboardLoading)return;dashboardLoading=true;
 try{const data=await request('dashboard');if(wizard)return;if(!data.available)throw Error('Métricas temporariamente indisponíveis');
 const format=n=>n==null?'Não informado':Number(n).toLocaleString('pt-BR');
 $('dashboard-updated').textContent='atualizado '+new Date(data.checked_at*1000).toLocaleTimeString('pt-BR');
 const values=[['Pedidos / segundo',format(Number(data.requests_per_second.toFixed(3))),'Média dos últimos 60 segundos'],['Em execução / fila',data.active+' / '+data.queued,'Estado atual'],['Tokens reportados',data.input_tokens==null&&data.output_tokens==null?'Não informado':format((data.input_tokens||0)+(data.output_tokens||0)),format(data.measured_jobs)+' pedidos com métricas'],['Saída / segundo',format(data.latest_output_tokens_per_second),'Última execução com medição']];
 $('dashboard-metrics').replaceChildren(...values.map(([label,value,detail])=>{const card=element('article',undefined,'card metric-card');card.append(element('span',label),element('strong',value),element('small',detail));return card;}));
 const hw=data.hardware;const gib=n=>n==null?'Não informado':(n/1073741824).toFixed(1)+' GiB';
 const resources=[['CPU',hw.cpu_percent==null?'Aguardando amostra':format(hw.cpu_percent)+'%',hw.cpu_percent,100],['Memória',gib(hw.memory_used)+' / '+gib(hw.memory_total),hw.memory_used,hw.memory_total]];
 for(const gpu of hw.gpus){resources.push(['GPU '+gpu.name,format(gpu.percent)+'% · '+format(gpu.temperature)+' °C',gpu.percent,100],['VRAM '+gpu.name,gib(gpu.vram_used)+' / '+gib(gpu.vram_total),gpu.vram_used,gpu.vram_total]);}
 if(!hw.gpus.length)resources.push(['GPU','Medição indisponível',null,100]);
 $('server-resources').replaceChildren(...resources.map(([label,value,current,max])=>{const box=element('div');box.append(element('span',label),element('strong',value));if(current!=null&&max){const meter=document.createElement('meter');meter.min=0;meter.max=max;meter.value=current;meter.setAttribute('aria-label',label);box.append(meter);}return box;}));
 const names={queued:'Na fila',running:'Executando',completed:'Concluído',failed:'Falhou',cancelled:'Cancelado',interrupted:'Interrompido'};
 $('recent-count').textContent='Mostrando '+data.recent.length+' de '+(data.recent_count??data.recent.length)+' · últimas encerradas em 30 min';$('recent-runs').setAttribute('aria-label','Mostrando '+data.recent.length+' de '+(data.recent_count??data.recent.length)+' execuções');const holder=$('recent-runs');const retained=new Map([...holder.querySelectorAll('details[data-job]')].map(e=>[e.dataset.job,e]));
 const rows=data.recent.map(job=>{let row=retained.get(job.id);if(!row){row=element('details',undefined,'execution-row');row.dataset.job=job.id;row.append(element('summary'),element('div',undefined,'execution-detail'));row.addEventListener('toggle',()=>{if(row.open)refreshExecution(row);});}row.firstElementChild.textContent=[new Date(job.created*1000).toLocaleTimeString('pt-BR'),[job.backend,job.model].filter(Boolean).join(' · ')||'Executor não informado',names[job.state]||job.state,job.project||'Sem projeto',format(job.output_tokens)+' tokens saída'].join(' · ');if(row.open)refreshExecution(row);return row;});
 for(const child of [...holder.children])if(!rows.includes(child))child.remove();
 for(const [i,row] of rows.entries())if(holder.children[i]!==row)holder.insertBefore(row,holder.children[i]||null);
 if(!rows.length)holder.replaceChildren(element('p','Nenhuma execução ativa ou encerrada nos últimos 30 minutos.','empty-history'));

 }catch(e){$('dashboard-updated').textContent='Não foi possível atualizar; os últimos dados permanecem visíveis.';}finally{dashboardLoading=false;}
}
refreshDashboard();setInterval(refreshDashboard,3000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshDashboard();});

async function refreshExecution(row){
 if(row.dataset.loading)return;row.dataset.loading='1';
 try{const data=await request('dashboard?job='+encodeURIComponent(row.dataset.job));if(!data||data.available===false)throw Error('execution_unavailable');const detail=row.lastElementChild;
 const signature=JSON.stringify(data);if(detail.dataset.signature===signature)return;detail.dataset.signature=signature;
 const events=data.events||[];const answer=data.answer||events.filter(e=>e.type==='answer_delta').map(e=>e.data.text||'').join('');
 const nodes=[];for(const [title,value] of [['Pergunta',data.prompt],['Resposta',answer||'Ainda não recebida']]){nodes.push(element('h4',title),element('pre',value,'execution-text'));}
 if(data.metrics){nodes.push(element('h4','Tokens e métricas reportadas'),describeExecutionData(data.metrics));}
 const agents=events.filter(e=>e.type==='maestro_step'||e.type==='maestro_planning');if(agents.length||data.orchestration){nodes.push(element('h4','Agentes e planejamento'),describeExecutionData(data.orchestration||agents.map(e=>e.data)));}
 nodes.push(element('h4','Atividade · últimos 500 eventos'));const activity=element('ol',undefined,'execution-events');
 const labels={thinking:'Pensando',reasoning_summary:'Resumo de pensamento',tool_start:'Ferramenta iniciada',tool_end:'Ferramenta concluída',context_usage:'Uso do contexto',maestro_planning:'Planejando',maestro_step:'Etapa do agente',completed:'Concluído',failed:'Falhou',cancelled:'Cancelado',running:'Executando',queued:'Na fila'};
 for(const event of events.filter(e=>!['answer_delta','reasoning_delta'].includes(e.type)).slice(-50)){const li=element('li');li.append(element('strong',new Date(event.time*1000).toLocaleTimeString('pt-BR')+' · '+(labels[event.type]||event.type)));if(!['completed','quota_before','quota_after'].includes(event.type))li.append(describeExecutionData(event.data));activity.append(li);}nodes.push(activity);detail.replaceChildren(...nodes);
 }catch(e){row.lastElementChild.textContent='Não foi possível consultar esta execução. A atualização será tentada novamente.';}finally{delete row.dataset.loading;}
}

function describeExecutionData(value){
 const labels={input_tokens:'Tokens de entrada',output_tokens:'Tokens de saída',cached_tokens:'Tokens em cache',thinking_tokens:'Tokens de pensamento',inference_seconds:'Tempo de execução (s)',total_seconds:'Duração (s)',backend:'Provedor',model:'Modelo',effort:'Esforço',role:'Papel',steps:'Etapas',coordinator:'Coordenador',metrics:'Métricas',text:'Mensagem',tool:'Ferramenta',index:'Etapa',status:'Estado',answer:'Resposta',context_usage:'Contexto',result:'Resultado',prompt:'Instrução'};
 const box=element('div',undefined,'observed-data');
 if(value===null||typeof value!=='object'){box.textContent=value==null?'Não informado':String(value);return box;}
 for(const [key,item] of Object.entries(value)){const group=element('div',undefined,'observed-field');group.append(element('strong',labels[key]||key.replaceAll('_',' ')));if(item!==null&&typeof item==='object')group.append(describeExecutionData(item));else group.append(element('span',item==null?'Não informado':String(item)));box.append(group);}return box;
}

function showStep(value){
 showStepBase(value);
 const connectorsAvailable=wizard&&["codex","claude"].includes(editing);
 const existing=!!(editing&&(state.settings.services[editing]?.added||state.settings.services[editing]?.enabled));
 $("inspector-tabs").hidden=!existing&&!connectorsAvailable;
 $("inspector-tabs").lastElementChild.hidden=!connectorsAvailable;
 if(connectorsAvailable){
  $("integration-provider").value=editing;$("integration-provider").disabled=true;
  const info=visibleProviders().find(item=>item.id===editing);
  const heading=$("integrations").querySelector("h2");
  heading.replaceChildren(providerIcon(editing),document.createTextNode((info?.name||editing)+" · Conectores e plugins"));
 }
 if(step===3){renderIntegrationSelection();if(integrationCatalogs.has(editing))renderCatalog();else loadCatalog();$("wizard-progress").textContent="Conectores e plugins · selecione o que o harness carregará";$("wizard-next").hidden=true;$("wizard-back").hidden=false;}
}

function renderIntegrationCliGuide(){
 const host=$("integrations").querySelector(".panel-body");
 if(!host||$("integration-cli-guide"))return;
 const guide=element("details",undefined,"advanced integration-cli-guide");guide.id="integration-cli-guide";guide.append(element("summary","Descobrir e adicionar conectores ou plugins pelo CLI"),element("p","O catálogo acima é consultado pelo servidor. MCP lista conectores configurados; plugins inclui opções ainda não instaladas dos marketplaces conhecidos. Instalação, login e autorização só acontecem quando você escolhe uma operação.","hint"));
 const providers=[{id:"codex",name:"Codex",commands:["codex mcp list","codex mcp add <name> --url <https-url>","codex plugin list --available --json","codex plugin add <plugin@marketplace>"]},{id:"claude",name:"Claude Code",commands:["claude mcp list","claude mcp add --transport http <name> <https-url>","claude plugin list --available --json","claude plugin install <plugin@marketplace>"]}];
 for(const provider of providers){const section=element("section",undefined,"integration-cli-provider"),heading=element("h3"),logo=element("span",undefined,"provider-logo");logo.append(providerIcon(provider.id));heading.append(logo,document.createTextNode(provider.name));section.append(heading);for(const command of provider.commands){const code=element("code",command);section.append(code);}guide.append(section);}
 host.append(guide);
}
renderIntegrationCliGuide();

$("provider-options").addEventListener("click",event=>{const button=event.target.closest(".discovery-option");if(!button)return;const id=button.dataset.provider,current=state.settings.services[id];if(current&&!current.added&&!current.enabled&&!current.models?.length)settings.services[id].integrations=(state.integrations?.[id]||[]).map(item=>item.id);},true);

const integrationCatalogs=new Map(),catalogPending=new Set();
let catalogVisibleCount=40,catalogViewKey='';
function renderCatalog(){
 const provider=$('integration-provider').value,data=integrationCatalogs.get(provider),query=$('catalog-search').value.trim().toLocaleLowerCase();
 const viewKey=JSON.stringify([provider,query]);
 if(viewKey!==catalogViewKey){catalogVisibleCount=40;catalogViewKey=viewKey;}
 const items=(data?.items||[]).filter(item=>(item.name+' '+item.id).toLocaleLowerCase().includes(query));
 $('catalog-refresh').disabled=catalogPending.has(provider);
 $('catalog-items').setAttribute('aria-busy',String(catalogPending.has(provider)));
 $('catalog-status').textContent=catalogPending.has(provider)?'Buscando conectores e plugins de '+(provider==='claude'?'Claude Code':'Codex')+' neste computador… Aguarde.':data?.error?'Não foi possível consultar o catálogo. '+data.error+(data.items?.length?' Exibindo resultados da última consulta bem-sucedida.':'')+' Tente novamente em Consultar catálogos.':data?items.length+' resultado(s). '+(data.warnings||[]).join(' '):'Consulte os catálogos conhecidos pelo CLI deste servidor.';
 $('catalog-items').replaceChildren(...items.slice(0,catalogVisibleCount).map(item=>{
  const card=element('article',undefined,'catalog-item'),title=element('strong');title.append(connectorIcon(item),document.createTextNode(connectorLabel(item)));
  const detail=element('div');detail.append(title,element('small',item.id.replace(/^(plugin|mcp):/,''),'subtle'));
  detail.append(element('small',item.kind==='plugin'?'Plugin':'Conector MCP','subtle'));
  card.append(detail,element('span',item.status==='installed'?'Instalado':item.status==='configured'?'Configurado':'Disponível para instalar','pill'));
  if(item.kind==='plugin'&&item.status==='available'){
   const install=element('button','Instalar','button secondary');install.setAttribute('aria-label','Instalar '+item.name);
   install.onclick=()=>action(async()=>{await request('integration',{provider,action:'plugin_install',name:item.id.replace(/^plugin:/,'')});say('Instalação iniciada. Acompanhe Operações; ao concluir, atualize as integrações e selecione o plugin.');pollOperations();});
   card.append(install);
  }
  return card;
 }));
 if(data&&!data.error&&!items.length)$('catalog-items').append(element('p','Nenhuma opção encontrada neste catálogo. Você pode cadastrar outro conector ou marketplace pelo CLI.','hint'));
 $('catalog-more').hidden=items.length<=catalogVisibleCount;
 $('catalog-count').textContent=items.length?'Mostrando '+Math.min(catalogVisibleCount,items.length)+' de '+items.length+' resultados.':'';
}
async function loadCatalog(){
 const provider=$('integration-provider').value;if(catalogPending.has(provider))return;
 catalogPending.add(provider);renderCatalog();
 try{integrationCatalogs.set(provider,await request('integration-catalog',{provider}));}
 catch(error){integrationCatalogs.set(provider,{...(integrationCatalogs.get(provider)||{items:[]}),error:error.message});}
 finally{catalogPending.delete(provider);renderCatalog();}
}
function createCatalog(){
 const host=$('integrations').querySelector('.panel-body'),section=element('section',undefined,'integration-catalog');
 const heading=element('h3','Catálogo do servidor'),refresh=element('button','Consultar catálogos','button secondary');refresh.id='catalog-refresh';refresh.onclick=()=>loadCatalog();
 const label=element('label','Buscar conectores e plugins'),search=element('input');search.id='catalog-search';search.type='search';search.placeholder='Nome ou marketplace';search.oninput=renderCatalog;label.append(search);
 const status=element('p',undefined,'hint');status.id='catalog-status';status.setAttribute('role','status');const items=element('div');items.id='catalog-items';
 const count=element('p',undefined,'hint');count.id='catalog-count';count.setAttribute('role','status');
 const more=element('button','Carregar mais','button secondary');more.id='catalog-more';more.hidden=true;more.onclick=()=>{catalogVisibleCount+=40;renderCatalog();};
 section.append(heading,refresh,label,items,count,more);host.prepend(section);$('integrations').prepend($('integrations').querySelector('h2'),status);
}
createCatalog();

// Help stays visible and is associated with its control for assistive technology.
const settingHelp={
 'hardware-model':'Cada arquivo de pesos tem seu próprio perfil de execução e permissões.',
 'profile-binary':'Caminho do programa llama-server que carregará este modelo nesta máquina.',
 'profile-description':'Anotação para reconhecer a finalidade deste perfil; não é enviada ao modelo.',
 'integration-provider':'Provedor cujo perfil receberá a integração.',
 'integration-action':'Escolha entre cadastrar, autenticar, instalar ou remover a integração.',
 'integration-name':'Identificador usado pelo serviço ou pelo marketplace do plugin.',
 'integration-transport':'HTTPS conecta a um servidor remoto; stdio inicia um processo nesta máquina.',
 'integration-source':'Endereço do servidor MCP ou lista JSON com o comando e seus argumentos.',
 'install-model':'Pesos que serão baixados. Confira o tamanho e a licença antes de autorizar.',
 'install-runtime':'Onde os pesos serão armazenados e qual runtime poderá carregá-los.',
 'model-folder':'Pasta nesta máquina em que o painel buscará arquivos de modelo GGUF.',
 'local-file':'Arquivo de pesos ao qual será associado um perfil próprio.',
 'import-settings':'Carrega um arquivo exportado pelo painel para revisão antes de aplicar.',
 'mcp-default-model':'Modelo usado por clientes MCP (aplicativos que chamam as ferramentas do harness) quando não escolhem outro.',
 'mcp-default-effort':'Quanto esforço de raciocínio solicitar. Níveis maiores podem aumentar o tempo e o consumo; as opções dependem do modelo.'
};
for(const [id,help] of Object.entries(settingHelp))fieldHelp($(id),help);

// Only connection creation needs a transport and server address.
function renderIntegrationForm(){
 const action=$('integration-action').value,connecting=action==='connector_add';
 for(const id of ['integration-transport','integration-source'])$(id).closest('label').hidden=!connecting;
 $('integration-name').placeholder=action.startsWith('plugin_')?'plugin@marketplace':'gmail, drive ou gitlab';
 const local=$('integration-transport').value==='stdio';
 $('integration-source').closest('label').firstChild.textContent=local?'Comando do servidor MCP em lista JSON':'URL oficial do servidor MCP';
 $('integration-source').placeholder=local?'["programa", "argumento"]':'https://seu-servidor-mcp/mcp';
}
$('integration-action').onchange=renderIntegrationForm;
$('integration-transport').onchange=renderIntegrationForm;
renderIntegrationForm();
