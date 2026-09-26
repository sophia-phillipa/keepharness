const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const b=await chromium.launch();
 try {
 const p=await b.newPage({viewport:{width:1440,height:1000}}),errors=[];
 p.on('pageerror',e=>errors.push(e.message));
 const fs=require('node:fs/promises'),path=require('node:path');
 await p.route('http://admin.test/**',async route=>{const url=new URL(route.request().url());if(url.pathname.startsWith('/api/'))return route.fallback();const file=url.pathname==='/'?'index.html':url.pathname.slice(1);return route.fulfill({body:await fs.readFile(path.join(__dirname,file.startsWith('assets/')?'../tail_ui':'../control',file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.svg')?'image/svg+xml':'text/html'});});
 const empty=id=>({added:false,enabled:false,models:[],projects:['sem-projeto'],permissions:{read:false,write:false,upload:false,shell:false,internet:false,hooks:false},mode:['local','deepseek'].includes(id)?'native':'scoped',integrations:[]});
 const state={settings:{services:Object.fromEntries(['codex','claude','local','deepseek'].map(id=>[id,empty(id)])),projects:[],logins:[],port:8095,tailnet_port:8095,uploads_enabled:false},inventory:{platform:'Linux',services:[{id:'codex',name:'Codex CLI',found:true},{id:'claude',name:'Claude Code',found:true},{id:'local',name:'Local model',found:true},{id:'deepseek',name:'DeepSeek',api:true,found:true}],projects:[{name:'Demo',path:'/workspace/demo'}],network:{online:true}},authentication:{},models:{codex:{'gpt-6-astra':['low','high'],'gpt-5.6-luna':['low'],'future-model':['low']},deepseek:{'deepseek-flash':['configured'],'deepseek-v4-pro':['configured']}},integrations:{codex:[{id:'mcp:drive',name:'Drive',kind:'mcp'}],claude:[],local:[],deepseek:[]},operations:[],credentials:{},status:{running:false,local_url:'http://127.0.0.1:8095/',remote_url:'http://demo.tailnet:8095/',shared:false}};

 state.inventory.services.find(s=>s.id==='claude').found=false;
 let mode='html',releaseCheck,checkStarted;
 await p.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname.slice(5);
  let result={};
  if(path==='state')result=state;
  if(path==='scan')result=state.inventory;
  if(path==='check'){
   if(mode==='html')return route.fulfill({status:502,contentType:'text/html',body:'<h1>Bad Gateway</h1>'});
   if(mode==='delay')await new Promise(resolve=>{releaseCheck=resolve;checkStarted?.();});
   result={authenticated:true,models:{'qwen-local':['low']}};
  }
  await route.fulfill({json:result});
 });
 await p.goto(process.env.ADMIN_URL||'http://admin.test/');await p.locator('[data-panel=providers]').click();
 assert.equal(await p.locator('#start').count(),0);
 assert.equal(await p.locator('#stop').count(),0);
 await p.click('#add-provider');
 await p.locator('#provider-options [data-provider="codex"]').click();
 assert.match(await p.locator('.provider-card').innerText(),/complex tasks/);
 assert.match(await p.locator('.provider-card').innerText(),/speed and efficiency/);
 assert.match(await p.locator('.provider-card').innerText(),/does not provide a description/);
 assert.equal(await p.locator('.provider-card .model-list strong use[href$="#brand-openai"]').count(),3);
 for(const width of [390,1440]){
  await p.setViewportSize({width,height:1000});
  assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await p.screenshot({path:'/tmp/tail-admin-codex-'+width+'.png',fullPage:true});
 }
 assert(!/Maestro/.test(await p.locator('#provider-dialog').innerText()));
 assert(await p.getByRole('checkbox',{name:/Preferred provider/}).isVisible());
 await p.click('#wizard-back');
 assert.equal(await p.locator('#provider-options use[href$="#brand-openai"]').count(),1);
 assert.equal(await p.locator('#provider-options use[href$="#brand-claude"]').count(),1);
 assert(await p.locator('#wizard-content-lock').evaluate(e=>parseFloat(getComputedStyle(e).paddingLeft)>=16));
 await p.waitForFunction(()=>document.activeElement?.closest('#provider-options'));
 assert.match(await p.evaluate(()=>document.activeElement.textContent),/Codex/);
 await p.keyboard.press('Tab');await p.keyboard.press('Enter');
 const missing=p.locator('.provider-card[data-provider=claude]');
 assert(await missing.getByRole('button',{name:'Log in or renew access — Claude Code',exact:true}).isDisabled());
 assert.match(await missing.innerText(),/Install this provider's CLI/);
 await p.click('#wizard-back');
 await p.locator('#provider-options').locator('[data-provider=local]').click();
 assert(await p.locator('#profile-temp').getAttribute('aria-describedby'));
 assert.match(await p.locator('#profile-temp-help').textContent(),/variability/);
 let check=p.getByRole('button',{name:'Check models',exact:true});
 await check.click();
 await p.waitForFunction(()=>document.querySelector('#feedback').textContent.includes('unexpected response'));
 assert(!await check.isDisabled());
 assert(!await p.locator('#busy-status').isVisible());
 mode='delay';const started=new Promise(resolve=>checkStarted=resolve);
 await check.click();await started;
 assert(await check.isDisabled());assert(await p.locator('#busy-status').isVisible());
 releaseCheck();
 await p.waitForFunction(()=>document.querySelector('#feedback').textContent.includes('Server verified'));
 assert(await p.locator('.provider-card[data-provider=local]').getByRole('checkbox',{name:/^qwen-local/}).isVisible());
 await p.getByRole('checkbox',{name:/^qwen-local/}).check();
 await p.click('#wizard-next');
 assert(await p.locator('#review').isVisible());
 await p.click('#wizard-back');assert(await p.locator('#providers').isVisible());
 assert(!await p.locator('#projects').isVisible(),'Projects are managed in the conversation sidebar, not a wizard step');
 assert(await p.getByRole('checkbox',{name:/^qwen-local/}).isChecked());
 p.once('dialog',d=>d.accept());
 await p.click('#wizard-back');
 const deepseekOption=p.locator('#provider-options [data-provider="deepseek"]');
 await p.locator('#provider-options').getByText('DeepSeek',{exact:false}).click();
 assert.equal(await p.locator('.provider-card[data-provider="deepseek"] .provider-logo').innerText(),'🐋','DeepSeek settings use the same whale mark');
 assert(await p.getByRole('checkbox',{name:/DeepSeek V4\.1 Flash/}).isVisible());assert(await p.getByRole('checkbox',{name:/DeepSeek V4 Pro/}).isVisible());
 await p.screenshot({path:'/tmp/tail-admin-deepseek.png'});
 await p.click('#wizard-cancel');await p.click('#add-provider');
 await p.waitForFunction(()=>document.activeElement?.closest('#provider-options'));
 assert(!await p.locator('#feedback').isVisible());
 await p.locator('#provider-options').locator('[data-provider=local]').click();
 for(const width of [390,720]){
  await p.setViewportSize({width,height:844});
  assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert(await p.locator('.provider-title').evaluate(e=>e.getBoundingClientRect().width>=150));
 }
 await p.setViewportSize({width:1440,height:1000});
 await p.evaluate(()=>document.documentElement.style.zoom='2');
 assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 assert(await p.locator('.provider-title').evaluate(e=>e.getBoundingClientRect().width>=300));
 await p.evaluate(()=>document.documentElement.style.zoom='1');
 state.status.running=true;
 await p.reload();
 await p.getByText('● Harness active',{exact:true}).waitFor();
 assert.equal(await p.locator('#configuration-lock').count(),0);
 assert(await p.locator('#overview .panel-description').isVisible());
 assert(!await p.locator('#add-provider').isDisabled(),'running harness must allow provider changes');
 await p.click('#add-provider');
 assert(!await p.locator('#wizard-content-lock').isDisabled());
 await p.locator('#provider-options').locator('[data-provider=local]').click();
 assert(!await p.locator('#wizard-next').isDisabled());
 assert.equal(await p.locator('#configuration-lock').count(),0);
 await p.screenshot({path:'/tmp/tail-admin-configuration-notice.png'});
 assert.deepEqual(errors,[]);
 console.log('PASS: admin discovery focus, missing CLI guidance, failed check recovery, pending action feedback, context notice reset, mobile and CSS 200% reflow');
 } finally {await b.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
