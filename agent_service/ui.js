'use strict';
const $=id=>document.getElementById(id);
let providers={},models=[],files=[],job='',last=0,controller=null,active=null,busy=false,parent=null,conversation='',loading=false,build='',reloadPending=false;
let policyProject=null,policyPending=false,policySequence=0;
let uploadsAllowed=false,streamDisconnected=false,submitting=false,cancelling=false,pendingSubmission=null;
try{pendingSubmission=JSON.parse(sessionStorage.getItem('pending-submission')||'null');}catch{}
function submissionKey(data){const payload=JSON.stringify(data);if(pendingSubmission?.payload!==payload){pendingSubmission={payload,key:crypto.randomUUID?.()||Array.from(crypto.getRandomValues(new Uint8Array(16)),n=>n.toString(16).padStart(2,'0')).join('')};try{sessionStorage.setItem('pending-submission',JSON.stringify(pendingSubmission));}catch{}}return pendingSubmission.key;}
function clearSubmission(){pendingSubmission=null;try{sessionStorage.removeItem('pending-submission');}catch{}}
let conversations=[],legacyHistory=false,historyRequest=0,uploads=0;
const welcomeTemplate=$('welcome').cloneNode(true);
const expandedProjects=new Map();
let preferredSelection={};
try{preferredSelection=JSON.parse(localStorage.getItem('chat-selection')||'{}')||{};}catch{}
const labels={maestro_planning:'Maestro está planejando',maestro_plan:'Agentes selecionados',maestro_step:'Executando etapa do Maestro',answer_delta:'Respondendo',reasoning_delta:'Pensando',reasoning_summary:'Resumo de raciocínio',interrupted:'Interrompido',queued:'Na fila',running:'Executando',thinking:'Pensando',planning:'Preparando a execução',tool_start:'Usando ferramenta',tool_end:'Ferramenta concluída',session_resumed:'Contexto da conversa retomado',context_compacting:'Otimizando o contexto da conversa…',context_compacted:'Contexto otimizado; conversa preservada',validating_changes:'Validando alterações',changes_applied:'Alterações aplicadas ao projeto',deployment_failed:'Alterações não aplicadas; consulte o erro',reload_scheduled:'Atualizando o painel',completed:'Concluído',cancelled:'Cancelado',failed:'Falha',loading:'Preparando modelo'};
const status=text=>{const target=$('status');target.hidden=false;target.className='';target.textContent=text;if(/^(Resposta copiada|Nova conversa\.|Pronto para conversar)/.test(text)){TailUI.toast(text);target.textContent='';}else if(/^(Não foi possível|Conexão interrompida|Falha|Erro|Conecte o Tailscale)/.test(text)){TailUI.notice(target,text,{error:true});}};
const names={'qwen-local':'Qwen3.6 · local','gpt-6-astra':'Astra','gpt-5.6-sol':'Sol','gpt-5.6-terra':'Terra','gpt-5.6-luna':'Luna','gpt-5.5':'GPT-5.5'};
const modelIcons={'qwen-local':'✦','gpt-6-astra':'🌟','gpt-5.6-sol':'☀️','gpt-5.6-terra':'🌍','gpt-5.6-luna':'🌙','gpt-5.5':'✳'};
const modelIcon=id=>{const name=(String(id||'')+' '+(models.find(m=>m.id===id)?.name||'')).toLowerCase();if(modelIcons[id])return modelIcons[id];if(name.includes('qwen'))return '✦';if(name.includes('claude'))return '✳';if(name.includes('deepseek'))return '🐋';if(name.includes('gemma'))return '💎';if(name.includes('llama'))return '🦙';return '◈';};
const efforts={auto:'Maestro escolhe por etapa',none:'Sem raciocínio',configured:'Padrão do provedor',low:'Baixo',medium:'Médio',high:'Alto',xhigh:'Muito alto',max:'Máximo',ultra:'Ultra'};
const userErrors={rate_limit:'Muitas consultas em pouco tempo. O servidor limitou temporariamente este acesso.',submission_rate_limit:'Você enviou novos pedidos muito rapidamente. Este pedido não entrou na fila.',queue_full:'A fila do servidor está cheia. Este pedido não entrou na fila; aguarde a conclusão de outras execuções.',owner_queue_full:'Você atingiu o limite de pedidos na fila. Aguarde uma das suas execuções terminar antes de enviar outra.',stream_limit:'Há muitas conexões de acompanhamento abertas nesta sessão. Feche as abas extras e tente novamente.',login_rate_limit:'Muitas tentativas de acesso. Confira seus dados antes de tentar entrar novamente.',model_not_allowed:'Este modelo não está habilitado. Verifique os modelos disponíveis.',model_not_available:'O modelo não está disponível. Peça ao administrador para verificar o servidor local.',uploads_denied:'Anexos estão desativados para este serviço.',authentication_required:'Sua conexão precisa de autorização.',project_denied:'Você não tem acesso a este projeto.',native_failed:'O serviço de IA não concluiu a execução. Verifique a atividade e tente novamente.'};
async function api(path,options={}){let r;try{r=await fetch(path,{...options,signal:options.signal||AbortSignal.timeout(30000)});}catch(e){if(e.name==='AbortError')throw e;throw Error(e.name==='TimeoutError'?'O servidor demorou a responder. Verifique a atividade antes de repetir o pedido.':'Não foi possível conectar ao servidor. Verifique sua conexão e tente novamente.');}if(!r.ok){let e;try{e=await r.json();}catch{e={code:'HTTP '+r.status};}let message=userErrors[e.code]||(r.status===429?'O servidor aplicou um limite temporário a esta solicitação.':'O servidor não concluiu a solicitação ('+(e.code||r.status)+'). Verifique os dados ou tente novamente.');if(r.status===429){const after=r.headers.get('Retry-After');const seconds=after===null?NaN:/^\d+(?:\.\d+)?$/.test(after.trim())?Number(after):Math.max(0,(Date.parse(after)-Date.now())/1000);message+=Number.isFinite(seconds)?' Tente novamente em '+Math.max(1,Math.ceil(seconds))+' segundos.':' Aguarde um pouco antes de tentar novamente.';}const error=Error(message);error.status=r.status;throw error;}return r;}
async function json(path,options){const r=await api(path,options);try{return await r.json();}catch{throw Error('O servidor retornou dados inválidos. Tente atualizar a conexão.');}}

const post=(path,value)=>json(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
function selected(){return models.find(m=>m.id===$('model').value)||models[0];}
function setBusy(value){value=value||streamDisconnected;busy=value;if(!value)paintMotion('');$('send').disabled=value||loading||uploads>0||!selected()||!$('prompt').value.trim();$('send').hidden=value;$('cancel').hidden=!value;$('cancel').disabled=submitting||cancelling||!job;$('attach').disabled=value;$('project').disabled=value;$('model').disabled=value;$('effort').disabled=value;$('task-label').disabled=value;$('attach').disabled=value||uploads>0||!canUpload()||!selected();$('new').disabled=value||loading||uploads>0;updateComposer();renderFiles();}
async function refreshProjectPermissions(){
 const project=$('project').value;if(project===policyProject)return;
 const sequence=++policySequence;policyPending=true;updateModelPermissions();updateComposer();
 try{const data=await json('/v1/models?project_id='+encodeURIComponent(project));if(sequence!==policySequence)return;
 if(!Array.isArray(data.models))throw Error('Catálogo de permissões inválido');
 const previous=$('model').value,effort=$('effort').value;models=data.models;uploadsAllowed=data.uploads_enabled===true;
 $('model').replaceChildren(...models.map(m=>new Option(modelIcon(m.id)+' '+(names[m.id]||m.name||m.id),m.id)));if(models.some(m=>m.id===previous))$('model').value=previous;
 policyProject=project;policyPending=false;updateEfforts();if([...$('effort').options].some(o=>o.value===effort))$('effort').value=effort;
 }catch(e){if(sequence!==policySequence)return;status('Não foi possível carregar as permissões deste projeto. Selecione-o novamente para tentar: '+e.message);}
 finally{if(sequence===policySequence){updateModelPermissions();updateComposer();}}
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
function updateEfforts(){updateModelPermissions();const m=selected();if(!m){$('effort').replaceChildren();return;}$('effort').replaceChildren(...m.efforts.map(e=>{const o=document.createElement('option');o.value=e;o.textContent=efforts[e]||e;return o;}));$('model-note').textContent=m.backend==='maestro'?'Codex coordena e escolhe modelos locais ou de nuvem habilitados para cada etapa':m.backend==='local'?'Modelo local executa no servidor · sem cota OpenAI · confira as fontes':m.backend==='deepseek'?'DeepSeek API · consome seus créditos DeepSeek':m.backend==='claude'?'Claude Code no servidor · inferência Anthropic · cota não disponível':'Codex CLI no servidor · inferência OpenAI · consome cota ChatGPT';}
function quotaText(q){if(!q?.available)return'Cota indisponível';const buckets=q.rateLimitsByLimitId||{codex:q.rateLimits};const result=[];for(const b of Object.values(buckets)){if(!b)continue;for(const w of [b.primary,b.secondary]){if(!w)continue;result.push(`${Math.max(0,100-w.usedPercent)}% · ${w.windowDurationMins>=10080?'semana':w.windowDurationMins===300?'5 horas':Math.round(w.windowDurationMins/60)+'h'}`);}}return result.join(' / ')||'Cota indisponível';}
function paintQuota(q){$('quota-short').textContent=quotaText(q);$('quota-current').replaceChildren();if(!q?.available){$('quota-current').textContent='Não foi possível consultar agora. Nenhum consumo foi estimado.';return;}for(const b of Object.values(q.rateLimitsByLimitId||{codex:q.rateLimits})){if(!b)continue;for(const w of [b.primary,b.secondary]){if(!w)continue;const wrap=document.createElement('div');wrap.className='quota-window';const remaining=Math.max(0,100-w.usedPercent);const label=document.createElement('div');label.textContent=`${remaining}% restante · ${w.windowDurationMins>=10080?'Semanal':w.windowDurationMins===300?'5 horas':w.windowDurationMins+' minutos'}`;const bar=document.createElement('progress');bar.max=100;bar.value=remaining;const reset=document.createElement('small');reset.textContent='Renova em '+new Date(w.resetsAt*1000).toLocaleString();wrap.append(label,bar,reset);$('quota-current').append(wrap);}}}
let quotaRequest=0;
async function quota(){
 const request=++quotaRequest,backend=selected()?.backend;
 if(backend==='local'){$('quota-short').textContent='Local · sem cota de nuvem';$('quota-current').textContent='Este modelo executa nesta máquina. Não utiliza cota OpenAI.';return;}
 if(backend==='claude'||backend==='deepseek'){$('quota-short').textContent=backend==='claude'?'Claude · cota não informada':'DeepSeek · créditos próprios';$('quota-current').textContent=backend==='claude'?'O Claude não disponibilizou sua cota nesta interface. Consulte o uso na sua conta Claude.':'Este modelo utiliza créditos da sua conta DeepSeek. A cota OpenAI não se aplica.';return;}
 try{const value=await json('/v1/usage');if(request===quotaRequest)paintQuota(value);}catch{if(request===quotaRequest)paintQuota(null);}
}
function quotaSnapshot(kind,q){const id='quota-'+kind;let p=$(id);if(!p){p=document.createElement('p');p.id=id;$('quota-comparison').append(p);}p.textContent=(kind==='before'?'Antes: ':'Depois: ')+quotaText(q);paintQuota(q);}
async function history(){
 const request=++historyRequest;
 try{
  let data;
  try{data=await json('/v1/conversations');legacyHistory=false;}
  catch(e){
   if(e.status!==404)throw e;
   const old=await json('/v1/history');
   data={conversations:old.jobs.map(r=>({...r,legacy:true}))};legacyHistory=true;
  }
  if(request!==historyRequest)return;
  conversations=data.conversations;renderProjects();
  $('history-note').textContent=legacyHistory?'Histórico compatível: execuções anteriores. Atualize o serviço para agrupar os turnos.':'';
 }catch(e){if(request===historyRequest)status('Não foi possível carregar conversas: '+e.message);}
}
function conversationRow(c){
 const row=document.createElement('div');row.className='conversation-row';
 const open=document.createElement('button');open.setAttribute('aria-current',c.id===conversation?'true':'false');open.textContent=c.title||'Conversa';open.title=open.textContent;open.className=c.id===conversation?'active':'';open.onclick=()=>load(c.id,c.legacy);
 const remove=document.createElement('button');remove.textContent='×';remove.className='delete-conversation';remove.setAttribute('aria-label','Excluir conversa '+open.textContent);
 remove.hidden=!!c.legacy;
 remove.onclick=async()=>{if(busy||loading||uploads)return;if(!confirm('Excluir esta conversa da lista? Os registros de execução serão mantidos para auditoria.'))return;
  try{await json('/v1/conversations/'+encodeURIComponent(c.id),{method:'DELETE'});if(conversation===c.id)newConversation();await history();}catch(e){status('Não foi possível excluir: '+e.message);}
 };
 const info=document.createElement('small');info.className='conversation-execution';
 const x=c.execution||{};
 info.dataset.motion=['queued','running'].includes(c.state)?(x.activity==='answer_delta'?'answer':'working'):'';
 info.textContent=[x.backend,x.model,x.task_label,labels[c.state]||c.state,
   !['completed','failed','cancelled','interrupted'].includes(c.state)?(labels[x.activity]||x.activity):'',x.tool].filter(Boolean).join(' · ');
 open.append(info);open.title=[c.title,info.textContent].filter(Boolean).join('\n');
 row.append(open,remove);return row;
}
function confirmNewConversation(){return !($('prompt').value.trim()||$('task-label').value.trim()||files.length)||confirm('Descartar o rascunho e os anexos para começar uma nova conversa?');}
function newConversation(){
 if(busy||loading||uploads){status('Aguarde a operação atual antes de iniciar outra conversa.');return;}
 restoreSelection();clearSubmission();
 resetActivity();active=null;$('task-label').value='';$('task-section').open=false;
 if(controller)controller.abort();controller=null;job='';last=0;parent=null;conversation='';files=[];renderFiles();
 $('messages').replaceChildren(welcomeTemplate.cloneNode(true));bindSuggestions();modelAvailability();$('prompt').value='';updateComposer();saveView();$('context-meter').textContent='Nova conversa · contexto independente';setBusy(false);status('Nova conversa.');$('prompt').focus();refreshProjectPermissions();
}
function chooseProject(id){
 if(busy||loading||uploads)return;
 const draft=$('prompt').value;$('project').value=id;newConversation();$('prompt').value=draft;updateComposer();saveView();renderProjects();
}
function renderProjects(){
 const query=normalizeSearch($('conversation-search').value.trim());
 const matches=conversations.filter(c=>normalizeSearch([c.title||'Conversa',c.execution?.task_label,c.execution?.backend,c.execution?.model,c.state].filter(Boolean).join(' ')).includes(query));
 $('search-clear').hidden=!query;$('search-results').hidden=!query;
 $('search-results').textContent=matches.length?matches.length+' conversa(s) encontrada(s)':'Nenhuma conversa encontrada. Tente outro título.';
 $('projects').replaceChildren(...Array.from($('project').options).filter(o=>o.value!=='sem-projeto').map(o=>{
  const group=document.createElement('details');group.className='project-group';
  group.open=!!query||(expandedProjects.get(o.value)??o.selected);
  group.ontoggle=()=>{if(!$('conversation-search').value.trim())expandedProjects.set(o.value,group.open);};
  const heading=document.createElement('summary');heading.className=o.selected?'active':'';
  const button=document.createElement('button');button.textContent=o.textContent;button.title=o.textContent;
  button.onclick=e=>{e.preventDefault();chooseProject(o.value);expandedProjects.set(o.value,true);renderProjects();};
  heading.append(button);
  const children=document.createElement('div');children.className='project-conversations';
  const items=matches.filter(c=>c.project===o.value);group.hidden=!!query&&!items.length;
  children.replaceChildren(...items.map(conversationRow));
  const create=document.createElement('button');create.className='project-new';create.textContent='＋ Novo chat';create.setAttribute('aria-label','Novo chat em '+o.textContent);
  create.onclick=()=>{
   if(busy||loading||uploads){status('Aguarde ou cancele a execução antes de iniciar outro chat.');return;}
   if(!confirmNewConversation())return;
   $('project').value=o.value;newConversation();expandedProjects.set(o.value,true);renderProjects();$('sidebar').classList.remove('open');
  };children.prepend(create);
  if(!items.length){const empty=document.createElement('p');empty.className='empty-history';empty.textContent='Nenhuma conversa';children.append(empty);}
  group.append(heading,children);return group;
 }));
 $('history').replaceChildren(...matches.filter(c=>c.project==null||c.project===''||c.project==='sem-projeto').map(conversationRow));
 if(!query&&!$('history').children.length){const empty=document.createElement('p');empty.className='empty-history';empty.textContent='Suas conversas sem projeto aparecerão aqui.';$('history').append(empty);}
}
function bubble(role,text=''){const el=document.createElement('article');el.className='message chat-item '+role;const body=document.createElement('div');body.className='text chat-bubble';body.textContent=text;el.append(body);$('messages').append(el);return{el,body};}
function assistant(id='',model=$('model').value,replayTools=false){const a=bubble('assistant');const title=document.createElement('h3');const badge=document.createElement('span');badge.className='model-badge';badge.setAttribute('aria-hidden','true');badge.textContent=modelIcon($('model').value);title.append(badge,document.createTextNode(names[$('model').value]||selected()?.name||'Resposta'));a.el.prepend(title);const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent='💭 Raciocínio do modelo';const reasoning=document.createElement('pre');details.append(summary,reasoning);details.hidden=true;a.el.insertBefore(details,a.body);const tools=document.createElement('details');const toolTitle=document.createElement('summary');toolTitle.textContent='🛠 Etapas e ferramentas';const toolText=document.createElement('pre');tools.append(toolTitle,toolText);const side=document.createElement('section');side.className='response-detail-card';side.dataset.job=id;side.tabIndex=-1;const sideTitle=document.createElement('h3');sideTitle.textContent=(names[model]||models.find(m=>m.id===model)?.name||model||'Resposta')+' · Execução '+id;side.append(sideTitle,tools);$('response-details').prepend(side);if(replayTools)tools.addEventListener('toggle',()=>{if(tools.open&&!tools.dataset.loaded){tools.dataset.loaded='1';loadResponseTools(id,toolText).catch(e=>{delete tools.dataset.loaded;toolText.textContent='Não foi possível recuperar as etapas: '+e.message;});}});const metrics=document.createElement('details');const metricTitle=document.createElement('summary');metricTitle.textContent='📊 Métricas, referências e alterações';const metricText=document.createElement('pre');metrics.append(metricTitle,metricText);metrics.hidden=true;side.append(metrics);const inspect=document.createElement('button');inspect.className='inspect-execution btn btn-secondary';inspect.textContent='Ver detalhes da execução';inspect.onclick=()=>{setActivityOpen(true);fitPanels();side.scrollIntoView({block:'nearest'});side.focus({preventScroll:true});};a.el.append(inspect);const chip=document.createElement('p');chip.className='run-highlight';chip.textContent='⏳ Aguardando execução';a.el.insertBefore(chip,a.body);const meta=document.createElement('div');meta.className='run-meta';a.el.append(meta);const copy=document.createElement('button');copy.className='copy-answer';copy.textContent='Copiar resposta';copy.onclick=async()=>{if(!a.body.textContent){status('A resposta ainda está vazia.');return;}try{await copyText(a.body.textContent);status('Resposta copiada.');}catch{status('Não foi possível copiar. Selecione o texto da resposta para copiá-lo.');}};a.el.append(copy);return{...a,reasoning,details,summary,toolText,metrics,metricText,meta,chip};}
async function loadResponseTools(id,target){
 target.textContent='Carregando etapas desta execução…';
 const response=await api('/v1/jobs/'+encodeURIComponent(id)+'/events',{signal:AbortSignal.timeout(15000)});
 const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',text='';
 try{while(true){const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});let end;
  while((end=buffer.indexOf('\n\n'))>=0){const block=buffer.slice(0,end);buffer=buffer.slice(end+2);const line=block.split('\n').find(l=>l.startsWith('data: '));if(!line)continue;
   const e=JSON.parse(line.slice(6)),data=e.data||{};
   if(['answer_delta','reasoning_delta','reasoning_summary','context_usage','quota_before','quota_after'].includes(e.type))continue;
   text=boundedText(text+(labels[e.type]||e.type)+(data.tool?' · '+data.tool:'')+'\n'+(e.type==='plan_updated'?(data.plan||[]).map(x=>x.step).join('\n')+'\n':''),24000);
  }
 }}finally{await reader.cancel();}
 target.textContent=text||'Nenhuma etapa ou ferramenta foi registrada nesta execução.';
}
// Animation follows execution events, not token timing or locally inferred progress.
function paintMotion(mode){
 $('status').dataset.motion=mode?'overall':'';
 for(const node of [$('activity-state'),active?.chip]){
  if(node)node.dataset.motion=mode;
 }
 if(active){
  active.body.dataset.motion=mode==='answer'?'answer':'';
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
function event(e){if(e.id<=last)return;last=e.id;if(e.type==='approval_required'){showApproval(e.data);return;}if(e.type==='approval_resolved'){document.getElementById('approval-'+e.data.approval_id)?.remove();return;}recordActivity(e);updateMotion(e.type);if(active)active.chip.textContent=$('activity-state').textContent;if(e.type==='context_usage'){paintContext(e.data);return;}if(e.type==='plan_updated'){active.toolText.textContent+='Plano atualizado\n'+(e.data.plan||[]).map(p=>(p.status==='completed'?'✓ ':p.status==='inProgress'?'→ ':'○ ')+p.step).join('\n')+'\n';status('Plano atualizado');return;}if(e.type==='answer_delta'){active.body.textContent+=e.data.text;status('Recebendo resposta…');}else if(e.type==='reasoning_delta'||e.type==='reasoning_summary'){active.details.hidden=false;active.summary.textContent=e.type==='reasoning_summary'?'💭 Resumo de raciocínio transmitido pelo Codex':'💭 Raciocínio transmitido pelo modelo';active.reasoning.textContent+=e.data.text;status('Raciocinando…');}else if(e.type==='quota_before'||e.type==='quota_after'){quotaSnapshot(e.type.endsWith('before')?'before':'after',e.data);}else{active.toolText.textContent+=(activityIcons[e.type]||'•')+' '+(e.type==='tool_end'&&['failed','error'].includes(e.data.status)?'Falha na ferramenta':labels[e.type]||e.type)+(e.data.tool?' · '+e.data.tool:'')+'\n';status(labels[e.type]||e.type);}scroll();}
function executionError(error){
 if(/context_limit_exceeded|exceed_context_size|exceeds the available context|maximum context length|source_context_limit|conversation_context_limit|context_window_exceeded/i.test(String(error)))return 'O conteúdo ultrapassou o limite de contexto do modelo. Os anexos envolvidos nesta tentativa foram retirados do contexto das próximas mensagens; os arquivos e o histórico foram preservados. Você pode continuar nesta conversa. Para analisar o CSV, envie uma parte menor ou disponibilize-o em uma pasta autorizada.';
 return 'A execução não foi concluída: '+error;
}
async function result(){const r=await json('/v1/jobs/'+job);updateMotion(r.state);$('activity-state').textContent=(activityIcons[r.state]||'•')+' '+(labels[r.state]||r.state);if(active)active.chip.textContent=$('activity-state').textContent;const data=r.result;if(data){if(data.context_usage)paintContext(data.context_usage);else if(data.metrics)paintLocalUsage(data.metrics);if(data.answer)active.body.textContent=data.answer;if(data.error)active.body.textContent=executionError(data.error);if(r.state==='cancelled'&&!active.body.textContent)active.body.textContent='Execução cancelada.';active.metrics.hidden=false;active.metricText.textContent=JSON.stringify({...data,staged_files:undefined},null,2);active.meta.textContent=(data.model||names[$('model').value]||'')+' · '+(data.total_seconds?data.total_seconds.toFixed(1)+' s · ':'')+'Execução '+job;if(data.deployment){active.meta.textContent+=' · '+(data.deployment.applied?'Alterações aplicadas':'Não aplicado: '+(data.deployment.error||'sem alterações'));}if(data.incomplete)status('Resposta incompleta. Reduza o escopo e tente novamente.');else status(({completed:'Concluído',cancelled:'Cancelado',failed:'Falha',interrupted:'Interrompido após reinício'})[r.state]||r.state);if(data.deployment)status(data.deployment.applied?'Alterações aplicadas ao projeto. Verificando atualização do painel…':'Alterações não aplicadas: '+(data.deployment.error||'sem alterações'));if(data.quota_before)quotaSnapshot('before',data.quota_before);if(data.quota_after)quotaSnapshot('after',data.quota_after);}if(['completed','failed','cancelled','interrupted'].includes(r.state))parent=job;saveView();return r;}
async function watch(retries=0){streamDisconnected=false;$('resume-execution').hidden=true;if(controller)controller.abort();controller=new AbortController();const current=controller;setBusy(true);paintMotion('working');try{const r=await api('/v1/jobs/'+job+'/events',{headers:{'Last-Event-ID':String(last)},signal:controller.signal});const reader=r.body.getReader(),decoder=new TextDecoder();let buffer='';while(true){const{value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});let end;while((end=buffer.indexOf('\n\n'))>=0){const block=buffer.slice(0,end);buffer=buffer.slice(end+2);const data=block.split('\n').find(l=>l.startsWith('data: '));if(controller!==current)return;if(data)event(JSON.parse(data.slice(6)));}}if(controller!==current)return;const final=await result();if(['running','queued'].includes(final.state)){if(retries<3){status('Reconectando à execução em andamento…');await new Promise(resolve=>setTimeout(resolve,1000));return await watch(retries+1);}throw Error('stream_closed');}if(controller!==current)return;await quota();await history();}catch(e){if(e.name!=='AbortError'){paintMotion('');$('activity-state').textContent='⚠ Conexão interrompida';streamDisconnected=true;$('resume-execution').hidden=false;status('Conexão interrompida. A execução pode continuar no servidor. Use Retomar acompanhamento ou Cancelar execução.');await history();}}finally{if(controller===current){setBusy(false);checkVersion();}}}
async function load(id,legacy=false){
 if(busy||loading||uploads)return;const priorDraft=$('prompt').value;loading=true;setBusy(true);
 try{
  let data;
  try{data=legacy?{turns:[await json('/v1/jobs/'+encodeURIComponent(id))]}:await json('/v1/conversations/'+encodeURIComponent(id));}
  catch(e){if(e.status!==404||!legacyHistory)throw e;data={turns:[await json('/v1/jobs/'+encodeURIComponent(id))]};}
  if(!data.turns?.length)throw Error('Conversa vazia');
  parent=null;job='';last=0;resetActivity();
  if(controller)controller.abort();controller=null;conversation=id;saveView();files=[];renderFiles();$('messages').replaceChildren();$('prompt').value='';
  for(const r of data.turns){
   $('project').value=r.project;const model=r.request?.backend==='qwen'?'qwen-local':r.request?.model;
   if(models.some(m=>m.id===model)){$('model').value=model;updateEfforts();$('effort').value=r.request?.effort||$('effort').value;}
   bubble('user',r.request?.prompt||'Execução anterior');active=assistant(r.id,model,r!==data.turns[data.turns.length-1]);job=r.id;
   if(r!==data.turns[data.turns.length-1]){active.body.textContent=r.result?.answer||(r.result?.error?executionError(r.result.error):r.state);active.chip.textContent=(activityIcons[r.state]||'•')+' '+(labels[r.state]||r.state);active.meta.textContent=(r.result?.model||model||'')+' · '+(r.result?.total_seconds?r.result.total_seconds.toFixed(1)+' s · ':'')+'Execução '+r.id;active.metrics.hidden=false;active.metricText.textContent=JSON.stringify({...r.result,staged_files:undefined},null,2);}
  }
  $('task-label').value=data.turns.at(-1).request?.task_label||'';$('task-section').open=false;
   const previous=data.turns.at(-2);
  if(previous&&['completed','failed','cancelled','interrupted'].includes(previous.state)){
   beginActivity(previous.id,previous.request?.backend==='qwen'?'qwen-local':previous.request?.model);
   try{await replayActivity(previous.id);}catch{$('activity-events').textContent='Não foi possível recuperar os eventos desta execução.';}
  }
  beginActivity(job);
  await refreshProjectPermissions();
  expandedProjects.set($('project').value,true);renderProjects();last=0;$('sidebar').classList.remove('open');await watch();
 }catch(e){loading=false;setBusy(false);$('prompt').value=priorDraft;updateComposer();$('sidebar').classList.remove('open');status('Não foi possível abrir a conversa. Seu rascunho foi preservado: '+e.message);}
 finally{loading=false;setBusy(false);}
}
function renderFiles(){$('attachments').replaceChildren(...files.map((f,i)=>{const el=document.createElement('span');el.className='attachment';el.textContent='▤ '+f.name;const b=document.createElement('button');b.textContent='×';b.title='Retirar da próxima mensagem';b.setAttribute('aria-label','Remover anexo '+f.name);b.disabled=busy||uploads>0;b.onclick=()=>{files.splice(i,1);renderFiles();};el.append(b);return el;}));}
async function upload(list){if(!canUpload()){status('Não foi possível anexar: anexos estão desativados na administração.');return;}if(busy||loading||uploads)return;uploads++;setBusy(busy);$('project').disabled=true;renderFiles();try{for(const f of Array.from(list)){const audio=/\.(wav|mp3|m4a|ogg|flac|webm|aac|opus)$/i.test(f.name);if(f.size>(audio?256:50)*1024*1024){status(audio?'O limite por áudio é 256 MiB.':'O limite por documento é 50 MiB.');continue;}status('Enviando e preparando '+f.name+'… Áudios são transcritos localmente.');try{const r=await json('/v1/files?project_id='+encodeURIComponent($('project').value)+'&backend='+encodeURIComponent(selected().backend)+'&model='+encodeURIComponent(selected().id),{method:'POST',headers:{'X-Filename':encodeURIComponent(f.name)},body:f,signal:AbortSignal.timeout(audio?8200000:600000)});files.push({id:r.file_id,name:f.name});renderFiles();status('Arquivo recebido.');}catch(e){status('Não foi possível enviar: '+attachmentError(e.message));}}}finally{uploads--;setBusy(busy);renderFiles();updateComposer();}}
async function send(){if(busy||loading||uploads||policyPending||!selected())return;const prompt=$('prompt').value.trim();if(!prompt)return;const m=selected();if(files.length&&!canUpload()){status('Este modelo não permite anexos. Remova os arquivos ou escolha um modelo com essa permissão.');return;}rememberSelection();submitting=true;setBusy(true);status('Enviando pedido ao servidor…');try{if(m.backend==='codex'){status('Consultando cota antes da execução…');quotaSnapshot('before',await json('/v1/usage'));}const data={project_id:$('project').value,task_label:$('task-label').value.trim(),prompt,file_ids:files.map(f=>f.id),backend:m.backend,model:m.id,effort:$('effort').value};if(parent)data.parent_job_id=parent;const r=await json('/v1/jobs',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':submissionKey(data)},body:JSON.stringify(data)});clearSubmission();$('welcome')?.remove();bubble('user',prompt);active=assistant(r.job_id,m.id);beginActivity(r.job_id);job=r.job_id;submitting=false;$('cancel').disabled=false;parent=job;if(!conversation)conversation=job;files=[];renderFiles();last=0;$('prompt').value='';updateComposer();saveView();$('messages').scrollTop=$('messages').scrollHeight;await history();await watch();}catch(e){status('Não foi possível executar: '+e.message+(e.status===429&&submitting?' Seu rascunho foi preservado.':''));}finally{submitting=false;setBusy(false);}}
$('send').onclick=send;$('prompt').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&e.keyCode!==229){e.preventDefault();send();}};$('cancel').onclick=async()=>{if(submitting||cancelling||!job)return;cancelling=true;$('cancel').disabled=true;try{await post('/v1/jobs/'+job+'/cancel',{});status('Cancelando…');if(streamDisconnected)await watch();}catch(e){status('Não foi possível cancelar: '+e.message);}finally{cancelling=false;$('cancel').disabled=submitting||!job;}};
$('new').onclick=()=>{if(busy||loading||uploads||!confirmNewConversation())return;if(Array.from($('project').options).some(o=>o.value==='sem-projeto'))$('project').value='sem-projeto';newConversation();renderProjects();history();$('sidebar').classList.remove('open');};
$('project').onchange=()=>{const draft=$('prompt').value;newConversation();$('prompt').value=draft;updateComposer();saveView();renderProjects();history();};
$('model').onchange=()=>{updateEfforts();rememberSelection();quota();};$('effort').onchange=rememberSelection;$('attach').onclick=()=>$('file').click();$('file').onchange=()=>{upload($('file').files);$('file').value='';};
$('dropzone').ondragover=e=>{e.preventDefault();$('dropzone').classList.add('drag');};$('dropzone').ondragleave=()=>$('dropzone').classList.remove('drag');$('dropzone').ondrop=e=>{e.preventDefault();$('dropzone').classList.remove('drag');upload(e.dataTransfer.files);};
$('quota-toggle').onclick=()=>setQuotaOpen($('quota-panel').hidden);$('quota-refresh').onclick=quota;function toggleSidebar(){
 if(matchMedia('(max-width:620px)').matches){$('sidebar').classList.toggle('open');}
 else{document.body.classList.toggle('sidebar-collapsed');try{localStorage.setItem('sidebar-collapsed',document.body.classList.contains('sidebar-collapsed')?'1':'0');}catch{}}
 $('menu').setAttribute('aria-expanded',String(matchMedia('(max-width:620px)').matches?$('sidebar').classList.contains('open'):!document.body.classList.contains('sidebar-collapsed')));
}
$('menu').onclick=()=>{toggleSidebar();fitPanels();};$('sidebar-close').onclick=()=>{toggleSidebar();fitPanels();};$('reload').onclick=()=>{saveView();location.reload();};
try{document.body.classList.toggle('sidebar-collapsed',localStorage.getItem('sidebar-collapsed')==='1');}catch{}
$('menu').setAttribute('aria-expanded',String(!matchMedia('(max-width:620px)').matches&&!document.body.classList.contains('sidebar-collapsed')));
$('setup').onclick=()=>$('setup-dialog').showModal();$('setup-close').onclick=()=>$('setup-dialog').close();
$('setup-code').textContent=`cd ~/Downloads
chmod +x setup-mcp.sh
./setup-mcp.sh '${location.origin}'`;
function bindSuggestions(){document.querySelectorAll('[data-prompt]').forEach(b=>b.onclick=()=>{$('prompt').value=b.dataset.prompt;updateComposer();saveView();$('prompt').focus();});}
bindSuggestions();
let startupTimer;
function modelAvailability(data,error=''){
 const panel=$('model-availability');panel.hidden=models.length>0&&!error;
 $('model-availability-title').textContent=error?'Não foi possível consultar os modelos':'Nenhum modelo disponível';
 $('model-availability-detail').textContent=error||'Adicione e habilite um provedor na administração deste servidor. Depois, inicie ou reinicie o harness para aplicar.';
 const link=$('admin-link');link.hidden=true;
 if(['127.0.0.1','localhost','::1'].includes(location.hostname)){const url=data?.admin_url||'http://127.0.0.1:8094/';try{const parsed=new URL(url);if(['127.0.0.1','localhost'].includes(parsed.hostname)&&parsed.protocol==='http:'){link.href=parsed.href;link.hidden=false;}}catch{}}
 const shortcut=$('admin-shortcut');shortcut.hidden=link.hidden;if(!link.hidden)shortcut.href=link.href;
 $('model').disabled=!models.length||busy;$('effort').disabled=!models.length||busy;$('prompt').placeholder=models.length?'Envie uma mensagem…':'Configure um modelo para enviar; seu rascunho será preservado.';
 $('model-note').hidden=!models.length;updateModelPermissions();
 if(!models.length){$('model').replaceChildren(new Option('Nenhum modelo disponível',''));$('effort').replaceChildren(new Option('Esforço indisponível',''));$('quota-short').textContent='Nenhum modelo selecionado';}
 if($('welcome'))$('welcome').hidden=!models.length;updateComposer();
}
async function initialize(){const retry=$('models-retry');retry.disabled=true;retry.textContent='Verificando…';try{
 const[p,m]=await Promise.all([json('/v1/projects'),json('/v1/models')]);
 const previous=$('model').value,project=$('project').value;
 $('project').replaceChildren(...p.projects.map(id=>{const o=new Option(p.details?.[id]?.label||id,id);return o;}));$('project').value=p.projects.includes(project)?project:p.projects.includes('sem-projeto')?'sem-projeto':p.projects[0];renderProjects();
 providers=m.providers||{};models=m.models||[];uploadsAllowed=m.uploads_enabled===true;policyProject='sem-projeto';policyPending=false;
 $('model').replaceChildren(...models.map(m=>new Option(modelIcon(m.id)+' '+(names[m.id]||m.name||m.id),m.id)));if(models.some(m=>m.id===previous))$('model').value=previous;
 updateEfforts();restoreSelection();await refreshProjectPermissions();modelAvailability(m);$('connection').textContent=['localhost','127.0.0.1'].includes(location.hostname)?'● Conectado ao servidor local':'● Conectado ao servidor';
 status(models.length?'Pronto para conversar.':'Configure um modelo para começar.');await Promise.all([quota(),history(),checkVersion()]);
 if(!startupTimer){try{const saved=JSON.parse(sessionStorage.getItem('remote-view')||'{}');if(saved.conversation)await load(saved.conversation);if(saved.draft)$('prompt').value=saved.draft;if(saved.task_label)$('task-label').value=saved.task_label;}catch{}startupTimer=setInterval(()=>{checkVersion();if(!document.hidden)history();},10000);}updateComposer();
 }catch(e){if(e.status===401)$('vpn-login').showModal();$('connection').textContent='● Sem conexão';modelAvailability(null,e.message);status('Não foi possível conectar: '+e.message);}finally{retry.disabled=false;retry.textContent='Verificar novamente';}}
$('resume-execution').onclick=()=>watch();
$('models-retry').onclick=()=>{if(!busy)initialize();};initialize();

function saveView(){try{sessionStorage.setItem('remote-view',JSON.stringify({conversation,draft:$('prompt').value,task_label:$('task-label').value}));}catch{}}
$('prompt').addEventListener('input',()=>{saveView();updateComposer();});
$('task-label').addEventListener('input',saveView);
function paintContext(u){
 const n=u.last?.totalTokens,w=u.modelContextWindow,total=u.total?.totalTokens;
 $('context-meter').textContent=(Number.isFinite(n)?'Contexto: '+n.toLocaleString()+(w?' / '+w.toLocaleString()+' tokens · '+Math.round(n/w*100)+'%':' tokens'):'Contexto não informado')+(Number.isFinite(total)?' · Consumo acumulado: '+total.toLocaleString()+' tokens':'');
}
function paintLocalUsage(m){$('context-meter').textContent='Última execução: '+(m.input_tokens??'—')+' tokens de entrada · '+(m.output_tokens??'—')+' de saída';}
async function checkVersion(){try{
 const v=await json('/v1/version');$('version').textContent='Painel '+v.version+' · '+v.build.slice(0,7);
 if(build&&build!==v.build)reloadPending=true;build=v.build;
 if(reloadPending&&!busy&&!loading&&!uploads&&!files.length&&!$('prompt').value.trim()){saveView();location.reload();}
 else if(reloadPending){$('reload').textContent='Atualização disponível ↻';}
}catch{}}

const activityIcons={queued:'⏳',running:'▶',loading:'⏳',thinking:'💭',planning:'📋',plan_updated:'📋',tool_start:'🛠',tool_end:'✓',completed:'✅',failed:'❌',error:'❌',cancelled:'■',interrupted:'⚠',validating_changes:'🔎',changes_applied:'💾',deployment_failed:'⚠',session_resumed:'↩',context_compacting:'⟳',context_compacted:'✓'};
function setActivityOpen(open,persist=true){
 $('activity-panel').hidden=!open;
 $('activity-toggle').setAttribute('aria-expanded',String(open));
 
 if(persist)try{localStorage.setItem('activity-open',open?'1':'0');}catch{}
}
function resetActivity(clearHistory=true){
 paintMotion('');
 if(clearHistory){$('response-details').replaceChildren();$('activity-previous').replaceChildren();$('activity-current').dataset.job='';$('activity-run-title').textContent='Aguardando execução';}
 $('activity-reasoning').textContent='Nenhum conteúdo transmitido.';
 $('activity-reasoning').dataset.received='';
 $('activity-events').replaceChildren();delete $('activity-events').dataset.omitted;$('activity-truncation').hidden=true;$('activity-failures').hidden=true;
 $('activity-state').textContent='Aguardando eventos da execução';
}
function recordActivity(e){
 const data=e.data||{},type=e.type;
 const toolFailed=type==='tool_end'&&(data.status==='failed'||data.result?.isError===true);
 if(type==='reasoning_delta'||type==='reasoning_summary'){
  const pre=$('activity-reasoning');
  if(!pre.dataset.received){pre.textContent='';pre.dataset.received='1';}
  pre.textContent=boundedText(pre.textContent+(data.text||''),24000);
  $('activity-state').textContent='💭 Recebendo raciocínio';
  return;
 }
 if(type==='answer_delta'){$('activity-state').textContent='✍ Recebendo resposta';return;}
 if(type==='context_usage'||type==='quota_before'||type==='quota_after')return;
 const list=$('activity-events'),row=document.createElement('li');
 const icon=document.createElement('span');icon.className='activity-icon';icon.setAttribute('aria-hidden','true');icon.textContent=toolFailed?'❌':activityIcons[type]||'•';
 const body=document.createElement('div'),title=document.createElement('strong');
 title.textContent=(toolFailed?'Falha na ferramenta':type==='plan_updated'?'Plano atualizado':labels[type]||type)+(data.tool?' · '+data.tool:'');
 if(type==='maestro_step'||type==='maestro_planning')title.textContent+=' · '+[data.index?'Etapa '+data.index:'',data.role,data.backend,data.model,data.effort].filter(Boolean).join(' · ');body.append(title);row.append(icon,body);row.dataset.state=toolFailed?'failed':type;
 if(type==='tool_end'&&data.result!=null){const details=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');summary.textContent='Resultado da ferramenta';pre.className='activity-result';pre.textContent=boundedText(typeof data.result==='string'?data.result:JSON.stringify(data.result,null,2),4000);details.append(summary,pre);body.append(details);}
 if(type==='plan_updated'){
  const plan=document.createElement('ul');
  for(const step of (data.plan||[]).slice(-40)){const item=document.createElement('li');item.textContent=(step.status==='completed'?'✓ ':step.status==='inProgress'?'→ ':'○ ')+boundedText(String(step.step),500);plan.append(item);}
  body.append(plan);
 }
 if(e.timestamp){const time=document.createElement('time');const date=new Date(typeof e.timestamp==='number'?e.timestamp*1000:e.timestamp);if(!Number.isNaN(date.getTime())){time.dateTime=date.toISOString();time.textContent=date.toLocaleTimeString();body.append(time);}}
 const box=document.querySelector('.activity-scroll'),follow=box.scrollHeight-box.scrollTop-box.clientHeight<80;
 list.append(row);
 if(list.children.length>80){const removed=list.firstElementChild;const count=Number(list.dataset.omitted||0)+1;list.dataset.omitted=count;const note=$('activity-truncation');note.hidden=false;note.textContent=count+' eventos anteriores omitidos desta lista; o registro completo permanece salvo.';if(removed.dataset.state==='failed'){const failure=$('activity-failures');failure.hidden=false;failure.textContent='Houve falha nesta execução: '+removed.textContent.slice(0,350);}removed.remove();}
 $('activity-state').textContent=icon.textContent+' '+title.textContent;
 if(follow)box.scrollTop=box.scrollHeight;
}
$('activity-toggle').onclick=()=>setActivityOpen($('activity-panel').hidden);
$('activity-close').onclick=()=>{setActivityOpen(false);$('activity-toggle').focus();};
$('activity-panel').addEventListener('keydown',e=>{if(e.key==='Escape'){setActivityOpen(false);$('activity-toggle').focus();}});
try{const preference=localStorage.getItem('activity-open');setActivityOpen(preference===null?matchMedia('(min-width:1200px)').matches:preference==='1',false);}catch{setActivityOpen(matchMedia('(min-width:1200px)').matches,false);}

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
const panelWidths={sidebar:250,'activity-panel':320};
function panelLimits(id){
 const mobile=innerWidth<=620, docked=innerWidth>=1200;
 const other=id==='sidebar'?$('activity-panel'):$('sidebar');
 const otherWidth=(id==='sidebar'?docked:!mobile)&&other.getClientRects().length?other.getBoundingClientRect().width:0;
 const max=Math.max(220,Math.min(720,(id==='activity-panel'&&!docked)||mobile?innerWidth-24:innerWidth-otherWidth-440));
 return {min:220,max};
}
function sizePanel(id,width,persist=true){
 const {min,max}=panelLimits(id),value=Math.round(Math.max(min,Math.min(max,width)));
 $(id).style.width=value+'px';
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
 handle.addEventListener('pointermove',e=>{if(drag)sizePanel(id,drag.width+(e.clientX-drag.x)*(id==='sidebar'?1:-1));});
 const stop=()=>{drag=null;document.body.classList.remove('resizing-panels');};
 handle.addEventListener('pointerup',stop);handle.addEventListener('pointercancel',stop);handle.addEventListener('lostpointercapture',stop);
 handle.addEventListener('keydown',e=>{
  if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();
  const {min,max}=panelLimits(id),width=$(id).getBoundingClientRect().width;
  sizePanel(id,e.key==='Home'?min:e.key==='End'?max:width+(e.key==='ArrowRight'?24:-24)*(id==='sidebar'?1:-1));
 });
}
function fitPanels(){sizePanel('sidebar',panelWidths.sidebar,false);sizePanel('activity-panel',panelWidths['activity-panel'],false);}
window.addEventListener('resize',fitPanels);fitPanels();

$('activity-toggle').onclick=()=>{setActivityOpen($('activity-panel').hidden);fitPanels();};

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
 $('catalog-models').replaceChildren(...models.map(m=>catalogCard({name:modelIcon(m.id)+' '+(names[m.id]||m.name||m.id),description:(m.backend==='local'?'Modelo local. ':m.backend==='claude'?'Modelo via Claude Code. ':'Modelo via Codex. ')+'Esforços: '+m.efforts.map(e=>efforts[e]||e).join(', '),status:'Modelo configurado',source:'/v1/models'})));
 for(const provider of ['codex','claude']){
  if(!providers[provider])$('catalog-models').append(catalogCard({name:provider==='claude'?'Claude Code':'Codex',description:'Configure o adaptador no serviço para habilitar este provedor.',status:'Não configurado',source:'/v1/models'}));
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
$('catalog-refresh').onclick=refreshCatalog;

/* Conversation navigation and composition enhancements. */
function normalizeSearch(value){return value.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase('pt-BR');}
$('conversation-search').addEventListener('input',renderProjects);
$('search-clear').onclick=()=>{$('conversation-search').value='';renderProjects();$('conversation-search').focus();};
function updateComposer(){
 const prompt=$('prompt');prompt.style.height='auto';prompt.style.height=Math.min(prompt.scrollHeight,170)+'px';
 const count=Array.from(prompt.value).length;
 $('character-count').textContent=count.toLocaleString('pt-BR')+(count===1?' caractere':' caracteres');
 $('send').disabled=busy||loading||uploads>0||policyPending||!selected()||!prompt.value.trim();
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
 if(document.querySelector('dialog[open]'))return;
 if((e.ctrlKey||e.metaKey)&&!e.altKey&&(e.key==='/'||e.key.toLowerCase()==='k')){
  e.preventDefault();
  if(e.key==='/')$('prompt').focus();
  else{if(matchMedia('(max-width:620px)').matches){$('sidebar').classList.add('open');}else document.body.classList.remove('sidebar-collapsed');
   $('menu').setAttribute('aria-expanded','true');fitPanels();$('conversation-search').focus();}
 }
 if(e.key==='Escape'){
  if(!$('quota-panel').hidden){setQuotaOpen(false);$('quota-toggle').focus();}
  else if($('sidebar').classList.contains('open')){$('sidebar').classList.remove('open');$('menu').setAttribute('aria-expanded','false');$('menu').focus();}
 }
});

function applyReadingSize(value){const size=['15','17','19'].includes(value)?value:'15';document.documentElement.style.setProperty('--reading-size',size+'px');$('reading-size').value=size;try{localStorage.setItem('reading-size',size);}catch{}}
let readingSize='15';try{readingSize=localStorage.getItem('reading-size')||'15';}catch{}
applyReadingSize(readingSize);$('reading-size').onchange=()=>applyReadingSize($('reading-size').value);
window.addEventListener('resize',()=>{updateComposer();updateLatest();});
updateComposer();

async function copyText(text){
 if(navigator.clipboard&&window.isSecureContext){try{await navigator.clipboard.writeText(text);return;}catch{}}
 // The panel can run over local HTTP, where Clipboard API is unavailable.
 const previous=document.activeElement,selection=window.getSelection(),ranges=[];
 if(selection)for(let i=0;i<selection.rangeCount;i++)ranges.push(selection.getRangeAt(i).cloneRange());
 const field=document.createElement('textarea');field.value=text;field.readOnly=true;field.className='clipboard-buffer';document.body.append(field);
 try{field.select();if(!document.execCommand('copy'))throw Error('Clipboard unavailable');}
 finally{field.remove();previous?.focus({preventScroll:true});if(selection){selection.removeAllRanges();for(const range of ranges)selection.addRange(range);}}
}

$('vpn-login-form').onsubmit=async e=>{e.preventDefault();try{await post('/v1/login',{token:$('vpn-login-token').value});location.reload();}catch(error){$('vpn-login-error').textContent=error.message;}};
function showApproval(data){
 if(document.getElementById('approval-'+data.approval_id))return;
 const box=document.createElement('section');box.id='approval-'+data.approval_id;box.className='approval-card';
 const title=document.createElement('h3');title.textContent='O CLI precisa da sua decisão';const pre=document.createElement('pre');pre.textContent=JSON.stringify(data.request,null,2);box.append(title,pre);
 const fields=[];for(const q of data.request.questions||[]){const label=document.createElement('label');label.textContent=q.question;const input=document.createElement('input');input.placeholder=(q.options||[]).map(o=>o.label).join(' / ');label.append(input);box.append(label);fields.push([q.id,input]);}
 let deciding=false;const progress=document.createElement('p');progress.setAttribute('role','status');progress.hidden=true;box.append(progress);
 for(const [text,approved] of [['Permitir uma vez',true],['Negar',false]]){const button=document.createElement('button');button.className='btn';button.textContent=text;button.onclick=async()=>{if(deciding)return;deciding=true;box.querySelectorAll('button,input').forEach(node=>node.disabled=true);progress.hidden=false;progress.textContent='Enviando sua decisão…';try{const answers=Object.fromEntries(fields.map(([id,input])=>[id,{answers:[input.value]}]));await post('/v1/approvals/'+data.approval_id,{approved,answers});box.remove();}catch(e){progress.textContent='Não foi possível confirmar sua decisão. '+e.message;}finally{deciding=false;box.querySelectorAll('button,input').forEach(node=>node.disabled=false);}};box.append(button);}
 $('messages').append(box);status('Aguardando sua aprovação');box.scrollIntoView({block:'nearest'});
}

for(const [id,name] of [['new','plus'],['menu','layout-sidebar'],['attach','paperclip'],['send','arrow-up'],['cancel','player-stop'],['settings','settings'],['reload','refresh']]){const b=$(id);if(!b)continue;b.classList.add('btn');if(['new','send'].includes(id))b.classList.add('btn-primary');if(['menu','attach','send','cancel'].includes(id))b.classList.add('btn-icon');const label={new:'Nova conversa',settings:'Configurações',reload:'Recarregar tela'}[id];b.replaceChildren(TailUI.icon(name));if(label)b.append(document.createTextNode(label));}
for(const [id,text] of [['model','Modelo'],['effort','Esforço']]){const select=$(id),label=document.createElement('label');label.htmlFor=id;label.append(document.createTextNode(text));select.before(label);label.append(select);}

for(const [id,name,label] of [['sidebar-close','chevron-left','Recolher'],['activity-close','x',''],['settings-close','x',''],['setup-close','x',''],['search-clear','x',''],['activity-toggle','adjustments','Atividade'],['catalog-refresh','refresh','Atualizar catálogo do projeto']]){const button=$(id);button.classList.add('btn');if(!label)button.classList.add('btn-icon');button.replaceChildren(TailUI.icon(name));if(label)button.append(document.createTextNode(label));}
for(const node of document.querySelectorAll('.brandmark,.welcome-icon'))node.replaceChildren(TailUI.icon('stack-2'));
for(const button of document.querySelectorAll('[data-settings]')){button.textContent=button.textContent.replace(/^[^A-Za-zÀ-ÿ]+/,'');button.prepend(TailUI.icon(button.dataset.settings==='appearance'?'adjustments':button.dataset.settings==='agents'?'stack-2':'message'));}

function attachmentError(code){return ({audio_transcription_unavailable:'A transcrição local de áudio não está instalada neste servidor.',audio_duration_limit:'Envie um áudio de até 2 horas.',invalid_audio:'Não foi possível reconhecer o áudio. Tente WAV, MP3, M4A, OGG ou FLAC.',audio_transcription_failed:'A transcrição local falhou; o áudio não foi anexado.',local_vision_not_enabled:'Este servidor local está sem visão habilitada. É necessário configurar o projetor visual (mmproj) do modelo e reiniciar o servidor. O arquivo não foi anexado.',image_capability_unavailable:'Não foi possível verificar a visão deste servidor. Tente novamente quando ele estiver disponível.',model_images_unavailable:'O serviço selecionado não oferece leitura de imagens.',images_require_native_service:'A leitura de imagens exige a execução nativa do serviço.',select_model_for_image:'Selecione um modelo com permissão para anexos antes de enviar a imagem.',image_size_limit:'Imagens podem ter até 5 MiB.',unsupported_binary_format:'Este formato binário ainda não tem um leitor disponível. Envie uma imagem compatível, PDF com texto, documento Office/OpenDocument ou arquivo de texto.',binary_denied:'Este arquivo contém dados binários sem leitor disponível.',invalid_document:'O documento está inválido ou corrompido.',document_expansion_limit:'O documento excede o limite seguro de descompactação.'})[code]||code;}
