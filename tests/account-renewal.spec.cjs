// Seven simulated profiles. Browser/API fixtures, no live login or inference.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
(async()=>{
 const browser=await chromium.launch(process.env.PLAYWRIGHT_CHANNEL?{channel:process.env.PLAYWRIGHT_CHANNEL}:{});
 try{
 const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[];
 page.setDefaultTimeout(7000);page.on('pageerror',e=>errors.push(e.message));
 const spec={added:true,enabled:true,models:['sonnet'],projects:['sem-projeto'],permissions:{},mode:'native',integrations:[]};
 const state={settings:{services:{claude:spec},projects:[],logins:[],port:8095,tailnet_port:8095,uploads_enabled:false},inventory:{platform:'Darwin',services:[{id:'claude',name:'Claude Code',found:true}],projects:[],network:{}},authentication:{claude:true},models:{claude:{sonnet:['configured']}},integrations:{claude:[]},operations:[],credentials:{},status:{running:true,local_url:'http://127.0.0.1:8095/',shared:false}};
 let logins=0,condition='claude_authentication_required';const posts=[],turns=[];
 await page.route('http://admin.test/**',async route=>{
  const q=new URL(route.request().url()).pathname;
  if(q.startsWith('/api/')){
   let data={};
   if(q==='/api/state')data=state;
   if(q==='/api/provider-login'){logins++;data={id:'login-fixture',state:'running',output:'https://claude.ai/oauth/authorize?fixture=1'};state.operations=[data];}
   if(q==='/api/check')data={authenticated:true,models:state.models.claude};
   if(q==='/api/cancel-operation'){state.operations[0].state='cancelled';data={cancelled:true};}
   return route.fulfill({json:data});
  }
  const file=q==='/'?'index.html':q.slice(1);
  return route.fulfill({body:await fs.readFile(path.join(__dirname,file.startsWith('assets/')?'../tail_ui':'../control',file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.svg')?'image/svg+xml':'text/html'});
 });
 await page.route('http://harness.test/**',async route=>{
  const q=new URL(route.request().url()).pathname;
  if(q.startsWith('/v1/')){
   let data={};
   if(q==='/v1/projects')data={projects:['sem-projeto'],details:{}};
   if(q==='/v1/models')data={models:[{id:'sonnet',backend:'claude',efforts:['configured'],permissions:{}}],providers:{claude:true},uploads_enabled:false};
   if(q==='/v1/version')data={version:'fixture',build:'renewal'};
   if(q==='/v1/conversations')data={conversations:turns.length?[{id:'turn-1',title:'Renovação',project:'sem-projeto',state:'interrupted',last_job_id:turns.at(-1).id,execution:{model:'sonnet',backend:'claude'}}]:[]};
   if(q==='/v1/usage')data={available:false};
   if(q==='/v1/project-directories')data={roots:[],entries:[]};
   if(q==='/v1/catalog')data={agents:[],skills:[],warnings:[]};
   if(q==='/v1/jobs'&&route.request().method()==='POST'){
    posts.push(route.request().postDataJSON());const id='turn-'+posts.length;
    turns.push({id,project:'sem-projeto',state:'interrupted',request:posts.at(-1),result:{condition}});data={job_id:id};
   }
   if(q.startsWith('/v1/jobs/')&&!q.endsWith('/events'))data=turns.find(t=>t.id===q.split('/').at(-1))||{};
   if(q==='/v1/conversations/turn-1')data={title:'Renovação',turns};
   if(q.endsWith('/events'))return route.fulfill({body:'',contentType:'text/event-stream'});
   return route.fulfill({json:data});
  }
  const file=q==='/'?'index.html':q.slice(1);
  return route.fulfill({body:await fs.readFile(path.join(__dirname,file.startsWith('assets/')?'../tail_ui':'../agent_service',file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.svg')?'image/svg+xml':'text/html'});
 });
 const idle=()=>page.waitForFunction(()=>!document.body.hasAttribute('aria-busy'));
 const login=()=>page.getByRole('button',{name:'Entrar ou renovar acesso — Claude Code',exact:true});
 const close=()=>page.getByRole('button',{name:'Fechar',exact:true}).click();
 // P1: novice finds renewal directly; missing CLI has a disabled action.
 await page.goto('http://admin.test/');await idle();assert(await login().isVisible());
 await login().click();await page.getByRole('link',{name:/Abrir autorização/}).waitFor();assert.equal(logins,1);await close();
 state.inventory.services[0].found=false;await page.reload();await idle();assert(await login().isDisabled());
 state.inventory.services[0].found=true;await page.reload();await idle();console.log('P1 PASS: visible renewal and missing CLI');
 // P2: cancel and retry remain available; duplicate requests are tested by API unit tests.
 await login().click();await page.getByRole('button',{name:'Cancelar',exact:true}).click();await page.getByRole('button',{name:'Tentar novamente',exact:true}).waitFor();
 await page.getByRole('button',{name:'Tentar novamente',exact:true}).click();await page.getByRole('link',{name:/Abrir autorização/}).waitFor();await close();console.log('P2 PASS: cancel and retry');
 // P4: keyboard activation, accessible status, escape returns focus.
 await login().focus();await page.keyboard.press('Enter');await page.locator('#operation-dialog').waitFor();
 assert.equal(await page.locator('#operation-message').getAttribute('role'),'status');await page.keyboard.press('Escape');assert(await login().evaluate(e=>e===document.activeElement));console.log('P4 PASS: keyboard and status');
 // P5: mobile layout and re-opening an ongoing login after reload.
 await page.setViewportSize({width:390,height:844});await page.reload();await idle();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.getByRole('button',{name:'Operações em andamento e resultados',exact:true}).click();await page.getByRole('link',{name:/Abrir autorização/}).waitFor();await close();
 await page.screenshot({path:'/tmp/tail-renewal-admin-mobile.png',fullPage:true});console.log('P5 PASS: mobile and operation recovery');
 // P6: selectable catalog and explicit provider are preserved.
 await page.goto('http://harness.test/');await page.locator('#startup-gate').waitFor({state:'hidden'});
 await page.click('#model-trigger');assert.equal(await page.locator('#model-menu').getByText(/Maestro/).count(),0);await page.keyboard.press('Escape');
 await page.fill('#prompt','Mensagem preservada');await page.click('#send');await page.getByText('Seu acesso ao Claude precisa ser renovado.',{exact:false}).waitFor();
 assert.equal(posts[0].backend,'claude');console.log('P6 PASS: catalog and selected executor');
 // P3: history and continuing after account condition, without losing the original request.
 condition='claude_quota_exhausted';await page.fill('#prompt','Continuar depois');await page.click('#send');await page.getByText('Sua cota do Claude está temporariamente esgotada.',{exact:false}).waitFor();
 assert.equal(posts[1].parent_job_id,'turn-1');await page.reload();await page.locator('#startup-gate').waitFor({state:'hidden'});
 await page.getByText('Seu acesso ao Claude precisa ser renovado.',{exact:false}).waitFor();await page.getByText('Sua cota do Claude está temporariamente esgotada.',{exact:false}).waitFor();
 assert(await page.getByText('Mensagem preservada',{exact:true}).isVisible());console.log('P3 PASS: history and continuation');
 // P7: both conditions have neutral, actionable summaries in latest and older turns.
 const legacy=await page.evaluate(()=>['claude_authentication_failed','claude_rate_limit'].map(executionError));
 assert.match(legacy[0],/painel administrativo/);assert.match(legacy[1],/cota/);assert(!/falha|claude_authentication_failed/i.test(legacy[0]));
 assert.match(await page.locator('#activity-state').textContent(),/Aguardar renovação da cota/);
 assert(!/Falha|❌|claude_execution_failed/.test(await page.locator('#messages').innerText()));
 await page.screenshot({path:'/tmp/tail-renewal-conditions-mobile.png',fullPage:true});
 await page.setViewportSize({width:1280,height:900});await page.screenshot({path:'/tmp/tail-renewal-conditions-desktop.png',fullPage:true});console.log('P7 PASS: neutral condition summaries');
 assert.deepEqual(errors,[]);
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
