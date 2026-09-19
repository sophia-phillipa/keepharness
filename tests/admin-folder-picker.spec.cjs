const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch();try{
 const p=await browser.newPage({viewport:{width:390,height:844}}),errors=[],writes=[];p.on('pageerror',e=>errors.push(e.message));
 const empty=id=>({added:false,enabled:false,models:[],projects:['sem-projeto'],permissions:{read:false,write:false,upload:false,shell:false,internet:false,hooks:false},mode:['local','deepseek'].includes(id)?'native':'scoped',integrations:[]});
 const state={settings:{services:Object.fromEntries(['codex','claude','local','deepseek'].map(id=>[id,empty(id)])),projects:[],logins:[],port:8095,tailnet_port:8095,uploads_enabled:false},inventory:{platform:'Linux',services:[{id:'codex',name:'Codex CLI',found:true},{id:'claude',name:'Claude Code',found:true},{id:'local',name:'Modelo local',found:true},{id:'deepseek',name:'DeepSeek',api:true,found:true}],projects:[{name:'Demo',path:'/workspace/demo'}],network:{online:true}},authentication:{},models:{},integrations:{codex:[{id:'mcp:drive',name:'Drive',kind:'mcp'}],claude:[],local:[],deepseek:[]},operations:[],credentials:{},status:{running:false,local_url:'http://127.0.0.1:8095/',remote_url:'http://demo.tailnet:8095/',shared:false}};


 state.settings.services.local={...empty('local'),added:true,enabled:true,models:['qwen-local']};
 state.local_profiles={'/models/Qwen.gguf':{model_file:'/models/Qwen.gguf',binary:'/usr/bin/llama-server',performance:{},allowed_roots:[]}};
 const local=state.inventory.services.find(s=>s.id==='local');local.models=['qwen-local'];local.runtimes=[{id:'qwen-local',model_file:'/models/Qwen.gguf',runtime:'llama.cpp'}];
 await p.route('**/api/**',async r=>{const url=new URL(r.request().url()),path=url.pathname.slice(5);let result={};
  if(path==='state')result=state;
  if(path==='folders'){
   const folder=url.searchParams.get('path')||'/server';
   if(folder==='/server/Privado')return r.fulfill({status:400,json:{error:'Não há permissão para abrir esta pasta.'}});
   result={path:folder,parent:folder==='/server'?'/':folder.slice(0,folder.lastIndexOf('/'))||'/',directories:folder==='/server'?[{name:'Projetos',path:'/server/Projetos'},{name:'Privado',path:'/server/Privado'}]:[],truncated:false};
  }
  if(path==='folders/create'){const data=r.request().postDataJSON();writes.push({kind:'create',...data});result={path:data.parent+'/'+data.name,created:true};}
  if(path==='local-profile'){result=r.request().postDataJSON();writes.push({kind:'profile',...result});state.local_profiles[result.model_file]=result;}
  if(path==='settings'){state.settings=r.request().postDataJSON();writes.push({kind:'settings',...state.settings});result={saved:true};}
  return r.fulfill({json:result});
 });
 await p.goto(process.env.ADMIN_URL||'http://127.0.0.1:8094/');
 await p.locator('[data-configured-provider=local]').getByRole('button',{name:/Editar/}).click();
 await p.locator('#inspector-tabs').getByText('Permissões e projetos').click();await p.click('#add-project');await p.click('#choose-project-folder');
 await p.locator('#folder-picker-list').getByText('Privado',{exact:true}).click();await p.waitForFunction(()=>!document.querySelector('#folder-picker-error').hidden);assert(await p.locator('#folder-picker-use').isDisabled());
 await p.click('#folder-picker-home');await p.locator('#folder-picker-list').getByText('Projetos',{exact:true}).click();
 await p.locator('.folder-picker-create>summary').click();await p.fill('#folder-picker-new-name','Meu projeto');await p.click('#folder-picker-create');
 await p.waitForFunction(()=>document.querySelector('#folder-picker-breadcrumb').textContent.includes('Meu projeto'));
 await p.screenshot({path:'/tmp/tail-folder-picker-mobile.png'});
 await p.click('#folder-picker-use');assert.equal(await p.locator('#folder-path').inputValue(),'/server/Projetos/Meu projeto');assert.equal(await p.locator('#folder-name').inputValue(),'Meu projeto');
 await p.locator('#folder-form').getByRole('button',{name:'Adicionar às escolhas'}).click();
 const project=p.locator('.project-row').filter({hasText:'/server/Projetos/Meu projeto'});await project.locator('.project-permission-policy>summary').click();await project.locator('[data-project-permission=read]').selectOption('allow');
 assert.equal(await project.locator('[data-project-permission=read] option').count(),2);
 await p.click('#model-roots-add');await p.locator('#folder-picker-list').getByText('Projetos',{exact:true}).click();await p.click('#folder-picker-use');
 assert.match(await p.locator('#model-roots-list').innerText(),/server\/Projetos/);await p.click('#profile-save');await p.waitForFunction(()=>document.querySelector('#profile-save').textContent==='Salvar perfil deste modelo');
 assert.deepEqual(writes.find(w=>w.kind==='profile').allowed_roots,['/server/Projetos']);
 await p.click('#save');await p.waitForFunction(()=>document.querySelector('#dirty').textContent==='Configurações salvas');
 const saved=writes.find(w=>w.kind==='settings');assert.equal(saved.projects.find(x=>x.root==='/server/Projetos/Meu projeto').permissions.read,true);
 assert.equal(writes.filter(w=>w.kind==='create').length,1);assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
 console.log('PASS: folder navigation, permission error recovery, new project directory, additive project grant, model folders, mobile');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
