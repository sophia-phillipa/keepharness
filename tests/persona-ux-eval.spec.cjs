async function openModelGroup(page,id){
 const group=page.locator('#model-menu details').filter({has:page.locator('[data-value="'+id+'"]')});
 if(await group.getAttribute('open')===null)await group.locator('summary').click();
}
// Synthetic persona walkthroughs: real Chromium interactions, deterministic API fixtures.
// No real accounts, provider inference, or human-research claims.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const out=process.env.EVAL_OUTPUT||'/tmp/tail-persona-eval';
const personas=[
 ['Ana','Iniciante',1280],['Bruno','Usuário ocasional em celular',390],
 ['Carla','Usuária experiente',1280],['Diego','Designer de interfaces',1280],
 ['Elisa','Especialista em UX e acessibilidade',1280],['Fábio','Especialista em Harness',1280],
];
const cases=[];
function test(persona,title,run){cases.push({persona,title,run});}
async function visible(p,s){assert(await p.locator(s).isVisible(),s+' deve estar visível');}
async function focus(p,s){assert(await p.locator(s).evaluate(e=>e===document.activeElement),s+' deve receber foco');}
async function fits(p,s){const b=await p.locator(s).boundingBox(),v=p.viewportSize();assert(b&&b.x>=-1&&b.y>=-1&&b.x+b.width<=v.width+1&&b.y+b.height<=v.height+1,s+' deve caber na tela');}
async function settings(p){await p.click('#settings');await visible(p,'#settings-dialog');}
async function sidebar(p){if(!await p.locator('#sidebar').isVisible())await p.click('#menu');}
async function named(p,s){assert(await p.locator(s).evaluate(e=>!!(e.getAttribute('aria-label')||(e.getAttribute('aria-labelledby')||'').split(' ').map(id=>document.getElementById(id)?.textContent||'').join('').trim())),s+' precisa de nome acessível');}
// Ana: discoverability, basic composition, recoverable mistakes.
test(0,'Identificar onde escrever',async p=>{await visible(p,'#welcome');await visible(p,'#prompt');assert.equal(await p.locator('#prompt').getAttribute('aria-label'),'Mensagem');});
test(0,'Descobrir como quebrar linha sem enviar',async p=>{const prompt=p.locator('#prompt');assert(await prompt.evaluate(e=>e.matches(':placeholder-shown')));assert.match(await prompt.getAttribute('placeholder'),/Enter envia.*Shift\+Enter quebra linha/);assert.match(await prompt.getAttribute('aria-describedby'),/composer-help/);assert.match(await p.locator('#composer-help').textContent(),/Enter envia.*Shift\+Enter quebra linha/);});
test(0,'Mensagem vazia não pode ser enviada',async p=>{await p.fill('#prompt','   ');assert(await p.locator('#send').isDisabled());});
test(0,'Quebrar linha com Shift+Enter',async(p,s)=>{await p.fill('#prompt','Olá');await p.press('#prompt','Shift+Enter');assert.equal(await p.locator('#prompt').inputValue(),'Olá\n');assert.equal(s.posts.length,0);});
test(0,'Enviar e ler a resposta',async(p,s)=>{await p.fill('#prompt','Olá');await p.click('#send');await p.getByText('Resposta de avaliação.',{exact:true}).waitFor();assert.equal(s.posts.length,1);});
test(0,'Cancelar descarte do rascunho',async p=>{await p.fill('#prompt','Meu rascunho');p.once('dialog',d=>d.dismiss());await p.click('#new');assert.equal(await p.locator('#prompt').inputValue(),'Meu rascunho');});
test(0,'Encontrar configurações e voltar',async p=>{await settings(p);await p.click('#settings-close');await focus(p,'#settings');});
test(0,'Buscar conversa sem resultado',async p=>{await p.click('#search-conversations');await p.fill('#conversation-search','inexistente');assert.match(await p.locator('#search-results').innerText(),/Nenhuma conversa/);});
test(0,'Entender a falta de modelos',async(p,s)=>{s.empty=true;await p.reload();await p.locator('#startup-gate').waitFor({state:'hidden'});await visible(p,'#model-availability');assert(await p.locator('#send').isDisabled());await visible(p,'#models-retry');});
test(0,'Receber erro sem perder mensagem',async(p,s)=>{s.reject=true;await p.fill('#prompt','Mensagem importante');await p.click('#send');await p.waitForFunction(()=>document.querySelector('#status').textContent.includes('Não'));assert.equal(await p.locator('#prompt').inputValue(),'Mensagem importante');});
// Bruno: narrow viewport and touch navigation.
test(1,'Ler e escrever no celular sem rolagem lateral',async p=>{assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await fits(p,'#prompt');await fits(p,'#send');assert(await p.locator('#welcome h1').evaluate(e=>{const b=e.getBoundingClientRect(),toast=document.querySelector('#th-toast');if(!toast||toast.hidden)return true;const t=toast.getBoundingClientRect();return t.bottom<=b.top||t.top>=b.bottom||t.right<=b.left||t.left>=b.right;}),'Boas-vindas não devem ser cobertas por uma notificação de prontidão');});
test(1,'Abrir e fechar conversas pelo botão',async p=>{await sidebar(p);await visible(p,'#sidebar');await p.click('#menu');assert(!await p.locator('#sidebar').isVisible());});
test(1,'Fechar conversas com Escape',async p=>{await sidebar(p);await p.keyboard.press('Escape');assert(!await p.locator('#sidebar').isVisible());await focus(p,'#menu');});
test(1,'Selecionar modelo no celular',async p=>{await p.click('#model-trigger');await fits(p,'#model-menu');await openModelGroup(p,'deepseek-flash');await p.locator('#model-menu [data-value="deepseek-flash"]').click();assert.equal(await p.locator('#model').inputValue(),'deepseek-flash');});
test(1,'Consultar modos de acesso no celular',async p=>{await p.click('#access-trigger');await fits(p,'#access-menu');await p.locator('[data-access="read_only"]').click();assert.equal(await p.locator('#access-mode').inputValue(),'read_only');});
test(1,'Abrir ajustes sem cortar a janela',async p=>{await settings(p);await fits(p,'#settings-dialog');});
test(1,'Buscar e limpar um título',async p=>{await p.click('#search-conversations');await p.fill('#conversation-search','revisao');assert.equal(await p.locator('.conversation-search-result').count(),1);await p.click('#search-clear');assert.equal(await p.locator('#conversation-search').inputValue(),'');await focus(p,'#conversation-search');});
test(1,'Fechar painel de arquivos com Escape',async p=>{await p.click('#panel-toggle');await visible(p,'#activity-panel');await p.keyboard.press('Escape');assert(!await p.locator('#activity-panel').isVisible());await focus(p,'#panel-toggle');});
test(1,'Adicionar projeto sem cortar o formulário',async p=>{await sidebar(p);await p.click('#add-project');await fits(p,'#project-dialog');await visible(p,'#project-create');});
test(1,'Rascunho sobrevive a recarregamento',async p=>{await p.fill('#prompt','No celular');await p.reload();await p.locator('#startup-gate').waitFor({state:'hidden'});assert.equal(await p.locator('#prompt').inputValue(),'No celular');});
// Carla: efficient use and persistence.
test(2,'Atalho para escrever',async p=>{await p.keyboard.press('Control+/');await focus(p,'#prompt');});
test(2,'Atalho para buscar',async p=>{await p.keyboard.press('Control+k');await focus(p,'#conversation-search');});
test(2,'Busca ignora acentos e maiúsculas',async p=>{await p.click('#search-conversations');await p.fill('#conversation-search','REVISAO');assert.match(await p.locator('.conversation-search-result').innerText(),/Revisão/);});
test(2,'Trocar modelo preserva rascunho e imagem',async(p,s)=>{s.allowUploads=true;await p.reload();await p.locator('#startup-gate').waitFor({state:'hidden'});await p.locator('#file').setInputFiles(process.env.EVAL_IMAGE||{name:'fixture.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nEIAAAAASUVORK5CYII=','base64')});await p.locator('.attachment-preview').waitFor();await p.waitForFunction(()=>document.querySelector('.attachment-preview').naturalWidth>0);await p.fill('#prompt','Continuar trabalho');await p.click('#model-trigger');await openModelGroup(p,'deepseek-flash');await p.locator('#model-menu [data-value="deepseek-flash"]').click();assert.equal(await p.locator('#prompt').inputValue(),'Continuar trabalho');assert.equal(await p.locator('.attachment').count(),1);assert.equal(s.uploads,1);await p.click('#send');await p.getByText('Resposta de avaliação.',{exact:true}).waitFor();assert.deepEqual(s.posts[0].file_ids,['eval-image']);});
test(2,'Escolher esforço pelo menu',async p=>{await p.click('#effort-trigger');await p.locator('#effort-menu [data-value="high"]').click();assert.equal(await p.locator('#effort').inputValue(),'high');});
test(2,'Persistir tamanho de leitura',async p=>{await settings(p);await p.selectOption('#reading-size','19');await p.reload();await p.locator('#startup-gate').waitFor({state:'hidden'});assert.equal(await p.locator('#reading-size').inputValue(),'19');});
test(2,'Manter rascunho ao trocar projeto',async p=>{await p.fill('#prompt','Manter');await p.locator('#projects summary button').first().click();assert.equal(await p.locator('#prompt').inputValue(),'Manter');});
test(2,'Confirmar nova conversa',async p=>{await p.fill('#prompt','Descartar');p.once('dialog',d=>d.accept());await p.click('#new');assert.equal(await p.locator('#prompt').inputValue(),'');});
test(2,'Seleção de modelo sobrevive a reload',async p=>{await p.click('#model-trigger');await openModelGroup(p,'deepseek-flash');await p.locator('#model-menu [data-value="deepseek-flash"]').click();await p.reload();await p.locator('#startup-gate').waitFor({state:'hidden'});assert.equal(await p.locator('#model').inputValue(),'deepseek-flash');});
test(2,'Não duplicar envio com clique repetido',async(p,s)=>{s.delay=200;await p.fill('#prompt','Apenas uma vez');await p.locator('#send').evaluate(e=>{e.click();e.click();});await p.getByText('Resposta de avaliação.',{exact:true}).waitFor();assert.equal(s.posts.length,1);});
// Diego: visual hierarchy, layout and consistency.
test(3,'Interface cabe em 320 px',async p=>{await p.setViewportSize({width:320,height:740});assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await fits(p,'#send');});
test(3,'Menus respeitam a viewport estreita',async p=>{if(await p.locator('#activity-panel').isVisible())await p.click('#panel-toggle');await p.setViewportSize({width:320,height:740});await p.click('#model-trigger');await fits(p,'#model-menu');});
test(3,'Todos os modelos têm ícone no seletor',async p=>{await p.click('#model-trigger');for(const o of await p.locator('#model-menu [role=option]').all())assert.equal(await o.locator('.model-logo-icon').count(),1);});
test(3,'Resultados de busca têm identidade do modelo',async p=>{await p.click('#search-conversations');assert.equal(await p.locator('.conversation-search-result .model-logo-icon').count(),2,'Cada modelo no resultado deve ter seu ícone');});
test(3,'Texto longo não ultrapassa o compositor',async p=>{await p.fill('#prompt','Mensagem muito longa '.repeat(200));await fits(p,'#prompt');await fits(p,'#send');assert(await p.locator('#prompt').evaluate(e=>e.clientHeight<=180));});
test(3,'Inverter a posição dos painéis',async p=>{await settings(p);await p.click('[data-panel-order="conversations-right"]');await p.click('#settings-close');if(!await p.locator('#activity-panel').isVisible())await p.click('#panel-toggle');assert((await p.locator('#activity-panel').boundingBox()).x<(await p.locator('#sidebar').boundingBox()).x);});
test(3,'Restaurar a posição padrão',async p=>{await settings(p);await p.click('[data-panel-order="conversations-right"]');await p.click('#panel-order-reset');assert.equal(await p.locator('[data-panel-order="conversations-left"]').getAttribute('aria-pressed'),'true');});
test(3,'Cota de modelo local é honesta',async p=>{assert.match(await p.locator('#quota-short').innerText(),/Sem cota do provedor/);});
test(3,'Menu fecha quando a janela muda',async p=>{await p.click('#model-trigger');await p.setViewportSize({width:900,height:800});await p.locator('#model-menu').waitFor({state:'hidden'});});
test(3,'Redução de movimento desativa rolagem animada',async p=>{await p.emulateMedia({reducedMotion:'reduce'});assert.equal(await p.locator('#messages').evaluate(e=>getComputedStyle(e).scrollBehavior),'auto');});
// Elisa: names, keyboard navigation, focus and semantics.
test(4,'Projeto tem nome acessível',async p=>{await p.click('#add-project');await named(p,'#project-dialog');});
test(4,'Conexão tem nome acessível',async p=>{await settings(p);await p.click('#setup');await named(p,'#setup-dialog');});
test(4,'Autenticação tem nome acessível',async p=>{await named(p,'#vpn-login');});
test(4,'Escape no seletor devolve foco',async p=>{await p.focus('#model-trigger');await p.keyboard.press('ArrowDown');await p.keyboard.press('Escape');assert(!await p.locator('#model-menu').isVisible());await focus(p,'#model-trigger');});
test(4,'Home e End navegam opções',async p=>{await p.click('#access-trigger');await p.keyboard.press('End');await focus(p,'[data-access="read_only"]');await p.keyboard.press('Home');await focus(p,'[data-access="ask"]');});
test(4,'Teclado escolhe modo de acesso',async p=>{await p.focus('#access-trigger');await p.keyboard.press('ArrowDown');await p.keyboard.press('End');await p.keyboard.press('Enter');assert.equal(await p.locator('#access-mode').inputValue(),'read_only');await focus(p,'#access-trigger');});
test(4,'Modal mantém navegação por Tab dentro dele',async p=>{await settings(p);for(let i=0;i<18;i++){await p.keyboard.press('Tab');assert(await p.evaluate(()=>document.activeElement===document.body||!!document.activeElement.closest('#settings-dialog')));}});
test(4,'Busca com Escape retorna ao acionador',async p=>{await p.click('#search-conversations');await p.keyboard.press('Escape');await focus(p,'#search-conversations');});
test(4,'Status e mensagem têm semântica acessível',async p=>{assert.equal(await p.locator('#status').getAttribute('role'),'status');assert.equal(await p.locator('#messages').getAttribute('aria-label'),'Mensagens da conversa');});
test(4,'Campo técnico oculto não entra no Tab',async p=>{assert.equal(await p.locator('#project').getAttribute('tabindex'),'-1','Select oculto do projeto não deve receber foco pelo Tab');});
// Fábio: frontend execution contracts against controlled failures, not provider certification.
test(5,'Enviar modelo e backend efetivamente escolhidos',async(p,s)=>{await p.click('#model-trigger');await openModelGroup(p,'deepseek-flash');await p.locator('#model-menu [data-value="deepseek-flash"]').click();await p.fill('#prompt','Executar');await p.click('#send');await p.getByText('Resposta de avaliação.',{exact:true}).waitFor();assert.equal(s.posts[0].backend,'deepseek');assert.equal(s.posts[0].model,'deepseek-flash');});
test(5,'Modo somente leitura segue no pedido',async(p,s)=>{await p.click('#access-trigger');await p.locator('[data-access="read_only"]').click();await p.fill('#prompt','Consultar');await p.click('#send');await p.getByText('Resposta de avaliação.',{exact:true}).waitFor();assert.equal(s.posts[0].access_mode,'read_only');});
test(5,'Reenvio após falha mantém idempotência',async(p,s)=>{s.abort=true;await p.fill('#prompt','Pedido estável');await p.click('#send');await p.waitForFunction(()=>document.querySelector('#status').textContent.includes('Não foi possível executar'));await p.click('#send');await p.waitForFunction(()=>document.querySelector('#status').textContent.includes('Não foi possível executar'));assert.equal(s.keys.length,2);assert(s.keys[0]);assert.equal(s.keys[0],s.keys[1]);});
test(5,'Pedido alterado recebe nova idempotência',async(p,s)=>{s.abort=true;await p.fill('#prompt','Primeiro');await p.click('#send');await p.waitForFunction(()=>document.querySelector('#status').textContent.includes('Não foi possível executar'));await p.fill('#prompt','Segundo');await p.click('#send');await p.waitForFunction(()=>document.querySelector('#status').textContent.includes('Não foi possível executar'));assert.notEqual(s.keys[0],s.keys[1]);});
test(5,'Cancelamento não se duplica',async(p,s)=>{s.running=true;await p.fill('#prompt','Longa execução');await p.click('#send');await p.waitForFunction(()=>!document.querySelector('#cancel').disabled);await p.locator('#cancel').evaluate(e=>{e.click();e.click();});await p.locator('#cancel').waitFor({state:'hidden',timeout:8000});assert.equal(s.cancels,1);});
test(5,'Perda de conexão bloqueia e preserva rascunho',async(p,s)=>{await p.fill('#prompt','Preservar');s.offline=true;await p.evaluate(()=>probeReadiness());await visible(p,'#startup-gate');assert(await p.locator('main').evaluate(e=>e.inert));assert.equal(await p.locator('#prompt').inputValue(),'Preservar');});
test(5,'Recuperação libera a interface',async(p,s)=>{s.offline=true;await p.evaluate(()=>probeReadiness());await visible(p,'#startup-gate');s.offline=false;await p.evaluate(()=>initialize());await p.locator('#startup-gate').waitFor({state:'hidden'});assert(!await p.locator('main').evaluate(e=>e.inert));});
test(5,'Uploads desativados não fazem requisições',async(p,s)=>{assert(await p.locator('#attach').isDisabled());await p.locator('#file').setInputFiles({name:'fixture.txt',mimeType:'text/plain',buffer:Buffer.from('fixture')});assert.equal(s.uploads,0);assert.match(await p.locator('#status').innerText(),/desativados/);});
test(5,'Trocar provedores e agentes na mesma sessão, continuar e recarregar',async(p,s)=>{
 s.extended=true;await p.reload();await p.locator('#startup-gate').waitFor({state:'hidden'});
 const choices=[['qwen-local','local'],['deepseek-flash','deepseek'],['gpt-6-astra','codex'],['gpt-5.6-terra','codex'],['claude-sonnet-4-6','claude'],['gemini-test','gemini'],['maestro-test','maestro'],['qwen-local','local']];
 for(const [i,[model,backend]] of choices.entries()){
  await p.click('#model-trigger');await openModelGroup(p,model);await p.locator('#model-menu [data-value="'+model+'"]').click();await p.fill('#prompt','Turno '+(i+1));await p.click('#send');
  await p.waitForFunction(n=>document.querySelectorAll('.message.assistant').length===n&&!document.querySelector('#model').disabled,i+1);
  assert.equal(s.posts[i].backend,backend);assert.equal(s.posts[i].model,model);assert.equal(s.posts[i].parent_job_id,i?'eval-job'+(i>1?'-'+i:''):undefined);
  assert.equal(await p.evaluate(()=>conversation),'eval-job');
 }
 await p.reload();await p.locator('#startup-gate').waitFor({state:'hidden'});assert.equal(await p.evaluate(()=>parent),'eval-job-8');assert.equal(await p.locator('.message.assistant').count(),8);
});
test(5,'Limite de consultas não simula desconexão',async(p,s)=>{s.limited=true;await p.evaluate(()=>probeReadiness());assert(!await p.locator('#startup-gate').isVisible());assert.match(await p.locator('#status').innerText(),/Muitas consultas/);});
(async()=>{
 await fs.mkdir(out,{recursive:true});const browser=await chromium.launch(),results=[];
 try{for(const [i,c] of cases.entries()){
  if(process.env.EVAL_CASE&&process.env.EVAL_CASE!==`P${c.persona+1}-R${String(i%10+1).padStart(2,'0')}`)continue;
  const [name,level,width]=personas[c.persona],context=await browser.newContext({viewport:{width,height:900}}),p=await context.newPage(),errors=[];
  p.setDefaultTimeout(3500);p.on('pageerror',e=>errors.push(e.message));
  const s={turns:[],posts:[],keys:[],cancels:0,uploads:0,empty:false,reject:false,delay:0};
  const models=[{id:'qwen-local',name:'Qwen local',backend:'local',efforts:['low','high']},{id:'deepseek-flash',name:'DeepSeek',backend:'deepseek',efforts:['low','high']}];
  const conversations=[{id:'old1',title:'Revisão de interface',project:'demo',state:'completed',execution:{model:'qwen-local',backend:'local'}},{id:'old2',title:'Planejamento',project:'sem-projeto',state:'completed',execution:{model:'deepseek-flash',backend:'deepseek'}}];
  await p.route('http://eval.test/**',async r=>{
   const u=new URL(r.request().url()),q=u.pathname;let data={};
   if(q.startsWith('/v1/')){
    if(s.offline)return r.abort('failed');
    if(s.limited)return r.fulfill({status:429,headers:{'Retry-After':'10'},json:{code:'rate_limit'}});
    if(q==='/v1/files'){s.uploads++;s.image=r.request().postDataBuffer();s.imageType=r.request().headers()['content-type']||'image/png';return r.fulfill({json:{file_id:'eval-image',preview_url:'/eval-image'}});}
    if(q.endsWith('/cancel')){s.cancels++;await new Promise(resolve=>setTimeout(resolve,200));s.running=false;}
    if(q==='/v1/projects')data={projects:['sem-projeto','demo'],details:{demo:{label:'Projeto demonstração'}}};
    if(q==='/v1/models')data={models:s.empty?[]:[...models,...(s.extended?[{id:'gpt-6-astra',backend:'codex',efforts:['low']},{id:'gpt-5.6-terra',backend:'codex',efforts:['medium']},{id:'claude-sonnet-4-6',backend:'claude',efforts:['low']},{id:'gemini-test',backend:'gemini',efforts:['low']},{id:'maestro-test',backend:'maestro',efforts:['auto']}]:[])].map(m=>({...m,permissions:{upload:!!s.allowUploads}})),providers:{local:true,deepseek:true,codex:true,claude:true,gemini:true,maestro:true},uploads_enabled:!!s.allowUploads};
    if(q==='/v1/conversations')data={conversations};
    if(q==='/v1/usage')data={available:false};
    if(q==='/v1/version')data={version:'fixture',build:'persona-eval'};
    if(q==='/v1/catalog')data={agents:[],skills:[],warnings:[],scope:'Projeto de avaliação'};
    if(q==='/v1/project-directories')data={roots:[{id:'home',label:'Pastas locais'}],root_id:'home',path:'',absolute_path:'/workspace',entries:[],limited:false};
    if(q==='/v1/jobs'&&r.request().method()==='POST'){
     s.posts.push(r.request().postDataJSON());s.keys.push(r.request().headers()['idempotency-key']);if(s.abort)return r.abort('failed');if(s.delay)await new Promise(resolve=>setTimeout(resolve,s.delay));
     if(s.reject)return r.fulfill({status:422,json:{code:'model_not_allowed'}});const id='eval-job'+(s.posts.length>1?'-'+s.posts.length:'');s.turns.push({id,project:'sem-projeto',state:'completed',request:s.posts.at(-1),result:{answer:'Resposta de avaliação.'}});data={job_id:id};
    }
    if(q.startsWith('/v1/jobs/')&&!q.endsWith('/events')&&!q.endsWith('/cancel')){data={...s.turns.find(t=>t.id===q.split('/').at(-1)),state:s.running?'running':'completed'};if(s.running)data.result=null;}
    if(q==='/v1/conversations/eval-job')data={title:'Sessão de avaliação',turns:s.turns};
    if(q.endsWith('/events'))return r.fulfill({body:'',contentType:'text/event-stream'});
    return r.fulfill({json:data});
   }
   if(q==='/eval-image')return r.fulfill({body:s.image,contentType:s.imageType});
   const file=q==='/'?'index.html':q.slice(1);
   try{return await r.fulfill({body:await fs.readFile(path.join(__dirname,file.startsWith('assets/')?'../tail_ui':'../agent_service',file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.svg')?'image/svg+xml':'text/html'});}catch{return r.fulfill({status:404,body:''});}
  });
  const result={id:`P${c.persona+1}-R${String(i%10+1).padStart(2,'0')}`,persona:name,level,task:c.title};
  try{await p.goto('http://eval.test');await p.locator('#startup-gate').waitFor({state:'hidden'});await c.run(p,s);assert.deepEqual(errors,[]);result.status='pass';}
  catch(e){result.status='fail';result.error=e.message;await p.screenshot({path:path.join(out,result.id+'.png'),fullPage:true});}
  if(i%10===0)await p.screenshot({path:path.join(out,`persona-${c.persona+1}.png`),fullPage:true});
  results.push(result);console.log(result.id,result.status,c.title,result.error||'');await context.close();
 }
 }finally{await browser.close();await fs.writeFile(path.join(out,'results.json'),JSON.stringify(results,null,2)+'\n');}
 const failed=results.filter(r=>r.status==='fail');console.log(`${results.length-failed.length}/${results.length} passed`);if(failed.length)process.exitCode=1;
})().catch(e=>{console.error(e);process.exitCode=1;});
