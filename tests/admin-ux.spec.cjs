const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const b=await chromium.launch();
 try {
 const p=await b.newPage({viewport:{width:1440,height:1000}}),errors=[];
 p.on('pageerror',e=>errors.push(e.message));
 const empty=id=>({added:false,enabled:false,models:[],projects:['sem-projeto'],permissions:{read:false,write:false,upload:false,shell:false,internet:false,hooks:false},mode:['local','deepseek'].includes(id)?'native':'scoped',integrations:[]});
 const state={settings:{services:Object.fromEntries(['codex','claude','local','deepseek'].map(id=>[id,empty(id)])),projects:[],logins:[],port:8095,tailnet_port:8095,uploads_enabled:false},inventory:{platform:'Linux',services:[{id:'codex',name:'Codex CLI',found:true},{id:'claude',name:'Claude Code',found:true},{id:'local',name:'Modelo local',found:true},{id:'deepseek',name:'DeepSeek',api:true,found:true}],projects:[{name:'Demo',path:'/workspace/demo'}],network:{online:true}},authentication:{},models:{},integrations:{codex:[{id:'mcp:drive',name:'Drive',kind:'mcp'}],claude:[],local:[],deepseek:[]},operations:[],credentials:{},status:{running:false,local_url:'http://127.0.0.1:8095/',remote_url:'http://demo.tailnet:8095/',shared:false}};

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
 await p.goto(process.env.ADMIN_URL||'http://127.0.0.1:8094/');
 await p.click('#add-provider');
 await p.waitForFunction(()=>document.activeElement?.closest('#provider-options'));
 assert.match(await p.evaluate(()=>document.activeElement.textContent),/Codex/);
 await p.keyboard.press('Tab');await p.keyboard.press('Enter');
 const missing=p.locator('[data-provider=claude]');
 assert(await missing.getByRole('button',{name:'Entrar',exact:true}).isDisabled());
 assert.match(await missing.innerText(),/Instale o CLI/);
 await p.click('#wizard-back');
 await p.locator('#provider-options').getByText('Modelo local',{exact:false}).click();
 let check=p.getByRole('button',{name:'Verificar modelos',exact:true});
 await check.click();
 await p.waitForFunction(()=>document.querySelector('#feedback').textContent.includes('resposta inesperada'));
 assert(!await check.isDisabled());
 assert(!await p.locator('#busy-status').isVisible());
 mode='delay';const started=new Promise(resolve=>checkStarted=resolve);
 await check.click();await started;
 assert(await check.isDisabled());assert(await p.locator('#busy-status').isVisible());
 releaseCheck();
 await p.waitForFunction(()=>document.querySelector('#feedback').textContent.includes('Servidor verificado'));
 assert(await p.locator('[data-provider=local]').getByRole('checkbox',{name:/^qwen-local/}).isVisible());
 await p.click('#wizard-back');
 await p.locator('#provider-options').getByText('DeepSeek',{exact:false}).click();
 await p.click('#wizard-cancel');await p.click('#add-provider');
 await p.waitForFunction(()=>document.activeElement?.closest('#provider-options'));
 assert(!await p.locator('#feedback').isVisible());
 await p.locator('#provider-options').getByText('Modelo local',{exact:false}).click();
 for(const width of [390,720]){
  await p.setViewportSize({width,height:844});
  assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert(await p.locator('.provider-title').evaluate(e=>e.getBoundingClientRect().width>=150));
 }
 await p.setViewportSize({width:1440,height:1000});
 await p.evaluate(()=>document.documentElement.style.zoom='2');
 assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 assert(await p.locator('.provider-title').evaluate(e=>e.getBoundingClientRect().width>=300));
 assert.deepEqual(errors,[]);
 console.log('PASS: admin discovery focus, missing CLI guidance, failed check recovery, pending action feedback, context notice reset, mobile and CSS 200% reflow');
 } finally {await b.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
