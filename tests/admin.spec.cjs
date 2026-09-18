// PLAYWRIGHT_MODULE=/path/to/playwright node tests/admin.spec.cjs
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const b=await chromium.launch();const p=await b.newPage({viewport:{width:1440,height:1000}});const errors=[],actions=[];
 p.on('pageerror',e=>errors.push(e.message));
 const perms={read:false,write:false,upload:false,shell:false,internet:false,hooks:false};
 const state={settings:{services:{codex:{enabled:false,models:[],projects:['sem-projeto'],permissions:{...perms},mode:'scoped',integrations:[]},claude:{enabled:false,models:[],projects:['sem-projeto'],permissions:{...perms},mode:'scoped',integrations:[]},local:{enabled:false,models:[],projects:['sem-projeto'],permissions:{...perms},mode:'native',integrations:[]}},projects:[],logins:[],port:8095,tailnet_port:8095,uploads_enabled:false},inventory:{platform:'Linux',services:[{id:'codex',name:'Codex CLI',found:true},{id:'claude',name:'Claude Code',found:true},{id:'local',name:'Modelo local',found:false}],projects:[{name:'Demo',path:'/workspace/demo'}],network:{online:true}},authentication:{},models:{},integrations:{codex:[{id:'mcp:drive',name:'Drive',kind:'mcp'}],claude:[],local:[]},operations:[],status:{running:false,local_url:'http://127.0.0.1:8095/',remote_url:'http://demo.tailnet:8095/',shared:false}};
 await p.route('**/api/**',async route=>{
  const r=route.request(),name=new URL(r.url()).pathname.slice(5),data=r.method()==='POST'?r.postDataJSON():null;
  if(data)actions.push([name,data]);let result={};
  if(name==='state')result=state;
  else if(name==='check')result={authenticated:true,models:{'model-one':['low']}};
  else if(name==='settings'){state.settings=data;result={saved:true};}
  else if(name==='start'){state.status.running=true;result=state.status;}
  else if(name==='stop'){state.status.running=false;result=state.status;}
  else if(name==='tailnet'){state.status.shared=data.enabled;result=state.status;}
  else if(name==='integration'||name==='model-install')result={id:'test-operation',state:'running'};
  else if(name==='scan')result=state.inventory;
  return route.fulfill({contentType:'application/json',body:JSON.stringify(result)});
 });
 await p.goto(process.env.ADMIN_URL||'http://127.0.0.1:8094/');await p.waitForSelector('.provider-card');
 assert.equal(await p.locator('.provider-card').count(),3);
 const codex=p.locator('[data-provider=codex]');await codex.getByText('Verificar conta',{exact:true}).click();await codex.getByLabel('model-one',{exact:true}).check();
 await codex.locator('select').selectOption('native');await codex.getByLabel('Internet',{exact:true}).check();await codex.getByLabel('Drive',{exact:true}).check();await codex.getByLabel('Disponibilizar este serviço',{exact:true}).check();
 await p.locator('#project-list').getByLabel('Demo',{exact:false}).check();await p.locator('.project-badges').getByLabel('codex',{exact:true}).check();
 await p.locator('#uploads').check();await codex.getByLabel('Receber anexos',{exact:true}).check();
 await p.fill('#logins','person@example.test');await p.click('#save');await p.waitForFunction(()=>document.querySelector('#feedback').textContent.includes('Configuração salva'));
 const saved=actions.filter(x=>x[0]==='settings').at(-1)[1];assert(saved.services.codex.enabled);assert(saved.services.codex.permissions.internet);assert(saved.services.codex.projects.includes('demo'));assert.deepEqual(saved.services.codex.integrations,['mcp:drive']);
 await p.click('#start');await p.waitForFunction(()=>document.querySelector('#harness-state').textContent==='Ativo');
 await p.click('#share');await p.waitForFunction(()=>document.querySelector('#network-state').textContent==='Compartilhado');await p.click('#stop');
 await p.selectOption('#integration-provider','claude');await p.fill('#integration-name','gmail');await p.fill('#integration-source','https://example.test/mcp');await p.click('#integration-run');
 await p.waitForFunction(()=>document.querySelector('#feedback').textContent.includes('Operação iniciada'));
 assert(actions.some(x=>x[0]==='integration'&&x[1].provider==='claude'&&x[1].name==='gmail'));
 await p.click('#add-project');await p.fill('#folder-name','Extra');await p.fill('#folder-path','/workspace/extra');await p.locator('#folder-form button[type=submit]').click();
 assert.equal(await p.locator('#project-count').textContent(),'2');
 await p.click('#theme');assert(await p.locator('html').evaluate(e=>e.classList.contains('light')));await p.click('#theme');
 await p.screenshot({path:'/tmp/tail-admin-test-desktop.png',fullPage:true});
 for(const width of [390,768]){await p.setViewportSize({width,height:844});assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);}
 assert.deepEqual(errors,[]);console.log('PASS: discovery, model selection, permissions, projects, upload policy, lifecycle, Tailscale, connectors, theme, responsive layout');await b.close();
})().catch(e=>{console.error(e);process.exit(1);});
