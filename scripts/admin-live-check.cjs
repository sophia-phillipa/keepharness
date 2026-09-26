// Opt-in browser check against a caller-provided isolated administrative server.
// Run only with synthetic state: saving settings can start configured providers.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
(async()=>{
 const {ADMIN_TEST_URL,ADMIN_TEST_STATE,ADMIN_TEST_OUTPUT}=process.env;
 assert(ADMIN_TEST_URL&&ADMIN_TEST_STATE&&ADMIN_TEST_OUTPUT,'Explicit isolated URL, synthetic state directory and output required');
 assert(new URL(ADMIN_TEST_URL).hostname==='127.0.0.1'&&path.resolve(ADMIN_TEST_STATE).startsWith('/tmp/tail-admin-live-'),'Refuse non-fixture destination');
 const browser=await chromium.launch(),results=[];
 try{
  const page=await browser.newPage();page.setDefaultTimeout(15000);await page.goto(ADMIN_TEST_URL);await page.waitForFunction(()=>typeof state!=='undefined'&&!working);
  // Seed synthetic provider via the actual settings endpoint, using its real cookie/CSRF contract.
  const initial=await page.evaluate(async()=>{const current=await requestRaw('state');const settings=current.settings;settings.services.codex.added=true;settings.services.codex.enabled=false;settings.services.codex.models=['gpt-6-astra'];await requestRaw('settings',settings);return settings;});
  assert(initial.services.codex.added);await page.reload();await page.waitForFunction(()=>!working&&!!state);
  for(let round=1;round<=10;round++){
   await page.setViewportSize({width:round%2?1280:390,height:900});await page.locator('[data-panel=provedores]').click();
   const enabled=round%2===1;const toggleResponse=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/settings'&&r.request().method()==='POST');
   await page.locator('[data-configured-provider=codex]').getByRole('checkbox').setChecked(enabled);assert.equal((await toggleResponse).status(),200);await page.waitForFunction(()=>!working);
   await page.locator('[data-configured-provider=codex] button[aria-label^="Edit"]').click();
   await page.locator('#provider-cards').getByRole('checkbox',{name:'Preferred provider',exact:true}).setChecked(enabled);
   const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/settings'&&r.request().method()==='POST');await page.click('#save');assert.equal((await response).status(),200);await page.waitForFunction(()=>!working);
   const persisted=JSON.parse(await fs.readFile(path.join(ADMIN_TEST_STATE,'settings.json'),'utf8'));
   assert.equal(persisted.services.codex.enabled,enabled);assert.deepEqual(persisted.services.codex.models,['gpt-6-astra']);
   await page.reload();await page.waitForFunction(()=>!working&&!!state);await page.locator('[data-panel=provedores]').click();
   assert.equal(await page.locator('[data-configured-provider=codex]').getByRole('checkbox').isChecked(),enabled);
   const noCsrf=await page.evaluate(async()=>{const r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});return {status:r.status,body:await r.json()};});assert.equal(noCsrf.status,400);assert.match(noCsrf.body.error,/Administrative header/);
   const foreign=await page.request.post(ADMIN_TEST_URL+'/api/settings',{data:{},headers:{'Origin':'https://foreign.example','X-Harness-Admin':'1'}});assert.equal(foreign.status(),403);
   results.push({round,status:'pass',enabled,save_http:200,csrf_without_header:400,foreign_origin:403,persistence_verified:true});console.log('PASS real admin HTTP round '+round);if(round===10)await page.screenshot({path:'/tmp/tail-admin-gauntlet/live-admin-mobile.png',fullPage:true});
  }
 }finally{await browser.close();await fs.writeFile(ADMIN_TEST_OUTPUT,JSON.stringify(results,null,2)+'\n');}
})().catch(e=>{console.error(e);process.exitCode=1;});
