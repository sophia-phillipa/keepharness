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
  const errors=[];page.on('pageerror',e=>errors.push(e.message));let failSave=false,failCatalog=false,releaseCatalog=null,catalogCalls=0;
  const service=()=>({added:false,enabled:false,models:[],projects:['sem-projeto'],permissions:{read:false,write:false,upload:false,shell:false,internet:false,hooks:false},mode:'scoped',integrations:[]});
  const state={settings:{services:{codex:service(),claude:service()},projects:[],logins:[],port:8095,tailnet_port:8095,uploads_enabled:false},inventory:{platform:'Linux',services:[{id:'codex',name:'Codex CLI',found:true},{id:'claude',name:'Claude Code',found:true}],projects:[{name:'Demo',path:'/workspace/demo'}],network:{online:true}},authentication:{},models:{},integrations:{codex:[{id:'mcp:drive',name:'Drive',kind:'mcp'},{id:'plugin:github@openai',name:'github@openai',kind:'plugin'}],claude:[{id:'mcp:linear',name:'Linear',kind:'mcp'}]},operations:[],credentials:{},status:{running:false}};
  await page.route('**/api/**',async route=>{const url=new URL(route.request().url());if(url.pathname.endsWith('/check')){state.authentication.claude=true;return route.fulfill({json:{authenticated:true,models:{sonnet:['configured']}}});}if(url.pathname.endsWith('/integration-catalog')){catalogCalls++;if(releaseCatalog)await releaseCatalog;if(failCatalog)return route.fulfill({status:503,json:{error:'Catálogo temporariamente indisponível'}});}if(url.pathname.endsWith('/settings')){if(failSave)return route.fulfill({status:400,json:{error:'Falha simulada'}});state.settings=route.request().postDataJSON();}
   const result=url.pathname.endsWith('/integration-catalog')?{items:[{id:'plugin:sentry@official',name:'Sentry',kind:'plugin',status:'available',description:'Acompanha erros de aplicações.'},{id:'plugin:github@openai',name:'GitHub',kind:'plugin',status:'installed'}],warnings:[]}:url.pathname.endsWith('/state')?state:url.pathname.endsWith('/scan')?state.inventory:{};await route.fulfill({json:result});});
  await page.goto('http://admin.test/');await page.locator('[data-panel=provedores]').click();
  await page.click('#add-provider');
  await page.locator('#provider-options').getByText('Codex CLI',{exact:false}).click();
  const tabs=page.locator('#inspector-tabs');
  assert(await tabs.isVisible(),'new provider registration exposes connector management');
  let release;releaseCatalog=new Promise(resolve=>release=resolve);
  await tabs.getByText('Plugins',{exact:true}).click();
  await page.getByRole('status').filter({hasText:'Buscando conectores e plugins de Codex'}).waitFor();
  assert(await page.locator('#catalog-refresh').isDisabled());
  await tabs.getByText('Modelo e hardware').click();await tabs.getByText('Plugins',{exact:true}).click();assert.equal(catalogCalls,1);
  release();releaseCatalog=null;
  assert(await page.locator('#integrations').isVisible());
  await page.locator('#catalog-search').fill('Sentry');
  await page.getByRole('button',{name:'Instalar Sentry',exact:true}).waitFor();
  assert.match(await page.locator('#catalog-items').innerText(),/Acompanha erros/);
  assert.equal(await page.locator('#catalog-items article').count(),1);
  await page.locator('#catalog-search').fill('GitHub');
  assert.equal(await page.locator('#catalog-items button').count(),0,'installed plugin must not offer duplicate installation');
  assert.match(await page.locator('#integrations').innerText(),/Codex/);
  // Browse all pages; search includes entries beyond the initial forty.
  await page.evaluate(()=>{
   integrationCatalogs.get('codex').items.push(...Array.from({length:83},(_,i)=>({id:'plugin:extra-'+i,name:'Extra '+i,kind:'plugin',status:'available'})));
  });
  await page.locator('#catalog-search').fill('');
  assert.equal(await page.locator('#catalog-items article').count(),40);
  await page.getByRole('button',{name:'Carregar mais',exact:true}).click();
  assert.equal(await page.locator('#catalog-items article').count(),80);
  await page.getByRole('button',{name:'Carregar mais',exact:true}).click();
  assert.equal(await page.locator('#catalog-items article').count(),85);
  assert(await page.locator('#catalog-more').isHidden());
  assert.equal(await page.locator('#catalog-count').innerText(),'Mostrando 85 de 85 resultados.');
  await page.locator('#catalog-search').fill('Extra 82');
  assert.equal(await page.locator('#catalog-items article').count(),1);
  assert.match(await page.locator('#catalog-items').innerText(),/Extra 82/);
  await page.locator('#catalog-search').fill('');
  assert.equal(await page.locator('#catalog-items article').count(),40);
  await page.locator('#catalog-search').fill('GitHub');
  assert.equal(await tabs.getByRole('button',{name:'Permissões',exact:true}).count(),0);
  assert.equal(await page.locator('.provider-connectors .connector-icon').count(),1);
  assert.match(await page.locator('#catalog-items').innerText(),/Descrição não fornecida/);
  await tabs.getByText('Conectores',{exact:true}).click();
  assert.equal(await page.locator('#catalog-items article').count(),0,'plugins never appear in connectors');
  assert.match(await page.locator('#integration-help').innerText(),/Autenticar conector/);
  const drive=page.getByRole('checkbox',{name:/Drive/});
  assert(await drive.isChecked(),'available MCP connector is selected by default for a new provider');
  await tabs.getByText('Plugins',{exact:true}).click();
  const plugin=page.getByRole('checkbox',{name:/github/i});
  assert(await plugin.isChecked(),'available plugin is selected by default for a new provider');
  await page.locator('#inspector-tabs').getByText('Plugins',{exact:true}).click();
  await page.locator('#integration-name').fill('sentry');
  assert.equal(await page.locator('#integration-provider').inputValue(),'codex');
  assert.doesNotMatch(await page.locator('#integration-action').innerText(),/conector/);
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
  assert.match(await page.locator('#scan').getAttribute('title'),/serviços de IA e modelos locais neste computador/);
  await page.screenshot({path:'/tmp/tail-admin-dashboard-updated.png',fullPage:true});
  for(const width of [390,768]){await page.setViewportSize({width,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);}await page.setViewportSize({width:1440,height:1000});
  await page.getByRole('button',{name:'Editar Codex CLI',exact:true}).click();
  assert.equal(await page.locator('#provider-dialog .provider-enabled').count(),0);
  await tabs.getByText('Plugins',{exact:true}).click();
  await tabs.getByText('Conectores',{exact:true}).click();
  assert(await page.getByRole('checkbox',{name:/Drive/}).isChecked());
  await tabs.getByText('Plugins',{exact:true}).click();
  assert(!await page.getByRole('checkbox',{name:/github/i}).isChecked(),'saved deselection must remain unchanged');
  // P1/P7: independent concepts, clear empty state and provider-specific explanation.
  assert.match(await page.locator('#integration-selection').innerText(),/No Codex/);
  await page.locator('#catalog-search').fill('missing-fixture');await page.locator('#catalog-items').getByText(/Nenhuma opção/).waitFor();
  // P4: keyboard search and accessible progress state.
  await page.locator('#catalog-search').focus();await page.keyboard.press('ControlOrMeta+A');await page.keyboard.type('Sentry');assert.equal(await page.locator('#catalog-items article').count(),1);
  assert.equal(await page.locator('#catalog-items').getAttribute('aria-busy'),'false');
  // P5: narrow layout with integrations open; failed catalogue can be retried.
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  failCatalog=true;await page.click('#catalog-refresh');await page.locator('#catalog-status').filter({hasText:'Catálogo temporariamente indisponível'}).waitFor();
  failCatalog=false;await page.click('#catalog-refresh');await page.getByRole('button',{name:'Instalar Sentry',exact:true}).waitFor();
  // P6: a stale negative account state is rechecked without changing selections.
  state.settings.services.claude={...service(),added:true,enabled:true,models:['sonnet']};state.authentication.claude=false;
  await page.reload();await page.locator('[data-configured-provider=claude]').waitFor();assert(!await page.locator('[data-configured-provider=claude]').innerText().then(text=>text.includes('Autorização pendente')));
  await page.getByRole('button',{name:'Editar Claude Code',exact:true}).click();await tabs.getByText('Plugins',{exact:true}).click();
  assert.match(await page.locator('#integration-selection').innerText(),/No Claude Code/);assert.match(await page.locator('#integration-selection').innerText(),/Nenhum plugin instalado/);
  await page.screenshot({path:'/tmp/tail-admin-integrations-mobile.png',fullPage:true});

  // Shared Codex engine, independent selections for Local and DeepSeek.
  for(const provider of ['local','deepseek']){
   state.inventory.services.push({id:provider,name:provider,found:true,models:['fixture']});
   state.settings.services[provider]={...service(),added:true,models:['fixture'],integrations:['mcp:drive']};
   state.integrations[provider]=state.integrations.codex;
   await page.reload();
   await page.locator('[data-configured-provider='+provider+']').getByRole('button',{name:/Editar/}).click();
   await tabs.getByText('Plugins',{exact:true}).click();
   assert.equal(await tabs.getByText('Plugins',{exact:true}).getAttribute('aria-pressed'),'true');
   assert.equal(await page.locator('#integration-provider').inputValue(),'codex');
   assert(!await page.getByRole('checkbox',{name:/github/i}).isChecked());
   await tabs.getByText('Conectores',{exact:true}).click();
   assert(await page.getByRole('checkbox',{name:/Drive/}).isChecked());
   if(provider==='local'){assert(await page.getByRole('checkbox',{name:/Drive/}).isDisabled());assert.match(await page.locator('#integration-selection').innerText(),/não são carregados/);}
   assert.doesNotMatch(await page.locator('#integration-action').innerText(),/plugin/i);
   assert(await tabs.getByText('Conectores',{exact:true}).getAttribute('title'));
  }
  assert.deepEqual(errors,[]);
  console.log('PASS: connector access, defaults, saved deselection and project hint padding');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
