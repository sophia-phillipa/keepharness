const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch();
 try{
  const page=await browser.newPage(),errors=[];let code='submission_rate_limit',retry='7',submissions=0;
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/v1/models',r=>r.fulfill({json:{models:[{id:'qwen-local',name:'Qwen local',backend:'local',efforts:['low']}],providers:{local:{}}}}));
  await page.route('**/v1/conversations',r=>r.fulfill({json:{conversations:[]}}));
  const limited=r=>r.fulfill({status:429,headers:retry===null?{}:{'Retry-After':retry},json:{code}});
  await page.route('**/v1/jobs',r=>{submissions++;return limited(r);});
  await page.route('**/v1/fixture-limit',limited);
  await page.goto(process.env.HARNESS_URL||'http://127.0.0.1:8095/');
  for(const [failure,expected] of [['submission_rate_limit','muito rapidamente'],['queue_full','fila do servidor está cheia'],['owner_queue_full','limite de pedidos na fila']]){
   code=failure;await page.fill('#prompt','Preserve este rascunho: '+failure);await page.click('#send');
   await page.waitForFunction(text=>document.querySelector('#status').textContent.includes(text),expected);
   const text=await page.locator('#status').innerText();
   assert(text.includes('7 segundos'));assert(text.includes('rascunho foi preservado'));
   assert.equal(await page.locator('#prompt').inputValue(),'Preserve este rascunho: '+failure);
   assert(!await page.locator('#send').isDisabled());
  }
  // Errors remain visible; rejected submissions are never automatically retried.
  await page.waitForTimeout(5500);assert(await page.locator('#status').isVisible());assert.equal(submissions,3);
  for(const [failure,expected] of [['rate_limit','Muitas consultas'],['login_rate_limit','Muitas tentativas de acesso']]){
   code=failure;retry=new Date(Date.now()+30000).toUTCString();
   const message=await page.evaluate(async()=>{try{await api('/v1/fixture-limit');}catch(e){return e.message;}});
   assert(message.includes(expected));assert.match(message,/Tente novamente em \d+ segundos/);
  }
  code='unknown_limit';retry=null;
  const fallback=await page.evaluate(async()=>{try{await api('/v1/fixture-limit');}catch(e){return e.message;}});
  assert(fallback.includes('limite temporário'));assert(fallback.includes('Aguarde um pouco'));
  retry='invalid';
  const invalid=await page.evaluate(async()=>{try{await api('/v1/fixture-limit');}catch(e){return e.message;}});
  assert(!invalid.includes('NaN'));assert(invalid.includes('Aguarde um pouco'));
  assert.deepEqual(errors,[]);
  console.log('PASS: request/queue/login limits, Retry-After seconds/date/missing/invalid, preserved drafts, persistent errors, no automatic resubmission');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
