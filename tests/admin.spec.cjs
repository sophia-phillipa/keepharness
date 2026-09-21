const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const b=await chromium.launch();const p=await b.newPage({viewport:{width:1440,height:1000}});const errors=[],actions=[];
 p.on('pageerror',e=>errors.push(e.message));p.on('dialog',d=>d.accept());
 const empty=id=>({added:false,enabled:false,models:[],projects:['sem-projeto'],permissions:{read:false,write:false,upload:false,shell:false,internet:false,hooks:false},mode:['local','deepseek'].includes(id)?'native':'scoped',integrations:[]});
 const state={settings:{services:Object.fromEntries(['codex','claude','local','deepseek'].map(id=>[id,empty(id)])),projects:[{id:'demo',label:'Demo',root:'/workspace/demo'}],logins:[],port:8095,tailnet_port:8095,uploads_enabled:false},inventory:{platform:'Linux',services:[{id:'codex',name:'Codex CLI',found:true},{id:'claude',name:'Claude Code',found:true},{id:'local',name:'Modelo local',found:false},{id:'deepseek',name:'DeepSeek',api:true,found:true}],projects:[{name:'Demo',path:'/workspace/demo'}],network:{online:true}},authentication:{},models:{},integrations:{codex:[{id:'mcp:drive',name:'Drive',kind:'mcp'}],claude:[],local:[],deepseek:[]},operations:[],credentials:{},status:{running:false,local_url:'http://127.0.0.1:8095/',remote_url:'http://demo.tailnet:8095/',shared:false}};
 const bundle=()=>({format:'tail-harness-settings',version:1,settings:state.settings,local_profile:{}});
 await p.route('**/api/**',async route=>{
  const r=route.request(),name=new URL(r.url()).pathname.slice(5),data=r.method()==='POST'?r.postDataJSON():null;
  if(data)actions.push([name,data]);let result={};
  if(name==='state')result=state;
  else if(name==='check')result={authenticated:true,models:{'model-one':['low']}};
  else if(name==='provider-token'){state.credentials.deepseek=true;result={saved:true};}
  else if(name==='provider-delete'){state.settings.services[data.provider]=empty(data.provider);result={removed:true};}
  else if(name==='settings-export')result=bundle();
  else if(name==='settings-import'){result={valid:true,applied:data.apply===true,services:['codex'],projects:1,local_profile:false};if(data.apply)state.settings=data.bundle.settings;}
  else if(name==='settings'){state.settings=data;if(Object.values(data.services).some(s=>s.enabled))state.status.running=true;result={saved:true};}
  else if(name==='start'){state.status.running=true;result=state.status;}
  else if(name==='stop'){state.status.running=false;result=state.status;}
  else if(name==='tailnet'){state.status.shared=data.enabled;result=state.status;}
  else if(name==='integration'||name==='model-install')result={id:'test-operation',state:'running'};
  else if(name==='scan')result=state.inventory;
  return route.fulfill({contentType:'application/json',body:JSON.stringify(result)});
 });
 await p.goto(process.env.ADMIN_URL||'http://127.0.0.1:8094/');await p.waitForSelector('#add-provider');
 assert.equal(await p.locator('#providers').isVisible(),false);assert.equal(await p.locator('#configured-providers article').count(),0);
 await p.click('#add-provider');assert.equal(await p.locator('#dashboard').isVisible(),true);assert(actions.some(x=>x[0]==='scan'));await p.waitForSelector('#provider-dialog:not([hidden])');await p.screenshot({path:'/tmp/tail-wizard-new.png'});for(const width of [390,768]){await p.setViewportSize({width,height:844});assert.equal(await p.locator('#provider-dialog').evaluate(e=>e.scrollWidth>e.clientWidth),false);}await p.setViewportSize({width:1440,height:1000});await p.locator('#provider-options').getByText('Codex CLI',{exact:false}).click();
 const codex=p.locator('[data-provider=codex]');await codex.getByText('Verificar conta',{exact:true}).click();await codex.getByLabel('model-one',{exact:true}).check();await codex.getByLabel('Disponibilizar este serviço',{exact:true}).check();
 await p.click('#wizard-next');assert.equal(await p.locator('#providers').isVisible(),false);
 const permissions=p.locator('#permission-editor');assert.match(await permissions.innerText(),/diretamente neste computador/);await p.locator('#inspector-tabs').getByText('Conectores e plugins',{exact:true}).click();await p.locator('#integration-selection').getByLabel('Drive',{exact:true}).check();await p.locator('#inspector-tabs').getByText('Permissões',{exact:true}).click();
 assert.match(await p.locator('#project-list').innerText(),/barra lateral do Tail Harness/);
 assert.equal(await permissions.getByLabel('Receber anexos',{exact:true}).count(),0);
 await p.click('#wizard-next');await p.click('#save');await p.waitForFunction(()=>document.querySelector('#th-toast')?.textContent.includes('Configurações salvas'));
 const saved=actions.filter(x=>x[0]==='settings').at(-1)[1];assert(saved.services.codex.added);assert(saved.services.codex.enabled);assert.deepEqual(saved.services.codex.models,['model-one']);assert(saved.projects.some(project=>project.id==='demo'));assert.deepEqual(saved.services.codex.integrations,['mcp:drive']);assert.equal(saved.uploads_enabled,false); // Effective cloud grants are normalized by the server, covered in Python.
 assert.equal(await p.locator('#configured-providers article').count(),1);
 await p.locator('#configured-providers').getByText('Editar',{exact:true}).click();await p.click('#wizard-cancel');await p.waitForFunction(()=>document.querySelector('#provider-dialog').hidden);assert.equal(await p.locator('#providers').isVisible(),false);
 assert.equal(await p.locator('#logins,#vpn-bind,#share,#save-network').count(),0);
 await p.click('#add-provider');await p.locator('#provider-options').getByText('Claude Code',{exact:false}).click();assert(await p.locator('#wizard-back').isVisible());assert.equal(await p.locator('#wizard-back').textContent(),'Escolher outro provedor');await p.click('#wizard-back');assert(await p.locator('#provider-options').isVisible());await p.locator('#provider-options').getByText('DeepSeek',{exact:false}).click();const ds=p.locator('[data-provider=deepseek]');await ds.locator('input[type=password]').fill('fixture-deepseek-token');await ds.getByText('Salvar chave e verificar',{exact:true}).click();await ds.getByLabel('model-one',{exact:true}).check();await ds.getByLabel('Disponibilizar este serviço',{exact:true}).check();await p.click('#wizard-next');await p.click('#wizard-next');await p.click('#save');await p.waitForFunction(()=>document.querySelector('#th-toast')?.textContent.includes('Configurações salvas'));await p.waitForFunction(()=>document.querySelectorAll('#configured-providers article').length===2);
 await p.locator('#configured-providers article').filter({hasText:'DeepSeek'}).getByText('Excluir',{exact:true}).click();await p.waitForFunction(()=>document.querySelectorAll('#configured-providers article').length===1);
 await p.click('#manage-network');await p.locator('#network>details>summary').click();await p.locator('#import-settings').setInputFiles({name:'settings.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(bundle()))});await p.waitForFunction(()=>!document.querySelector('#import-preview').hidden);assert.equal(actions.filter(x=>x[0]==='settings-import'&&x[1].apply===true).length,0);await p.click('#apply-import');await p.waitForFunction(()=>document.querySelector('#feedback').textContent.includes('Configuração importada'));
 await p.click('#manage-network');const downloading=p.waitForEvent('download');await p.click('#export-settings');const download=await downloading;assert.equal(download.suggestedFilename(),'tail-harness-settings.json');
 await p.click('#theme');await p.locator('[data-theme-choice=arizona]').click();assert.equal(await p.locator('html').getAttribute('data-palette'),'arizona');await p.locator('[data-theme-choice=violet-bordeaux]').click();await p.click('#appearance-close');
 await p.screenshot({path:'/tmp/tail-admin-test-desktop.png',fullPage:true});
 state.settings.services.local={...empty('local'),added:true,enabled:true,models:['qwen-local']};state.local_profile={model_file:'/models/Qwen-test.gguf',performance:{'n-gpu-layers':'0','n-cpu-moe':'8','cpu-range':'6-13'}};await p.reload();await p.locator('[data-configured-provider=local]').getByRole('button',{name:'Editar Modelo Local via Codex',exact:true}).click();assert.equal(await p.locator('dialog[open]').count(),0);assert.equal(await p.locator('#wizard-next').isVisible(),false);assert.equal(await p.locator('#save').isVisible(),true);await p.getByText('Qwen-test.gguf',{exact:true}).first().waitFor();assert.equal(await p.locator('.hardware-row').filter({hasText:'Camadas solicitadas'}).locator('strong').textContent(),'0');await p.locator('#inspector-tabs').getByText('Permissões').click();assert(await p.locator('#projects').isVisible());await p.locator('#inspector-tabs').getByText('Modelo e hardware').click();await p.screenshot({path:'/tmp/tail-inspector.png',fullPage:true});
for(const width of [390,768]){await p.setViewportSize({width,height:844});assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);}
 assert.deepEqual(errors,[]);console.log('PASS: dashboard, add/edit/delete wizard, BYOK, models, permissions, projects, uploads, lifecycle, import preview/apply/export, theme and responsive layout');await b.close();
})().catch(e=>{console.error(e);process.exit(1);});
