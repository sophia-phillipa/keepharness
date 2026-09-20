const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');

(async()=>{
 const browser=await chromium.launch();
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const fs=require('node:fs/promises'),path=require('node:path');
  await page.route('http://admin.test/**',async route=>{
   const url=new URL(route.request().url());
   if(url.pathname.startsWith('/api/'))return route.fallback();
   const file=url.pathname==='/'?'index.html':url.pathname.slice(1);
   return route.fulfill({body:await fs.readFile(path.join(__dirname,file.startsWith('assets/')?'../tail_ui':'../control',file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.svg')?'image/svg+xml':'text/html'});
  });
  const errors=[];page.on('pageerror',e=>errors.push(e.message));let failSave=false;
  const service=()=>({added:false,enabled:false,models:[],projects:['sem-projeto'],permissions:{read:false,write:false,upload:false,shell:false,internet:false,hooks:false},mode:'scoped',integrations:[]});
  const state={settings:{services:{codex:service(),claude:service()},projects:[],logins:[],port:8095,tailnet_port:8095,uploads_enabled:false},inventory:{platform:'Linux',services:[{id:'codex',name:'Codex CLI',found:true},{id:'claude',name:'Claude Code',found:true}],projects:[{name:'Demo',path:'/workspace/demo'}],network:{online:true}},authentication:{},models:{},integrations:{codex:[{id:'mcp:drive',name:'Drive',kind:'mcp'},{id:'plugin:github@openai',name:'github@openai',kind:'plugin'}],claude:[{id:'mcp:linear',name:'Linear',kind:'mcp'}]},operations:[],credentials:{},status:{running:false}};
  await page.route('**/api/**',async route=>{const url=new URL(route.request().url());if(url.pathname.endsWith('/settings')){if(failSave)return route.fulfill({status:400,json:{error:'Falha simulada'}});state.settings=route.request().postDataJSON();}
   const result=url.pathname.endsWith('/integration-catalog')?{items:[{id:'plugin:sentry@official',name:'Sentry',kind:'plugin',status:'available'},{id:'plugin:github@openai',name:'GitHub',kind:'plugin',status:'installed'}],warnings:[]}:url.pathname.endsWith('/state')?state:url.pathname.endsWith('/scan')?state.inventory:{};await route.fulfill({json:result});});
  await page.goto('http://admin.test/');
  await page.click('#add-provider');
  await page.locator('#provider-options').getByText('Codex CLI',{exact:false}).click();
  const tabs=page.locator('#inspector-tabs');
  assert(await tabs.isVisible(),'new provider registration exposes connector management');
  await tabs.getByText('Conectores',{exact:true}).click();
  assert(await page.locator('#integrations').isVisible());
  await page.locator('#catalog-search').fill('Sentry');
  await page.getByRole('button',{name:'Instalar Sentry',exact:true}).waitFor();
  assert.equal(await page.locator('#catalog-items article').count(),1);
  await page.locator('#catalog-search').fill('GitHub');
  assert.equal(await page.locator('#catalog-items button').count(),0,'installed plugin must not offer duplicate installation');
  assert.match(await page.locator('#integrations').innerText(),/Codex/);
  await page.locator('#inspector-tabs').getByText('Permissões e projetos').click();
  const projectInset=await page.evaluate(()=>document.querySelector('#project-list .hint').getBoundingClientRect().left-document.querySelector('#projects .panel').getBoundingClientRect().left);
  assert(projectInset>=16,'project-panel description has visible inset padding');
  assert.equal(await page.locator('.provider-connectors .connector-icon').count(),2,'each integration has an associated icon');
  const drive=page.getByRole('checkbox',{name:/Drive/});
  assert(await drive.isChecked(),'available MCP connector is selected by default for a new provider');
  const plugin=page.getByRole('checkbox',{name:/github/i});
  assert(await plugin.isChecked(),'available plugin is selected by default for a new provider');
  await page.locator('#inspector-tabs').getByText('Conectores',{exact:true}).click();
  await page.locator('#integration-name').fill('sentry');
  assert.equal(await page.locator('#integration-provider').inputValue(),'codex');
  assert.match(await page.locator('#integration-action').innerText(),/Cadastrar conector MCP/);
  await page.locator('#integration-cli-guide summary').click();
  assert.match(await page.locator('#integrations').innerText(),/codex mcp list|claude mcp list|plugin list/i);
  await page.screenshot({path:'/tmp/tail-admin-integrations.png',fullPage:true});
  state.settings.services.codex={...service(),added:true,models:['fixture'],integrations:['mcp:drive']};
  await page.reload();
  const available=page.locator('[data-configured-provider=codex]').getByRole('checkbox',{name:'Disponibilizar Codex CLI',exact:true});
  const before=structuredClone(state.settings);
  await available.click();await page.waitForFunction(()=>document.querySelector('#feedback').textContent.includes('Serviço ativado'));
  assert(state.settings.services.codex.enabled);before.services.codex.enabled=true;assert.deepEqual(state.settings,before);
  failSave=true;await available.click();await page.waitForFunction(()=>document.querySelector('#feedback').textContent.includes('Falha simulada'));assert(await available.isChecked());assert(state.settings.services.codex.enabled);
  failSave=false;await available.click();await page.waitForFunction(()=>document.querySelector('#feedback').textContent.includes('Serviço desativado'));assert(!state.settings.services.codex.enabled);
  assert.equal(await page.locator('#vpn-bind,#logins,#share,#tailscale-badge,#save-network').count(),0);
  assert.match(await page.locator('#scan-description').innerText(),/disponível neste computador/);
  await page.screenshot({path:'/tmp/tail-admin-dashboard-updated.png',fullPage:true});
  for(const width of [390,768]){await page.setViewportSize({width,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);}await page.setViewportSize({width:1440,height:1000});
  await page.getByRole('button',{name:'Editar Codex CLI',exact:true}).click();
  assert.equal(await page.locator('#provider-dialog .provider-enabled').count(),0);
  await page.locator('#inspector-tabs').getByText('Permissões e projetos').click();
  assert(await page.getByRole('checkbox',{name:/Drive/}).isChecked());
  assert(!await page.getByRole('checkbox',{name:/github/i}).isChecked(),'saved deselection must remain unchanged');
  assert.deepEqual(errors,[]);
  console.log('PASS: connector access, defaults, saved deselection and project hint padding');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
