const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
(async()=>{
 const browser=await chromium.launch();
 try{
  const page=await browser.newPage({viewport:{width:1515,height:950}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>localStorage.setItem('activity-open','0'));
  const origin=process.env.HARNESS_URL||'http://panel.test';let submitted,conversationTitle='Conversa de teste';
  const serve=async route=>{
   const p=new URL(route.request().url()).pathname;
   if(p.startsWith('/v1/')){
    let data={};
    if(p==='/v1/projects')data={projects:['sem-projeto'],details:{}};
    if(p==='/v1/usage')data=new URL(route.request().url()).searchParams.get('backend')==='claude'?{available:true,provider:'claude',checked_at:1790000000,rateLimitsByLimitId:{five_hour:{limitName:'five_hour',primary:{usedPercent:34.5,windowDurationMins:300,resetsAt:1800000000}},seven_day_opus:{limitName:'seven_day_opus',primary:{usedPercent:12,windowDurationMins:10080,resetsAt:1800000000}}}}:{available:true,rateLimitsByLimitId:{codex:{primary:{usedPercent:21.4,windowDurationMins:300,resetsAt:1800000000},secondary:{usedPercent:62,windowDurationMins:10080,resetsAt:1800000000}}}};
    if(p==='/v1/models')data={models:[{id:'gpt-6-astra',backend:'codex',efforts:['low','medium','high']},{id:'local-long',name:'Modelo local com um nome muito longo para testar',backend:'local',efforts:['low']},{id:'claude-demo',name:'Claude Code',backend:'claude',efforts:['configured']}],providers:{codex:true,local:true,claude:true},uploads_enabled:true};
    if(p==='/v1/conversations')data={conversations:[{id:'conversation-fixture',title:conversationTitle,project:'sem-projeto',state:'completed',execution:{backend:'local',model:'fixture'}}]};
    if(p==='/v1/conversations/conversation-fixture'&&route.request().method()==='PATCH'){
     conversationTitle=route.request().postDataJSON().title.trim();return route.fulfill({json:{id:'conversation-fixture',title:conversationTitle}});
    }
    if(p==='/v1/version')data={version:'test',build:'composer-test'};
    if(p==='/v1/catalog')data={agents:[],skills:[],warnings:[],scope:'teste'};
    if(p==='/v1/files')data={file_id:'attachment-fixture'};
    if(p==='/v1/jobs'&&route.request().method()==='POST'){
     submitted=route.request().postDataJSON();
     return route.fulfill({status:422,json:{code:'model_not_allowed'}});
    }
    return route.fulfill({json:data});
   }
   if(process.env.HARNESS_URL)return route.continue();
   const file=p==='/'?'index.html':p.slice(1);
   return route.fulfill({body:await fs.readFile(path.join(__dirname,file.startsWith('assets/')?'../tail_ui':'../agent_service',file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.svg')?'image/svg+xml':'text/html'});
  };
  await page.route(origin+'/**',serve);
  await page.goto(origin);
  await page.locator('#startup-gate').waitFor({state:'hidden'});
  assert.equal(await page.locator('#task-section,#task-label').count(),0);
  assert.equal(await page.locator('#conversation-search').getAttribute('placeholder'),'Digite um título…');
  assert.equal(Math.round((await page.locator('#sidebar').boundingBox()).width),280,'default conversation sidebar width');
  await page.waitForFunction(()=>document.querySelector('#quota-short').textContent.includes('5 h: 78.6%')&&document.querySelector('#quota-short').textContent.includes('Semanal: 38%'));assert.equal(await page.locator('#quota-model-icon').textContent(),'🌟');assert.match(await page.locator('#quota-model-name').textContent(),/GPT-6 Astra/);await page.screenshot({path:'/tmp/tail-quota-header-desktop.png'});await page.setViewportSize({width:1280,height:950});await page.evaluate(()=>setPanelOpen(true,false));await page.waitForTimeout(100);const narrowQuota=await page.locator('#quota-toggle').boundingBox();assert(narrowQuota.x>=0&&narrowQuota.x+narrowQuota.width<=1280,'quota header stays inside viewport with both panels open');await page.screenshot({path:'/tmp/tail-quota-both-panels.png'});await page.evaluate(()=>setPanelOpen(false,false));await page.setViewportSize({width:1515,height:950});
  await page.evaluate(()=>setPanelOpen(true,false));assert.equal(Math.round((await page.locator('#activity-panel').boundingBox()).width),400,'default activity sidebar width');await page.evaluate(()=>setPanelOpen(false,false));
  const footer=await page.locator('#app-topbar #settings').boundingBox();assert(footer.height>=32&&footer.height<=40,'compact settings control keeps accessible hit area');
  await page.selectOption('#model','gpt-6-astra');await page.selectOption('#effort','medium');
  await page.selectOption('#access-mode','auto');
  if(await page.locator('#th-toast').isVisible())await page.locator('#th-toast button').click();
  const draft='Precisamos reformular a nossa caixa de prompt. Eu quero algo parecido com isto';
  await page.fill('#prompt',draft);
  assert.equal(await page.locator('#model option:checked').textContent(),'GPT-6 Astra');
  async function checkLayout(width){
   await page.setViewportSize({width,height:950});
   const layout=await page.evaluate(()=>{
    const box=id=>{const r=document.getElementById(id).getBoundingClientRect();return{x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height};};
    return {overflow:document.documentElement.scrollWidth>innerWidth,controls:['attach','access-trigger','model-trigger','effort-trigger','send'].map(box),composer:box('dropzone'),prompt:box('prompt'),radius:parseFloat(getComputedStyle(document.querySelector('#dropzone')).borderRadius)};
   });
   assert(!layout.overflow,'horizontal overflow at '+width);
   assert(layout.composer.width<=1042,'composer must have bounded reading width');
   if(width>=1366)assert(layout.composer.height<=165,'composer disproportionally tall');
   assert(layout.radius>=12,'compact composer must retain rounded proportions');
   for(const control of layout.controls){
    assert(control.width>0&&control.height>=28,'visible usable control at '+width);
    assert(control.x>=layout.composer.x&&control.right<=layout.composer.right+1,'control outside composer at '+width);
    assert(control.y>=layout.prompt.bottom-1,'controls below writing area');
   }
   for(let i=0;i<layout.controls.length;i++)for(let j=i+1;j<layout.controls.length;j++){
    const a=layout.controls[i],b=layout.controls[j];
    assert(a.right<=b.x+1||b.right<=a.x+1||a.bottom<=b.y+1||b.bottom<=a.y+1,'overlapping controls at '+width);
   }
   const send=layout.controls.at(-1);assert(Math.abs(send.width-send.height)<1,'circular send proportions');
   assert.equal(await page.locator('#prompt').inputValue(),draft);
   return layout;
  }
  for(const theme of ['violet-bordeaux','porcelain','mineral-rose','amethyst','petroleum','arizona']){
   await page.evaluate(t=>TailTheme.apply(t,false),theme);
   for(const width of [3840,1920,1366,768,390]){
    for(const access of ['ask','auto','full','read_only']){await page.selectOption('#access-mode',access);await checkLayout(width);}
   }
  }
  await page.selectOption('#access-mode','auto');
  await page.setViewportSize({width:1515,height:950});
  // Settings opens the dialog directly, without an intermediate menu.
  assert.equal(await page.locator('#account-menu,#forget-approvals').count(),0);
  assert.equal(await page.locator('#settings').innerText(),'Configurações');
  assert.equal(await page.locator('#settings').getAttribute('aria-haspopup'),'dialog');
  await page.click('#settings');
  assert(await page.locator('#settings-dialog').isVisible());
  assert.match(await page.locator('#version').innerText(),/^Release: test$/);
  assert.equal(await page.locator('[data-panel-order=conversations-left]').getAttribute('aria-pressed'),'true');await page.screenshot({path:'/tmp/tail-panel-order-desktop.png'});await page.locator('[data-panel-order=conversations-right]').click();assert.equal(await page.locator('[data-panel-order=conversations-right]').getAttribute('aria-pressed'),'true');assert.equal(await page.evaluate(()=>localStorage.getItem('panel-order')),'conversations-right');await page.evaluate(()=>setPanelOpen(true,false));const reversed1280=await page.evaluate(()=>({sidebar:document.querySelector('#sidebar').getBoundingClientRect().x,main:document.querySelector('main').getBoundingClientRect().x,activity:document.querySelector('#activity-panel').getBoundingClientRect().x}));assert(reversed1280.sidebar>reversed1280.main&&reversed1280.activity<reversed1280.main,'reversed 1280px positions panels on opposite sides of chat');await page.screenshot({path:'/tmp/tail-panel-order-reversed-1280.png'});await page.setViewportSize({width:900,height:950});await page.waitForFunction(()=>document.querySelector('#app-topbar #quota-toggle'));assert.equal(await page.locator('#app-topbar #quota-toggle').count(),1,'quota remains pinned to the visible app bar while drawer overlays chat');const reversed900=await page.evaluate(()=>({sidebar:document.querySelector('#sidebar').getBoundingClientRect().x,main:document.querySelector('main').getBoundingClientRect().x,activity:document.querySelector('#activity-panel').getBoundingClientRect().x}));assert(reversed900.sidebar>reversed900.main&&reversed900.activity<=reversed900.main,'reversed 900px keeps the drawer on the left');await page.screenshot({path:'/tmp/tail-panel-order-reversed-900.png'});await page.evaluate(()=>setPanelOpen(false,false));assert.equal(await page.locator('main #quota-toggle').count(),1,'quota returns to the conversation header when drawer closes');await page.locator('#panel-order-reset').click();assert.equal(await page.evaluate(()=>localStorage.getItem('panel-order')),'conversations-left');await page.setViewportSize({width:1515,height:950});await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/tmp/tail-panel-order-mobile.png'});await page.setViewportSize({width:1515,height:950});await page.click('#settings-close');await page.evaluate(()=>{applyPanelOrder('conversations-right');setPanelOpen(true,false)});await page.screenshot({path:'/tmp/tail-panels-reversed-live-1280.png'});await page.setViewportSize({width:900,height:950});await page.screenshot({path:'/tmp/tail-panels-reversed-live-900.png'});await page.evaluate(()=>{setPanelOpen(false,false);applyPanelOrder('conversations-left')});await page.setViewportSize({width:1515,height:950});
  const actions=page.locator('.conversation-actions').first();await actions.locator('summary').click();
  assert(await actions.getByRole('button',{name:'Renomear conversa'}).isVisible());
  assert(await actions.getByRole('button',{name:'Excluir conversa'}).isVisible());
  await actions.getByRole('button',{name:'Renomear conversa'}).click();
  await page.fill('#rename-conversation-name','Conversa renomeada');
  await page.click('#rename-conversation-save');
  await page.locator('#rename-conversation-dialog').waitFor({state:'hidden'});
  await page.waitForFunction(()=>document.querySelector('.conversation-row .conversation-title').textContent==='Conversa renomeada');
  assert.equal(conversationTitle,'Conversa renomeada');
  await page.setViewportSize({width:390,height:844});await page.click('#menu');await page.locator('#sidebar').waitFor();
  await page.click('#settings');
  const accountBox=await page.locator('#settings-dialog').boundingBox();
  assert(accountBox.x>=0&&accountBox.x+accountBox.width<=390&&accountBox.y>=0&&accountBox.y+accountBox.height<=844,'mobile settings dialog remains in viewport');
  await page.screenshot({path:'/tmp/tail-account-menu-mobile.png',fullPage:true});
  await page.keyboard.press('Escape');
  // Closing a modal preserves the drawer underneath; close it separately.
  await page.keyboard.press('Escape');
  await page.setViewportSize({width:1515,height:950});
  await page.evaluate(()=>document.documentElement.style.zoom='2');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'200% zoom overflow');
  await page.evaluate(()=>document.documentElement.style.zoom='1');
  for(const width of [3840,1366,390]){
   await page.setViewportSize({width,height:950});
   for(const theme of ['amethyst','violet-bordeaux','arizona']){
    await page.evaluate(t=>TailTheme.apply(t,false),theme);
    await page.mouse.move(0,0);await page.waitForTimeout(250);
    await page.locator('#dropzone').screenshot({path:'/tmp/tail-composer-'+width+'-'+theme+'.png'});
   }
  }
  // Large bordered permission popover, keyboard choice and full-access payload.
  for(const width of [1515,390]){
   await page.setViewportSize({width,height:950});await page.click('#access-trigger');
   await page.locator('#access-menu').waitFor({state:'visible'});
   const menu=await page.locator('#access-menu').boundingBox();
   assert(menu.x>=0&&menu.x+menu.width<=width&&menu.y>=0&&menu.y+menu.height<=950);
   assert(menu.width>Math.min(450,width-40));
   await page.locator('#access-menu [data-access=full]').click();
   assert.equal(await page.locator('#access-mode').inputValue(),'full');
   assert.equal(await page.locator('#access-label').textContent(),'Acesso total');
   await page.click('#access-trigger');
   const colors=await page.locator('#access-menu [data-access]').evaluateAll(nodes=>nodes.map(node=>({mode:node.dataset.access,color:getComputedStyle(node).color,selected:node.getAttribute('aria-selected')})));
   const full=colors.find(item=>item.mode==='full'),selected=colors.find(item=>item.selected==='true');
   assert.equal(selected.color,selected.mode==='full'?full.color:colors.find(item=>item.mode==='ask').color,'selected modes other than full stay neutral');
   if(selected.mode!=='full')assert.notEqual(full.color,selected.color,'full access alone uses the accent color');
   await page.screenshot({path:'/tmp/tail-access-menu-'+width+'.png',fullPage:true});
   await page.keyboard.press('Escape');await page.waitForFunction(()=>document.querySelector('#access-trigger').getAttribute('aria-expanded')==='false');
  }
  await page.locator('#access-trigger').focus();await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Home');await page.keyboard.press('Enter');
  assert.equal(await page.locator('#access-mode').inputValue(),'ask');
  await page.click('#access-trigger');await page.locator('#access-menu [data-access=full]').click();
  // Both custom menus reflect the current catalog and supported efforts.
  await page.click('#model-trigger');await page.locator('#model-menu [data-value=local-long]').click();
  assert.equal(await page.locator('#model-label').textContent(),'Modelo local com um nome muito longo para testar');
  await page.click('#effort-trigger');assert.equal(await page.locator('#effort-menu [role=option]').count(),1);
  await page.keyboard.press('Escape');
  await page.click('#model-trigger');await page.locator('#model-menu [data-value=gpt-6-astra]').click();
  await page.click('#effort-trigger');assert.equal(await page.locator('#effort-menu [role=option]').count(),3);
  await page.locator('#effort-menu [data-value=high]').click();assert.equal(await page.locator('#effort-label').textContent(),'Alto');
  for(const id of ['model','effort']){
   await page.click('#'+id+'-trigger');await page.locator('#'+id+'-menu').screenshot({path:'/tmp/tail-'+id+'-picker.png'});await page.keyboard.press('Escape');
  }
  // Long provider names, keyboard selection and uploads use the real controls.
  await page.selectOption('#model','local-long');assert.match(await page.locator('#quota-short').innerText(),/Sem cota do provedor/);assert.doesNotMatch(await page.locator('#quota-toggle').getAttribute('aria-label'),/cota compartilhada da conta Codex/i);
  for(const width of [1515,1000,768,390])await checkLayout(width);
  await page.setViewportSize({width:1280,height:950});await page.evaluate(()=>setPanelOpen(true,false));await checkLayout(1280);await page.evaluate(()=>setPanelOpen(false,false));
  await page.selectOption('#model','claude-demo');await page.waitForFunction(()=>document.querySelector('#quota-short').textContent.includes('5 h · Claude: 65.5% restante'));assert.equal(await page.locator('#quota-model-icon').textContent(),'✳');assert.match(await page.locator('#quota-toggle').getAttribute('aria-label'),/Última informação do Claude/);assert.match(await page.locator('#quota-toggle').getAttribute('title'),/Atualizada em/);await page.selectOption('#model','gpt-6-astra');await page.waitForFunction(()=>document.querySelector('#quota-short').textContent.includes('5 h: 78.6%'));await page.locator('#effort-trigger').focus();await page.keyboard.press('ArrowDown');await page.keyboard.press('Home');await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter');
  assert.equal(await page.locator('#effort').inputValue(),'medium');
  const chooser=page.waitForEvent('filechooser');await page.click('#attach');await (await chooser).setFiles({name:'teste.txt',mimeType:'text/plain',buffer:Buffer.from('Anexo de teste')});
  await page.waitForFunction(()=>document.querySelector('#attachments').textContent.includes('teste.txt'));
  await page.click('#send');await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('Este modelo não está habilitado'));
  assert.equal(submitted.model,'gpt-6-astra');assert.equal(submitted.effort,'medium');assert.equal(submitted.access_mode,'full');assert.deepEqual(submitted.file_ids,['attachment-fixture']);
  assert.equal(submitted.prompt,draft);assert.equal(await page.locator('#prompt').inputValue(),draft);
  await page.reload();await page.locator('#startup-gate').waitFor({state:'hidden'});
  assert.equal(await page.locator('#model-label').textContent(),'GPT-6 Astra');
  assert.equal(await page.locator('#effort-label').textContent(),'Médio');
  // A physical 4K screen with 200% display scaling has a 1920x1080 CSS viewport.
  const scaled=await browser.newPage({viewport:{width:1920,height:1080},deviceScaleFactor:2});
  await scaled.addInitScript(()=>localStorage.setItem('activity-open','0'));
  await scaled.route(origin+'/**',serve);await scaled.goto(origin);await scaled.locator('#startup-gate').waitFor({state:'hidden'});
  if(await scaled.locator('#th-toast').isVisible())await scaled.locator('#th-toast button').click();
  const scaledBox=await scaled.locator('#dropzone').boundingBox();assert(scaledBox.width<=1042&&scaledBox.height<=165);
  await scaled.screenshot({path:'/tmp/tail-4k-scaled.png',fullPage:true});await scaled.close();
  const motion=await page.evaluate(()=>{
   const prior=active,priorLast=last,el=document.createElement('article'),body=document.createElement('div');body.className='text chat-bubble';el.append(body);document.querySelector('#messages').append(el);active={body,el};
   body.rawAnswer=renderAnswer(body,'Resposta parcial');updateMotion('answer_delta');const duringAnswer=!!body.querySelector('p .response-motion');
   updateMotion('thinking');const duringThinking=!!body.querySelector('p .response-motion');
   updateMotion('completed');const afterComplete=!!body.querySelector('.response-motion');status('Concluído');const routineStatus=document.querySelector('#status').textContent;
   status('Não foi possível concluir: erro de teste');const errorStatus=document.querySelector('#status').textContent;
   active=prior;last=priorLast;el.remove();return{duringAnswer,duringThinking,afterComplete,routineStatus,errorStatus};
  });
  assert.deepEqual(motion,{duringAnswer:true,duringThinking:true,afterComplete:false,routineStatus:'',errorStatus:'Não foi possível concluir: erro de teste'});
  assert.deepEqual(errors,[]);
  console.log('PASS: composer reference layout, six themes, desktop/tablet/mobile, zoom, long names, keyboard, settings, rename menu, inline response motion and status behavior.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
