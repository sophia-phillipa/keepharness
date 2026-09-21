const MAX_ATTACHMENTS=20;
'use strict';
const $=id=>document.getElementById(id);
let providers={},models=[],files=[],job='',last=0,controller=null,active=null,busy=false,parent=null,conversation='',loading=false,build='',reloadPending=false;
let executionMode="native",executionModeChosen=false;
let policyProject=null,policyPending=false,policySequence=0;
let uploadsAllowed=false,streamDisconnected=false,submitting=false,cancelling=false,pendingSubmission=null;
try{pendingSubmission=JSON.parse(sessionStorage.getItem('pending-submission')||'null');}catch{}
function submissionKey(data){const payload=JSON.stringify(data);if(pendingSubmission?.payload!==payload){pendingSubmission={payload,key:crypto.randomUUID?.()||Array.from(crypto.getRandomValues(new Uint8Array(16)),n=>n.toString(16).padStart(2,'0')).join('')};try{sessionStorage.setItem('pending-submission',JSON.stringify(pendingSubmission));}catch{}}return pendingSubmission.key;}
function clearSubmission(){pendingSubmission=null;try{sessionStorage.removeItem('pending-submission');}catch{}}
let conversations=[],legacyHistory=false,historyRequest=0,conversationLoad=0,uploads=0;
let conversationActivity={};
try{const saved=JSON.parse(localStorage.getItem('conversation-activity')||'{}');if(saved&&typeof saved==='object'&&!Array.isArray(saved))conversationActivity=saved;}catch{}
function saveConversationActivity(){try{localStorage.setItem('conversation-activity',JSON.stringify(conversationActivity));}catch{}}
function observeConversation(c){
 const previous=conversationActivity[c.id],token=c.last_job_id||c.id;
 const unread=!!previous?.unread||(c.state==='completed'&&!!previous&&(previous.token!==token||previous.state!==c.state));
 conversationActivity[c.id]={token,state:c.state,unread};
}
function conversationIndicator(c){
 const working=['queued','running'].includes(c.state),unread=conversationActivity[c.id]?.unread;
 if(!working&&!unread)return null;
 const indicator=document.createElement('span');indicator.className='conversation-indicator '+(working?'working':'unread');
 indicator.setAttribute('role','img');indicator.setAttribute('aria-label',working?'Em andamento':'Resposta não lida');indicator.title=indicator.getAttribute('aria-label');return indicator;
}
let fileTree={project:'',roots:[],rootId:'',basePath:'',cache:new Map(),expanded:new Set(),selected:new Set(),anchor:'',request:0,ready:false};
function setConversationTitle(value){const full=String(value||'Nova Conversa').trim()||'Nova Conversa',el=$('conversation-title');el.textContent=full.length>80?full.slice(0,79).trimEnd()+'…':full;el.title=full;syncActiveProjectBadge();}
let composerProjectId=null,composerGitRequest=0,resourceRequest=0,resourceItems=[],resourceSelections=[],invalidResourceTokens=new Set();
const resourceToken=/(^|\s)(@@|\/\/|@|\/)([^\s@/]*)$/;
function resourceEngine(){const m=selected();return {backend:m?.backend||'',model:m?.id||'',execution_mode:executionMode};}
function triggerAtCaret(){const input=$('prompt'),before=input.value.slice(0,input.selectionStart||0),match=resourceToken.exec(before);if(!match)return null;const start=match.index+match[1].length;return {prefix:match[2],query:match[3],start,end:before.length};}
function syncResourceSelections(){const tokens=new Set($('prompt').value.split(/\s+/));resourceSelections=resourceSelections.filter(ref=>tokens.has(ref.token));invalidResourceTokens=new Set([...invalidResourceTokens].filter(token=>tokens.has(token)));}
function renderPromptHighlights(){
 const input=$('prompt'),mirror=$('prompt-highlights'),tokens=new Set(resourceSelections.map(ref=>ref.token));
 mirror.replaceChildren();
 for(const part of input.value.split(/(\s+)/)){
  if(tokens.has(part)){const span=document.createElement('span');span.className='prompt-resource';span.textContent=part;mirror.append(span);}
  else mirror.append(document.createTextNode(part));
 }
 // Preserve the last empty line and use text nodes so prompt content stays inert.
 mirror.append(document.createTextNode('\u200b'));
 input.classList.toggle('has-resource-highlights',tokens.size>0);
 mirror.hidden=tokens.size===0;
 syncPromptHighlightLayout();
}
function syncPromptHighlightLayout(){
 const input=$('prompt'),mirror=$('prompt-highlights'),style=getComputedStyle(input);
 for(const property of ['fontFamily','fontSize','fontWeight','fontStyle','fontStretch','fontVariant','lineHeight','letterSpacing','padding','textIndent','tabSize','wordSpacing'])mirror.style[property]=style[property];
 mirror.style.width=input.clientWidth+'px';mirror.style.height=input.clientHeight+'px';
 mirror.scrollTop=input.scrollTop;mirror.scrollLeft=input.scrollLeft;
}
$('prompt').addEventListener('scroll',syncPromptHighlightLayout,{passive:true});
new ResizeObserver(syncPromptHighlightLayout).observe($('prompt'));
function closeResourceMenu(){resourceRequest++;const menu=$('resource-menu');if(menu.matches(':popover-open'))menu.hidePopover();menu.replaceChildren();}
function resourceKeydown(event){
 const menu=$('resource-menu');
 if(!menu.matches(':popover-open')||event.isComposing||event.keyCode===229)return false;
 const options=[...menu.querySelectorAll('[role=option]:not(:disabled)')],index=options.indexOf(document.activeElement);
 if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)&&options.length){
  event.preventDefault();event.stopPropagation();
  const next=event.key==='Home'?0:event.key==='End'?options.length-1:index<0?(event.key==='ArrowUp'?options.length-1:0):(index+(event.key==='ArrowDown'?1:-1)+options.length)%options.length;
  options[next].focus();options[next].scrollIntoView({block:'nearest'});return true;
 }
 if(event.key==='Enter'&&!event.shiftKey&&options.length){event.preventDefault();event.stopPropagation();options[index<0?0:index].click();return true;}
 if(event.key==='Escape'){event.preventDefault();event.stopPropagation();closeResourceMenu();$('prompt').focus();return true;}
 if(event.key==='Tab')closeResourceMenu();
 return false;
}
$('resource-menu').addEventListener('keydown',resourceKeydown);
function resourceIcon(item){const origin=String(item.origin||'').toLowerCase(),symbol=origin.includes('claude')?'brand-claude':origin.includes('gemini')?'brand-gemini':origin.includes('codex')?'brand-openai':'';if(origin.includes('deepseek'))return document.createTextNode('🐋');if(!symbol)return document.createTextNode('◈');const svg=document.createElementNS('http://www.w3.org/2000/svg','svg'),use=document.createElementNS('http://www.w3.org/2000/svg','use');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');svg.classList.add('provider-logo-icon');use.setAttribute('href','/assets/icons.svg#'+symbol);svg.append(use);return svg;}
function renderResourceMenu(trigger,items,loading=false,warnings=[]){const menu=$('resource-menu'),groups=new Map();menu.replaceChildren();const heading=document.createElement('p');heading.className='access-menu-heading';heading.textContent=trigger.prefix==='@@'?'Agentes do Tail Harness':trigger.prefix==='//'?'Skills e comandos do Tail Harness':trigger.prefix==='@'?'Agentes disponíveis':'Skills e comandos disponíveis';menu.append(heading);for(const warning of warnings){const note=document.createElement('p');note.className='resource-warning';note.setAttribute('role','status');note.textContent=warning;menu.append(note);}
 if(trigger.prefix==='@@'||trigger.prefix==='//'){const empty=document.createElement('p');empty.className='resource-empty';empty.textContent='Recursos do Tail Harness ainda não estão disponíveis.';menu.append(empty);}
 else if(loading){const row=document.createElement('p');row.className='resource-empty';row.textContent='Atualizando recursos…';menu.append(row);}
 else {for(const item of items){const key=item.scope==='project'?'Projeto':'Global',groupKey=key+'\0'+item.origin;if(!groups.has(groupKey)){const section=document.createElement('section'),title=document.createElement('h3');section.className='resource-group';title.textContent=key+' · '+item.origin;section.append(title);groups.set(groupKey,section);menu.append(section);}const option=document.createElement('button');option.type='button';option.setAttribute('role','option');option.className='resource-option';option.dataset.resourceId=item.id;option.dataset.resourceRevision=item.revision;option.dataset.resourceKind=item.kind;option.disabled=item.selectable===false;option.title=item.source||item.origin||item.name;const glyph=document.createElement('span');glyph.className='resource-origin-icon';glyph.setAttribute('aria-hidden','true');glyph.append(resourceIcon(item));const text=document.createElement('span'),name=document.createElement('strong'),description=document.createElement('small');name.textContent=item.name;description.textContent=(item.kind==='agent'?'Agente':item.kind==='skill'?'Skill':'Comando')+(item.description?' · '+item.description:'')+(item.unavailable_reason?' · '+item.unavailable_reason:'');text.append(name,description);option.append(glyph,text);option.onclick=()=>selectResource(item,trigger);groups.get(groupKey).append(option);}}
 if(trigger.prefix[0]==='@'&&!items.some(item=>item.kind==='agent')||trigger.prefix[0]==='/'&&!items.some(item=>item.kind==='skill'||item.kind==='command')){const empty=document.createElement('p');empty.className='resource-empty';empty.textContent=loading?'':'Nenhum recurso compatível com este motor.';menu.append(empty);}menu.hidden=false;if(!menu.matches(':popover-open'))menu.showPopover();const rect=$('prompt').getBoundingClientRect();menu.style.left=Math.max(12,Math.min(rect.left,innerWidth-menu.offsetWidth-12))+'px';menu.style.top=Math.max(12,rect.top-menu.offsetHeight-8)+'px';}
function selectResource(item,trigger){if(item.selectable===false)return;const marker=trigger.prefix[0]==='@'?'@':'/',token=marker+item.name,conflict=resourceSelections.find(ref=>ref.token===token&&ref.id!==item.id);if(conflict){status('Já há outro recurso chamado '+token+' nesta mensagem. Remova-o antes de escolher outra origem.');closeResourceMenu();return;}const input=$('prompt'),before=input.value.slice(0,trigger.start),after=input.value.slice(trigger.end);input.value=before+token+' '+after;const caret=(before+token+' ').length;input.setSelectionRange(caret,caret);resourceSelections=resourceSelections.filter(ref=>ref.token!==token);resourceSelections.push({id:item.id,revision:item.revision,token});invalidResourceTokens.delete(token);closeResourceMenu();input.focus();updateComposer();saveView();}
async function refreshResources(trigger){const request=++resourceRequest,m=resourceEngine(),project=$('project').value;if(!m.backend){renderResourceMenu(trigger,[],false);return;}renderResourceMenu(trigger,[],true);try{const query=new URLSearchParams({project_id:project,backend:m.backend,model:m.model,execution_mode:m.execution_mode});const data=await json('/v1/resources?'+query,{signal:AbortSignal.timeout(5000)});if(request!==resourceRequest||project!==$('project').value||m.backend!==resourceEngine().backend||m.model!==resourceEngine().model||m.execution_mode!==executionMode)return;resourceItems=Array.isArray(data.items)?data.items:[];const filtered=resourceItems.filter(item=>trigger.prefix==='@'?item.kind==='agent':item.kind==='skill'||item.kind==='command').filter(item=>item.name.toLowerCase().includes(trigger.query.toLowerCase()));renderResourceMenu(trigger,filtered,false,Array.isArray(data.warnings)?data.warnings:[]);}catch{if(request!==resourceRequest)return;resourceItems=[];renderResourceMenu(trigger,[],false);const note=$('resource-menu').querySelector('.resource-empty');if(note)note.textContent='Não foi possível atualizar os recursos.';}}
function openResourceMenu(){const trigger=triggerAtCaret();if(!trigger){closeResourceMenu();return;}if(trigger.prefix==='@@'||trigger.prefix==='//'){renderResourceMenu(trigger,[],false);return;}void refreshResources(trigger);}
function invalidateResources(){for(const ref of resourceSelections)invalidResourceTokens.add(ref.token);resourceSelections=[];resourceItems=[];closeResourceMenu();renderPromptHighlights();saveView();}
function syncComposerProject(){
 const option=$('project').selectedOptions[0],id=option?.value||'',name=id&&id!=='sem-projeto'?option.textContent:'';
 $('composer-project').hidden=!name;
 $('composer-project-name').textContent=name;
 $('composer-project-name').title=name;
 if(composerProjectId!==id){composerProjectId=id;$('composer-git').hidden=true;void refreshComposerGit();}
}
async function refreshComposerGit(){
 const id=$('project').value,request=++composerGitRequest;
 if(!id||id==='sem-projeto')return;
 try{
  const data=await json('/v1/project-git?project_id='+encodeURIComponent(id),{signal:AbortSignal.timeout(5000)});
  if(request!==composerGitRequest||id!==$('project').value)return;
  $('composer-git').textContent=data.revision||'';
  $('composer-git').title=data.revision?'Git: '+data.revision:'';
  $('composer-git').hidden=!data.revision;
 }catch{if(request===composerGitRequest&&id===$('project').value)$('composer-git').hidden=true;}
}
function syncActiveProjectBadge(){syncComposerProject();const option=$('project').selectedOptions[0],badge=$('active-project-badge');if(!badge)return;const name=option?.value&&option.value!=='sem-projeto'?option.textContent:'';badge.hidden=!name;badge.lastElementChild.textContent=name||'';badge.title=name||'';}
const welcomeTemplate=$('welcome').cloneNode(true);
const expandedProjects=new Map();
let preferredSelection={};
try{preferredSelection=JSON.parse(localStorage.getItem('chat-selection')||'{}')||{};}catch{}
const labels={maestro_planning:'Maestro está planejando',maestro_plan:'Agentes selecionados',maestro_step:'Executando etapa do Maestro',answer_delta:'Respondendo',reasoning_delta:'Pensando',reasoning_summary:'Resumo de raciocínio',interrupted:'Interrompido',queued:'Na fila',running:'Executando',thinking:'Pensando',planning:'Preparando a execução',tool_start:'Usando ferramenta',tool_end:'Ferramenta concluída',session_resumed:'Contexto da conversa retomado',context_compacting:'Otimizando o contexto da conversa…',context_compacted:'Contexto otimizado; conversa preservada',validating_changes:'Validando alterações',changes_applied:'Alterações aplicadas ao projeto',deployment_failed:'Alterações não aplicadas; consulte o erro',reload_scheduled:'Atualizando o painel',completed:'Concluído',cancelled:'Cancelado',failed:'Falha',loading:'Preparando modelo'};
const status=text=>{const target=$('status');target.hidden=false;target.className='';target.textContent=text;if(/^Pronto para conversar/.test(text)){target.className='visually-hidden';}else if(/^(Não foi possível|Conexão interrompida|Falha|Erro|Conecte o Tailscale|Resposta incompleta|Execução interrompida|Execução cancelada|Interrompido|Alterações não aplicadas)/.test(text)){TailUI.notice(target,text,{error:true});}else if(Object.values(labels).includes(text)||/^(Concluído|Falha na execução|Cancelado|Executando|Pensando|Raciocinando|Recebendo resposta|Preparando|Usando ferramenta|Ferramenta concluída|Plano atualizado|Etapas da execução|Trabalhando|Consultando cota|Enviando pedido|Carregando|Conectado|Cancelando|Reconectando|Pronto para conversar)/i.test(text)){target.textContent='';}};
const names={'qwen-local':'Qwen3.6 · local','gpt-6-astra':'GPT-6 Astra','gpt-5.6-sol':'GPT-5.6 Sol','gpt-5.6-terra':'GPT-5.6 Terra','gpt-5.6-luna':'GPT-5.6 Luna','gpt-5.5':'GPT-5.5','deepseek-flash':'DeepSeek V4.1 Flash','deepseek-v4-pro':'DeepSeek V4 Pro'};
const modelIcons={'qwen-local':'✦','gpt-6-astra':'🌟','gpt-5.6-sol':'☀️','gpt-5.6-terra':'🌍','gpt-5.6-luna':'🌙','gpt-5.5':'✳'};
const modelIcon=id=>{const name=(String(id||'')+' '+(models.find(m=>m.id===id)?.name||'')).toLowerCase();if(modelIcons[id])return modelIcons[id];if(name.includes('qwen'))return '✦';if(name.includes('claude'))return '✳';if(name.includes('deepseek'))return '🐋';if(name.includes('gemini'))return '✦';if(name.includes('gemma'))return '💎';if(name.includes('llama'))return '🦙';return '◈';};
const providerNames={local:'Servidor local',codex:'Codex',claude:'Claude Code',gemini:'Gemini CLI',deepseek:'DeepSeek',maestro:'Maestro'};
const modelName=id=>names[id]||models.find(m=>m.id===id)?.name||id||'Nenhum modelo';
const selectedIdentity=()=>{const m=selected();return m?{backend:m.backend,provider:providerNames[m.backend]||m.backend||'Provedor',model:modelName(m.id),modelIcon:modelIcon(m.id)}:null;};
const efforts={auto:'Maestro escolhe por etapa',none:'Sem raciocínio',configured:'Padrão do provedor',low:'Baixo',medium:'Médio',high:'Alto',xhigh:'Muito alto',max:'Máximo',ultra:'Ultra'};
const userErrors={rate_limit:'Muitas consultas em pouco tempo. O servidor limitou temporariamente este acesso.',submission_rate_limit:'Você enviou novos pedidos muito rapidamente. Este pedido não entrou na fila.',queue_full:'A fila do servidor está cheia. Este pedido não entrou na fila; aguarde a conclusão de outras execuções.',owner_queue_full:'Você atingiu o limite de pedidos na fila. Aguarde uma das suas execuções terminar antes de enviar outra.',stream_limit:'Há muitas conexões de acompanhamento abertas nesta sessão. Feche as abas extras e tente novamente.',login_rate_limit:'Muitas tentativas de acesso. Confira seus dados antes de tentar entrar novamente.',model_not_allowed:'Este modelo não está habilitado. Verifique os modelos disponíveis.',model_not_available:'O modelo não está disponível. Peça ao administrador para verificar o servidor local.',uploads_denied:'Anexos estão desativados para este serviço.',authentication_required:'Sua conexão precisa de autorização.',project_denied:'Você não tem acesso a este projeto.',native_failed:'O serviço de IA não concluiu a execução. Verifique a atividade e tente novamente.',project_name_exists:'Já existe um projeto com esse nome. Escolha outro nome.',invalid_project_name:'O nome precisa ter pelo menos 3 letras e no máximo 100 caracteres.',project_directory_required:'Adicione pelo menos uma pasta existente ao projeto.',project_directory_forbidden:'Uma das pastas escolhidas é protegida ou não está autorizada.'};
async function api(path,options={}){let r;try{r=await fetch(path,{...options,signal:options.signal||AbortSignal.timeout(30000)});}catch(e){if(e.name==='AbortError')throw e;throw Error(e.name==='TimeoutError'?'O servidor demorou a responder. Verifique a atividade antes de repetir o pedido.':'Não foi possível conectar ao servidor. Verifique sua conexão e tente novamente.');}if(!r.ok){let e;try{e=await r.json();}catch{e={code:'HTTP '+r.status};}let message=userErrors[e.code]||(r.status===429?'O servidor aplicou um limite temporário a esta solicitação.':'O servidor não concluiu a solicitação ('+(e.code||r.status)+'). Verifique os dados ou tente novamente.');if(r.status===429){const after=r.headers.get('Retry-After');const seconds=after===null?NaN:/^\d+(?:\.\d+)?$/.test(after.trim())?Number(after):Math.max(0,(Date.parse(after)-Date.now())/1000);message+=Number.isFinite(seconds)?' Tente novamente em '+Math.max(1,Math.ceil(seconds))+' segundos.':' Aguarde um pouco antes de tentar novamente.';}const error=Error(message);error.code=e.code;error.status=r.status;if(r.status===429){const after=r.headers.get('Retry-After');error.retryAfter=/^\d+(?:\.\d+)?$/.test(after||'')?Number(after)*1000:Math.max(0,Date.parse(after)-Date.now())||5000;}throw error;}return r;}
async function json(path,options){const r=await api(path,options);try{return await r.json();}catch{throw Error('O servidor retornou dados inválidos. Tente atualizar a conexão.');}}

const post=(path,value)=>json(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
function selected(){return models.find(m=>m.id===$('model').value)||models[0];}
function setBusy(value){value=value||streamDisconnected;busy=value;$('add-project').disabled=value||loading;if(!value)paintMotion('');$('send').disabled=value||loading||uploads>0||!selected()||!$('prompt').value.trim();$('send').hidden=value;$('cancel').hidden=!value;$('cancel').disabled=submitting||cancelling||!job;$('attach').disabled=value;$('project').disabled=value;$('model').disabled=value;$('effort').disabled=value;$('access-mode').disabled=value;$('access-trigger').disabled=value;$('attach').disabled=value||uploads>0||!canUpload()||!selected();$('new').disabled=submitting||cancelling||loading||uploads>0;updateComposer();renderFiles();}
async function refreshProjectPermissions(timeout=30000){
 const project=$('project').value;if(project===policyProject)return true;
 const sequence=++policySequence;policyPending=true;updateModelPermissions();updateComposer();
 try{const data=await json('/v1/models?project_id='+encodeURIComponent(project),{signal:AbortSignal.timeout(timeout)});if(sequence!==policySequence)return;
 if(!Array.isArray(data.models))throw Error('Catálogo de permissões inválido');
 const previous=$('model').value,effort=$('effort').value;models=data.models;uploadsAllowed=data.uploads_enabled===true;
 $('model').replaceChildren(...models.map(m=>new Option(names[m.id]||m.name||m.id,m.id)));if(models.some(m=>m.id===previous))$('model').value=previous;
 policyProject=project;policyPending=false;updateEfforts();if([...$('effort').options].some(o=>o.value===effort))$('effort').value=effort;
 }catch(e){if(sequence!==policySequence)return;status('Não foi possível carregar as permissões deste projeto. Selecione-o novamente para tentar: '+e.message);return false;}
 finally{if(sequence===policySequence){updateModelPermissions();updateComposer();}}
 return true;
}
function canUpload(){const m=selected();return !policyPending&&uploadsAllowed&&!!m&&(m.permissions?m.permissions.upload===true:true);}
function updateModelPermissions(){
 const m=selected(),allowed=canUpload();
 $('attach').disabled=busy||loading||uploads>0||!allowed;
 $('attach').title=allowed?'Anexar arquivo':'Anexos não permitidos para este modelo';
 $('attachment-help').textContent=allowed?'Documentos: 50 MiB · imagens: 5 MiB · áudio: até 2 h / 256 MiB':'Anexos desativados para este modelo. Revise suas permissões no painel administrativo.';
 let panel=$('model-permissions');if(!panel){panel=document.createElement('p');panel.id='model-permissions';panel.className='footnote';$('model-note').after(panel);}
 panel.hidden=!m?.permissions;
 if(m?.permissions)panel.textContent=(m.permissions.upload?'Anexos permitidos':'Sem anexos')+' · '+(m.permissions.internet?(m.backend==='local'?'Internet permitida às ferramentas locais':'Internet permitida'):'Internet desativada')+' · '+(m.permissions.shell?'Terminal permitido':'Terminal desativado');
}
function updateEfforts(){updateModelPermissions();renderQuotaIdentity();const m=selected();if(!m){$('effort').replaceChildren();return;}$('effort').replaceChildren(...m.efforts.map(e=>{const o=document.createElement('option');o.value=e;o.textContent=efforts[e]||e;return o;}));$('model-note').textContent=m.backend==='maestro'?'Codex coordena e escolhe modelos locais ou de nuvem habilitados para cada etapa':m.backend==='local'?'Modelo local executa no servidor · sem cota OpenAI · confira as fontes':m.backend==='deepseek'?'DeepSeek API · consome seus créditos DeepSeek':m.backend==='gemini'?'Gemini CLI · conta Google · cota da assinatura':m.backend==='claude'?'Claude Code no servidor · inferência Anthropic · cota não disponível':'Codex CLI no servidor · inferência OpenAI · consome cota ChatGPT';}
function quotaWindows(q,backend=selected()?.backend){const buckets=q?.rateLimitsByLimitId||{codex:q?.rateLimits},entries=Object.entries(buckets||{}),windows=[];for(const [id,bucket] of entries){if(!bucket)continue;const bucketNames={five_hour:'Claude',seven_day:'Claude',seven_day_opus:'✳ Opus',seven_day_sonnet:'✳ Sonnet',overage:'Excedente'};const bucketLabel=entries.length>1?(backend==='claude'?(bucketNames[id]||bucket.limitName||bucket.name||bucket.limitId||id.replaceAll('_',' ')):(bucket.limitName||bucket.name||bucket.limitId||bucketNames[id]||id.replaceAll('_',' '))):'';for(const window of [bucket.primary,bucket.secondary]){const used=window?.usedPercent;if(!window||typeof used!=='number'||!Number.isFinite(used))continue;const duration=window.windowDurationMins,label=typeof duration==='number'&&duration>=10080?'Semanal':duration===300?'5 h':typeof duration==='number'&&Number.isFinite(duration)&&duration>0?duration<60?Math.round(duration)+' min':Math.round(duration/60)+' h':'Cota',remaining=Math.max(0,Math.min(100,Math.round((100-used)*10)/10)),percent=Number.isInteger(remaining)?String(remaining):remaining.toFixed(1);windows.push({label:bucketLabel?`${label} · ${bucketLabel}`:label,remaining,percent,resetsAt:Number(window.resetsAt)});}}return windows;}
function quotaText(q){if(!q?.available)return'Cota indisponível';const windows=quotaWindows(q);return windows.length?windows.map(w=>`${w.label}: ${w.percent}% restante`).join(' · '):'Percentual indisponível';}
let quotaIdentityBackend='';
function renderQuotaIdentity(){
 const model=selected(),backend=model?.backend||'',changed=backend!==quotaIdentityBackend;quotaIdentityBackend=backend;
 $('quota-model-icon').textContent=model?modelIcon(model.id):'◈';$('quota-model-name').textContent=model?modelName(model.id):'Modelo';$('quota-model-identity').title=model?modelName(model.id)+' · '+(providerNames[backend]||backend):'Modelo selecionado';$('quota-toggle').hidden=false;
 const states={local:'Sem cota do provedor',claude:'Consultando cota Claude…',deepseek:'Créditos DeepSeek',maestro:'Cota varia por modelo da etapa'};
 if(backend==='codex'||backend==='claude'){if(changed)$('quota-short').textContent=backend==='claude'?'Consultando cota Claude…':'Consultando cota…';}
 else{$('quota-short').textContent=states[backend]||'Cota indisponível';$('quota-comparison').replaceChildren();$('quota-current').replaceChildren();const detail=document.createElement('p');detail.textContent=backend==='local'?'Este modelo executa localmente. O uso de contexto aparece separado no indicador de contexto.':backend==='deepseek'?'Este modelo usa créditos próprios da sua conta DeepSeek.':backend==='maestro'?'O Maestro pode encaminhar etapas a modelos diferentes; a cota depende do executor de cada etapa.':'Selecione um modelo para consultar a cota do provedor.';$('quota-current').append(detail);}
 const description=model?modelName(model.id)+' · '+(providerNames[backend]||backend)+' · '+$('quota-short').textContent:'Modelo selecionado · cota indisponível';$('quota-toggle').setAttribute('aria-label',description);$('quota-toggle').title=description;requestAnimationFrame(updateHeaderToastOffset);
}
function paintQuota(q,backend=selected()?.backend){if(selected()?.backend!==backend||!['codex','claude'].includes(backend))return;const available=q?.available===true,windows=available?quotaWindows(q,backend):[];const source=backend==='claude'?'Última informação do Claude':'Cota compartilhada da conta Codex';$('quota-short').textContent=available?(windows.length?windows.map(w=>`${w.label}: ${w.percent}% restante`).join(' · '):'Percentual indisponível'):backend==='claude'?'Cota Claude não informada':'Cota indisponível';let checked='';if(backend==='claude'&&q?.checked_at){const timestamp=typeof q.checked_at==='number'?q.checked_at*1000:Date.parse(q.checked_at);if(Number.isFinite(timestamp))checked=' · Atualizada em '+new Date(timestamp).toLocaleString();}$('quota-toggle').setAttribute('aria-label',modelName(selected()?.id)+' · '+source+' · '+$('quota-short').textContent+checked);$('quota-toggle').title=source+checked+' · '+$('quota-short').textContent;$('quota-current').replaceChildren();requestAnimationFrame(updateHeaderToastOffset);if(!available){$('quota-current').textContent=backend==='claude'?'Nenhuma observação recente de cota Claude está disponível.' :'Não foi possível consultar a cota agora. Nenhum percentual foi estimado.';return;}if(!windows.length){$('quota-current').textContent='O provedor não informou percentuais de cota para esta conta.';return;}if(backend==='claude'){const note=document.createElement('p');note.textContent='Última informação do Claude'+checked;$('quota-current').append(note);}for(const window of windows){const wrap=document.createElement('div');wrap.className='quota-window';const label=document.createElement('div');label.textContent=`${window.label}: ${window.percent}% restante`;const bar=document.createElement('progress');bar.max=100;bar.value=window.remaining;const reset=document.createElement('small');reset.textContent=Number.isFinite(window.resetsAt)&&window.resetsAt>0?'Renova em '+new Date(window.resetsAt*1000).toLocaleString():'Horário de renovação indisponível';wrap.append(label,bar,reset);$('quota-current').append(wrap);}}
let quotaRequest=0;
async function quota(){
 const request=++quotaRequest,backend=selected()?.backend;renderQuotaIdentity();if(!['codex','claude'].includes(backend)){setQuotaOpen(false);return;}
 try{const value=await json(backend==='claude'?'/v1/usage?backend=claude':'/v1/usage');if(request===quotaRequest&&selected()?.backend===backend)paintQuota(value,backend);}catch{if(request===quotaRequest&&selected()?.backend===backend)paintQuota(null,backend);}
}
function quotaSnapshot(kind,q){if(selected()?.backend!=='codex')return;const id='quota-'+kind;let p=$(id);if(!p){p=document.createElement('p');p.id=id;$('quota-comparison').append(p);}p.textContent=(kind==='before'?'Antes: ':'Depois: ')+quotaText(q);}
async function history(timeout=30000){
 const request=++historyRequest;
 try{
  let data;
  try{data=await json('/v1/conversations',timeout<30000?{signal:AbortSignal.timeout(timeout)}:undefined);legacyHistory=false;}
  catch(e){
   if(e.status!==404)throw e;
   const old=await json('/v1/history',timeout<30000?{signal:AbortSignal.timeout(timeout)}:undefined);
   if(!Array.isArray(old.jobs))throw Error('Histórico inválido');
   data={conversations:old.jobs.map(r=>({...r,legacy:true}))};legacyHistory=true;
  }
  if(!Array.isArray(data.conversations))throw Error('Histórico inválido');
  if(request!==historyRequest)return false;
  conversations=data.conversations;conversations.forEach(observeConversation);saveConversationActivity();renderProjects();if($('conversation-search-dialog').open)renderConversationSearch();if(conversation){const current=conversations.find(item=>item.id===conversation);if(current)setConversationTitle(current.title);}
  $('history-note').textContent=legacyHistory?'Histórico compatível: execuções anteriores. Atualize o serviço para agrupar os turnos.':'';
  return true;
 }catch(e){if(request===historyRequest)status('Não foi possível carregar conversas: '+e.message);return false;}
}
function conversationRow(c){
 const row=document.createElement('div');row.className='conversation-row';
 const open=document.createElement('button');open.setAttribute('aria-current',c.id===conversation?'true':'false');open.textContent=c.title||'Conversa';open.title=open.textContent;open.className=c.id===conversation?'active':'';open.onclick=()=>load(c.id,c.legacy);
 const actions=document.createElement('details');actions.className='conversation-actions';actions.hidden=!!c.legacy;
 const trigger=document.createElement('summary');trigger.textContent='⋯';trigger.setAttribute('aria-label','Ações para '+open.textContent);trigger.title='Ações da conversa';
 const menu=document.createElement('div');menu.className='conversation-actions-menu';
 const rename=document.createElement('button');rename.type='button';const renameIcon=document.createElementNS('http://www.w3.org/2000/svg','svg');renameIcon.classList.add('th-icon','menu-action-icon');renameIcon.setAttribute('viewBox','0 0 24 24');renameIcon.setAttribute('aria-hidden','true');const renameUse=document.createElementNS('http://www.w3.org/2000/svg','use');renameUse.setAttribute('href','/assets/icons.svg#pencil');renameIcon.append(renameUse);rename.append(renameIcon,document.createTextNode('Renomear conversa'));
 rename.onclick=()=>{actions.open=false;if(busy||loading||uploads)return;openRenameConversation(c,trigger);};
 const remove=document.createElement('button');remove.type='button';const removeIcon=document.createElementNS('http://www.w3.org/2000/svg','svg');removeIcon.classList.add('th-icon','menu-action-icon');removeIcon.setAttribute('viewBox','0 0 24 24');removeIcon.setAttribute('aria-hidden','true');const removeUse=document.createElementNS('http://www.w3.org/2000/svg','use');removeUse.setAttribute('href','/assets/icons.svg#trash');removeIcon.append(removeUse);remove.append(removeIcon,document.createTextNode('Excluir conversa'));
 remove.onclick=()=>{actions.open=false;if(busy||loading||uploads)return;openDeleteConversation(c,trigger);};
 actions.addEventListener('toggle',()=>{if(!actions.open)return;document.querySelectorAll('.conversation-actions[open]').forEach(other=>{if(other!==actions)other.open=false;});const rect=trigger.getBoundingClientRect();const below=rect.bottom+4,above=rect.top-menu.offsetHeight-4;menu.style.left=Math.max(8,Math.min(rect.right-menu.offsetWidth,innerWidth-menu.offsetWidth-8))+'px';menu.style.top=(below+menu.offsetHeight<=innerHeight-8?below:Math.max(8,above))+'px';});
 actions.addEventListener('keydown',event=>{if(event.key==='Escape'){actions.open=false;trigger.focus();}});
 menu.append(rename,remove);actions.append(trigger,menu);
 const model=c.execution?.model;
 const icon=document.createElement('span');icon.className='conversation-model-icon';icon.textContent=modelIcon(model);icon.setAttribute('aria-hidden','true');
 const title=document.createElement('span');title.className='conversation-title';title.textContent=c.title||'Conversa';open.replaceChildren(icon,title);const indicator=conversationIndicator(c);if(indicator)open.prepend(indicator);
 open.title=[title.textContent,model?modelName(model).replace(/ · local$/,''):''].filter(Boolean).join('\n');
 row.append(open,actions);return row;
}
function openRenameConversation(c,trigger){
 const dialog=$('rename-conversation-dialog'),form=$('rename-conversation-form'),input=$('rename-conversation-name'),save=$('rename-conversation-save'),cancel=$('rename-conversation-cancel'),error=$('rename-conversation-error');
 let saving=false;
 input.value=c.title||'Conversa';input.disabled=false;cancel.disabled=false;save.textContent='Salvar';
 const validate=()=>{
  const length=Array.from(input.value.trim()).length;
  const message=!length?'Digite um nome; apenas espaços não são permitidos.':length>100?'Use no máximo 100 caracteres.':'';
  error.textContent=message;input.setAttribute('aria-invalid',String(!!message));save.disabled=saving||!!message;
  $('rename-conversation-count').textContent=length+' / 100 caracteres';return !message;
 };
 input.oninput=validate;cancel.onclick=()=>dialog.close();
 dialog.oncancel=event=>{if(saving)event.preventDefault();};
 dialog.onclose=()=>{if(trigger.isConnected)trigger.focus();else $('history').querySelector('.conversation-actions summary')?.focus();};
 form.onsubmit=async event=>{
  event.preventDefault();if(saving||busy||loading||uploads||!validate())return;
  saving=true;input.disabled=true;save.disabled=true;cancel.disabled=true;save.textContent='Salvando…';
  try{
   await json('/v1/conversations/'+encodeURIComponent(c.id),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:input.value.trim()})});
   await history();dialog.close();
  }catch(e){error.textContent='Não foi possível renomear: '+e.message;}
  finally{saving=false;input.disabled=false;save.disabled=false;cancel.disabled=false;save.textContent='Salvar';}
 };
 validate();dialog.showModal();input.focus();input.select();
}
function openDeleteConversation(c,trigger){
 const dialog=$('delete-conversation-dialog'),confirm=$('delete-conversation-confirm'),cancel=$('delete-conversation-cancel'),error=$('delete-conversation-error');
 $('delete-conversation-name').textContent=c.title||'Conversa';error.textContent='';
 cancel.onclick=()=>dialog.close();
 dialog.onclose=()=>{if(trigger.isConnected)trigger.focus();};
 dialog.oncancel=event=>{if(confirm.disabled)event.preventDefault();};
 confirm.onclick=async()=>{
  if(confirm.disabled||busy||loading||uploads)return;
  confirm.disabled=true;cancel.disabled=true;confirm.textContent='Excluindo…';error.textContent='';
  try{await json('/v1/conversations/'+encodeURIComponent(c.id),{method:'DELETE'});dialog.close();if(conversation===c.id)newConversation();await history();}
  catch(e){error.textContent='Não foi possível excluir: '+e.message;}
  finally{confirm.disabled=false;cancel.disabled=false;confirm.textContent='Excluir conversa';}
 };
 dialog.showModal();cancel.focus();
}
function confirmNewConversation(){return !($('prompt').value.trim()||files.length)||confirm('Descartar o rascunho e os anexos para começar uma nova conversa?');}
function supportedExecutionModes(){return selected()?.execution_modes||(['codex','claude'].includes(selected()?.backend)?['native','scoped']:['native']);}
function syncExecutionMode(){
 const started=!!conversation||!!parent,modes=supportedExecutionModes();
 if(!started&&!executionModeChosen)executionMode=modes.includes('native')?'native':'scoped';
 const isolated=executionMode==='scoped';
 const modeContract=Array.isArray(selected()?.execution_modes);
 $('execution-mode-choice').hidden=started||!modeContract;
 $('isolation-toggle').setAttribute('aria-checked',String(isolated));
 $('isolation-toggle').disabled=started||busy||loading||submitting||modes.length<2;
 $('execution-mode-label').textContent=isolated?'Conversa isolada':'Conversa nativa';
 const warning=$('execution-mode-unavailable');warning.hidden=!selected()||(modes.includes(executionMode)&&modes.length>1);
 warning.textContent=modes.length===1&&modes.includes(executionMode)?(isolated?'Este modelo usa isolamento obrigatório.':'Este modelo oferece apenas o modo nativo.'):'Este modelo não oferece o modo '+(isolated?'isolado':'nativo')+'. Escolha outro modelo'+(started?'.':' ou altere o modo antes de enviar.');
 const indicator=$('execution-mode-indicator');indicator.hidden=!started||!modeContract;indicator.dataset.isolated=String(isolated);
 const label=isolated?'Conversa isolada · isolamento ligado':'Conversa nativa · isolamento desligado';indicator.title=label;indicator.setAttribute('aria-label',label);
 $('dropzone').classList.toggle('has-execution-mode',started&&modeContract);
}
$('isolation-toggle').onclick=()=>{if(conversation||parent||busy||loading||submitting)return;executionMode=executionMode==='scoped'?'native':'scoped';executionModeChosen=true;invalidateResources();updateComposer();saveView();};
function newConversation(title='Nova Conversa'){
 resourceSelections=[];invalidResourceTokens.clear();if(submitting||cancelling||loading||uploads){status('Aguarde o envio atual antes de iniciar outra conversa.');return;}
 conversationLoad++;
 streamDisconnected=false;$('resume-execution').hidden=true;
 restoreSelection();clearSubmission();resetProjectFiles();
 resetActivity();active=null;setConversationTitle(title);
 if(controller)controller.abort();controller=null;job='';last=0;parent=null;conversation='';executionMode='native';executionModeChosen=false;files=[];renderFiles();
 $('messages').replaceChildren(welcomeTemplate.cloneNode(true));bindSuggestions();modelAvailability();$('prompt').value='';updateComposer();saveView();$('context-meter').textContent='Nova conversa · contexto independente';setBusy(false);status('');$('prompt').focus({preventScroll:true});refreshProjectPermissions();
}
function chooseProject(id){
 if(busy||loading||uploads)return;
 const draft=$('prompt').value;$('project').value=id;invalidateResources();const stale=[...invalidResourceTokens];newConversation();invalidResourceTokens=new Set(stale);$('prompt').value=draft;updateComposer();saveView();renderProjects();syncActiveProjectBadge();
}
function renderProjects(){
 syncActiveProjectBadge();const matches=conversations;
 $('projects').replaceChildren(...Array.from($('project').options).filter(o=>o.value!=='sem-projeto').map(o=>{
  const group=document.createElement('details');group.className='project-group';
  group.open=expandedProjects.get(o.value)??o.selected;
  group.ontoggle=()=>expandedProjects.set(o.value,group.open);
  const heading=document.createElement('summary');heading.className=o.selected?'active':'';
  const button=document.createElement('button');button.textContent=o.textContent;button.title=o.textContent;
  button.onclick=e=>{e.preventDefault();group.open=!group.open;expandedProjects.set(o.value,group.open);};
  const actions=document.createElement('span');actions.className='project-actions';
  const trigger=document.createElement('button');trigger.type='button';trigger.textContent='⋯';trigger.setAttribute('aria-label','Ações do projeto '+o.textContent);trigger.setAttribute('aria-expanded','false');
  const menu=document.createElement('div');menu.className='conversation-actions-menu project-actions-menu';menu.setAttribute('popover','auto');
  const navigate=document.createElement('button');navigate.type='button';navigate.append(TailUI.icon('folder'),document.createTextNode('Navegar para pasta do projeto'));
  navigate.onclick=()=>{menu.hidePopover();void navigateProjectFolder(o.value);};
  trigger.onclick=e=>{e.preventDefault();e.stopPropagation();if(menu.matches(':popover-open')){menu.hidePopover();return;}menu.showPopover();const rect=trigger.getBoundingClientRect();menu.style.left=Math.max(8,Math.min(rect.right-menu.offsetWidth,innerWidth-menu.offsetWidth-8))+'px';menu.style.top=Math.max(8,Math.min(rect.bottom+4,innerHeight-menu.offsetHeight-8))+'px';navigate.focus();};
  menu.addEventListener('toggle',e=>trigger.setAttribute('aria-expanded',String(e.newState==='open')));
  menu.addEventListener('click',e=>e.stopPropagation());
  menu.append(navigate);actions.append(trigger,menu);heading.append(button,actions);
  const children=document.createElement('div');children.className='project-conversations';
  const items=matches.filter(c=>c.project===o.value);
  children.replaceChildren(...items.map(conversationRow));
  const create=document.createElement('button');create.className='project-new';create.append(TailUI.icon('folder-message'),document.createTextNode('Nova Conversa'));create.setAttribute('aria-label','Nova Conversa em '+o.textContent);
  create.onclick=()=>{
   if(submitting||cancelling||loading||uploads){status('Aguarde o envio atual antes de iniciar outro chat.');return;}
   if(!confirmNewConversation())return;
   $('project').value=o.value;newConversation('Nova Conversa no projeto '+o.textContent);expandedProjects.set(o.value,true);renderProjects();$('sidebar').classList.remove('open');
  };children.prepend(create);
  if(!items.length){const empty=document.createElement('p');empty.className='empty-history';empty.textContent='Nenhuma conversa';children.append(empty);}
  group.append(heading,children);return group;
 }));
 $('history').replaceChildren(...matches.filter(c=>c.project==null||c.project===''||c.project==='sem-projeto').map(conversationRow));
 if(!$('history').children.length){const empty=document.createElement('p');empty.className='empty-history';empty.textContent='Suas conversas aparecerão aqui.';$('history').append(empty);}
}
const answerMarkdown=window.markdownit({html:false,linkify:false,breaks:true});answerMarkdown.renderer.rules.image=()=>'';
answerMarkdown.renderer.rules.link_open=(tokens,index,options,env,self)=>{
 tokens[index].attrSet('target','_blank');tokens[index].attrSet('rel','noopener noreferrer');
 return self.renderToken(tokens,index,options);
};
const answerFence=answerMarkdown.renderer.rules.fence;
answerMarkdown.renderer.rules.fence=(tokens,index,options,env,self)=>{const token=tokens[index];if(token.info.trim().toLowerCase()==='json'){try{token.content=JSON.stringify(JSON.parse(token.content),null,2)+'\n';}catch{}}return answerFence(tokens,index,options,env,self);};
function renderAnswer(body,value){
 const source=typeof value==='string'?value:value==null?'':JSON.stringify(value,null,2);
 let parsed;try{const candidate=JSON.parse(source);if(candidate&&typeof candidate==='object')parsed=candidate;}catch{}
 if(parsed!==undefined){const pre=document.createElement('pre'),code=document.createElement('code');code.className='language-json';code.textContent=JSON.stringify(parsed,null,2);pre.append(code);body.replaceChildren(pre);syncResponseMotion(body);return source;}
 body.innerHTML=answerMarkdown.render(source);syncResponseMotion(body);return source;
}
function syncResponseMotion(body){
 body.querySelector('.response-motion')?.remove();
 if(body.dataset.motion!=='answer')return;
 const final=body.lastElementChild;if(!final||final.tagName!=='P')return;
 const marker=document.createElement('span');marker.className='response-motion';marker.dataset.motion='answer';marker.setAttribute('aria-hidden','true');final.append(marker);
}
function setAnswer(answer,value){answer.body.rawAnswer=renderAnswer(answer.body,value);}
function messageAttachments(message,attachments=[]){
 const gallery=document.createElement('div');gallery.className='message-images';
 for(const file of attachments){
  const card=document.createElement('span');card.className='attachment message-file';
  const icon=projectFileIcon(file.name);
  const label=document.createElement('span');label.textContent=file.name;card.append(icon,label);
  if(!file.preview_url){gallery.append(card);continue;}
  const button=document.createElement('button');button.type='button';button.className='attachment message-image';button.setAttribute('aria-label','Ampliar imagem '+file.name);
  const img=document.createElement('img');img.src=file.preview_url;img.alt=file.name;img.className='attachment-preview';img.onerror=()=>button.replaceWith(card);
  const name=document.createElement('span');name.className='attachment-name';name.textContent=file.name;
  button.append(img,name);button.onclick=()=>{
   const dialog=document.createElement('dialog');dialog.className='image-modal';dialog.setAttribute('aria-label',file.name);
   const close=document.createElement('button');close.type='button';close.className='dialog-close';close.textContent='×';close.setAttribute('aria-label','Fechar imagem');close.autofocus=true;close.onclick=()=>dialog.close();
   const full=document.createElement('img');full.src=file.preview_url;full.alt=file.name;
   dialog.append(close,full);dialog.onclick=e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();}};
   dialog.onclose=()=>{dialog.remove();button.focus();};document.body.append(dialog);dialog.showModal();
  };gallery.append(button);
 }
 if(gallery.childElementCount)message.body.prepend(gallery);
}
function bubble(role,text=''){const el=document.createElement('article');el.className='message chat-item '+role;const body=document.createElement('div');body.className='text chat-bubble';if(role==='assistant')body.rawAnswer=renderAnswer(body,text);else body.textContent=text;el.append(body);$('messages').append(el);return{el,body};}
function eventToolName(data={}){
 const command=typeof data.command_name==='string'?data.command_name.trim():'';
 if(command&&/^[\w./+-]{1,128}$/.test(command))return command.split(/[\\/]/).pop().slice(0,64);
 return ({Read:'arquivo',Glob:'arquivos',Grep:'texto',webSearch:'web',Bash:'ferramenta',commandExecution:'ferramenta'})[data.tool]||'';
}
function activityTitle(e){
 const data=e.data||{},type=e.type,tool=eventToolName(data);
 const condition=executionCondition(data.condition||data.error);if(condition)return condition.title;
 if(type==='tool_start')return data.command_name&&tool?'Executando comando '+tool:({Read:'Lendo arquivo',Glob:'Buscando arquivos',Grep:'Pesquisando no texto',webSearch:'Pesquisando na web'})[data.tool]||('Executando '+(tool||'ferramenta'));
 if(type==='tool_end')return (data.status==='failed'?'Falha na ferramenta':'Ferramenta concluída');
 if(['thinking','reasoning_delta','reasoning_summary'].includes(type))return 'Pensando';
 if(['queued','running','loading','planning','completed','failed','error','cancelled','interrupted'].includes(type))return labels[type]||'Execução interrompida';
 if(type==='plan_updated')return 'Plano atualizado';
 if(type==='maestro_planning')return 'Maestro planejando';
 if(type==='maestro_plan')return 'Plano de execução definido';
 if(type==='maestro_step')return 'Especialista começou a trabalhar';
 return ({session_resumed:'Contexto da conversa retomado',context_compacting:'Otimizando o contexto',context_compacted:'Contexto otimizado',validating_changes:'Validando alterações',changes_applied:'Alterações aplicadas',deployment_failed:'Alterações não aplicadas',reload_scheduled:'Painel será atualizado'})[type]||'';
}
function appendActivityTitle(list,e){
 list.eventIds??=new Set();list.toolRows??=new Map();
 const eventId=e.id==null?'':String(e.id);if(eventId&&list.eventIds.has(eventId))return; if(eventId)list.eventIds.add(eventId);
 const data=e.data||{},toolId=data.tool_id==null?'':String(data.tool_id),toolFailed=e.type==='tool_end'&&(data.status==='failed'||data.result?.isError===true);
 if(e.type==='tool_end'&&toolId&&list.toolRows.has(toolId)){const row=list.toolRows.get(toolId);row.dataset.state=toolFailed?'failed':'completed';row.textContent=toolFailed?'Falha: '+row.textContent:row.textContent.replace(/^Executando/,'Executou').replace(/^Lendo/,'Leu').replace(/^Buscando/,'Buscou').replace(/^Pesquisando/,'Pesquisou');return;}
 const title=activityTitle(e);if(!title)return;
 if(title==='Pensando'&&list.lastElementChild?.textContent===title)return;
 const row=document.createElement('li');row.textContent=title;row.dataset.state=toolFailed?'failed':e.type; if(eventId)row.dataset.eventId=eventId;
 list.append(row);if(e.type==='tool_start'&&toolId)list.toolRows.set(toolId,row);
}
function setActivitySummary(answer,text){answer.activitySummary.textContent=text;answer.activity.hidden=false;}
function assistant(id='',model=$('model').value,replayTools=false){
 const a=bubble('assistant'),title=document.createElement('h3'),badge=document.createElement('span');
 badge.className='model-badge';badge.setAttribute('aria-hidden','true');badge.textContent=modelIcon(model);
 title.append(badge,document.createTextNode(names[model]||models.find(m=>m.id===model)?.name||'Resposta'));a.el.prepend(title);
 const chip=document.createElement('p');chip.className='run-highlight';chip.textContent='⏳ Aguardando execução';a.el.insertBefore(chip,a.body);
 const activity=document.createElement('details'),activitySummary=document.createElement('summary'),milestones=document.createElement('ol');
 activity.className='message-activity';activitySummary.textContent='Etapas da execução';milestones.className='activity-milestones';activity.append(activitySummary,milestones);activity.hidden=!replayTools;
 a.el.insertBefore(activity,a.body);
 if(replayTools)activity.addEventListener('toggle',()=>{if(activity.open&&!activity.dataset.loaded){activity.dataset.loaded='1';loadResponseTools(id,milestones).catch(()=>{delete activity.dataset.loaded;const row=document.createElement('li');row.textContent='Não foi possível recuperar os títulos das etapas.';milestones.replaceChildren(row);});}});
 const meta=document.createElement('div');meta.className='run-meta';a.el.append(meta);
 return{...a,activity,activitySummary,milestones,meta,chip};
}
async function loadResponseTools(id,target){
 target.replaceChildren();target.eventIds=new Set();target.toolRows=new Map();
 const response=await api('/v1/jobs/'+encodeURIComponent(id)+'/events',{signal:AbortSignal.timeout(15000)});
 const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
 try{while(true){const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});let end;
  while((end=buffer.indexOf('\n\n'))>=0){const block=buffer.slice(0,end);buffer=buffer.slice(end+2);const line=block.split('\n').find(l=>l.startsWith('data: '));if(!line)continue;
   const e=JSON.parse(line.slice(6));if(!['answer_delta','context_usage','quota_before','quota_after'].includes(e.type))appendActivityTitle(target,e);
  }
 }}finally{await reader.cancel();}
 if(!target.children.length){const row=document.createElement('li');row.textContent='Nenhuma etapa foi registrada nesta execução.';target.append(row);}
}
// Animation follows execution events, not token timing or locally inferred progress.
function paintMotion(mode){
 $('status').removeAttribute('data-motion');
 for(const node of [$('activity-state'),active?.chip])if(node)node.removeAttribute('data-motion');
 if(active){
  active.body.dataset.motion=mode?'answer':'';syncResponseMotion(active.body);
  active.el.setAttribute('aria-busy',String(!!mode));
 }
}
function updateMotion(type){
 if(['completed','failed','cancelled','interrupted','error'].includes(type))paintMotion('');
 else if(type==='answer_delta')paintMotion('answer');
 else if(['queued','running','loading','planning','thinking','reasoning_delta','reasoning_summary',
   'tool_start','tool_end','plan_updated','session_resumed','context_compacting','context_compacted',
   'validating_changes','changes_applied','deployment_failed','reload_scheduled'].includes(type))paintMotion('working');
}
function scroll(){const box=$('messages');if(box.scrollHeight-box.scrollTop-box.clientHeight<250)box.scrollTop=box.scrollHeight;updateLatest();}
function event(e){
 if(e.id<=last)return;last=e.id;
 if(e.type==='approval_required'){showApproval(e.data);return;}
 if(e.type==='approval_resolved'){document.getElementById('approval-'+e.data.approval_id)?.remove();return;}
 if(active){appendActivityTitle(active.milestones,e);if(active.milestones.children.length){active.activity.hidden=false;setActivitySummary(active,['completed','failed','cancelled','interrupted'].includes(e.type)?(labels[e.type]||'Etapas da execução'):'Trabalhando…');}}
 recordActivity(e);updateMotion(e.type);if(active)active.chip.textContent=$('activity-state').textContent;
 if(e.type==='context_usage'){paintContext(e.data);return;}
 if(e.type==='plan_updated'){status('Plano atualizado');}
 else if(e.type==='answer_delta'){active.body.rawAnswer=(active.body.rawAnswer||'')+e.data.text;renderAnswer(active.body,active.body.rawAnswer);status('Recebendo resposta…');}
 else if(e.type==='reasoning_delta'||e.type==='reasoning_summary'){if(active)setActivitySummary(active,'Pensando…');status('Raciocinando…');}
 else if(e.type==='quota_before'||e.type==='quota_after'){if(selected()?.backend==='codex')quotaSnapshot(e.type.endsWith('before')?'before':'after',e.data);}
 else if(e.type==='quota_update'){if(e.data?.provider===selected()?.backend&&['codex','claude'].includes(selected()?.backend))void quota();return;}
 else{status(labels[e.type]||e.type);}
 scroll();
}
function executionCondition(code){
 if(['claude_authentication_required','claude_authentication_failed'].includes(code))return {title:'Renovar acesso ao Claude',message:'Seu acesso ao Claude precisa ser renovado. No painel administrativo, encontre Claude Code e clique em “Renovar acesso”. Conclua o login no navegador e envie sua mensagem novamente.'};
 if(['claude_quota_exhausted','claude_rate_limit'].includes(code))return {title:'Aguardar renovação da cota',message:'Sua cota do Claude está temporariamente esgotada. Aguarde a renovação ou selecione outro provedor para continuar nesta conversa.'};
 return null;
}
function executionError(error){
 const condition=executionCondition(error);if(condition)return condition.message;
 if(/context_limit_exceeded|exceed_context_size|exceeds the available context|maximum context length|source_context_limit|conversation_context_limit|context_window_exceeded/i.test(String(error)))return 'O conteúdo ultrapassou o limite de contexto do modelo. Os anexos envolvidos nesta tentativa foram retirados do contexto das próximas mensagens; os arquivos e o histórico foram preservados. Você pode continuar nesta conversa. Para analisar o CSV, envie uma parte menor ou disponibilize-o em uma pasta autorizada.';
 return 'A execução não foi concluída: '+error;
}
async function result(expectedJob=job,expectedController=controller,snapshot=null){
 const r=snapshot||await json('/v1/jobs/'+expectedJob);if(job!==expectedJob||controller!==expectedController)return r;const terminal={completed:'Concluído',failed:'Falha na execução',cancelled:'Execução cancelada',interrupted:'Execução interrompida'};
 const condition=executionCondition(r.result?.condition||r.result?.error);
 updateMotion(r.state);$('activity-state').textContent=condition?'ℹ '+condition.title:(activityIcons[r.state]||'•')+' '+(labels[r.state]||r.state);
 if(active){
  active.chip.textContent=$('activity-state').textContent;
  const data=r.result||{},seconds=Number(data.total_seconds);
  if(data.context_usage)paintContext(data.context_usage,data.metrics);else if(data.metrics)paintLocalUsage(data.metrics);
  if(data.answer!==undefined)setAnswer(active,data.answer);
  if(condition)setAnswer(active,condition.message);else if(data.error)setAnswer(active,executionError(data.error));
  if(r.state==='cancelled'&&!active.body.rawAnswer)setAnswer(active,'Execução cancelada.');
  const modelId=data.model||$('model').value,model=modelId?modelIcon(modelId)+' '+(names[modelId]||models.find(m=>m.id===modelId)?.name||modelId):'',duration=Number.isFinite(seconds)&&seconds>0?seconds.toFixed(1)+' s':'';
  active.meta.textContent=[model,duration].filter(Boolean).join(' · ');
  if(data.deployment)active.meta.textContent+=(active.meta.textContent?' · ':'')+(data.deployment.applied?'Alterações aplicadas':'Alterações não aplicadas');
  setActivitySummary(active,condition?condition.title:terminal[r.state]?(duration?'Trabalhou por '+duration:terminal[r.state]):'Trabalhando…');
  if(data.incomplete)status('Resposta incompleta. Reduza o escopo e tente novamente.');else status(condition?.title||terminal[r.state]||r.state);
  if(data.deployment)status(data.deployment.applied?'Alterações aplicadas ao projeto. Verificando atualização do painel…':'Alterações não aplicadas.');
  if(selected()?.backend==='codex'){if(data.quota_before)quotaSnapshot('before',data.quota_before);if(data.quota_after)quotaSnapshot('after',data.quota_after);}
 }
 if(['completed','failed','cancelled','interrupted'].includes(r.state)){
  parent=job;
  const current=conversations.find(c=>c.id===conversation);
  if(current){current.state=r.state;current.last_job_id=r.id||expectedJob;observeConversation(current);
   if(r.state==='completed')conversationActivity[current.id].unread=document.hidden;
   saveConversationActivity();renderProjects();if($('conversation-search-dialog').open)renderConversationSearch();
  }
 }saveView();return r;
}
async function watch(retries=0){streamDisconnected=false;$('resume-execution').hidden=true;if(controller)controller.abort();controller=new AbortController();const current=controller;setBusy(true);paintMotion('working');try{const r=await api('/v1/jobs/'+job+'/events',{headers:{'Last-Event-ID':String(last)},signal:controller.signal});const reader=r.body.getReader(),decoder=new TextDecoder();let buffer='';while(true){const{value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});let end;while((end=buffer.indexOf('\n\n'))>=0){const block=buffer.slice(0,end);buffer=buffer.slice(end+2);const data=block.split('\n').find(l=>l.startsWith('data: '));if(controller!==current)return;if(data)event(JSON.parse(data.slice(6)));}}if(controller!==current)return;const final=await result();if(controller!==current)return;if(['running','queued'].includes(final.state)){if(retries<3){status('Reconectando à execução em andamento…');await new Promise(resolve=>setTimeout(resolve,1000));if(controller!==current)return;return await watch(retries+1);}throw Error('stream_closed');}if(controller!==current)return;await quota();await history();}catch(e){if(controller===current&&e.name!=='AbortError'){paintMotion('');$('activity-state').textContent='⚠ Conexão interrompida';streamDisconnected=true;$('resume-execution').hidden=false;status('Conexão interrompida. A execução pode continuar no servidor. Use Retomar acompanhamento ou Cancelar execução.');await history();}}finally{if(controller===current){setBusy(false);checkVersion();}}}
async function load(id,legacy=false,restoredView=null){
 if(submitting||cancelling||uploads)return;const request=++conversationLoad,priorDraft=$('prompt').value;loading=true;if(controller){controller.abort();controller=null;}setBusy(true);
 try{
  let data;
  try{data=legacy?{turns:[await json('/v1/jobs/'+encodeURIComponent(id))]}:await json('/v1/conversations/'+encodeURIComponent(id));}
  catch(e){if(e.status!==404||!legacyHistory)throw e;data={turns:[await json('/v1/jobs/'+encodeURIComponent(id))]};}
  if(request!==conversationLoad)return;
  if(!data.turns?.length)throw Error('Conversa vazia');
  parent=null;job='';last=0;resetActivity();setConversationTitle(data.title||conversations.find(item=>item.id===id)?.title||'Nova Conversa');
  executionMode=data.execution_mode||data.turns[0].request?.execution_mode||conversations.find(c=>c.id===id)?.execution_mode||conversations.find(c=>c.id===id)?.execution?.execution_mode||'native';
  conversation=id;if(!restoredView)saveView();files=[];renderFiles();$('messages').replaceChildren();$('prompt').value='';
  for(const r of data.turns){
   $('project').value=r.project;syncActiveProjectBadge();const model=r.request?.backend==='qwen'?'qwen-local':r.request?.model;
   if(models.some(m=>m.id===model)){$('model').value=model;updateEfforts();$('effort').value=r.request?.effort||$('effort').value;}
   messageAttachments(bubble('user',r.request?.prompt||'Execução anterior'),r.attachments);active=assistant(r.id,model,r!==data.turns[data.turns.length-1]||['completed','failed','cancelled','interrupted'].includes(r.state));job=r.id;
   if(r!==data.turns[data.turns.length-1]){
    const condition=executionCondition(r.result?.condition||r.result?.error);
    setAnswer(active,condition?.message??r.result?.answer??(r.result?.error?executionError(r.result.error):r.state));
    active.chip.textContent=condition?'ℹ '+condition.title:(activityIcons[r.state]||'•')+' '+(labels[r.state]||r.state);
    const seconds=Number(r.result?.total_seconds),duration=Number.isFinite(seconds)&&seconds>0?seconds.toFixed(1)+' s':'';
    active.meta.textContent=[r.result?.model||model||'',duration].filter(Boolean).join(' · ');
    setActivitySummary(active,condition?condition.title:duration?'Trabalhou por '+duration:(labels[r.state]||'Etapas da execução'));
   }
  }
  $('access-mode').value=data.turns.at(-1).request?.access_mode||'ask';syncAccessMode();void quota();
  beginActivity(job);
  resetProjectFiles();await refreshProjectPermissions();if(request!==conversationLoad)return;
  expandedProjects.set($('project').value,true);renderProjects();$('sidebar').querySelector('.conversation-row > button[aria-current="true"]')?.scrollIntoView({block:'nearest'});last=0;$('sidebar').classList.remove('open');loading=false;
  if(restoredView)restoreView(restoredView);
  const latest=data.turns.at(-1);
  if(['completed','failed','cancelled','interrupted'].includes(latest.state))await result(job,controller,latest);
  else if(!restoredView)await watch();
 }catch(e){if(request!==conversationLoad)return;loading=false;setBusy(false);$('prompt').value=priorDraft;updateComposer();$('sidebar').classList.remove('open');status('Não foi possível abrir a conversa. Seu rascunho foi preservado: '+e.message);}
 finally{if(request===conversationLoad){loading=false;setBusy(false);}}
}
async function upload(list){if(!canUpload()){status('Não foi possível anexar: anexos estão desativados na administração.');return;}if(busy||loading||uploads)return;uploads++;setBusy(busy);$('project').disabled=true;renderFiles();try{for(const f of Array.from(list)){if(files.length>=MAX_ATTACHMENTS){status('Limite de 20 anexos atingido. Remova um anexo antes de adicionar outro.');break;}const audio=/\.(wav|mp3|m4a|ogg|flac|webm|aac|opus)$/i.test(f.name);if(f.size>(audio?256:50)*1024*1024){status(audio?'O limite por áudio é 256 MiB.':'O limite por documento é 50 MiB.');continue;}status('Enviando e preparando '+f.name+'… Áudios são transcritos localmente.');try{const r=await json('/v1/files?project_id='+encodeURIComponent($('project').value)+'&backend='+encodeURIComponent(selected().backend)+'&model='+encodeURIComponent(selected().id),{method:'POST',headers:{'X-Filename':encodeURIComponent(f.name)},body:f,signal:AbortSignal.timeout(audio?8200000:600000)});files.push({id:r.file_id,name:f.name,preview_url:r.preview_url});renderFiles();saveView();status('Arquivo recebido.');}catch(e){attachmentNotice(f.name,e.code);status('Não foi possível enviar: '+attachmentError(e.code||e.message));}}}finally{uploads--;setBusy(busy);renderFiles();updateComposer();}}
$('files-retry').onclick=()=>fileTree.basePath?loadProjectFileDirectory(fileTree.rootId,fileTree.basePath):loadProjectFileRoots(true);
async function send(){if(busy||loading||uploads||policyPending||!selected())return;const prompt=$('prompt').value.trim();if(!prompt)return;const reserved=prompt.match(/(?:^|\s)(@@|\/\/)[^\s@/]+/);if(reserved){status(reserved[1].startsWith('@@')?'Agentes do Tail Harness ainda não estão disponíveis.':'Skills e comandos do Tail Harness ainda não estão disponíveis.');return;}syncResourceSelections();const stale=[...invalidResourceTokens].find(token=>prompt.split(/\s+/).includes(token));if(stale){status('O recurso '+stale+' foi invalidado pela troca de projeto ou motor. Selecione-o novamente antes de enviar.');return;}const m=selected();if(!supportedExecutionModes().includes(executionMode)){status('Este modelo não oferece o modo selecionado. Escolha outro modelo.');return;}if(files.length&&!canUpload()){status('Este modelo não permite anexos. Remova os arquivos ou escolha um modelo com essa permissão.');return;}syncResourceSelections();rememberSelection();submitting=true;setBusy(true);status('Enviando pedido ao servidor…');let sentJob=null;try{if(m.backend==='codex'){status('Consultando cota antes da execução…');quotaSnapshot('before',await json('/v1/usage'));}const data={project_id:$('project').value,prompt,file_ids:files.map(f=>f.id),backend:m.backend,model:m.id,effort:$('effort').value,access_mode:$('access-mode').value,resource_selections:resourceSelections.map(({id,revision,token})=>({id,revision,token}))};if(parent)data.parent_job_id=parent;else if(Array.isArray(m.execution_modes))data.execution_mode=executionMode;const r=await json('/v1/jobs',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':submissionKey(data)},body:JSON.stringify(data)});clearSubmission();$('welcome')?.remove();messageAttachments(bubble('user',prompt),files);active=assistant(r.job_id,m.id);beginActivity(r.job_id);job=r.job_id;sentJob=job;submitting=false;$('cancel').disabled=false;parent=job;if(!conversation){conversation=job;setConversationTitle(prompt);}files=[];renderFiles();last=0;$('prompt').value='';resourceSelections=[];invalidResourceTokens.clear();updateComposer();saveView();$('messages').scrollTop=$('messages').scrollHeight;await history();await watch();}catch(e){status('Não foi possível executar: '+e.message+(e.status===429&&submitting?' Seu rascunho foi preservado.':''));}finally{if(!sentJob||job===sentJob){submitting=false;setBusy(false);}}}
function resetProjectFiles(){
 const project=$('project').value;if(fileTree.project===project)return;
 fileTree.project=project;fileTree.selected.clear();fileTree.anchor='';renderProjectFileTree();renderProjectFileSelection();
}
function renderProjectFileSelection(){
 const count=fileTree.selected.size,node=$('files-selection-count');node.textContent=count?`${count} selecionado(s)`:'';node.hidden=!count;
}
function renderProjectFileTree(){
 const entries=fileTree.cache.get(fileTree.rootId+'\0'+fileTree.basePath)||[];$('files-tree').replaceChildren();
 const group=document.createElement('ul');group.setAttribute('role','group');renderProjectFileEntries(group,entries);$('files-tree').append(group);renderProjectFileSelection();
}
function renderProjectFileEntries(list,entries,tree=fileTree){
 for(const entry of entries.filter(entry=>!tree.foldersOnly||entry.type==='directory')){
  const item=document.createElement('li');item.setAttribute('role','treeitem');item.setAttribute('aria-selected',String(tree.selected.has(entry.path)));item.tabIndex=0;item.dataset.path=entry.path;
  const row=document.createElement('div');row.className='project-file-row';
  if(entry.type==='directory'){
   const open=tree.expanded.has(entry.path),toggle=document.createElement('button');toggle.type='button';toggle.className='file-chevron';toggle.setAttribute('aria-label',(open?'Recolher ':'Expandir ')+entry.name);toggle.setAttribute('aria-expanded',String(open));toggle.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>';toggle.onclick=()=>tree.foldersOnly?toggleProjectFolder(entry):toggleProjectDirectory(entry);
   const name=document.createElement('span');name.textContent=entry.name;name.title=entry.name;row.append(toggle,projectFileIcon(entry.name,true,open),name);item.append(row);
   if(open){const childList=document.createElement('ul');childList.setAttribute('role','group');childList.className='project-file-children';const children=tree.cache.get(tree.rootId+'\0'+entry.path);if(children)renderProjectFileEntries(childList,children,tree);else{const loading=document.createElement('li');loading.textContent='Carregando…';loading.setAttribute('role','status');childList.append(loading);}item.append(childList);}
  }else{
   const name=document.createElement('span');name.textContent=entry.name;name.title=entry.name;row.append(projectFileIcon(entry.name),name);item.append(row);
  }
  if(tree.foldersOnly){
   item.onclick=e=>{if(e.target.closest('button')||e.target.closest('[role=treeitem]')!==item)return;selectProjectFolder(entry);};
   item.onkeydown=e=>{if(e.target!==item)return;if([' ','Enter'].includes(e.key)){e.preventDefault();selectProjectFolder(entry);}else if(e.key==='ArrowRight'&&!tree.expanded.has(entry.path)||e.key==='ArrowLeft'&&tree.expanded.has(entry.path)){e.preventDefault();void toggleProjectFolder(entry);}};
   list.append(item);continue;
  }
  item.onclick=e=>{if(e.target.closest('button')||e.target.closest('[role="treeitem"]')!==item)return;selectProjectFileEntry(item,entry,e);};
  item.onkeydown=e=>{if(e.target!==item)return;if(e.key===' '){e.preventDefault();selectProjectFileEntry(item,entry,{ctrlKey:true,shiftKey:e.shiftKey});}else if(e.key==='Enter'){e.preventDefault();if(entry.type==='directory')void toggleProjectDirectory(entry);else{const paths=tree.selected.has(entry.path)?Array.from(tree.selected):[entry.path];void attachSelectedProjectFiles({root_id:tree.rootId,paths});}}};
  if(entry.type!=='directory'){item.setAttribute('aria-keyshortcuts','Enter Space');item.title='Enter para anexar; Espaço para selecionar.';}
  item.draggable=canUpload()&&!busy&&!uploads&&files.length<MAX_ATTACHMENTS;item.ondragstart=e=>{if(e.target.closest('[role="treeitem"]')!==item)return;if(!canUpload()||busy||uploads||files.length>=MAX_ATTACHMENTS){e.preventDefault();return;}if(!tree.selected.has(entry.path)){tree.selected.clear();tree.selected.add(entry.path);tree.anchor=entry.path;for(const node of $('files-tree').querySelectorAll('[role=treeitem]'))node.setAttribute('aria-selected',String(tree.selected.has(node.dataset.path)));renderProjectFileSelection();}const paths=Array.from(tree.selected);e.dataTransfer.effectAllowed='copy';e.dataTransfer.setData('application/x-tail-authorized-project-files',JSON.stringify({root_id:tree.rootId,paths}));};
  list.append(item);
 }
}
function selectProjectFileEntry(item,entry,event={}){
 if(!canUpload()||uploads>0)return;if(files.length>=MAX_ATTACHMENTS){status('Limite de 20 anexos atingido. Remova um anexo antes de adicionar outro.');return;}
 const multi=event.ctrlKey||event.metaKey;
 if(event.shiftKey&&fileTree.anchor){
  const visible=Array.from($('files-tree').querySelectorAll('[role="treeitem"]')),start=visible.findIndex(node=>node.dataset.path===fileTree.anchor),end=visible.indexOf(item);
  if(start>=0&&end>=0){fileTree.selected.clear();for(const node of visible.slice(Math.min(start,end),Math.max(start,end)+1))fileTree.selected.add(node.dataset.path);}
 }else if(multi){if(fileTree.selected.has(entry.path))fileTree.selected.delete(entry.path);else if(files.length+fileTree.selected.size<MAX_ATTACHMENTS)fileTree.selected.add(entry.path);fileTree.anchor=entry.path;}
 else{fileTree.selected.clear();fileTree.selected.add(entry.path);fileTree.anchor=entry.path;}
 if(fileTree.selected.size>MAX_ATTACHMENTS-files.length){fileTree.selected=new Set([...fileTree.selected].slice(0,MAX_ATTACHMENTS-files.length));status('Limite de 20 anexos por mensagem. A seleção foi limitada aos espaços disponíveis.');}
 for(const node of $('files-tree').querySelectorAll('[role="treeitem"]'))node.setAttribute('aria-selected',String(fileTree.selected.has(node.dataset.path)));
 renderProjectFileSelection();
}
function projectFileIcon(filename='',folder=false,open=false){
 const theme=window.TailFileIcons,name=filename.replaceAll('\\','/').split('/').pop().toLowerCase();
 if(!theme){
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');svg.classList.add('project-file-icon');
  const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('fill','currentColor');path.setAttribute('d',folder?'M3 5h7l2 2h9v13H3z':'M5 2h9l5 5v15H5z');svg.append(path);return svg;
 }
 let id;
 if(folder){const names=theme[open?'folderNamesExpanded':'folderNames'];id=Object.hasOwn(names,name)?names[name]:theme[open?'folderExpanded':'folder'];}
 else{
  id=Object.hasOwn(theme.fileNames,name)?theme.fileNames[name]:null;
  // Match compound extensions (e.g. spec.ts) before their shorter suffixes.
  const parts=name.split('.');
  for(let i=1;!id&&i<parts.length;i++){const suffix=parts.slice(i).join('.');if(Object.hasOwn(theme.fileExtensions,suffix))id=theme.fileExtensions[suffix];}
  id=id||theme.file;
 }
 const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 16 16');svg.setAttribute('aria-hidden','true');svg.classList.add('project-file-icon');
 const use=document.createElementNS('http://www.w3.org/2000/svg','use');use.setAttribute('href','/assets/file-icons.svg#'+id);svg.append(use);return svg;
}
async function navigateProjectFolder(project){
 try{
  const data=await json('/v1/projects'),folder=data.details?.[project]?.root;
  if(!folder)throw new Error('Este projeto não possui uma pasta associada.');
  // Keep the active conversation and its draft independent of file navigation.
  setPanelView('files');setPanelOpen(true);await loadProjectFileRoots(true);
  const destination=await json('/v1/project-files?view=tree&navigate_project=1&project_id='+encodeURIComponent(project)+'&start=1&limit=100');
  ++fileTree.request;fileTree.rootId=destination.root_id;fileTree.basePath=destination.path;fileTree.expanded.clear();fileTree.selected.clear();fileTree.anchor='';
  $('project-folder-location')?.remove();
  const label=document.createElement('div');label.id='project-folder-location';label.className='file-tree-note';label.textContent=folder;label.title=folder;
  for(const button of $('files-roots').querySelectorAll('.file-root'))button.setAttribute('aria-expanded','false');
  $('files-roots').append(label,$('files-tree'));$('files-tree').hidden=false;$('files-no-roots').hidden=true;
  $('sidebar').classList.remove('open');await loadProjectFileDirectory(fileTree.rootId,fileTree.basePath);
 }catch(error){status('Não foi possível navegar para a pasta do projeto: '+error.message);}
}
async function loadProjectFileRoots(force=false){
 if(!interfaceReady||$('activity-panel').hidden||rightPanelView!=='files')return;
 const project=$('project').value;if(fileTree.project!==project)resetProjectFiles();
 if(fileTree.ready&&!force)return;if(force){fileTree.cache.clear();fileTree.expanded.clear();}const request=++fileTree.request;fileTree.ready=false;$('files-loading').hidden=false;$('files-error').hidden=true;$('files-retry').hidden=true;$('files-no-roots').hidden=true;
 try{const result=await json('/v1/project-files?view=tree&start=1&limit=100');if(request!==fileTree.request)return;
  fileTree.roots=(result.roots||[]).filter(root=>root.id==='home'||root.id==='media-user').sort((a,b)=>Number(b.id==='home')-Number(a.id==='home'));fileTree.ready=true;$('files-loading').hidden=true;$('files-roots').replaceChildren($('files-tree'));
  if(!fileTree.roots.length){$('files-no-roots').hidden=false;$('files-tree').replaceChildren();return;}
  for(const root of fileTree.roots){
   const section=document.createElement('div'),button=document.createElement('button');section.className='file-root-section';
   button.type='button';button.dataset.rootId=root.id;button.className='file-root';button.setAttribute('aria-expanded','false');button.setAttribute('aria-controls','files-tree');
   button.innerHTML='<svg class="root-chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>';
   button.append(projectFileIcon(root.id,true),document.createTextNode(root.id==='home'?'Pastas Locais':'Pastas Externas'));
   button.onclick=()=>{if(root.id===fileTree.rootId&&!$('files-tree').hidden){$('files-tree').hidden=true;button.setAttribute('aria-expanded','false');}else void selectProjectFileRoot(root);};
   section.append(button);$('files-roots').append(section);
  }
  const root=fileTree.roots.find(item=>item.id===fileTree.rootId)||fileTree.roots.find(item=>item.id==='home')||fileTree.roots[0];await selectProjectFileRoot(root);
 }catch(error){if(request!==fileTree.request)return;$('files-loading').hidden=true;$('files-error').hidden=false;$('files-error').textContent='Não foi possível carregar as pastas autorizadas: '+error.message;$('files-retry').hidden=false;}
}
async function selectProjectFileRoot(root){
 if(!root)return;
 const changed=fileTree.rootId!==root.id||fileTree.basePath!=='';
 fileTree.basePath='';$('project-folder-location')?.remove();
 fileTree.rootId=root.id;
 if(changed){fileTree.selected.clear();fileTree.anchor='';fileTree.expanded.clear();$('files-tree').replaceChildren();}
 for(const button of $('files-roots').querySelectorAll('.file-root')){const active=button.dataset.rootId===root.id;button.setAttribute('aria-expanded',String(active));if(active)button.after($('files-tree'));}
 $('files-tree').hidden=false;renderProjectFileSelection();
 if(fileTree.cache.has(root.id+'\0'))renderProjectFileTree();else await loadProjectFileDirectory(root.id,'');
}

async function loadProjectFileDirectory(rootId,path){
 const version=fileTree.request,key=rootId+'\0'+path;
 try{const data=await json('/v1/project-files?view=tree&root_id='+encodeURIComponent(rootId)+'&path='+encodeURIComponent(path)+'&start=1&limit=100');if(version!==fileTree.request||rootId!==fileTree.rootId)return;
  fileTree.cache.set(key,data.entries||[]);$('files-error').hidden=true;$('files-retry').hidden=true;if(path==='')$('files-no-roots').hidden=true;
  renderProjectFileTree();if(data.limited){$('files-error').hidden=false;$('files-error').textContent='Exibindo os primeiros 100 itens desta pasta.';$('files-error').className='file-tree-note';}
 }catch(error){if(version!==fileTree.request||rootId!==fileTree.rootId)return;fileTree.expanded.delete(path);renderProjectFileTree();$('files-error').hidden=false;$('files-error').className='';$('files-error').textContent='Não foi possível abrir esta pasta: '+error.message;$('files-retry').hidden=false;}
}
async function toggleProjectDirectory(entry){
 if(fileTree.expanded.has(entry.path)){fileTree.expanded.delete(entry.path);renderProjectFileTree();return;}
 fileTree.expanded.add(entry.path);renderProjectFileTree();const key=fileTree.rootId+'\0'+entry.path;if(!fileTree.cache.has(key))await loadProjectFileDirectory(fileTree.rootId,entry.path);
}
async function attachSelectedProjectFiles(selection={root_id:fileTree.rootId,paths:Array.from(fileTree.selected)}){
 if(busy||loading||uploads)return;if(!canUpload()){status('Este modelo não permite anexar arquivos.');return;}
 const maxFiles=Math.max(0,MAX_ATTACHMENTS-files.length),paths=[...new Set(selection.paths||[])];if(!selection.root_id||!paths.length||!maxFiles){status(maxFiles?'Selecione arquivos para anexar.':'O limite de 20 anexos já foi atingido.');return;}
 uploads++;updateComposer();renderProjectFileTree();status('Anexando arquivos selecionados…');
 try{const url='/v1/project-files/attach?project_id='+encodeURIComponent($('project').value)+'&max_files='+maxFiles;const result=await json(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({root_id:selection.root_id,paths,backend:selected().backend,model:selected().id})});
  for(const attachment of result.attachments||[])files.push({id:attachment.file_id,name:attachment.name,preview_url:attachment.preview_url});renderFiles();saveView();
  if(selection.root_id===fileTree.rootId){for(const path of paths)fileTree.selected.delete(path);renderProjectFileTree();}
  for(const item of result.skipped||[])attachmentNotice(item.path,item.reason);
  const skipped=(result.skipped||[]).length;status(`${(result.attachments||[]).length} arquivo(s) anexado(s)${skipped?`; ${skipped} item(ns) indisponível(is) ou ignorado(s)`:''}.`);
 }catch(error){status('Não foi possível anexar a seleção: '+error.message);}finally{uploads--;renderFiles();updateComposer();renderProjectFileTree();}
}
function renderFiles(){
 $('attachments').replaceChildren(...files.map((f,i)=>{
  const el=document.createElement('span');el.className='attachment';
  if(f.preview_url){const img=document.createElement('img');img.src=f.preview_url;img.alt=f.name;img.className='attachment-preview';img.onerror=()=>img.remove();el.append(img);el.classList.add('image-attachment');}
  if(!f.preview_url)el.append(projectFileIcon(f.name));
  const name=document.createElement('span');name.textContent=f.name;name.className='attachment-name';el.append(name);
  const b=document.createElement('button');b.type='button';b.textContent='×';b.title='Retirar da próxima mensagem';b.setAttribute('aria-label','Remover anexo '+f.name);b.disabled=busy||uploads>0;
  b.onclick=()=>{files.splice(i,1);renderFiles();saveView();renderProjectFileTree();updateComposer();};el.append(b);return el;
 }));
 let count=$('attachment-count');if(!count){count=document.createElement('span');count.id='attachment-count';count.setAttribute('role','status');$('attachments').after(count);}
 count.hidden=files.length===0;count.textContent=files.length+' / '+MAX_ATTACHMENTS+' arquivos anexados';renderProjectFileSelection();
}
$('send').onclick=send;$('prompt').oninput=()=>{syncResourceSelections();openResourceMenu();};$('prompt').onkeydown=e=>{if(resourceKeydown(e))return;if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&e.keyCode!==229){e.preventDefault();send();}};$('cancel').onclick=async()=>{if(submitting||cancelling||!job)return;cancelling=true;$('cancel').disabled=true;try{await post('/v1/jobs/'+job+'/cancel',{});status('Cancelando…');if(streamDisconnected)await watch();}catch(e){status('Não foi possível cancelar: '+e.message);}finally{cancelling=false;setBusy(busy);}};
$('new').onclick=()=>{if(submitting||cancelling||loading||uploads||!confirmNewConversation())return;const loose=Array.from($('project').options).some(o=>o.value==='sem-projeto');if(loose)$('project').value='sem-projeto';newConversation(loose?'Nova Conversa':'Nova Conversa no projeto '+$('project').selectedOptions[0]?.textContent);renderProjects();history();$('sidebar').classList.remove('open');};
$('project').onchange=()=>{const draft=$('prompt').value;invalidateResources();const stale=[...invalidResourceTokens];newConversation();invalidResourceTokens=new Set(stale);$('prompt').value=draft;resourceSelections=[];updateComposer();saveView();renderProjects();history();};
$('model').onchange=()=>{invalidateResources();updateEfforts();rememberSelection();updateComposer();saveView();quota();};$('quota-refresh').onclick=()=>void quota();document.addEventListener('visibilitychange',()=>{if(!document.hidden&&['codex','claude'].includes(selected()?.backend))void quota();});setInterval(()=>{if(!document.hidden&&['codex','claude'].includes(selected()?.backend))void quota();},60000);$('effort').onchange=rememberSelection;$('attach').onclick=()=>$('file').click();$('file').onchange=()=>{upload($('file').files);$('file').value='';};
$('dropzone').ondragover=e=>{if(e.dataTransfer.types.includes('application/x-tail-authorized-project-files')||e.dataTransfer.files.length){e.preventDefault();$('dropzone').classList.add('drag');}};$('dropzone').ondragleave=()=>$('dropzone').classList.remove('drag');$('dropzone').ondrop=e=>{e.preventDefault();$('dropzone').classList.remove('drag');const payload=e.dataTransfer.getData('application/x-tail-authorized-project-files');if(payload){try{const selection=JSON.parse(payload);if(Array.isArray(selection.paths))void attachSelectedProjectFiles(selection);else status('A seleção de arquivos não é válida.');}catch{status('A seleção de arquivos não é válida.');}}else upload(e.dataTransfer.files);};
$('quota-toggle').onclick=()=>setQuotaOpen($('quota-panel').hidden);$('quota-refresh').onclick=quota;function toggleSidebar(){
 if(matchMedia('(max-width:620px)').matches){$('sidebar').classList.toggle('open');}
 else{document.body.classList.toggle('sidebar-collapsed');try{localStorage.setItem('sidebar-collapsed',document.body.classList.contains('sidebar-collapsed')?'1':'0');}catch{}}
 $('menu').setAttribute('aria-expanded',String(matchMedia('(max-width:620px)').matches?$('sidebar').classList.contains('open'):!document.body.classList.contains('sidebar-collapsed')));
}
$('menu').onclick=()=>{toggleSidebar();fitPanels();};
try{document.body.classList.toggle('sidebar-collapsed',localStorage.getItem('sidebar-collapsed')==='1');}catch{}
$('menu').setAttribute('aria-expanded',String(!matchMedia('(max-width:620px)').matches&&!document.body.classList.contains('sidebar-collapsed')));
$('setup').onclick=()=>$('setup-dialog').showModal();$('setup-close').onclick=()=>$('setup-dialog').close();
$('setup-code').textContent=`cd ~/Downloads
chmod +x setup-mcp.sh
./setup-mcp.sh '${location.origin}'`;
function bindSuggestions(){document.querySelectorAll('[data-prompt]').forEach(b=>b.onclick=()=>{$('prompt').value=b.dataset.prompt;updateComposer();saveView();$('prompt').focus();});}
bindSuggestions();
let startupTimer,readinessTimer,initializing=false,probing=false,interfaceReady=false,readinessRetryAt=0;
function setReadiness(ready,message=''){
 interfaceReady=ready;
 for(const node of [$('app-topbar'),$('sidebar'),document.querySelector('main'),$('activity-panel'),document.querySelector('.skip-link')])node.inert=!ready;
 $('startup-gate').hidden=ready;
 $('startup-gate').querySelector('span').textContent=ready?'':message||'Aguardando o servidor… A conexão será verificada automaticamente.';
 document.body.dataset.connectionReady=String(ready);
 if(!ready){for(const menu of document.querySelectorAll('.composer-menu:popover-open'))menu.hidePopover();for(const dialog of document.querySelectorAll('dialog[open]:not(#vpn-login)'))dialog.close();}
 else if($('vpn-login').open)$('vpn-login').close();
}
function modelAvailability(data,error=''){
 const panel=$('model-availability');panel.hidden=models.length>0&&!error;
 $('model-availability-title').textContent=error?'Não foi possível consultar os modelos':'Nenhum modelo disponível';
 $('model-availability-detail').textContent=error||'Adicione e habilite um provedor na administração deste servidor. Depois, inicie ou reinicie o harness para aplicar.';
 const link=$('admin-link');link.hidden=true;
 {const url=data?.admin_url;try{const parsed=new URL(url);if(['127.0.0.1','localhost'].includes(parsed.hostname)&&parsed.protocol==='http:'){link.href=parsed.href;link.hidden=false;}}catch{}}
 const shortcut=$('admin-shortcut'),topShortcut=$('admin-shortcut-top');shortcut.hidden=link.hidden;topShortcut.hidden=link.hidden;if(!link.hidden){shortcut.href=link.href;topShortcut.href=link.href;}
 $('model').disabled=!models.length||busy;$('effort').disabled=!models.length||busy;$('prompt').placeholder=models.length?'Envie uma mensagem… · Enter envia · Shift+Enter quebra linha':'Configure um modelo para enviar; seu rascunho será preservado.';
 $('model-note').hidden=!models.length;updateModelPermissions();
 if(!models.length){$('model').replaceChildren(new Option('Nenhum modelo disponível',''));$('effort').replaceChildren(new Option('Esforço indisponível',''));$('quota-short').textContent='Nenhum modelo selecionado';}
 if($('welcome'))$('welcome').hidden=!models.length;updateComposer();
}
async function initialize(){if(initializing)return;initializing=true;setReadiness(false);const retry=$('models-retry');retry.disabled=true;retry.textContent='Verificando…';try{
 const[p,m]=await Promise.all([json('/v1/projects',{signal:AbortSignal.timeout(5000)}),json('/v1/models',{signal:AbortSignal.timeout(5000)})]);
 if(!Array.isArray(p.projects)||!p.projects.length||!Array.isArray(m.models))throw Error('O servidor retornou um catálogo inválido.');
 let saved={};if(!startupTimer){try{saved=JSON.parse(sessionStorage.getItem('remote-view')||'{}')||{};}catch{}}
 const previous=$('model').value,project=saved.project||$('project').value;
 $('project').replaceChildren(...p.projects.map(id=>{const o=new Option(p.details?.[id]?.label||id,id);return o;}));$('project').value=p.projects.includes(project)?project:p.projects.includes('sem-projeto')?'sem-projeto':p.projects[0];renderProjects();syncActiveProjectBadge();
 providers=m.providers||{};models=m.models;uploadsAllowed=m.uploads_enabled===true;policyProject=null;policyPending=false;
 $('model').replaceChildren(...models.map(m=>new Option(names[m.id]||m.name||m.id,m.id)));if(models.some(m=>m.id===previous))$('model').value=previous;
 updateEfforts();restoreSelection();if(!await refreshProjectPermissions(5000))throw Error('Não foi possível carregar as permissões deste projeto.');modelAvailability(m);
 if(!await history(5000))throw Error('Não foi possível carregar o histórico de conversas.');
 status(models.length?'Pronto para conversar.':'Configure um modelo para começar.');
 if(!startupTimer){try{if(saved.conversation)await load(saved.conversation,false,saved);restoreView(saved);}catch{}startupTimer=setInterval(()=>{if(!document.hidden&&interfaceReady&&!initializing){checkVersion();history();}},10000);}setReadiness(true);if(!$('activity-panel').hidden&&rightPanelView==='files')loadProjectFileRoots();void Promise.all([quota(),checkVersion()]);updateComposer();if(saved.conversation&&job&&!parent)void watch();
 }catch(e){setReadiness(false,e.status===401?'Aguardando autorização. Informe sua chave de acesso para conectar.':'Aguardando o servidor… Verificando a conexão automaticamente.');if(e.status===401&&!$('vpn-login').open)$('vpn-login').showModal();modelAvailability(null,e.message);status('Não foi possível conectar: '+e.message);}finally{initializing=false;retry.disabled=false;retry.textContent='Verificar novamente';if(!readinessTimer)readinessTimer=setInterval(()=>{retryReadiness();},5000);}}
function retryReadiness(){if(document.hidden||initializing||probing||Date.now()<readinessRetryAt)return;return interfaceReady?probeReadiness():initialize();}
document.addEventListener('visibilitychange',()=>{void retryReadiness();});
window.addEventListener('online',()=>{void retryReadiness();});
async function probeReadiness(){if(probing||Date.now()<readinessRetryAt)return;readinessRetryAt=Date.now()+15000;probing=true;try{
 const[p,m]=await Promise.all([json('/v1/projects',{signal:AbortSignal.timeout(5000)}),json('/v1/models',{signal:AbortSignal.timeout(5000)})]);if(!Array.isArray(p.projects)||!p.projects.length||!Array.isArray(m.models))throw Error('Catálogo inválido');
 const project=$('project').value;if(!p.projects.includes(project))throw Error('Projeto indisponível');
 const scoped=await json('/v1/models?project_id='+encodeURIComponent(project),{signal:AbortSignal.timeout(5000)});if(!Array.isArray(scoped.models))throw Error('Permissões inválidas');
 if(!busy&&!loading&&project===$('project').value&&(JSON.stringify(scoped.models)!==JSON.stringify(models)||(scoped.uploads_enabled===true)!==uploadsAllowed)){policyProject=null;if(await refreshProjectPermissions(5000)){providers=m.providers||{};modelAvailability(m);}}
 if(!busy&&!loading&&JSON.stringify(p.projects)!==JSON.stringify([...$('project').options].map(o=>o.value))){$('project').replaceChildren(...p.projects.map(id=>new Option(p.details?.[id]?.label||id,id)));$('project').value=project;renderProjects();}
 try{const h=await json('/v1/conversations',{signal:AbortSignal.timeout(5000)});if(!Array.isArray(h.conversations))throw Error('Histórico inválido');}catch(e){if(e.status!==404)throw e;const h=await json('/v1/history',{signal:AbortSignal.timeout(5000)});if(!Array.isArray(h.jobs))throw Error('Histórico inválido');}
 }catch(e){if(e.status===429){readinessRetryAt=Date.now()+Math.max(5000,e.retryAfter||5000);status(e.message);return;}readinessRetryAt=0;if(e.status===401&&!$('vpn-login').open)$('vpn-login').showModal();setReadiness(false,e.status===401?'Aguardando autorização. Informe sua chave de acesso para conectar.':'Aguardando o servidor… Verificando a conexão automaticamente.');status('Conexão com o servidor interrompida: '+e.message);}finally{probing=false;}}
$('resume-execution').onclick=()=>watch();
$('models-retry').onclick=()=>{if(!busy)initialize();};initialize();

function restoreView(saved){
 if(!conversation&&["native","scoped"].includes(saved.execution_mode)){executionMode=saved.execution_mode;executionModeChosen=saved.execution_mode_chosen!==false;}
 if(typeof saved.draft==='string')$('prompt').value=saved.draft;invalidResourceTokens=new Set(Array.isArray(saved.invalid_resource_tokens)?saved.invalid_resource_tokens.filter(token=>typeof token==='string'&&$('prompt').value.split(/\s+/).includes(token)):[]);const engine=resourceEngine(),sameResourceContext=saved.resource_context?.project===$('project').value&&saved.resource_context?.backend===engine.backend&&saved.resource_context?.model===engine.model&&saved.resource_context?.execution_mode===engine.execution_mode;if(sameResourceContext&&Array.isArray(saved.resource_selections))resourceSelections=saved.resource_selections.filter(ref=>ref&&typeof ref.id==='string'&&typeof ref.revision==='string'&&typeof ref.token==='string'&&$('prompt').value.split(/\s+/).includes(ref.token));else{resourceSelections=[];invalidResourceTokens=new Set([...invalidResourceTokens,...(Array.isArray(saved.resource_selections)?saved.resource_selections.map(ref=>ref?.token).filter(token=>typeof token==='string'&&$('prompt').value.split(/\s+/).includes(token)):[])]);}
 if(saved.project===$('project').value&&Array.isArray(saved.files)){
  files=saved.files.filter(f=>f&&typeof f.id==='string'&&typeof f.name==='string').slice(0,MAX_ATTACHMENTS);renderFiles();
 }
 saveView();updateComposer();
}
function saveView(){try{sessionStorage.setItem('remote-view',JSON.stringify({conversation,execution_mode:executionMode,execution_mode_chosen:executionModeChosen,project:$('project').value,draft:$('prompt').value,files,resource_selections:resourceSelections,invalid_resource_tokens:[...invalidResourceTokens],resource_context:{project:$('project').value,...resourceEngine()}}));return true;}catch{return false;}}
$('prompt').addEventListener('input',()=>{saveView();updateComposer();});

function throughputLabel(m){
 const direct=m?.generated_tokens_per_second;
 if(Number.isFinite(direct)&&direct>=0)return ' · '+direct.toLocaleString('pt-BR',{maximumFractionDigits:1})+' tk/s';
 const tokens=m?.output_tokens,seconds=m?.inference_seconds;
 if(Number.isFinite(tokens)&&tokens>=0&&Number.isFinite(seconds)&&seconds>0)return ' · '+(tokens/seconds).toLocaleString('pt-BR',{maximumFractionDigits:1})+' tk/s';
 return ' · tk/s: —';
}
function paintContext(u,metrics){
 const current=selected(),n=u.last?.totalTokens,w=current?.backend==='local'?current.context_window:u.modelContextWindow;
 $('context-meter').textContent=(Number.isFinite(n)?'Contexto: '+n.toLocaleString()+(w?' / '+w.toLocaleString()+' tokens · '+Math.round(n/w*100)+'%':' tokens'):'Contexto não informado')+throughputLabel(metrics);
}
function paintLocalUsage(m){$('context-meter').textContent='Última execução: '+(m.input_tokens??'—')+' tokens de entrada · '+(m.output_tokens??'—')+' de saída'+throughputLabel(m);}
async function checkVersion(){void refreshComposerGit();try{
 const v=await json('/v1/version');$('version').textContent='Release: '+v.version;
 if(v.config_reload_error)status('Não foi possível aplicar a configuração. O harness manteve a última configuração válida. Revise o painel administrativo.');
 if(build&&build!==v.build)reloadPending=true;build=v.build;
 if(reloadPending&&!busy&&!loading&&!uploads&&saveView()){location.reload();}
 else if(reloadPending){$('version').title='Uma atualização será aplicada ao concluir a execução ou o envio de anexos, preservando seu rascunho.';}
}catch{}}

const activityIcons={queued:'⏳',running:'▶',loading:'⏳',thinking:'💭',planning:'📋',plan_updated:'📋',tool_start:'🛠',tool_end:'✓',completed:'✅',failed:'❌',error:'❌',cancelled:'■',interrupted:'⚠',validating_changes:'🔎',changes_applied:'💾',deployment_failed:'⚠',session_resumed:'↩',context_compacting:'⟳',context_compacted:'✓'};
function setPanelView(view,persist=true){
 rightPanelView=view;
 $('files-view').hidden=view!=='files';$('activity-view').hidden=view!=='activity';
 $('files-title').textContent='Arquivos';$('activity-title').textContent='Atividade';
 $('files-toggle').setAttribute('aria-expanded',String(!$('activity-panel').hidden&&view==='files'));
 $('activity-toggle').setAttribute('aria-expanded',String(!$('activity-panel').hidden&&view==='activity'));
 if(persist)try{localStorage.setItem('right-panel-view',view);}catch{}
}
const quotaHome=document.createComment('quota-indicator-home');$('quota-toggle').before(quotaHome);
function syncQuotaDock(){const dock=innerWidth<1200&&!$('activity-panel').hidden;if(dock){$(document.body.classList.contains('panel-order-reversed')?'menu':'panel-toggle').before($('quota-toggle'));}else if(quotaHome.parentNode&&$('quota-toggle').parentNode!==quotaHome.parentNode){quotaHome.before($('quota-toggle'));}updateHeaderToastOffset();}
function setPanelOpen(open,persist=true){
 $('activity-panel').hidden=!open;syncQuotaDock();
 $('panel-toggle').setAttribute('aria-expanded',String(open));
 if(!open&&$('activity-panel').contains(document.activeElement))$('panel-toggle').focus();
 $('files-toggle').setAttribute('aria-expanded',String(open&&rightPanelView==='files'));
 $('activity-toggle').setAttribute('aria-expanded',String(open&&rightPanelView==='activity'));
 if(open&&rightPanelView==='files'&&interfaceReady)loadProjectFileRoots();
 if(persist)try{localStorage.setItem('activity-open',open?'1':'0');}catch{}
}
function togglePanelView(view){
 if($('activity-panel').hidden){setPanelView(view);setPanelOpen(true);}
 else if(rightPanelView===view)setPanelOpen(false);
 else{setPanelView(view);if(view==='files')loadProjectFileRoots();}
}
let rightPanelView='files';
function resetActivity(clearHistory=true){
 paintMotion('');
 if(clearHistory){$('response-details')?.replaceChildren();$('activity-previous').replaceChildren();$('activity-current').dataset.job='';$('activity-run-title').textContent='Aguardando execução';}
 if($('activity-reasoning'))$('activity-reasoning').textContent='';
 const list=$('activity-events');list.replaceChildren();list.eventIds=new Set();delete list.dataset.omitted;
 $('activity-truncation').hidden=true;$('activity-failures').hidden=true;
 $('activity-state').textContent='Aguardando execução';
}
function recordActivity(e){
 const type=e.type,data=e.data||{},failed=type==='tool_end'&&(data.status==='failed'||data.result?.isError===true);
 if(['context_usage','quota_before','quota_after'].includes(type))return;
 if(type==='answer_delta'){$('activity-state').textContent='Recebendo resposta';return;}
 if(['reasoning_delta','reasoning_summary','thinking'].includes(type)){$('activity-state').textContent='Pensando';return;}
 if(type==='tool_start'||type==='tool_end'){
  $('activity-state').textContent=failed?'Uma etapa falhou':'Execução em andamento';
  if(failed){$('activity-failures').hidden=false;$('activity-failures').textContent='Uma etapa desta execução falhou.';}
  return;
 }
 const title=activityTitle(e);if(!title)return;
 const list=$('activity-events');list.eventIds??=new Set();
 if(e.id!=null){if(list.eventIds.has(e.id))return;list.eventIds.add(e.id);}
 const row=document.createElement('li'),label=document.createElement('strong');label.textContent=title;row.append(label);row.dataset.state=type;
 if(type==='plan_updated'){
  const plan=document.createElement('ul');
  for(const step of (data.plan||[]).slice(0,40)){const item=document.createElement('li');item.textContent=String(step.step||'').slice(0,200);plan.append(item);}row.append(plan);
 }
 list.append(row);
 if(list.children.length>80){list.firstElementChild.remove();const count=Number(list.dataset.omitted||0)+1;list.dataset.omitted=count;$('activity-truncation').hidden=false;$('activity-truncation').textContent=count+' marcos anteriores permanecem no registro da execução.';}
 $('activity-state').textContent=title;
}
$('panel-toggle').onclick=()=>{setPanelOpen($('activity-panel').hidden);fitPanels();};
$('files-toggle').onclick=()=>togglePanelView('files');
$('activity-toggle').onclick=()=>togglePanelView('activity');
try{const preference=localStorage.getItem('activity-open'),savedView=localStorage.getItem('right-panel-view');rightPanelView=savedView||(preference==='1'?'activity':'files');setPanelView(rightPanelView,false);setPanelOpen(preference===null?matchMedia('(min-width:1200px)').matches:preference==='1',false);}catch{rightPanelView='files';setPanelView('files',false);setPanelOpen(matchMedia('(min-width:1200px)').matches,false);}

function boundedText(text,limit){return text.length>limit?'[… conteúdo anterior omitido …]\n'+text.slice(-limit):text;}
function beginActivity(id,model=$('model').value){
 const current=$('activity-current');
 if(current.dataset.job===id)return;
 if(current.dataset.job){
  const archived=current.cloneNode(true);
  archived.removeAttribute('id');
  archived.querySelectorAll('[id]').forEach(el=>el.removeAttribute('id'));
  archived.querySelectorAll('details').forEach(el=>el.open=false);
  $('activity-previous').replaceChildren(archived);
 }
 resetActivity(false);current.dataset.job=id;
 $('activity-run-title').textContent=modelIcon(model)+' '+(names[model]||model||'Modelo')+' · Execução '+id;
}
async function replayActivity(id){
 const response=await api('/v1/jobs/'+encodeURIComponent(id)+'/events',{signal:AbortSignal.timeout(15000)});
 const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',seen=0;
 try{while(true){
  const {value,done}=await reader.read();if(done)break;
  buffer+=decoder.decode(value,{stream:true});let end;
  while((end=buffer.indexOf('\n\n'))>=0){
   const block=buffer.slice(0,end);buffer=buffer.slice(end+2);
   const line=block.split('\n').find(l=>l.startsWith('data: '));
   if(line){const e=JSON.parse(line.slice(6));if(e.id>seen){seen=e.id;recordActivity(e);}}
  }
 }}finally{await reader.cancel();}
}
let panelOrder='conversations-left';try{if(localStorage.getItem('panel-order')==='conversations-right')panelOrder='conversations-right';}catch{}
const panelWidths={sidebar:280,'activity-panel':400};
const panelIsLeft=id=>id==='sidebar'?panelOrder==='conversations-left':panelOrder==='conversations-right';
function panelLimits(id){
 const mobile=innerWidth<=620, docked=innerWidth>=1200;
 const other=id==='sidebar'?$('activity-panel'):$('sidebar');
 const otherWidth=(id==='sidebar'?docked:!mobile)&&other.getClientRects().length?other.getBoundingClientRect().width:0;
 const max=Math.max(220,Math.min(720,(id==='activity-panel'&&!docked)||mobile?innerWidth-24:innerWidth-otherWidth-480));
 return {min:220,max};
}
function sizePanel(id,width,persist=true){
 const {min,max}=panelLimits(id),value=Math.round(Math.max(min,Math.min(max,width)));
 $(id).style.width=value+'px';
 if(id==='sidebar'&&!document.body.classList.contains('sidebar-collapsed'))document.body.style.setProperty('--sidebar-width',value+'px');
 const handle=$(id+'-resize');handle.setAttribute('aria-valuemin',min);handle.setAttribute('aria-valuemax',Math.floor(max));handle.setAttribute('aria-valuenow',value);handle.setAttribute('aria-valuetext',value+' pixels');
 if(persist){panelWidths[id]=value;try{localStorage.setItem(id+'-width',String(value));}catch{}}
}
for(const id of Object.keys(panelWidths)){
 try{const saved=Number(localStorage.getItem(id+'-width'));if(saved>=220&&saved<=720)panelWidths[id]=saved;}catch{}
 const handle=$(id+'-resize');let drag=null;
 handle.addEventListener('pointerdown',e=>{
  if(e.button!==0)return;e.preventDefault();handle.focus();
  drag={x:e.clientX,width:$(id).getBoundingClientRect().width};handle.setPointerCapture(e.pointerId);document.body.classList.add('resizing-panels');
 });
 handle.addEventListener('pointermove',e=>{if(drag)sizePanel(id,drag.width+(e.clientX-drag.x)*(panelIsLeft(id)?1:-1));});
 const stop=()=>{drag=null;document.body.classList.remove('resizing-panels');};
 handle.addEventListener('pointerup',stop);handle.addEventListener('pointercancel',stop);handle.addEventListener('lostpointercapture',stop);
 handle.addEventListener('keydown',e=>{
  if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();
  const {min,max}=panelLimits(id),width=$(id).getBoundingClientRect().width;
  sizePanel(id,e.key==='Home'?min:e.key==='End'?max:width+(e.key==='ArrowRight'?24:-24)*(panelIsLeft(id)?1:-1));
 });
}
function fitPanels(){sizePanel('sidebar',panelWidths.sidebar,false);sizePanel('activity-panel',panelWidths['activity-panel'],false);}
window.addEventListener('resize',fitPanels);
function applyPanelOrder(value,persist=true){panelOrder=value==='conversations-right'?'conversations-right':'conversations-left';document.body.classList.toggle('panel-order-reversed',panelOrder==='conversations-right');const reversed=panelOrder==='conversations-right';$('app-brand').after($(reversed?'panel-toggle':'menu'));$('app-topbar').append($(reversed?'menu':'panel-toggle'));for(const button of document.querySelectorAll('[data-panel-order]'))button.setAttribute('aria-pressed',String(button.dataset.panelOrder===panelOrder));if(persist)try{localStorage.setItem('panel-order',panelOrder);}catch{}fitPanels();}
applyPanelOrder(panelOrder,false);for(const button of document.querySelectorAll('[data-panel-order]'))button.onclick=()=>applyPanelOrder(button.dataset.panelOrder);
$('panel-order-reset').onclick=()=>{panelWidths.sidebar=280;panelWidths['activity-panel']=400;try{localStorage.removeItem('sidebar-width');localStorage.removeItem('activity-panel-width');}catch{}applyPanelOrder('conversations-left');fitPanels();};
fitPanels();

$('activity-toggle').onclick=()=>{togglePanelView('activity');fitPanels();};

function rememberSelection(){
 preferredSelection={model:$('model').value,effort:$('effort').value};
 try{localStorage.setItem('chat-selection',JSON.stringify(preferredSelection));}catch{}
}
function restoreSelection(){
 if(!models.length)return;
 const model=models.find(m=>m.id===preferredSelection.model);
 if(!model)return;
 $('model').value=model.id;updateEfforts();
 if(model.efforts.includes(preferredSelection.effort))$('effort').value=preferredSelection.effort;
}
document.querySelectorAll('[data-settings]').forEach(button=>button.onclick=()=>{
 for(const name of ['appearance','agents','skills'])$('settings-'+name).hidden=name!==button.dataset.settings;
 document.querySelectorAll('[data-settings]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
});
let catalogRequest=0;
function catalogCard(item){
 const card=document.createElement('article');card.className='catalog-card';
 const title=document.createElement('h4');title.textContent=item.name;
 const description=document.createElement('p');description.textContent=item.description;
 const meta=document.createElement('small');meta.textContent=[item.status,item.source].filter(Boolean).join(' · ');
 card.append(title,description,meta);return card;
}
async function refreshCatalog(){
 const request=++catalogRequest,project=$('project').value;
 $('catalog-status').textContent='Consultando catálogo de '+project+'…';
 $('catalog-models').replaceChildren(...models.map(m=>catalogCard({name:modelIcon(m.id)+' '+(names[m.id]||m.name||m.id),description:(m.backend==='local'?'Modelo local. ':m.backend==='gemini'?'Modelo via Gemini CLI. ':m.backend==='claude'?'Modelo via Claude Code. ':m.backend==='deepseek'?'Modelo via DeepSeek. ':'Modelo via Codex. ')+'Esforços: '+m.efforts.map(e=>efforts[e]||e).join(', '),status:'Modelo configurado',source:'/v1/models'})));
 for(const provider of ['codex','claude','gemini']){
  if(!providers[provider])$('catalog-models').append(catalogCard({name:provider==='gemini'?'Gemini CLI':provider==='claude'?'Claude Code':'Codex',description:'Configure o adaptador no serviço para habilitar este provedor.',status:'Não configurado',source:'/v1/models'}));
 }
 $('catalog-agents').replaceChildren();$('catalog-skills').replaceChildren();
 try{
  const data=await json('/v1/catalog?project_id='+encodeURIComponent(project));
  if(request!==catalogRequest)return;
  for(const kind of ['agents','skills']){
   $('catalog-'+kind).replaceChildren(...data[kind].map(catalogCard));
   if(!data[kind].length)$('catalog-'+kind).textContent='Nenhum item encontrado no projeto selecionado.';
  }
  $('catalog-status').textContent='Projeto: '+project+'. '+data.scope+(data.warnings.length?' '+data.warnings.join(' '):'');
 }catch(e){
  if(request!==catalogRequest)return;
  $('catalog-status').textContent='Catálogo indisponível neste serviço. '+e.message;
  $('catalog-skills').textContent='Não foi possível verificar as skills disponíveis.';
 }
}
$('settings').onclick=()=>{$('settings-dialog').showModal();refreshCatalog();};
$('settings-close').onclick=()=>$('settings-dialog').close();
for(const id of ['settings-dialog','conversation-search-dialog']){
 const dialog=$(id);
 dialog.addEventListener('click',event=>{
  if(event.target!==dialog)return;
  const r=dialog.getBoundingClientRect();
  if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();
 });
}

$('catalog-refresh').onclick=refreshCatalog;

/* Conversation navigation and composition enhancements. */
function normalizeSearch(value){return value.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase('pt-BR');}
function renderConversationSearch(){
 const query=normalizeSearch($('conversation-search').value.trim());
 const matches=conversations.filter(c=>normalizeSearch(c.title||'Conversa').includes(query));
 $('search-clear').hidden=!query;
 $('search-results').textContent=matches.length?matches.length+' conversa(s) encontrada(s)':query?'Nenhuma conversa encontrada. Tente outro título.':'Nenhuma conversa disponível.';
 $('conversation-search-list').replaceChildren(...matches.map(c=>{
  const button=document.createElement('button');button.type='button';button.className='conversation-search-result';
  const title=document.createElement('strong'),detail=document.createElement('small');title.textContent=c.title||'Conversa';
  const project=[...$('project').options].find(o=>o.value===c.project)?.textContent||'Sem projeto';
  detail.append(document.createTextNode(project));
  if(c.execution?.model){
   const icon=document.createElement('span');icon.className='model-logo-icon';icon.textContent=modelIcon(c.execution.model);icon.setAttribute('aria-hidden','true');
   detail.append(document.createTextNode(' · '),icon,document.createTextNode(' '+modelName(c.execution.model)));
  }
  const indicator=conversationIndicator(c);if(indicator)title.prepend(indicator);button.append(title,detail);
  button.disabled=submitting||cancelling||uploads>0;
  button.onclick=()=>{if(submitting||cancelling||uploads)return;$('conversation-search-dialog').close();void load(c.id,c.legacy);};return button;
 }));
}
function openConversationSearch(){renderConversationSearch();$('conversation-search-dialog').showModal();$('conversation-search').focus();}
$('search-conversations').onclick=openConversationSearch;
$('conversation-search-close').onclick=()=>$('conversation-search-dialog').close();
$('conversation-search-dialog').addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();$('conversation-search-dialog').close();}});
$('conversation-search').addEventListener('input',renderConversationSearch);
$('search-clear').onclick=()=>{$('conversation-search').value='';renderConversationSearch();$('conversation-search').focus();};
function updateComposer(){
 syncComposerPickers();syncExecutionMode();
 const prompt=$('prompt');prompt.style.height='auto';prompt.style.height=Math.min(prompt.scrollHeight,170)+'px';
 renderPromptHighlights();
 const count=Array.from(prompt.value).length;
 $('character-count').textContent=count.toLocaleString('pt-BR')+(count===1?' caractere':' caracteres');
 $('send').disabled=busy||loading||uploads>0||policyPending||!selected()||!prompt.value.trim()||!supportedExecutionModes().includes(executionMode);
}
function updateLatest(){const box=$('messages');$('latest-message').hidden=box.scrollHeight-box.scrollTop-box.clientHeight<150;}
$('messages').addEventListener('scroll',updateLatest,{passive:true});
new MutationObserver(updateLatest).observe($('messages'),{childList:true,subtree:true,characterData:true});
function jumpToLatest(){
 const box=$('messages');
 // Focus must not undo the jump; instant scrolling cannot be interrupted by streaming updates.
 box.focus({preventScroll:true});
 box.scrollTo({top:box.scrollHeight,behavior:'instant'});
 updateLatest();
}
$('latest-message').onclick=jumpToLatest;
new ResizeObserver(updateLatest).observe($('messages'));
function setQuotaOpen(open){$('quota-panel').hidden=!open;$('quota-toggle').setAttribute('aria-expanded',String(open));}
document.addEventListener('pointerdown',e=>{if(!e.target.closest('#quota-panel, #quota-toggle'))setQuotaOpen(false);});
document.addEventListener('keydown',e=>{
 if(e.defaultPrevented||!interfaceReady||document.querySelector('dialog[open]')||document.querySelector('.composer-menu:popover-open'))return;
 if((e.ctrlKey||e.metaKey)&&!e.altKey&&(e.key==='/'||e.key.toLowerCase()==='k')){
  e.preventDefault();
  if(e.key==='/')$('prompt').focus();
  else openConversationSearch();
 }
 if(e.key==='Escape'){
  if(!$('quota-panel').hidden){setQuotaOpen(false);$('quota-toggle').focus();}
  else if(!$('activity-panel').hidden){setPanelOpen(false);$('panel-toggle').focus();}
  else if($('sidebar').classList.contains('open')){$('sidebar').classList.remove('open');$('menu').setAttribute('aria-expanded','false');$('menu').focus();}
 }
});

function applyReadingSize(value){const size=['15','17','19'].includes(value)?value:'15';document.documentElement.style.setProperty('--reading-size',size+'px');$('reading-size').value=size;try{localStorage.setItem('reading-size',size);}catch{}}
let readingSize='15';try{readingSize=localStorage.getItem('reading-size')||'15';}catch{}
applyReadingSize(readingSize);$('reading-size').onchange=()=>applyReadingSize($('reading-size').value);
function updateHeaderToastOffset(){const header=$('conversation-title').closest('header');if(header)document.documentElement.style.setProperty('--conversation-header-bottom',Math.ceil(header.getBoundingClientRect().bottom)+'px');}
new ResizeObserver(updateHeaderToastOffset).observe($('conversation-title').closest('header'));window.addEventListener('resize',()=>{updateComposer();updateLatest();updateHeaderToastOffset();syncQuotaDock();});updateHeaderToastOffset();
updateComposer();

$('vpn-login-form').onsubmit=async e=>{e.preventDefault();try{await post('/v1/login',{token:$('vpn-login-token').value});location.reload();}catch(error){$('vpn-login-error').textContent=error.message;}};
function showApproval(data){
 if(document.getElementById('approval-'+data.approval_id))return;
 const box=document.createElement('section');box.id='approval-'+data.approval_id;box.className='approval-card';
 const title=document.createElement('h3');title.textContent='Autorizar esta ação?';const reason=document.createElement('p');reason.textContent=data.request.reason||'O executor solicitou uma autorização adicional.';const explanation=document.createElement('p');explanation.textContent='As permissões do administrador continuam valendo. Permitir sempre memoriza apenas este comando e esta pasta, nesta conversa.';const command=document.createElement('pre');command.textContent=data.request.command||data.request.tool_name||data.kind;const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent='Detalhes técnicos';const pre=document.createElement('pre');pre.textContent=JSON.stringify(data.request,null,2);details.append(summary,pre);box.append(title,reason,command,explanation,details);
 const fields=[];for(const q of data.request.questions||[]){const label=document.createElement('label');label.textContent=q.question;const input=document.createElement('input');input.placeholder=(q.options||[]).map(o=>o.label).join(' / ');label.append(input);box.append(label);fields.push([q.id,input]);}
 let deciding=false;const progress=document.createElement('p');progress.setAttribute('role','status');progress.hidden=true;box.append(progress);
 for(const [text,approved,scope] of [['Permitir uma vez',true,'once'],...(data.can_remember?[['Permitir sempre nesta conversa',true,'conversation']]:[]),['Negar',false,'once']]){const button=document.createElement('button');button.className='btn';button.textContent=text;button.onclick=async()=>{if(deciding)return;deciding=true;box.querySelectorAll('button,input').forEach(node=>node.disabled=true);progress.hidden=false;progress.textContent='Enviando sua decisão…';try{const answers=Object.fromEntries(fields.map(([id,input])=>[id,{answers:[input.value]}]));await post('/v1/approvals/'+data.approval_id,{approved,answers,scope});box.remove();}catch(e){progress.textContent='Não foi possível confirmar sua decisão. '+e.message;}finally{deciding=false;box.querySelectorAll('button,input').forEach(node=>node.disabled=false);}};box.append(button);}
 $('messages').append(box);status('Aguardando sua aprovação');box.scrollIntoView({block:'nearest'});
}

for(const [id,name] of [['add-project','folder-plus'],['new','message-plus'],['menu','layout-sidebar'],['attach','plus'],['send','arrow-up'],['cancel','player-stop']]){const b=$(id);if(!b)continue;b.classList.add('btn');if(['new','send'].includes(id))b.classList.add('btn-primary');if(['menu','attach','send','cancel'].includes(id))b.classList.add('btn-icon');const label={'add-project':'Adicionar projeto',new:'Nova Conversa',reload:'Recarregar tela'}[id];b.replaceChildren(TailUI.icon(name));if(label)b.append(document.createTextNode(label));}

for(const [id,name,label] of [['settings-close','x',''],['setup-close','x',''],['search-clear','x',''],['catalog-refresh','refresh','Atualizar catálogo do projeto']]){const button=$(id);button.classList.add('btn');if(!label)button.classList.add('btn-icon');button.replaceChildren(TailUI.icon(name));if(label)button.append(document.createTextNode(label));}
for(const node of document.querySelectorAll('.brandmark,.welcome-icon'))node.replaceChildren(TailUI.icon('stack-2'));
for(const button of document.querySelectorAll('[data-settings]')){button.textContent=button.textContent.replace(/^[^A-Za-zÀ-ÿ]+/,'');button.prepend(TailUI.icon(button.dataset.settings==='appearance'?'adjustments':button.dataset.settings==='agents'?'stack-2':'message'));}

function attachmentNotice(filename,code){
 const reasons={model_images_unavailable:'o modelo selecionado não suporta leitura de imagens',local_vision_not_enabled:'o modelo local está sem suporte a imagens habilitado neste servidor',images_require_native_service:'o modo de execução selecionado não suporta leitura de imagens',unsupported_binary_format:'este formato não possui um leitor disponível no aplicativo',binary_denied:'este formato binário não possui um leitor disponível no aplicativo'};
 if(!Object.hasOwn(reasons,code))return;
 $('welcome')?.remove();
 const notice=assistant('',selected()?.id);notice.chip.textContent='Arquivo ignorado';
 notice.body.textContent=`Arquivo “${filename}” ignorado porque ${reasons[code]}.`;
 $('messages').scrollTop=$('messages').scrollHeight;
}
function attachmentError(code){return ({audio_transcription_unavailable:'A transcrição local de áudio não está instalada neste servidor.',audio_duration_limit:'Envie um áudio de até 2 horas.',invalid_audio:'Não foi possível reconhecer o áudio. Tente WAV, MP3, M4A, OGG ou FLAC.',audio_transcription_failed:'A transcrição local falhou; o áudio não foi anexado.',local_vision_not_enabled:'Este servidor local está sem visão habilitada. É necessário configurar o projetor visual (mmproj) do modelo e reiniciar o servidor. O arquivo não foi anexado.',image_capability_unavailable:'Não foi possível verificar a visão deste servidor. Tente novamente quando ele estiver disponível.',model_images_unavailable:'O serviço selecionado não oferece leitura de imagens.',images_require_native_service:'A leitura de imagens exige a execução nativa do serviço.',select_model_for_image:'Selecione um modelo com permissão para anexos antes de enviar a imagem.',image_size_limit:'Imagens podem ter até 5 MiB.',unsupported_binary_format:'Este formato binário ainda não tem um leitor disponível. Envie uma imagem compatível, PDF com texto, documento Office/OpenDocument ou arquivo de texto.',binary_denied:'Este arquivo contém dados binários sem leitor disponível.',invalid_document:'O documento está inválido ou corrompido.',document_expansion_limit:'O documento excede o limite seguro de descompactação.'})[code]||code;}

document.addEventListener('click',event=>{document.querySelectorAll('.conversation-actions[open]').forEach(actions=>{if(!actions.contains(event.target))actions.open=false;});});

// Shared native popovers for the three concrete composer controls.
function syncAccessMode(){
 const mode=$('access-mode').value;
 $('access-label').textContent=$('access-mode').selectedOptions[0]?.textContent||'Pedir aprovação';
 $('access-trigger').dataset.mode=mode;
 const option=$('access-menu').querySelector('[data-access="'+mode+'"]'),optionIcon=option?.querySelector('.access-option-icon'),description=option?.querySelector('small')?.textContent||'';
 if(optionIcon){const icon=optionIcon.cloneNode(true);icon.id='access-trigger-icon';icon.setAttribute('aria-hidden','true');$('access-trigger-icon').replaceWith(icon);}
 $('access-trigger').title=description;
 for(const item of $('access-menu').querySelectorAll('[data-access]')){item.setAttribute('aria-selected',String(item.dataset.access===mode));const help=item.querySelector('small');if(help)item.title=help.textContent;}
}
function syncComposerPickers(){
 for(const id of ['model','effort']){
  const select=$(id),trigger=$(id+'-trigger');
  $(id+'-label').textContent=select.selectedOptions[0]?.textContent||(id==='model'?'Nenhum modelo':'Esforço indisponível');
  if(id==='model')$('model-trigger-icon').textContent=modelIcon(select.value);
  if(id==='model'){
   const identity=selectedIdentity();$('model-trigger-icon').title=identity?.model||'';
   trigger.title=identity?identity.provider+' · '+identity.model:$(id+'-label').textContent;
  }
  if(id!=='model')trigger.title=$(id+'-label').textContent;
  trigger.disabled=select.disabled||!select.options.length||busy||loading||policyPending;
  if(trigger.disabled&&$(id+'-menu').matches(':popover-open'))$(id+'-menu').hidePopover();
 }
}
function renderPicker(id){
 if(id==='access'){syncAccessMode();return;}
 const descriptions={none:'Sem etapa de raciocínio.',configured:'Usar o padrão configurado no provedor.',auto:'O coordenador escolhe o esforço para cada etapa.',low:'Raciocínio breve para tarefas simples.',medium:'Esforço intermediário de raciocínio.',high:'Mais raciocínio para tarefas complexas.',xhigh:'Esforço de raciocínio muito alto.',max:'Esforço máximo disponibilizado pelo modelo.',ultra:'Nível de raciocínio mais intenso disponibilizado pelo modelo.'};
 const providers={local:'Modelo local no servidor',qwen:'Modelo local no servidor',codex:'Codex',claude:'Claude Code',gemini:'Gemini CLI',deepseek:'DeepSeek',maestro:'Coordenador de modelos'};
 const groups=new Map();
 const buttons=[...$(id).options].map(option=>{
  const button=document.createElement('button');button.type='button';button.setAttribute('role','option');button.dataset.value=option.value;button.disabled=option.disabled;
  button.setAttribute('aria-selected',String(option.selected));
  const model=models.find(m=>m.id===option.value);
  const icon=document.createElement('span');icon.className='access-option-icon';icon.setAttribute('aria-hidden','true');
  if(id==='model'){
   const modelGlyph=document.createElement('span');modelGlyph.className='model-logo-icon';modelGlyph.textContent=modelIcon(option.value);modelGlyph.title=option.textContent;icon.append(modelGlyph);
  }else icon.textContent='◷';
  const text=document.createElement('span'),title=document.createElement('strong'),detail=document.createElement('small');title.textContent=option.textContent;
  detail.textContent=id==='model'?(providers[model?.backend]||model?.backend||'Modelo configurado no servidor'):(descriptions[option.value]||'Nível disponibilizado pelo modelo selecionado.');
  text.append(title);if(id!=='model')text.append(detail);const check=document.createElement('span');check.className='access-check';check.setAttribute('aria-hidden','true');check.textContent='✓';button.append(icon,text,check);
  if(id==='model'){
   const backend=model?.backend==='qwen'?'local':model?.backend||'other';
   if(!groups.has(backend)){
    const group=document.createElement('div'),heading=document.createElement('div');
    const label=({codex:'Codex',claude:'Claude',local:'Modelo local',deepseek:'DeepSeek',gemini:'Google',maestro:'Maestro'})[backend]||model?.backend||'Outros';
    group.setAttribute('role','group');group.setAttribute('aria-label',label);
    heading.className='model-provider-heading';heading.setAttribute('aria-hidden','true');heading.textContent=label;
    group.append(heading);groups.set(backend,group);
   }
   groups.get(backend).append(button);
  }
  return button;
 });
 $(id+'-menu').querySelector('.picker-options').replaceChildren(...(id==='model'?groups.values():buttons));
}
function openComposerPicker(id){
 const trigger=$(id+'-trigger'),menu=$(id+'-menu');if(trigger.disabled)return;
 renderPicker(id);menu.showPopover();const rect=trigger.getBoundingClientRect();
 menu.style.left=Math.max(12,Math.min(rect.left,innerWidth-menu.offsetWidth-12))+'px';
 menu.style.top=Math.max(12,rect.top-menu.offsetHeight-10)+'px';
 (menu.querySelector('[aria-selected="true"]:not(:disabled)')||menu.querySelector('[role=option]:not(:disabled)'))?.focus();
}
for(const id of ['access','model','effort']){
 const trigger=$(id+'-trigger'),menu=$(id+'-menu'),select=$(id==='access'?'access-mode':id);
 select.addEventListener('change',()=>{syncAccessMode();syncComposerPickers();});
 trigger.onclick=()=>{menu.matches(':popover-open')?menu.hidePopover():openComposerPicker(id);};
 menu.addEventListener('toggle',event=>trigger.setAttribute('aria-expanded',String(event.newState==='open')));
 menu.addEventListener('click',event=>{
  const option=event.target.closest('[role=option]');if(!option||option.disabled)return;
  select.value=option.dataset.value??option.dataset.access;select.dispatchEvent(new Event('change',{bubbles:true}));menu.hidePopover();trigger.focus();
 });
 trigger.addEventListener('keydown',event=>{if(['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();openComposerPicker(id);}});
 menu.addEventListener('keydown',event=>{
  const options=[...menu.querySelectorAll('[role=option]:not(:disabled)')],index=options.indexOf(document.activeElement);
  if(options.length&&['ArrowDown','ArrowUp','Home','End'].includes(event.key)){
   event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?options.length-1:(index+(event.key==='ArrowDown'?1:-1)+options.length)%options.length;options[next].focus();
  }
  if(event.key==='Tab')menu.hidePopover();
 });
}
window.addEventListener('resize',()=>{for(const menu of document.querySelectorAll('.composer-menu:popover-open'))menu.hidePopover();});
syncAccessMode();syncComposerPickers();

// Project registration and folder selection.
const projectDirectory={rootId:'home',request:0,selected:new Map(),candidate:null,roots:[],cache:new Map(),expanded:new Set()};
const projectFolderTree={foldersOnly:true,rootId:'home',cache:projectDirectory.cache,expanded:projectDirectory.expanded,selected:new Set()};
function renderProjectFolders(){
 const list=$('project-directory-list');list.replaceChildren();
 renderProjectFileEntries(list,projectDirectory.roots,projectFolderTree);
 $('project-directory-add-current').disabled=!projectDirectory.candidate||projectDirectory.selected.has(projectDirectory.candidate.absolute_path);
}
function selectProjectFolder(entry){
 projectDirectory.candidate=entry;projectFolderTree.selected.clear();projectFolderTree.selected.add(entry.path);
 for(const item of $('project-directory-list').querySelectorAll('[role=treeitem]'))item.setAttribute('aria-selected',String(item.dataset.path===entry.path));
 $('project-directory-selection').textContent=entry.absolute_path;
 $('project-directory-add-current').disabled=projectDirectory.selected.has(entry.absolute_path);
}
async function loadProjectDirectories(path='',rootId=projectDirectory.rootId){
 const request=projectDirectory.request;let start=1,entries=[],data;
 try{
  do{data=await json('/v1/project-directories?root_id='+encodeURIComponent(rootId)+'&path='+encodeURIComponent(path)+'&start='+start+'&limit=100');if(request!==projectDirectory.request)return;entries.push(...(data.entries||[]));start=entries.length+1;}while(data.limited&&(data.entries||[]).length);
  if(!projectDirectory.roots.length){
   projectDirectory.rootId=data.root_id;projectFolderTree.rootId=data.root_id;
   projectDirectory.roots=[{name:(data.roots||[]).find(root=>root.id===data.root_id)?.label||'Pasta pessoal',path:'',absolute_path:data.absolute_path,type:'directory'}];
   const roots=$('project-directory-roots');roots.replaceChildren(...(data.roots||[]).map(root=>{const button=document.createElement('button');button.type='button';button.textContent=root.label;button.setAttribute('aria-pressed',String(root.id===data.root_id));button.onclick=()=>{resetProjectFolderBrowser(root.id);void loadProjectDirectories('',root.id);};return button;}));
   projectDirectory.expanded.add('');
  }
  projectDirectory.cache.set(rootId+'\0'+path,entries);$('project-directory-error').textContent='';renderProjectFolders();
 }catch(error){if(request===projectDirectory.request){projectDirectory.expanded.delete(path);renderProjectFolders();$('project-directory-error').textContent='Não foi possível listar pastas: '+error.message;}}
}
function resetProjectFolderBrowser(rootId='home'){
 ++projectDirectory.request;projectDirectory.rootId=rootId;projectFolderTree.rootId=rootId;projectDirectory.roots=[];projectDirectory.cache.clear();projectDirectory.expanded.clear();projectFolderTree.selected.clear();projectDirectory.candidate=null;
 $('project-directory-selection').textContent='Selecione uma pasta';$('project-directory-error').textContent='';$('project-directory-add-current').disabled=true;$('project-directory-list').textContent='Carregando pastas…';
}
async function toggleProjectFolder(entry){
 if(projectDirectory.expanded.has(entry.path)){projectDirectory.expanded.delete(entry.path);renderProjectFolders();return;}
 projectDirectory.expanded.add(entry.path);renderProjectFolders();
 if(!projectDirectory.cache.has(projectDirectory.rootId+'\0'+entry.path))await loadProjectDirectories(entry.path);
}
function renderSelectedProjectDirectories(){const list=$('project-selected-paths');list.hidden=!projectDirectory.selected.size;$('project-folders-empty').hidden=!!projectDirectory.selected.size;list.replaceChildren();for(const [path,name] of projectDirectory.selected){const li=document.createElement('li'),label=document.createElement('span'),remove=document.createElement('button');label.textContent=name+' · '+path;label.title=path;remove.type='button';remove.textContent='Remover';remove.onclick=()=>{projectDirectory.selected.delete(path);renderSelectedProjectDirectories();renderProjectFolders();};li.append(label,remove);list.append(li);}}
$('add-project').onclick=()=>{if(!busy&&!loading){projectDirectory.selected.clear();renderSelectedProjectDirectories();$('project-form').reset();resetProjectFolderBrowser();$('project-create-note').textContent='';$('project-dialog').showModal();void loadProjectDirectories();}};
$('project-dialog-close').onclick=$('project-dialog-cancel').onclick=()=>$('project-dialog').close();
$('project-directory-add-current').onclick=()=>{const entry=projectDirectory.candidate;if(!entry||projectDirectory.selected.has(entry.absolute_path))return;if(projectDirectory.selected.size>=20){$('project-create-note').textContent='Adicione até 20 pastas por projeto.';return;}projectDirectory.selected.set(entry.absolute_path,entry.name);renderSelectedProjectDirectories();$('project-directory-add-current').disabled=true;$('project-create-note').textContent='';};
$('project-form').onsubmit=async event=>{
 event.preventDefault();const name=$('project-name').value.trim(),button=$('project-create'),note=$('project-create-note');if(Array.from(name.matchAll(/\p{L}/gu)).length<3){note.textContent='O nome precisa ter pelo menos 3 letras.';return;}if(!projectDirectory.selected.size){note.textContent='Adicione pelo menos uma pasta.';return;}if(projectDirectory.selected.size>20){note.textContent='Adicione até 20 pastas por projeto.';return;}button.disabled=true;note.textContent='Adicionando projeto…';
 try{const result=await post('/v1/projects',{name,paths:Array.from(projectDirectory.selected.keys())});const p=await json('/v1/projects');$('project').replaceChildren(...p.projects.map(id=>new Option(p.details?.[id]?.label||id,id)));policyProject=null;chooseProject(result.project_id);renderProjects();$('project-dialog').close();$('project-form').reset();projectDirectory.selected.clear();renderSelectedProjectDirectories();note.textContent='';
 }catch(e){note.textContent='Não foi possível adicionar: '+e.message;}finally{button.disabled=false;}
};
