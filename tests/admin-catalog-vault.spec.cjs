const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { VIEWPORTS, THEMES } = require('./visual/harness-visual-helpers.cjs');
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
    const output=process.env.EVAL_OUTPUT || await fs.mkdtemp(path.join(os.tmpdir(),'tail-harness-p5-admin-'));
    await fs.mkdir(output,{recursive:true});
    const visualEvidence=[];
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
    async function captureVisualMatrix(state,target,pattern) {
      assert.match(await page.locator(target).innerText(),pattern,`${state} content is present before capture`);
      for (const theme of THEMES) {
        await page.evaluate(value => window.TailTheme.apply(value,false),theme);
        for (const viewport of VIEWPORTS) {
          await page.setViewportSize(viewport);
          await page.locator(target).scrollIntoViewIfNeeded();
          assert.equal(await page.locator('#catalog-panel').isVisible(),true);
          assert.equal(await page.locator(target).isVisible(),true);
          const label=`${viewport.width}x${viewport.height}`;
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true,`${state} ${theme} ${label} has horizontal page overflow`);
          const rect=await page.locator(target).boundingBox();
          assert.ok(rect && rect.x>=0 && rect.x+rect.width<=viewport.width+1,`${state} ${theme} ${label} ${target} overflows`);
          const filename=`admin-catalog-vault-${state}-${theme}-${label}.png`;
          await page.screenshot({path:path.join(output,filename)});
          visualEvidence.push({state,theme,viewport:label,filename});
        }
      }
      await page.setViewportSize({width:1440,height:900});
      await page.evaluate(() => window.TailTheme.apply('violet-bordeaux',false));
    }
    const duplicates=await page.locator('[id]').evaluateAll(nodes=>{const seen=new Set();return nodes.filter(node=>{if(seen.has(node.id))return true;seen.add(node.id);return false;}).map(node=>node.id);});
    assert.deepEqual(duplicates,[],'Panel IDs must remain unique across provider and catalog views');
    assert.match(await page.locator('#catalog-preflight').innerText(), /Provision/); // P1 prerequisite
    await page.locator('#catalog-ref').fill('main');
    await page.locator('#catalog-preview').click();
    await page.waitForFunction(() => !document.getElementById('catalog-move').disabled);
    assert.equal(moves,0); // P2 preview is not a write
    assert.match(await page.locator('#catalog-diff').innerText(), /check.md/);
    assert.match(await page.locator('#catalog-drift').innerText(), /other/);
    await captureVisualMatrix('pin-preview','#catalog-diff',/Proposed revision:[\s\S]*check\.md/);
    await captureVisualMatrix('drift-report','#catalog-drift',/check\.md[\s\S]*other · revision bbb/);
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
    await captureVisualMatrix('vault-status','#vault-status',/demo-reader · configured fields: token/);
    fail=true;
    await page.locator('#vault-secret').fill('synthetic-secret-never-echo');
    await page.locator('#vault-save').click();
    await page.waitForFunction(() => document.getElementById('catalog-message').textContent.includes('registered project'));
    assert.equal(await page.locator('#vault-secret').inputValue(),'');
    assert.equal(writes,2);
    assert.equal(visualEvidence.length,72,'P5 admin visual matrix covers 3 states × 6 themes × 4 viewports'); // P7 visual evidence
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
    console.log(`PASS seven-profile catalog/pin/vault/drift matrix, 72 visual states; evidence ${output}`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode=1; });
