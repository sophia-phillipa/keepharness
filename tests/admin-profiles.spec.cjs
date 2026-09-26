const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch();try{
 const p=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],writes=[];
 p.on('pageerror',e=>errors.push(e.message));let accept=false,dialogs=0;
 p.on('dialog',async d=>{dialogs++;await (accept?d.accept():d.dismiss());});
 const empty=id=>({added:false,enabled:false,models:[],projects:['sem-projeto'],permissions:{read:false,write:false,upload:false,shell:false,internet:false,hooks:false},mode:['local','deepseek'].includes(id)?'native':'scoped',integrations:[]});
 const state={settings:{services:Object.fromEntries(['codex','claude','local','deepseek'].map(id=>[id,empty(id)])),projects:[],logins:[],port:8095,tailnet_port:8095,uploads_enabled:false},inventory:{platform:'Linux',services:[{id:'codex',name:'Codex CLI',found:true},{id:'claude',name:'Claude Code',found:true},{id:'local',name:'Local model',found:true},{id:'deepseek',name:'DeepSeek',api:true,found:true}],projects:[{name:'Demo',path:'/workspace/demo'}],network:{online:true}},authentication:{},models:{},integrations:{codex:[{id:'mcp:drive',name:'Drive',kind:'mcp'}],claude:[],local:[],deepseek:[]},operations:[],credentials:{},status:{running:false,local_url:'http://127.0.0.1:8095/',remote_url:'http://demo.tailnet:8095/',shared:false}};

 const a='/models/Qwen-family-Q3_K_M.gguf',b='/models/Qwen-family-Q4_K_M.gguf';
 state.settings.services.local={...empty('local'),added:true,enabled:true,models:['qwen-local']};
 state.local_profiles={[a]:{model_file:a,binary:'/usr/bin/llama-server',description:'Base Qwen profile',mmproj_file:'/models/mmproj.gguf',flags:['--some-flag'],performance:{'cpu-range':'2-7','n-gpu-layers':'99','threads':'6'}}};
 const local=state.inventory.services.find(s=>s.id==='local');local.name='Local models';local.models=['qwen-local'];local.runtimes=[{id:'qwen-local',runtime:'llama.cpp',model_file:a},{id:'qwen-q4',runtime:'llama.cpp',model_file:b}];
 await p.route('**/api/**',async r=>{const path=new URL(r.request().url()).pathname.slice(5);let result={};
  if(path==='state')result=state;
  if(path==='scan')result=state.inventory;
  if(path==='local-profile'){result=r.request().postDataJSON();writes.push(result);state.local_profiles[result.model_file]=result;}
  if(path==='local-import'){const file=r.request().postDataJSON().file;result={...state.local_profiles[file],model_file:file,description:'Imported description'};}
  if(path==='settings'){writes.push({unexpectedGeneralSave:true});result={saved:true};}
  await r.fulfill({json:result});
 });
 await p.goto(process.env.ADMIN_URL||'http://127.0.0.1:8094/');await p.locator('[data-panel=providers]').click();
 await p.locator('[data-configured-provider=local]').getByRole('button',{name:/Edit/}).click();
 await p.locator('#hardware-editor-details>summary').click();
 assert.equal(await p.locator('#hardware-model').inputValue(),a);
 assert.equal(await p.locator('#profile-cpu-range').inputValue(),'2-7');
 assert.equal(await p.locator('#profile-description').inputValue(),'Base Qwen profile');
 assert.match(await p.locator('#local-profile-summary').innerText(),/Base Qwen profile/);
 await p.selectOption('#hardware-model',b);
 assert.equal(await p.locator('#profile-cpu-range').inputValue(),'');
 assert.match(await p.locator('#local-profile-summary').innerText(),/None · does not inherit from another model/);
 await p.selectOption('#hardware-model',a);await p.fill('#profile-cpu-range','4-9');
 await p.selectOption('#hardware-model',b);
 assert.equal(await p.locator('#hardware-model').inputValue(),a);assert.equal(await p.locator('#profile-cpu-range').inputValue(),'4-9');
 await p.click('#wizard-cancel');assert(await p.locator('#provider-dialog').isVisible());
 assert.equal(await p.locator('#profile-cpu-range').inputValue(),'4-9');
 await p.click('#save');await p.waitForFunction(()=>document.querySelector('#feedback').textContent.includes('CPU and GPU profile has pending changes'));
 assert.equal(writes.length,0);
 await p.fill('#profile-description','Profile created by the test person for Qwen3.6-35B-A3B UD-Q3_K_M');
 await p.locator('#inspector-tabs').getByText('Model and hardware').click();
 await p.check('#profile-tools');await p.check('#profile-permission-internet');await p.check('#profile-permission-upload');
 await p.click('#profile-save');await p.waitForFunction(()=>document.querySelector('#profile-save').textContent==='Save this model profile');
 assert.equal(writes.length,1);assert.equal(writes[0].model_file,a);assert.equal(writes[0].performance['cpu-range'],'4-9');assert(!state.local_profiles[b]);assert.equal(writes[0].permissions.internet,true);assert.equal(writes[0].permissions.upload,true);assert.equal(writes[0].capabilities.tools,true);assert.equal(writes[0].description,'Profile created by the test person for Qwen3.6-35B-A3B UD-Q3_K_M');assert.equal(writes[0].mmproj_file,'/models/mmproj.gguf');assert.deepEqual(writes[0].flags,['--some-flag']);
 await p.selectOption('#hardware-model',b);assert(!(await p.isChecked('#profile-permission-internet')));assert(!(await p.isChecked('#profile-permission-upload')));assert(await p.isDisabled('#profile-permission-internet'));
 await p.locator('#inspector-tabs').getByText('Model and hardware').click();
 await p.click('#local-import');await p.waitForFunction(()=>document.querySelector('#profile-description').value==='Imported description');
 await p.click('#add-local-model');assert(await p.locator('#local-download').isVisible());await p.click('#source-file');assert(await p.locator('#local-existing').isVisible());assert(!(await p.locator('#local-download').isVisible()));await p.selectOption('#local-file',b);await p.click('#use-local-file');assert.equal(await p.locator('#hardware-model').inputValue(),b);assert(!(await p.locator('#local-add').isVisible()));
 await p.selectOption('#hardware-model',b);assert.equal(await p.locator('#profile-cpu-range').inputValue(),'');
 await p.selectOption('#hardware-model',a);assert.equal(await p.locator('#profile-cpu-range').inputValue(),'4-9');
 await p.fill('#profile-cpu-range','1-2');accept=true;await p.click('#wizard-cancel');await p.waitForFunction(()=>document.querySelector('#provider-dialog').hidden);
 await p.locator('[data-configured-provider=local]').getByRole('button',{name:/Edit/}).click();
 assert.equal(await p.locator('#profile-cpu-range').inputValue(),'4-9');
 await p.setViewportSize({width:390,height:844});assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await p.screenshot({path:'/tmp/tail-admin-profiles-mobile.png',fullPage:true});
 assert(dialogs>=3);assert.deepEqual(errors,[]);
 console.log('PASS: profiles isolated by exact model file, no inheritance, scoped save, dirty switch/cancel guards, independent provider save, mobile');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
