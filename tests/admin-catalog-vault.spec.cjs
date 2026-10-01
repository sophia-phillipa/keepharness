const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    page.setDefaultTimeout(2500);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const project = { id: 'demo', label: 'Demo project', root: '/fixture/project', catalogs: ['demo'], catalog_pins: {demo: {commit: 'a'.repeat(40)}} };
    const catalog = {id: 'demo', root: '/fixture/catalog', kind: 'git', trusted: true};
    const status = {catalogs: [catalog], projects: [project], preflight: [{project_id:'demo',catalog_id:'demo',manifest:true,integrations:[{integration:'reader',consumers:['codex','claude'],environment:{ISSUE_TOKEN:'token'},precedence:'vault',mediated:false}],preflight:['Provision the catalog Python environment in Admin.']}], drift: [{resource_id:'catalog/demo/commands/check.md', locations:['demo','other'], revisions:{demo:{revision:'aaa'},other:{revision:'bbb'}}}]};
    const vault = { credentials: [], bindings: [], contracts: [] };
    let writes = 0, moves = 0, fail = false, malformed = false;
    await page.route('http://admin.test/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith('/api/')) {
        let result = {};
        if (url.pathname === '/api/state') result = {settings:{services:{},projects:[project],catalogs:[catalog],logins:[],port:8095},inventory:{services:[],projects:[],network:{}},authentication:{},models:{},integrations:{},operations:[],credentials:{},status:{running:false}};
        if (url.pathname === '/api/catalogs') result = malformed?{}:status;
        if (url.pathname === '/api/vault') {
          if (route.request().method() === 'POST') {
            writes++;
            if (fail) return route.fulfill({status:400,json:{error:'Choose a registered project.'}});
            const data = route.request().postDataJSON();
            assert.equal(data.values.token,'synthetic-secret-never-echo');
            assert.equal(data.contract,undefined,'Reuse the catalog contract without narrowing its providers');
            vault.credentials = [{binding:data.binding,fields:['token']}];
          }
          result = vault;
        }
        if (url.pathname === '/api/catalog-pin') {
          const data = route.request().postDataJSON();
          if (data.action === 'preview') result = {preview_token:'fixture-preview',pin:{commit:'b'.repeat(40)},diff:[{resource_id:'catalog/demo/commands/check.md',before:'aaa',after:'bbb'}]};
          else { moves++; project.catalog_pins.demo.commit='b'.repeat(40); result=status; }
        }
        return route.fulfill({json:result});
      }
      const file = url.pathname === '/' ? 'index.html' : url.pathname.slice(1);
      return route.fulfill({body:await fs.readFile(path.join(__dirname,file.startsWith('assets/')?'../tail_ui':'../control',file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.svg')?'image/svg+xml':'text/html'});
    });
    await page.goto('http://admin.test/#catalogs');
    await page.locator('#catalog-project').selectOption('demo');
    const duplicates=await page.locator('[id]').evaluateAll(nodes=>{const seen=new Set();return nodes.filter(node=>{if(seen.has(node.id))return true;seen.add(node.id);return false;}).map(node=>node.id);});
    assert.deepEqual(duplicates,[],'Panel IDs must remain unique across provider and catalog views');
    assert.match(await page.locator('#catalog-preflight').innerText(), /Provision/); // P1 prerequisite
    await page.locator('#catalog-ref').fill('main');
    await page.locator('#catalog-preview').click();
    await page.waitForFunction(() => !document.getElementById('catalog-move').disabled);
    assert.equal(moves,0); // P2 preview is not a write
    assert.match(await page.locator('#catalog-diff').innerText(), /check.md/);
    await page.locator('#catalog-move').focus();
    await page.keyboard.press('Enter'); // P4 keyboard ownership action
    await page.waitForFunction(() => document.getElementById('catalog-message').textContent.includes('moved'));
    assert.equal(moves,1);
    assert.equal(await page.evaluate(()=>document.activeElement.id),'catalog-message');
    assert.match(await page.locator('#catalog-drift').innerText(), /other/); // P3 cross-project provenance
    await page.locator('#vault-binding').fill('demo-reader');
    await page.locator('#vault-mode').selectOption('mediated');
    await page.locator('#vault-integration').fill('reader');
    assert.equal(await page.locator('#vault-mode').inputValue(),'environment');
    assert.equal(await page.locator('#vault-mode').isDisabled(),true);
    assert.equal(await page.locator('#vault-variable').inputValue(),'ISSUE_TOKEN');
    await page.locator('#vault-secret').fill('synthetic-secret-never-echo');
    assert.equal(await page.locator('#vault-secret').getAttribute('type'),'password');
    await page.locator('#vault-save').click();
    await page.waitForFunction(() => document.getElementById('vault-status').textContent.includes('demo-reader'));
    assert.equal(await page.locator('#vault-secret').inputValue(),'');
    assert.equal((await page.locator('body').innerText()).includes('synthetic-secret-never-echo'),false); // P6 no echo
    fail=true;
    await page.locator('#vault-secret').fill('synthetic-secret-never-echo');
    await page.locator('#vault-save').click();
    await page.waitForFunction(() => document.getElementById('catalog-message').textContent.includes('registered project'));
    assert.equal(await page.locator('#vault-secret').inputValue(),'');
    assert.equal(writes,2);
    const output=process.env.EVAL_OUTPUT || '/tmp/p5-visual';
    await fs.mkdir(output,{recursive:true});
    for (const [label,width,height] of [['desktop',1440,900],['mobile',400,812]]) {
      await page.setViewportSize({width,height});
      await page.locator('#catalog-panel').scrollIntoViewIfNeeded();
      for (const selector of ['#catalog-preview','#vault-save','#vault-secret']) {
        await page.locator(selector).scrollIntoViewIfNeeded();
        const rect=await page.locator(selector).boundingBox();
        assert.ok(rect.x>=0 && rect.x+rect.width<=width+1,`${label} ${selector} overflows`);
      }
      await page.screenshot({path:path.join(output,`admin-catalog-vault-${label}.png`),fullPage:true}); // P7 visual evidence
    }
    await page.reload(); // P5 recovery/reload
    await page.waitForFunction(() => document.getElementById('vault-status').textContent.includes('demo-reader'));
    assert.equal(await page.locator('#vault-secret').inputValue(),'');
    malformed=true;
    await page.locator('#catalog-admin-refresh').click();
    await page.waitForFunction(() => document.getElementById('catalog-message').textContent.includes('unavailable'));
    assert.equal(await page.locator('#catalog-admin-refresh').isEnabled(),true);
    malformed=false;
    await page.locator('#catalog-admin-refresh').click();
    await page.waitForFunction(() => document.getElementById('catalog-message').textContent.includes('refreshed'));
    assert.equal(await page.locator('#catalog-project').inputValue(),'demo');
    assert.deepEqual(errors,[]);
    console.log('PASS seven-profile catalog/pin/vault/drift matrix, desktop and mobile');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode=1; });
