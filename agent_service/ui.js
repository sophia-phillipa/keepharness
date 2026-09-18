'use strict';
const $=id=>document.getElementById(id);
let providers={},models=[],files=[],job='',last=0,controller=null,active=null,busy=false,parent=null,conversation='',loading=false,build='',reloadPending=false;
let conversations=[],legacyHistory=false,historyRequest=0,uploads=0;
const welcomeTemplate=$('welcome').cloneNode(true);
const expandedProjects=new Map();
let preferredSelection={};
try{preferredSelection=JSON.parse(localStorage.getItem('chat-selection')||'{}')||{};}catch{}
const labels={answer_delta:'Respondendo',reasoning_delta:'Pensando',reasoning_summary:'Resumo de raciocínio',interrupted:'Interrompido',queued:'Na fila',running:'Executando',thinking:'Pensando',planning:'Preparando a execução',tool_start:'Usando ferramenta',tool_end:'Ferramenta concluída',session_resumed:'Contexto da conversa retomado',context_compacting:'Otimizando o contexto da conversa…',context_compacted:'Contexto otimizado; conversa preservada',validating_changes:'Validando alterações',changes_applied:'Alterações aplicadas ao projeto',deployment_failed:'Alterações não aplicadas; consulte o erro',reload_scheduled:'Atualizando o painel',completed:'Concluído',cancelled:'Cancelado',failed:'Falha',loading:'Preparando modelo'};
const status=text=>{$('status').textContent=text;};
const names={'qwen-local':'Qwen3.6 · local','gpt-6-astra':'Astra','gpt-5.6-sol':'Sol','gpt-5.6-terra':'Terra','gpt-5.6-luna':'Luna','gpt-5.5':'GPT-5.5'};
const modelIcons={'qwen-local':'✦','gpt-6-astra':'🌟','gpt-5.6-sol':'☀️','gpt-5.6-terra':'🌍','gpt-5.6-luna':'🌙','gpt-5.5':'✳'};
const modelIcon=id=>modelIcons[id]||'🤖';
const efforts={configured:'Padrão do provedor',low:'Baixo',medium:'Médio',high:'Alto',xhigh:'Muito alto',max:'Máximo',ultra:'Ultra'};
async function api(path,options={}){const r=await fetch(path,options);if(!r.ok){let e;try{e=await r.json();}catch{e={code:'HTTP '+r.status};}const error=Error(e.code||'HTTP '+r.status);error.status=r.status;throw error;}return r;}
async function json(path,options){return(await api(path,options)).json();}
const post=(path,value)=>json(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
function selected(){return models.find(m=>m.id===$('model').value)||models[0];}
function setBusy(value){busy=value;if(!value)paintMotion('');$('send').disabled=value||loading||uploads>0||!selected()||!$('prompt').value.trim();$('send').hidden=value;$('cancel').hidden=!value;$('attach').disabled=value;$('project').disabled=value;$('model').disabled=value;$('effort').disabled=value;$('task-label').disabled=value;$('attach').disabled=value||uploads>0;$('new').disabled=value||loading||uploads>0;updateComposer();renderFiles();}
function updateEfforts(){const m=selected();if(!m)return;$('effort').replaceChildren(...m.efforts.map(e=>{const o=document.createElement('option');o.value=e;o.textContent=efforts[e]||e;return o;}));$('model-note').textContent=m.backend==='local'?'Modelo local executa no servidor · sem cota OpenAI · confira as fontes':m.backend==='claude'?'Claude Code no servidor · inferência Anthropic · cota não disponível':'Codex CLI no servidor · inferência OpenAI · consome cota ChatGPT';}
function quotaText(q){if(!q?.available)return'Cota indisponível';const buckets=q.rateLimitsByLimitId||{codex:q.rateLimits};const result=[];for(const b of Object.values(buckets)){if(!b)continue;for(const w of [b.primary,b.secondary]){if(!w)continue;result.push(`${Math.max(0,100-w.usedPercent)}% · ${w.windowDurationMins>=10080?'semana':w.windowDurationMins===300?'5 horas':Math.round(w.windowDurationMins/60)+'h'}`);}}return result.join(' / ')||'Cota indisponível';}
function paintQuota(q){$('quota-short').textContent=quotaText(q);$('quota-current').replaceChildren();if(!q?.available){$('quota-current').textContent='Não foi possível consultar agora. Nenhum consumo foi estimado.';return;}for(const b of Object.values(q.rateLimitsByLimitId||{codex:q.rateLimits})){if(!b)continue;for(const w of [b.primary,b.secondary]){if(!w)continue;const wrap=document.createElement('div');wrap.className='quota-window';const remaining=Math.max(0,100-w.usedPercent);const label=document.createElement('div');label.textContent=`${remaining}% restante · ${w.windowDurationMins>=10080?'Semanal':w.windowDurationMins===300?'5 horas':w.windowDurationMins+' minutos'}`;const bar=document.createElement('progress');bar.max=100;bar.value=remaining;const reset=document.createElement('small');reset.textContent='Renova em '+new Date(w.resetsAt*1000).toLocaleString();wrap.append(label,bar,reset);$('quota-current').append(wrap);}}}
async function quota(){try{paintQuota(await json('/v1/usage'));}catch{paintQuota(null);}}
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
function newConversation(){
 if(busy||loading||uploads){status('Aguarde a operação atual antes de iniciar outra conversa.');return;}
 restoreSelection();
 resetActivity();active=null;$('task-label').value='';$('task-section').open=false;
 if(controller)controller.abort();controller=null;job='';last=0;parent=null;conversation='';files=[];renderFiles();
 $('messages').replaceChildren(welcomeTemplate.cloneNode(true));bindSuggestions();$('prompt').value='';updateComposer();saveView();$('context-meter').textContent='Nova conversa · contexto independente';setBusy(false);status('Nova conversa.');$('prompt').focus();
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
   $('project').value=o.value;newConversation();expandedProjects.set(o.value,true);renderProjects();$('sidebar').classList.remove('open');
  };children.prepend(create);
  if(!items.length){const empty=document.createElement('p');empty.className='empty-history';empty.textContent='Nenhuma conversa';children.append(empty);}
  group.append(heading,children);return group;
 }));
 $('history').replaceChildren(...matches.filter(c=>c.project==null||c.project===''||c.project==='sem-projeto').map(conversationRow));
 if(!query&&!$('history').children.length){const empty=document.createElement('p');empty.className='empty-history';empty.textContent='Suas conversas sem projeto aparecerão aqui.';$('history').append(empty);}
}
function bubble(role,text=''){const el=document.createElement('article');el.className='message '+role;const body=document.createElement('div');body.className='text';body.textContent=text;el.append(body);$('messages').append(el);return{el,body};}
function assistant(){const a=bubble('assistant');const title=document.createElement('h3');const badge=document.createElement('span');badge.className='model-badge';badge.setAttribute('aria-hidden','true');badge.textContent=modelIcon($('model').value);title.append(badge,document.createTextNode(names[$('model').value]||selected()?.name||'Resposta'));a.el.prepend(title);const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent='💭 Raciocínio do modelo';const reasoning=document.createElement('pre');details.append(summary,reasoning);details.hidden=true;a.el.insertBefore(details,a.body);const tools=document.createElement('details');const toolTitle=document.createElement('summary');toolTitle.textContent='🛠 Etapas e ferramentas';const toolText=document.createElement('pre');tools.append(toolTitle,toolText);a.el.append(tools);const metrics=document.createElement('details');const metricTitle=document.createElement('summary');metricTitle.textContent='📊 Métricas, referências e alterações';const metricText=document.createElement('pre');metrics.append(metricTitle,metricText);metrics.hidden=true;a.el.append(metrics);const chip=document.createElement('p');chip.className='run-highlight';chip.textContent='⏳ Aguardando execução';a.el.insertBefore(chip,a.body);const meta=document.createElement('div');meta.className='run-meta';a.el.append(meta);const copy=document.createElement('button');copy.className='copy-answer';copy.textContent='Copiar resposta';copy.onclick=async()=>{if(!a.body.textContent){status('A resposta ainda está vazia.');return;}try{await copyText(a.body.textContent);status('Resposta copiada.');}catch{status('Não foi possível copiar. Selecione o texto da resposta para copiá-lo.');}};a.el.append(copy);return{...a,reasoning,details,summary,toolText,metrics,metricText,meta,chip};}
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
function event(e){if(e.id<=last)return;last=e.id;if(e.type==='approval_required'){showApproval(e.data);return;}if(e.type==='approval_resolved'){document.getElementById('approval-'+e.data.approval_id)?.remove();return;}recordActivity(e);updateMotion(e.type);if(active)active.chip.textContent=$('activity-state').textContent;if(e.type==='context_usage'){paintContext(e.data);return;}if(e.type==='plan_updated'){active.toolText.textContent+='Plano atualizado\n'+(e.data.plan||[]).map(p=>(p.status==='completed'?'✓ ':p.status==='inProgress'?'→ ':'○ ')+p.step).join('\n')+'\n';status('Plano atualizado');return;}if(e.type==='answer_delta'){active.body.textContent+=e.data.text;status('Recebendo resposta…');}else if(e.type==='reasoning_delta'||e.type==='reasoning_summary'){active.details.hidden=false;active.summary.textContent=e.type==='reasoning_summary'?'💭 Resumo de raciocínio transmitido pelo Codex':'💭 Raciocínio transmitido pelo modelo';active.reasoning.textContent+=e.data.text;status('Raciocinando…');}else if(e.type==='quota_before'||e.type==='quota_after'){quotaSnapshot(e.type.endsWith('before')?'before':'after',e.data);}else{active.toolText.textContent+=(activityIcons[e.type]||'•')+' '+(labels[e.type]||e.type)+(e.data.tool?' · '+e.data.tool:'')+'\n';status(labels[e.type]||e.type);}scroll();}
async function result(){const r=await json('/v1/jobs/'+job);updateMotion(r.state);$('activity-state').textContent=(activityIcons[r.state]||'•')+' '+(labels[r.state]||r.state);if(active)active.chip.textContent=$('activity-state').textContent;const data=r.result;if(data){if(data.context_usage)paintContext(data.context_usage);else if(data.metrics)paintLocalUsage(data.metrics);if(data.answer)active.body.textContent=data.answer;if(data.error)active.body.textContent='A execução não foi concluída: '+data.error;if(r.state==='cancelled'&&!active.body.textContent)active.body.textContent='Execução cancelada.';active.metrics.hidden=false;active.metricText.textContent=JSON.stringify({...data,staged_files:undefined},null,2);active.meta.textContent=(data.model||names[$('model').value]||'')+' · '+(data.total_seconds?data.total_seconds.toFixed(1)+' s · ':'')+'Execução '+job;if(data.deployment){active.meta.textContent+=' · '+(data.deployment.applied?'Alterações aplicadas':'Não aplicado: '+(data.deployment.error||'sem alterações'));}if(data.incomplete)status('Resposta incompleta. Reduza o escopo e tente novamente.');else status(({completed:'Concluído',cancelled:'Cancelado',failed:'Falha',interrupted:'Interrompido após reinício'})[r.state]||r.state);if(data.deployment)status(data.deployment.applied?'Alterações aplicadas ao projeto. Verificando atualização do painel…':'Alterações não aplicadas: '+(data.deployment.error||'sem alterações'));if(data.quota_before)quotaSnapshot('before',data.quota_before);if(data.quota_after)quotaSnapshot('after',data.quota_after);}if(['completed','failed','cancelled','interrupted'].includes(r.state))parent=job;saveView();return r;}
async function watch(){if(controller)controller.abort();controller=new AbortController();const current=controller;setBusy(true);paintMotion('working');try{const r=await api('/v1/jobs/'+job+'/events',{headers:{'Last-Event-ID':String(last)},signal:controller.signal});const reader=r.body.getReader(),decoder=new TextDecoder();let buffer='';while(true){const{value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});let end;while((end=buffer.indexOf('\n\n'))>=0){const block=buffer.slice(0,end);buffer=buffer.slice(end+2);const data=block.split('\n').find(l=>l.startsWith('data: '));if(controller!==current)return;if(data)event(JSON.parse(data.slice(6)));}}if(controller!==current)return;await result();if(controller!==current)return;await quota();await history();}catch(e){if(e.name!=='AbortError'){paintMotion('');$('activity-state').textContent='⚠ Conexão interrompida';status('Conexão interrompida. Clique nesta conversa no histórico para retomar.');await history();}}finally{if(controller===current){setBusy(false);checkVersion();}}}
async function load(id,legacy=false){
 if(busy||loading||uploads)return;loading=true;setBusy(true);parent=null;
 try{
  let data;
  try{data=legacy?{turns:[await json('/v1/jobs/'+encodeURIComponent(id))]}:await json('/v1/conversations/'+encodeURIComponent(id));}
  catch(e){if(e.status!==404||!legacyHistory)throw e;data={turns:[await json('/v1/jobs/'+encodeURIComponent(id))]};}
  if(!data.turns?.length)throw Error('Conversa vazia');
  job='';last=0;resetActivity();
  if(controller)controller.abort();controller=null;conversation=id;saveView();files=[];renderFiles();$('messages').replaceChildren();$('prompt').value='';
  for(const r of data.turns){
   $('project').value=r.project;const model=r.request?.backend==='qwen'?'qwen-local':r.request?.model;
   if(models.some(m=>m.id===model)){$('model').value=model;updateEfforts();$('effort').value=r.request?.effort||$('effort').value;}
   bubble('user',r.request?.prompt||'Execução anterior');active=assistant();job=r.id;
   if(r!==data.turns[data.turns.length-1]){active.body.textContent=r.result?.answer||r.result?.error||r.state;active.chip.textContent=(activityIcons[r.state]||'•')+' '+(labels[r.state]||r.state);active.metrics.hidden=false;active.metricText.textContent=JSON.stringify({...r.result,staged_files:undefined},null,2);}
  }
  $('task-label').value=data.turns.at(-1).request?.task_label||'';$('task-section').open=false;
   const previous=data.turns.at(-2);
  if(previous&&['completed','failed','cancelled','interrupted'].includes(previous.state)){
   beginActivity(previous.id,previous.request?.backend==='qwen'?'qwen-local':previous.request?.model);
   try{await replayActivity(previous.id);}catch{$('activity-events').textContent='Não foi possível recuperar os eventos desta execução.';}
  }
  beginActivity(job);
  expandedProjects.set($('project').value,true);renderProjects();last=0;$('sidebar').classList.remove('open');await watch();
 }catch(e){loading=false;setBusy(false);newConversation();status('Não foi possível abrir a conversa: '+e.message);}
 finally{loading=false;setBusy(false);}
}
function renderFiles(){$('attachments').replaceChildren(...files.map((f,i)=>{const el=document.createElement('span');el.className='attachment';el.textContent='▤ '+f.name;const b=document.createElement('button');b.textContent='×';b.title='Retirar da próxima mensagem';b.setAttribute('aria-label','Remover anexo '+f.name);b.disabled=busy||uploads>0;b.onclick=()=>{files.splice(i,1);renderFiles();};el.append(b);return el;}));}
async function upload(list){if(busy||loading||uploads)return;uploads++;setBusy(busy);$('project').disabled=true;renderFiles();try{for(const f of Array.from(list)){if(f.size>50*1024*1024){status('O limite por arquivo é 50 MiB.');continue;}status('Enviando '+f.name+'…');try{const r=await json('/v1/files?project_id='+encodeURIComponent($('project').value),{method:'POST',headers:{'X-Filename':encodeURIComponent(f.name)},body:f});files.push({id:r.file_id,name:f.name});renderFiles();status('Arquivo recebido.');}catch(e){status('Não foi possível enviar: '+e.message);}}}finally{uploads--;setBusy(busy);renderFiles();updateComposer();}}
async function send(){if(busy||loading||uploads||!selected())return;const prompt=$('prompt').value.trim();if(!prompt)return;const m=selected();rememberSelection();setBusy(true);try{if(m.backend==='codex'){status('Consultando cota antes da execução…');quotaSnapshot('before',await json('/v1/usage'));}const data={project_id:$('project').value,task_label:$('task-label').value.trim(),prompt,file_ids:files.map(f=>f.id),backend:m.backend,model:m.id,effort:$('effort').value};if(parent)data.parent_job_id=parent;const r=await json('/v1/jobs',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':crypto.randomUUID?crypto.randomUUID():Date.now().toString(36)+Math.random().toString(36)},body:JSON.stringify(data)});$('welcome')?.remove();bubble('user',prompt);active=assistant();beginActivity(r.job_id);job=r.job_id;parent=job;if(!conversation)conversation=job;files=[];renderFiles();last=0;$('prompt').value='';updateComposer();saveView();$('messages').scrollTop=$('messages').scrollHeight;await history();await watch();}catch(e){status('Não foi possível executar: '+e.message);}finally{setBusy(false);}}
$('send').onclick=send;$('prompt').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&e.keyCode!==229){e.preventDefault();send();}};$('cancel').onclick=async()=>{try{await post('/v1/jobs/'+job+'/cancel',{});status('Cancelando…');}catch(e){status(e.message);}};
$('new').onclick=()=>{if(busy||loading||uploads)return;if(Array.from($('project').options).some(o=>o.value==='sem-projeto'))$('project').value='sem-projeto';newConversation();renderProjects();history();$('sidebar').classList.remove('open');};
$('project').onchange=()=>{const draft=$('prompt').value;newConversation();$('prompt').value=draft;updateComposer();saveView();renderProjects();history();};
$('model').onchange=()=>{updateEfforts();rememberSelection();};$('effort').onchange=rememberSelection;$('attach').onclick=()=>$('file').click();$('file').onchange=()=>{upload($('file').files);$('file').value='';};
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
$('setup-code').textContent=`mkdir -p "$HOME/.local/share/tail-harness"
python3 -m venv "$HOME/.local/share/tail-harness/venv"
"$HOME/.local/share/tail-harness/venv/bin/pip" install 'mcp>=1.12,<2' 'httpx>=0.27,<1'
curl -fS ${location.origin}/mcp_bridge.py -o "$HOME/.local/share/tail-harness/mcp_bridge.py"
claude mcp add --transport stdio --scope user --env LOCAL_AGENT_URL=${location.origin} tail-harness -- "$HOME/.local/share/tail-harness/venv/bin/python" "$HOME/.local/share/tail-harness/mcp_bridge.py"`;
function bindSuggestions(){document.querySelectorAll('[data-prompt]').forEach(b=>b.onclick=()=>{$('prompt').value=b.dataset.prompt;updateComposer();saveView();$('prompt').focus();});}
bindSuggestions();
(async()=>{try{const[p,m]=await Promise.all([json('/v1/projects'),json('/v1/models')]);$('project').replaceChildren(...p.projects.map(id=>{const o=document.createElement('option');o.value=id;o.textContent=p.details?.[id]?.label||id;return o;}));if(p.projects.includes('sem-projeto'))$('project').value='sem-projeto';renderProjects();providers=m.providers||{};models=m.models;$('model').replaceChildren(...models.map(m=>{const o=document.createElement('option');o.value=m.id;o.textContent=modelIcon(m.id)+' '+(names[m.id]||m.name);return o;}));updateEfforts();restoreSelection();updateComposer();$('connection').textContent='● Conectado pela Tailscale';status('Pronto para conversar.');await Promise.all([quota(),history(),checkVersion()]);try{const saved=JSON.parse(sessionStorage.getItem('remote-view')||'{}');if(saved.conversation)await load(saved.conversation);if(saved.draft)$('prompt').value=saved.draft;if(saved.task_label)$('task-label').value=saved.task_label;}catch{}updateComposer();setInterval(()=>{checkVersion();if(!document.hidden)history();},10000);}catch(e){if(e.status===401)$('vpn-login').showModal();$('connection').textContent='● Sem conexão';status('Conecte o Tailscale com sua conta autorizada e atualize a página. '+e.message);}})();

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
 if(clearHistory){$('activity-previous').replaceChildren();$('activity-current').dataset.job='';$('activity-run-title').textContent='Aguardando execução';}
 $('activity-reasoning').textContent='Nenhum conteúdo transmitido.';
 $('activity-reasoning').dataset.received='';
 $('activity-events').replaceChildren();
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
 body.append(title);row.append(icon,body);row.dataset.state=toolFailed?'failed':type;
 if(type==='tool_end'&&data.result!=null){const details=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');summary.textContent='Resultado da ferramenta';pre.className='activity-result';pre.textContent=boundedText(typeof data.result==='string'?data.result:JSON.stringify(data.result,null,2),4000);details.append(summary,pre);body.append(details);}
 if(type==='plan_updated'){
  const plan=document.createElement('ul');
  for(const step of (data.plan||[]).slice(-40)){const item=document.createElement('li');item.textContent=(step.status==='completed'?'✓ ':step.status==='inProgress'?'→ ':'○ ')+boundedText(String(step.step),500);plan.append(item);}
  body.append(plan);
 }
 if(e.timestamp){const time=document.createElement('time');const date=new Date(e.timestamp);if(!Number.isNaN(date.getTime())){time.dateTime=date.toISOString();time.textContent=date.toLocaleTimeString();body.append(time);}}
 const box=document.querySelector('.activity-scroll'),follow=box.scrollHeight-box.scrollTop-box.clientHeight<80;
 list.append(row);
 if(list.children.length>80)list.firstElementChild.remove();
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
function applyTheme(theme){
 const preference=['dark','light'].includes(theme)?theme:'system';
 const value=preference==='system'?(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light'):preference;
 document.documentElement.dataset.theme=value;$('theme').value=preference;
 try{localStorage.setItem('theme',preference);}catch{}
}
let initialTheme='system';try{initialTheme=localStorage.getItem('theme')||'system';}catch{}
applyTheme(initialTheme);$('theme').onchange=()=>applyTheme($('theme').value);
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
 $('send').disabled=busy||loading||uploads>0||!selected()||!prompt.value.trim();
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
matchMedia('(prefers-color-scheme:dark)').addEventListener('change',()=>{if($('theme').value==='system')applyTheme('system');});
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
 for(const [text,approved] of [['Permitir uma vez',true],['Negar',false]]){const button=document.createElement('button');button.textContent=text;button.onclick=async()=>{try{const answers=Object.fromEntries(fields.map(([id,input])=>[id,{answers:[input.value]}]));await post('/v1/approvals/'+data.approval_id,{approved,answers});box.remove();}catch(e){status(e.message);}};box.append(button);}
 $('messages').append(box);status('Aguardando sua aprovação');box.scrollIntoView({block:'nearest'});
}
