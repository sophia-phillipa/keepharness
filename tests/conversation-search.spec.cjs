const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
(async()=>{const browser=await chromium.launch();try{
 let eventRequests=0,cancelRequests=0,createdProject=null;
 const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 const conversations=Array.from({length:35},(_,i)=>({id:'c'+i,title:i===34?'Revisão Filosófica':'Conversa '+i,project:i===34?'p':'sem-projeto',state:'completed',execution:{backend:'local',model:i===34?'qwen-local':'fixture'}}));
 const turn={id:'c34',project:'p',state:'completed',request:{backend:'local',model:'qwen-local',prompt:'Texto original'},result:{answer:'Resposta recuperada'}};
 await page.route('http://search.test/**',async route=>{const url=new URL(route.request().url()),p=url.pathname;if(p.startsWith('/v1/')){
  if(p.endsWith('/cancel'))cancelRequests++;
  if(p.endsWith('/events')){eventRequests++;return route.fulfill({body:'',contentType:'text/event-stream'});}
  let data={};if(p==='/v1/projects'&&route.request().method()==='POST'){createdProject=route.request().postDataJSON();return route.fulfill({json:{project_id:'novo'}});}if(p==='/v1/projects')data=createdProject?{projects:['sem-projeto','p','novo'],details:{p:{label:'Filosofia'},novo:{label:createdProject.name}}}:{projects:['sem-projeto','p'],details:{p:{label:'Filosofia'}}};
  if(p==='/v1/project-directories'){const urlPath=url.searchParams.get('path')||'',q=url.searchParams.get('q')||'',entries=urlPath?[]:[{name:'Trabalho A',path:'Trabalho A',absolute_path:'/home/test-user/Trabalho A',type:'directory'},{name:'Trabalho B',path:'Trabalho B',absolute_path:'/home/test-user/Trabalho B',type:'directory'}];data={roots:[{id:'home',label:'Pastas locais'}],root_id:'home',path:urlPath,absolute_path:urlPath?'/home/test-user/'+urlPath:'/home/test-user',entries:entries.filter(e=>e.name.toLowerCase().includes(q.toLowerCase())),limited:false};}
  if(p==='/v1/models')data={models:[{id:'qwen-local',backend:'local',efforts:['configured']}]};
  if(p==='/v1/conversations')data={conversations};
  if(p==='/v1/conversations/c34')data={title:'Revisão Filosófica',turns:[{...turn,id:'older',state:'completed',result:{answer:'Resposta antiga completa'}},turn]};
  if(p==='/v1/jobs/c34')data=turn;
  if(p==='/v1/version')data={version:'test',build:'search-test'};
  if(p==='/v1/catalog')data={agents:[],skills:[],warnings:[]};
  return route.fulfill({json:data});}
  const file=p==='/'?'index.html':p.slice(1);return route.fulfill({body:await fs.readFile(path.join(__dirname,file.startsWith('assets/')?'../tail_ui':'../agent_service',file)),contentType:file.endsWith('.svg')?'image/svg+xml':file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
 });
 await page.emulateMedia({reducedMotion:'reduce'});
 await page.goto('http://search.test');await page.locator('#startup-gate').waitFor({state:'hidden'});
 assert.equal(await page.locator('#sidebar input[type=search]').count(),0);
 assert.equal(await page.locator('#search-conversations svg').count(),1);
 assert.equal(await page.locator('#sidebar #search-conversations').count(),0);
 assert.equal(await page.locator('#app-topbar #menu + #search-conversations').innerText(),'Buscar');
 assert.equal(await page.locator('#projects .conversation-model-icon').textContent(),'✦');
 assert.equal(await page.locator('#projects .conversation-row > button').textContent(),'✦Revisão Filosófica');
 assert.match(await page.locator('#projects .conversation-row > button').getAttribute('title'),/Qwen3.6/);
 const creationIcons=await page.locator('#add-project use,#new use,.project-new use').evaluateAll(nodes=>nodes.map(e=>e.getAttribute('href')));
 assert.equal(new Set(creationIcons).size,3);assert.equal(creationIcons.length,3);
 for(const palette of ['violet-bordeaux','porcelain','mineral-rose','amethyst','petroleum','arizona']){
  await page.evaluate(p=>TailTheme.apply(p,false),palette);
  const separator=await page.locator('#history .conversation-row').first().evaluate(e=>({shadow:getComputedStyle(e).boxShadow,border:getComputedStyle(document.documentElement).getPropertyValue('--th-border').trim()}));
  assert.notEqual(separator.shadow,'none');assert(separator.border);
 }
 await page.evaluate(()=>TailTheme.apply('violet-bordeaux',false));
 assert.equal(await page.locator('#projects .conversation-title').evaluate(e=>getComputedStyle(e).webkitLineClamp),'2');
 await page.locator('.project-group>summary>button').hover();
 const hover=await page.locator('.project-group>summary>button').evaluate(e=>({inner:getComputedStyle(e).boxShadow,outer:getComputedStyle(e.parentElement).boxShadow}));assert.equal(hover.inner,'none');assert.notEqual(hover.outer,'none');
 assert(await page.locator('#sidebar .section-label').evaluateAll(nodes=>nodes.every(e=>getComputedStyle(e).borderBottomWidth==='0px')));
 assert.equal(await page.locator('.sidebar-section-divider').count(),1);
 await page.click('#search-conversations');await page.locator('#conversation-search-dialog').waitFor({state:'visible'});
 assert(await page.locator('#conversation-search').evaluate(e=>e===document.activeElement));
 await page.fill('#conversation-search','FILOSOFICA');assert.equal(await page.locator('.conversation-search-result').count(),1);
 assert.equal(await page.locator('#history .conversation-row').count(),34,'modal search must not filter sidebar');
 await page.fill('#conversation-search','inexistente');assert.equal(await page.locator('.conversation-search-result').count(),0);
 assert.match(await page.locator('#search-results').innerText(),/Nenhuma conversa/);
 await page.click('#search-clear');assert.equal(await page.locator('.conversation-search-result').count(),35);
 await page.fill('#conversation-search','qwen-local');assert.equal(await page.locator('.conversation-search-result').count(),0,'search matches titles only');await page.fill('#conversation-search','Revisão');await page.locator('.conversation-search-result').click();
 await page.locator('#conversation-search-dialog').waitFor({state:'hidden'});
 await page.waitForFunction(()=>document.querySelector('#conversation-title').textContent==='Revisão Filosófica'&&document.querySelector('#projects button[aria-current=true]'));
 assert(await page.locator('.project-group').evaluate(e=>e.open));assert.equal(await page.locator('#active-project-badge').innerText(),'Filosofia');
 assert.match(await page.locator('.assistant .text').last().innerText(),/Resposta recuperada/);assert.equal(eventRequests,0,'completed history must render saved answers without replaying events');assert.equal(await page.locator('.assistant').count(),2);assert.match(await page.locator('.assistant .text').first().innerText(),/Resposta antiga completa/);
 const visible=await page.locator('#projects button[aria-current=true]').evaluate(e=>{const r=e.getBoundingClientRect(),s=document.querySelector('.sidebar-tree').getBoundingClientRect();return r.top>=s.top&&r.bottom<=s.bottom;});assert(visible);
 await page.screenshot({path:'/tmp/tail-sidebar-icons-light.png'});
 await page.evaluate(()=>TailTheme.apply('amethyst',false));await page.waitForTimeout(300);await page.screenshot({path:'/tmp/tail-sidebar-icons-dark.png'});
 await page.evaluate(()=>TailTheme.apply('violet-bordeaux',false));
 await page.setViewportSize({width:390,height:844});await page.keyboard.press('Control+k');await page.locator('#conversation-search-dialog').waitFor({state:'visible'});
 assert(await page.locator('#conversation-search-dialog').evaluate(e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth;}));
 await page.screenshot({path:'/tmp/tail-conversation-search-mobile.png'});await page.keyboard.press('Escape');await page.locator('#conversation-search-dialog').waitFor({state:'hidden'});
 await page.setViewportSize({width:1280,height:900});
 await page.click('#add-project');await page.locator('#project-dialog').waitFor({state:'visible'});await page.fill('#project-name','Novo projeto');await page.locator('#project-directory-list input[type=checkbox]').nth(0).check();await page.locator('#project-directory-list input[type=checkbox]').nth(1).check();await page.click('#project-create');await page.locator('#project-dialog').waitFor({state:'hidden'});assert.deepEqual(createdProject,{name:'Novo projeto',paths:['/home/test-user/Trabalho A','/home/test-user/Trabalho B']});assert.equal(await page.locator('#active-project-badge').innerText(),'Novo projeto');
 turn.state='running';turn.result={};conversations.at(-1).state='running';
 for(const create of ['#new','.project-new']){
  const jobResult=page.waitForResponse(r=>new URL(r.url()).pathname==='/v1/jobs/c34');await page.locator('#projects .conversation-row > button').click();await jobResult;
  await page.locator('#cancel').waitFor({state:'visible'});await page.waitForFunction(()=>!document.querySelector('#new').disabled);
  const count=eventRequests;await page.locator(create).first().click();await page.fill('#prompt','Novo rascunho independente');
  await page.waitForTimeout(1300);
  assert.equal(await page.locator('#cancel').isVisible(),false);assert.equal(await page.locator('#prompt').inputValue(),'Novo rascunho independente');
  assert.equal(eventRequests,count,'old stream must not reconnect after starting another conversation');assert.equal(cancelRequests,0,'detaching must not cancel the server job');
  assert.equal(await page.locator('.assistant').count(),0);await page.fill('#prompt','');
 }
 assert.deepEqual(errors,[]);console.log('PASS: search dialog, title-only/accent-insensitive search, project folder selection, and project badge, clear, empty results, sidebar navigation, keyboard and mobile.');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
