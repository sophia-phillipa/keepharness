// Actual HTTP admission, stored invocations and SSE; only provider inference is synthetic.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises'), os = require('node:os'), path = require('node:path');
const { spawn } = require('node:child_process');
(async () => {
 const folder=await fs.mkdtemp(path.join(os.tmpdir(),'round9-http-'));
 const proc=spawn(process.env.PYTHON||path.join(__dirname,'../.venv/bin/python'),['-m','tests.maestro_browser_fixture',folder,'round9'],{cwd:path.join(__dirname,'..'),env:{...process.env,HOME:folder},stdio:['ignore','pipe','pipe']});
 let log='',browser;proc.stderr.on('data',s=>log+=s);const failures=[];
 try {
  let ready;for(let i=0;i<100;i++){try{ready=JSON.parse(await fs.readFile(path.join(folder,'ready.json')));if((await fetch('http://127.0.0.1:'+ready.port+'/v1/version')).ok)break;}catch{}if(proc.exitCode!==null)throw Error(log);await new Promise(r=>setTimeout(r,50));}assert(ready,log);
  const origin='http://127.0.0.1:'+ready.port;browser=await chromium.launch();
  async function page(){const context=await browser.newContext();await context.addCookies([{name:'harness_session',value:ready.session,url:origin}]);const p=await context.newPage();p.setDefaultTimeout(8000);await p.addInitScript(v=>localStorage.setItem('keepharness-tour-seen',v),(await fs.readFile(path.join(__dirname,'../agent_service/VERSION'),'utf8')).trim());await p.goto(origin);await p.locator('#startup-gate').waitFor({state:'hidden'});return p;}
  async function check(name,fn){try{await fn();console.log('PASS '+name);}catch(e){failures.push(name+': '+e.stack);console.error('FAIL '+name+': '+e.message);}}
  await check('A5-F1 failed agent and skill retry retains persisted executable identity',async()=>{
   for(const name of ['reviewer','check']) {
    const p=await page();await p.locator('#prompt').fill('/'+name);await p.getByRole('option',{name:new RegExp(name)}).click();await p.locator('#prompt').fill((await p.locator('#prompt').inputValue())+'RETRY-'+name);await p.locator('#send').click();await p.waitForFunction(()=>!submitting&&!busy&&!!parent&&document.querySelector('#prompt').value.includes('RETRY-'));
    const first=await p.evaluate(()=>parent), original=await (await p.request.get(origin+'/v1/jobs/'+first)).json();assert.equal(original.state,'failed');assert.equal(original.request.resource_selections.length,1);assert.equal(await p.locator('.resource-chip').count(),1);
    await p.locator('#send').click();await p.waitForFunction(id=>!submitting&&!busy&&parent!==id,first);const second=await p.evaluate(()=>parent), retry=await(await p.request.get(origin+'/v1/jobs/'+second)).json();assert.equal(retry.state,'completed');assert.deepEqual(retry.request.resource_selections,original.request.resource_selections);assert.deepEqual(retry.request.invocations,original.request.invocations);await p.context().close();
   }
   const p=await page();await p.locator('#prompt').fill('/reviewer');await p.getByRole('option',{name:/reviewer/}).click();await p.locator('#prompt').fill((await p.locator('#prompt').inputValue())+'RETRY-changed');await p.locator('#send').click();await p.waitForFunction(()=>!busy&&!submitting&&!!parent&&document.querySelector('#prompt').value.includes('RETRY-'));
   const old=await p.evaluate(()=>parent);await fs.appendFile(path.join(folder,'project/.codex/agents/reviewer.toml'),'\n# synthetic revision change\n');const response=p.waitForResponse(r=>r.url()===origin+'/v1/jobs'&&r.request().method()==='POST');await p.locator('#send').click();assert.equal((await response).status(),409);assert.equal(await p.evaluate(()=>parent),old);assert.equal(await p.locator('.resource-chip').count(),1);await p.context().close();
  });
  await check('A2-F1 partial SSE manual and online resume replaces lost-connection announcements',async()=>{
   for(const mode of ['manual','online']) {
    const p=await page();const created=await(await p.request.post(origin+'/v1/jobs',{data:{project_id:'sem-projeto',backend:'codex',model:'gpt-6-astra',effort:'low',prompt:'ASK-'+mode}})).json();const id=created.job_id;assert(id);
    let row;for(let i=0;i<100;i++){row=await(await p.request.get(origin+'/v1/jobs/'+id)).json();if(row.gates?.length)break;await p.waitForTimeout(20);}assert(row.gates?.length);
    const events=(await(await p.request.get(origin+'/v1/jobs/'+id+'/events?format=json')).json()).events;const first=events.find(e=>e.type==='running'),gate=events.find(e=>e.type==='gate_required');assert(gate);
    const cursors=[];await p.route('**/v1/jobs/'+id+'/events',route=>{cursors.push(route.request().headers()['last-event-id']);if(cursors.length===1)return route.fulfill({contentType:'text/event-stream',body:'data: '+JSON.stringify(first)+'\n\ndata: '+JSON.stringify(gate).slice(0,40)});if(cursors.length===2)return route.abort('connectionreset');return route.continue();});
    await p.evaluate(id=>{void load(id,true);},id);await p.locator('#resume-execution').waitFor().catch(async e=>{console.error('SSE DIAGNOSTIC',JSON.stringify({cursors,events,row,ui:await p.evaluate(()=>({job,busy,last,loading,streamDisconnected,status:document.querySelector('#status').textContent}))}));throw e;});await p.locator('#prompt').fill('Unsent recovery draft 🐈');await p.locator('#resume-execution').focus();
    if(mode==='manual')await p.keyboard.press('Enter');else await p.evaluate(()=>window.dispatchEvent(new Event('online')));
    await p.waitForFunction(eventId=>!streamDisconnected&&last>=eventId,gate.id);await p.waitForTimeout(100);
    assert.deepEqual(cursors.slice(0,3),['0',String(first.id),String(first.id)]);assert.equal(await p.locator('.gate-card').count(),1);assert.equal(await p.locator('#prompt').inputValue(),'Unsent recovery draft 🐈');assert(await p.locator('#resume-execution').isHidden());assert.equal(await p.evaluate(()=>document.activeElement.id),'prompt');assert.match(await p.locator('#status').innerText(),/Waiting for your choice/);assert.match(await p.locator('#activity-state').innerText(),/Waiting for your choice/);
    await p.request.post(origin+'/v1/jobs/'+id+'/cancel',{data:{}});await p.context().close();
   }
  });
 } finally {if(browser)await browser.close();proc.kill('SIGTERM');await new Promise(resolve=>{if(proc.exitCode!==null)return resolve();proc.once('exit',resolve);setTimeout(()=>proc.kill('SIGKILL'),2000).unref();});await fs.rm(folder,{recursive:true,force:true});}
 if(failures.length){console.error(failures.join('\n'));process.exitCode=1;}
})().catch(e=>{console.error(e);process.exitCode=1;});
