const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch();try{
 const p=await browser.newPage({viewport:{width:390,height:844}}),errors=[],writes=[];p.on('pageerror',e=>errors.push(e.message));
 const empty=id=>({added:false,enabled:false,models:[],projects:['sem-projeto'],permissions:{read:false,write:false,upload:false,shell:false,internet:false,hooks:false},mode:['local','deepseek'].includes(id)?'native':'scoped',integrations:[]});
 const state={settings:{services:Object.fromEntries(['codex','claude','local','deepseek'].map(id=>[id,empty(id)])),projects:[],logins:[],port:8095,tailnet_port:8095,uploads_enabled:false},inventory:{platform:'Linux',services:[{id:'codex',name:'Codex CLI',found:true},{id:'claude',name:'Claude Code',found:true},{id:'local',name:'Local model',found:true},{id:'deepseek',name:'DeepSeek',api:true,found:true}],projects:[{name:'Demo',path:'/workspace/demo'}],network:{online:true}},authentication:{},models:{},integrations:{codex:[{id:'mcp:drive',name:'Drive',kind:'mcp'}],claude:[],local:[],deepseek:[]},operations:[],credentials:{},status:{running:false,local_url:'http://127.0.0.1:8095/',remote_url:'http://demo.tailnet:8095/',shared:false}};


 state.settings.services.local={...empty('local'),added:true,enabled:true,models:['qwen-local']};
 state.local_profiles={'/models/Qwen.gguf':{model_file:'/models/Qwen.gguf',binary:'/usr/bin/llama-server',performance:{},allowed_roots:[]}};
 const local=state.inventory.services.find(s=>s.id==='local');local.models=['qwen-local'];local.runtimes=[{id:'qwen-local',model_file:'/models/Qwen.gguf',runtime:'llama.cpp'}];
 await p.route('**/api/**',async r=>{const url=new URL(r.request().url()),path=url.pathname.slice(5);let result={};
  if(path==='state')result=state;
  if(path==='folders'){
   const folder=url.searchParams.get('path')||'/server';
   if(folder==='/server/Private')return r.fulfill({status:400,json:{error:'No permission to open this folder.'}});
   result={path:folder,parent:folder==='/server'?'/':folder.slice(0,folder.lastIndexOf('/'))||'/',directories:folder==='/server'?[{name:'Projects',path:'/server/Projects'},{name:'Private',path:'/server/Private'}]:[],truncated:false};
  }
  if(path==='folders/create'){const data=r.request().postDataJSON();writes.push({kind:'create',...data});result={path:data.parent+'/'+data.name,created:true};}
  if(path==='local-profile'){result=r.request().postDataJSON();writes.push({kind:'profile',...result});state.local_profiles[result.model_file]=result;}
  if(path==='settings'){state.settings=r.request().postDataJSON();writes.push({kind:'settings',...state.settings});result={saved:true};}
  return r.fulfill({json:result});
 });
 await p.goto(process.env.ADMIN_URL||'http://127.0.0.1:8094/');await p.locator('[data-panel=providers]').click();
 await p.locator('[data-configured-provider=local]').getByRole('button',{name:/Edit/}).click();
 await p.locator('#inspector-tabs').getByText('Model and hardware').click();assert.match(await p.locator('#project-list').innerText(),/Tail Harness sidebar/);await p.click('#model-roots-add');
 await p.locator('#folder-picker-list').getByText('Private',{exact:true}).click();await p.waitForFunction(()=>!document.querySelector('#folder-picker-error').hidden);assert(await p.locator('#folder-picker-use').isDisabled());
 await p.click('#folder-picker-home');await p.locator('#folder-picker-list').getByText('Projects',{exact:true}).click();
 await p.locator('.folder-picker-create>summary').click();await p.fill('#folder-picker-new-name','My project');await p.click('#folder-picker-create');
 await p.waitForFunction(()=>document.querySelector('#folder-picker-breadcrumb').textContent.includes('My project'));
 await p.screenshot({path:'/tmp/tail-folder-picker-mobile.png'});
 await p.click('#folder-picker-use');assert.match(await p.locator('#model-roots-list').innerText(),/server\/Projects\/My project/);
 await p.click('#model-roots-add');await p.locator('#folder-picker-list').getByText('Projects',{exact:true}).click();await p.click('#folder-picker-use');
 assert.match(await p.locator('#model-roots-list').innerText(),/server\/Projects/);await p.click('#profile-save');await p.waitForFunction(()=>document.querySelector('#profile-save').textContent==='Save this model profile');
 assert.deepEqual(writes.find(w=>w.kind==='profile').allowed_roots,['/server/Projects/My project','/server/Projects']);
 assert.equal(writes.filter(w=>w.kind==='create').length,1);assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
 console.log('PASS: folder navigation, permission error recovery, new model directory, additive model folders, mobile');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
