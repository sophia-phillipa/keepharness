// Regression: catalog refresh and asset reload preserve an unsent draft and attachments.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
(async()=>{
 const browser=await chromium.launch();
 try{
  const page=await browser.newPage();let build='one',models=['qwen-test'],loads=0,submissions=0;
  await page.route('http://reload.test/**',async route=>{
   const p=new URL(route.request().url()).pathname;
   if(p.startsWith('/v1/')){
    let data={};
    if(p==='/v1/projects')data={projects:['sem-projeto']};
    if(p==='/v1/models')data={models:models.map(id=>({id,backend:'local',efforts:['configured'],permissions:{upload:true}})),uploads_enabled:true,providers:{local:true}};
    if(p==='/v1/conversations')data={conversations:[]};
    const active={id:'active-job',project:'sem-projeto',state:'running',request:{backend:'local',model:'novo-modelo',prompt:'Pedido anterior'},result:null};
    if(p==='/v1/conversations/active-job')data={title:'Em execução',turns:[active]};
    if(p==='/v1/jobs/active-job')data=active;
    if(p==='/v1/jobs/active-job/events')return route.fulfill({body:'',contentType:'text/event-stream'});
    if(p==='/v1/version')data={version:'test',build};
    if(p==='/v1/jobs'&&route.request().method()==='POST')submissions++;
    return route.fulfill({json:data});
   }
   if(p==='/')loads++;
   const file=p==='/'?'index.html':p.slice(1);
   return route.fulfill({body:await fs.readFile(path.join(__dirname,file.startsWith('assets/')?'../tail_ui':'../agent_service',file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
  });
  await page.goto('http://reload.test');await page.locator('#startup-gate').waitFor({state:'hidden'});
  await page.fill('#prompt','Texto ainda não enviado');
  models=['qwen-test','novo-modelo'];
  await page.evaluate(()=>{readinessRetryAt=0;return probeReadiness();});
  assert.equal(await page.locator('#model option').count(),2);
  assert.equal(await page.locator('#prompt').inputValue(),'Texto ainda não enviado');
  models=['novo-modelo'];await page.evaluate(()=>{readinessRetryAt=0;return probeReadiness();});
  assert.equal(await page.locator('#model').inputValue(),'novo-modelo');
  assert.equal(await page.locator('#prompt').inputValue(),'Texto ainda não enviado');
  await page.evaluate(()=>{files=[{id:'attachment-test',name:'rascunho.txt'}];renderFiles();saveView();});
  await page.evaluate(()=>{window.originalSetItem=Storage.prototype.setItem;Storage.prototype.setItem=()=>{throw Error('storage unavailable');};});
  build='two';await page.evaluate(()=>checkVersion());assert.equal(loads,1,'failed draft persistence must prevent automatic reload');
  await page.evaluate(()=>{Storage.prototype.setItem=window.originalSetItem;});
  await page.evaluate(()=>checkVersion());
  await page.waitForFunction(()=>sessionStorage.getItem('remote-view')?.includes('Texto ainda não enviado'));
  await page.waitForTimeout(500);
  assert.equal(loads,2,'asset update should reload even with a draft and attachments');
  await page.locator('#startup-gate').waitFor({state:'hidden'});
  assert.equal(await page.locator('#prompt').inputValue(),'Texto ainda não enviado');
  assert.equal(await page.evaluate(()=>files[0]?.id),'attachment-test');
  await page.evaluate(()=>{conversation='active-job';saveView();});
  await page.reload();await page.locator('#startup-gate').waitFor({state:'hidden'});
  await page.waitForTimeout(300);
  assert.equal(await page.locator('#prompt').inputValue(),'Texto ainda não enviado','manual reload during a running job must restore draft before the job ends');
  assert.equal(await page.evaluate(()=>files[0]?.id),'attachment-test');
  page.once('dialog',dialog=>dialog.accept());
  await page.locator('#new').click();
  await page.fill('#prompt','Novo rascunho durante acompanhamento anterior');
  await page.waitForTimeout(1200);
  assert.equal(await page.locator('#prompt').inputValue(),'Novo rascunho durante acompanhamento anterior','finishing the restored watcher must not overwrite a new conversation draft');
  assert.equal(submissions,0);
  console.log('PASS: added/removed models and asset reload preserve draft and attachments.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
