// Round-six synthetic browser regressions: actual rendering, keyboard and network boundaries.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
async function capture(page, name) { if (!process.env.EVAL_OUTPUT) return; const folder = path.join(process.env.EVAL_OUTPUT, 'round6'); await fs.mkdir(folder, { recursive: true }); await page.screenshot({ path: path.join(folder, name + '.png') }); }
const { mount, run, span } = require('./run-console-fixture.cjs');
const resource = { id: 'project/p/reviewer', resource_id: 'project/p/reviewer', revision: '1', name: 'reviewer', kind: 'agent', mode: 'delegated', scope: 'project', origin: 'codex', selectable: true };
const plan = { steps: [{ role: 'Reviewer', backend: 'codex', model: 'fixture-model-with-a-long-identity', effort: 'configured', task: 'Review synthetic report' }] };
async function fixture(browser, width = 1024, height = 768) {
  const page = await browser.newPage({ viewport: { width, height } });
  page.setDefaultTimeout(4000);
  const state = { emptySpans: false, resourceDelay: null, rootDelay: null, roots: false, usage: null, completed: false, delay: null, running: false, plan: false, checkpointCount: 0, inbox: [], jobs: [], conflict: false, posts: [], approvalResponse: null, successfulSend: false, missingSource: false, pendingEvents: false };
  const turn = id => ({ id: id + '-job', project: id === 'b' ? 'q' : 'p', state: state.running ? 'running' : state.completed ? 'completed' : 'failed', workflow_checkpoint: true, workflow_completed_steps: state.checkpointCount, gates: state.plan ? [{ gate_id: 'plan', kind: 'maestro_plan', state: 'pending', plan }] : [], request: { prompt: 'Review ' + id, backend: 'codex', model: 'fixture-model', effort: 'configured', execution_mode: 'native' }, result: state.running ? null : { answer: 'Synthetic answer', ...(state.completed ? {} : {error:'fixture'}), ...(state.usage ? {metrics:state.usage} : {}) } });
  await mount(page, async (url, request) => {
    if (request.method() === 'POST' && state.missingSource && url.pathname.endsWith('/resume')) return {status:422,json:{code:'workflow_source_unavailable'}};
    if (request.method() === 'POST' && state.successfulSend && url.pathname === '/v1/jobs') { state.posts.push({path:url.pathname,data:request.postDataJSON()}); return {json:{job_id:'child'}}; }
    if (request.method() === 'POST') { if (state.conflict) return {status:409,json:{code:'gate_already_resolved'}}; state.posts.push({ path: url.pathname, data: request.postDataJSON() }); if (state.approvalResponse) await state.approvalResponse; return { status: state.approvalResponse ? 200 : 422, json: state.approvalResponse ? {resolved:true} : {error:'synthetic_stop'} }; }
    if (url.pathname === '/v1/projects') return { json: { projects: ['p', 'q'], details: { p: { label: 'Project P' }, q: { label: 'Project Q' } } } };
    if (url.pathname === '/v1/models') return { json: { models: [{ id: 'fixture-model', backend: 'codex', efforts: ['configured'] }], providers: { codex: true } } };
    if (url.pathname === '/v1/resources' && url.searchParams.get('project_id') === 'p' && state.resourceDelay) await state.resourceDelay;
    if (url.pathname === '/v1/resources') return { json: { items: [{...resource,name:'reviewer-' + url.searchParams.get('project_id')}], warnings: [] } };
    if (url.pathname === '/v1/activity') return { json: { counts: { running: 1 }, jobs: [...state.jobs, { ...run, job_id: 'a-job', conversation_id: 'a', project_id: 'p', backend: 'codex', wait_reason: 'human_approval' }], needs_you: state.inbox.length ? state.inbox : state.plan ? [{ job_id:'a-job', conversation_id:'a', gate_id:'plan', kind:'maestro_plan', options:[{id:'approve',label:'Approve'},{id:'deny',label:'Discard'}], plan }] : [], providers: [] } };
    if (url.pathname === '/v1/conversations') return { json: { conversations: ['a','b'].map(id => ({ id, title: id.toUpperCase() + ' report', state: 'failed', project: 'p', last_job_id: id + '-job' })) } };
    if (/^\/v1\/conversations\/(a|b|child)$/.test(url.pathname)) {
      if (state.delay) await state.delay;
      const id = url.pathname.split('/').at(-1);
      return { json: { title: id.toUpperCase() + ' report', turns: [turn(id)] } };
    }
    if (url.pathname.endsWith('/cancel')) { state.running = false; return { json: { cancelled: true } }; }
    if (/^\/v1\/jobs\/.*-job$/.test(url.pathname)) return { json: turn(url.pathname.split('/').at(-1).replace('-job','')) };
    if (url.pathname.endsWith('/spans') && state.emptySpans) return {json:{spans:[]}};
    if (url.pathname.endsWith('/spans')) return { json: { spans: ['Planner', 'Accessibility and interaction reviewer', 'Implementation and integration engineer'].map((name, i) => ({ ...span, start_ts: 10 + i, end_ts: 20 + i, name, span_id: 'span-' + i, attrs: { ...span.attrs, 'gen_ai.provider.name': 'codex', 'gen_ai.request.model': 'fixture-model', effort: 'medium' } })) } };
    if (url.pathname === '/v1/project-files' && url.searchParams.get('project_id') === 'p' && state.rootDelay) await state.rootDelay;
    if (url.pathname === '/v1/project-files' && url.searchParams.get('view') === 'authorized') return {json:{state:'ready',roots:state.roots?[{id:'root',path:'/synthetic/' + url.searchParams.get('project_id')}]:[]}};
    if (url.pathname === '/v1/project-files') return {json:{state:'ready',root_id:'home', path:url.searchParams.get('path')||'', roots:[{id:'home',label:'Local Folders'}], entries:url.searchParams.get('path')?[]:[{name:'examples',path:'examples',type:'directory'}]}};
    if (url.pathname.endsWith('/events') && state.pendingEvents) await new Promise(()=>{});
    if (url.pathname.endsWith('/events')) return { body: '', contentType: 'text/event-stream' };
  });
  async function open(id) { if (width <= 620) await page.locator('#menu').click(); await page.locator('#sidebar .conversation-row > button').filter({ hasText: id.toUpperCase() + ' report' }).click(); await page.waitForFunction(title => !loading && document.querySelector('#conversation-title').textContent === title, id.toUpperCase() + ' report'); }
  return { page, open, state };
}
const hit = locator => locator.evaluate(node => { const r = node.getBoundingClientRect(); const h = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); return h === node || node.contains(h); });
const settle = page => page.evaluate(() => Promise.all(document.getAnimations().filter(a => a.effect?.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {}))));

(async () => {
 const browser = await chromium.launch(), failures = [];
 async function check(name, fn) { if(process.env.ONLY && !process.env.ONLY.split(',').some(id=>name.startsWith(id)))return; try {await fn(); console.log('PASS '+name);} catch(error) {failures.push(name+': '+error.stack);console.error('FAIL '+name+': '+error.message);} }
 async function pending() {const f=await fixture(browser,1280,720);f.state.plan=true;f.state.running=true;await f.open('a');await f.page.evaluate(()=>runConsole.openRun('a-job'));await f.page.locator('.run-span-row').nth(2).waitFor();return f;}
 try {
 await check('A1-F1 right edge has pointer ownership',async()=>{
  for(const width of [400,1024,1280,1440]){const {page}=await fixture(browser,width,812);await settle(page);
   const bounds=await page.locator('#panel-toggle').evaluate(n=>{const r=n.getBoundingClientRect(),root=document.documentElement.getBoundingClientRect();return {right:r.right,limit:root.right,hit:n.contains(document.elementFromPoint(r.right-1,r.y+r.height/2))};});assert(bounds.right<=bounds.limit,JSON.stringify(bounds));assert(bounds.hit,JSON.stringify(bounds));assert(await page.locator('.run-status-shortcut').evaluate(n=>{const r=n.getBoundingClientRect();return r.right<=document.documentElement.getBoundingClientRect().right&&n.contains(document.elementFromPoint(r.right-1,r.y+r.height/2));}));await page.close();}
 });
 await check('A1-F2 Timeline default decisions and cards fit',async()=>{
  for(const theme of ['porcelain','amethyst','petroleum']){const {page}=await pending();await page.evaluate(t=>HarnessTheme.apply(t),theme);await page.getByRole('tab',{name:'Timeline',exact:true}).click();await settle(page);
   const g=await page.locator('.run-console-body').evaluate(body=>({scroll:body.scrollTop,bottom:body.getBoundingClientRect().bottom,items:[...body.querySelectorAll('.run-span-row,.run-plan-actions')].map(n=>({bottom:n.getBoundingClientRect().bottom,text:n.textContent}))}));assert.equal(g.scroll,0);for(const item of g.items)assert(item.bottom<=g.bottom+1,JSON.stringify(g));for(const b of await page.locator('.run-plan-actions button').all())assert(await hit(b));await capture(page,'timeline-'+theme);await page.close();}
 });
 await check('A1-F3 draft and placeholder remeasure after width changes',async()=>{
  for(const value of ['This synthetic draft has enough text to wrap onto a second line in the desktop chat column.','']){const {page}=await fixture(browser,1280,720);await page.locator('#prompt').fill(value);await page.setViewportSize({width:400,height:812});await page.waitForTimeout(500);await page.setViewportSize({width:1280,height:720});await page.waitForTimeout(500);assert.equal(await page.locator('#prompt').inputValue(),value);const sizes=await page.locator('#prompt').evaluate(n=>[n.clientHeight,n.scrollHeight]);assert(sizes[0]>=Math.min(sizes[1],166),JSON.stringify(sizes));await page.close();}
 });
 await check('A1-F4 Timeline zoom keeps duration bars in their cards',async()=>{
  const {page}=await pending();await page.getByRole('tab',{name:'Timeline',exact:true}).click();const zoom=page.getByRole('slider',{name:'Timeline zoom'});await zoom.focus();await page.keyboard.press('Home');const before=await page.locator('.run-waterfall-track').first().boundingBox();await page.keyboard.press('End');const after=await page.locator('.run-waterfall-track').first().boundingBox();assert(after.width>before.width);await page.locator('.run-span-list').evaluate(list=>{const [a,b]=list.querySelectorAll('.run-span-row');list.scrollLeft+=(a.getBoundingClientRect().right+b.getBoundingClientRect().left)/2-(list.getBoundingClientRect().left+list.clientWidth/2);});const g=await page.locator('.run-span-list').evaluate(list=>{const [a,b]=list.querySelectorAll('.run-span-row'),r=a.getBoundingClientRect(),next=b.getBoundingClientRect(),bar=a.querySelector('.run-waterfall-bar').getBoundingClientRect(),track=a.querySelector('.run-waterfall-track').getBoundingClientRect();return {right:r.right,track:track.right,bar:bar.right,gap:document.elementFromPoint((r.right+next.left)/2,bar.y+bar.height/2)?.className};});assert(g.track<=g.right&&g.bar<=g.right,JSON.stringify(g));assert(g.gap&&!g.gap.includes('waterfall'),JSON.stringify(g));await capture(page,'timeline-zoom');await page.close();
 });
 await check('A2-F1 Runs poll preserves focus selection and row actions',async()=>{
  for(const width of [1440,400]){const f=await fixture(browser,width,900),{page,state}=f;await page.keyboard.press('Control+j');await page.getByRole('tab',{name:'Runs',exact:true}).click();const input=page.locator('#console-work-item');await input.fill('unsent filter');await input.evaluate(n=>n.setSelectionRange(2,5));state.jobs.push({...run,job_id:'new-job',project_id:'p'});await page.waitForTimeout(4500);assert(await input.evaluate(n=>n===document.activeElement));assert.deepEqual(await input.evaluate(n=>[n.selectionStart,n.selectionEnd]),[2,5]);await page.keyboard.type('X');assert.equal(await input.inputValue(),'unXt filter');const action=page.getByRole('button',{name:'a-job',exact:true});await action.focus();state.jobs.push({...run,job_id:'another-job',project_id:'p'});await page.waitForTimeout(4500);assert(await action.evaluate(n=>n===document.activeElement));await page.close();}
 });
 await check('A2-F2 reflow keyboard filters are above status strip',async()=>{
  const {page}=await fixture(browser,720,450);await page.locator('#prompt').fill('ordinary draft');await page.keyboard.press('Control+j');for(let i=0;i<3;i++)await page.keyboard.press('ArrowRight');for(let i=0;i<2;i++)await page.keyboard.press('Tab');for(const selector of ['#console-project','#console-work-item']){await page.keyboard.press('Tab');const c=page.locator(selector);assert(await c.evaluate(n=>n===document.activeElement));assert(await hit(c),JSON.stringify(await c.evaluate(n=>{const r=n.getBoundingClientRect();return {rect:r.toJSON(),hit:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.outerHTML,drawer:document.querySelector("#run-console").getBoundingClientRect().toJSON(),main:document.querySelector("main").getBoundingClientRect().toJSON(),body:document.querySelector(".run-console-body").getBoundingClientRect().toJSON(),bodyScroll:document.querySelector(".run-console-body").scrollTop,scroll:document.querySelector("main").scrollTop};})));const r=await c.boundingBox(),strip=await page.locator('.run-status-strip').boundingBox();assert(r.y+r.height<=strip.y,JSON.stringify({r,strip}));}await page.close();
 });
 await check('A2-F3 Shift Tab preserves resource draft',async()=>{
  const {page}=await fixture(browser,1280,720);await page.locator('#prompt').fill('/');await page.locator('#resource-menu [role=option]').first().waitFor();await page.keyboard.press('Shift+Tab');assert.equal(await page.locator('#prompt').inputValue(),'/');assert(!(await page.locator('#prompt').evaluate(n=>n===document.activeElement)));await page.locator('#prompt').focus();await page.locator('#prompt').fill('');await page.locator('#prompt').fill('/');await page.locator('#resource-menu [role=option]').first().waitFor();await page.keyboard.press('ArrowDown');await page.keyboard.press('Shift+Tab');assert.equal(await page.locator('#prompt').inputValue(),'/');await page.locator('#prompt').focus();await page.locator('#prompt').fill('');await page.locator('#prompt').fill('/');await page.locator('#resource-menu [role=option]').first().waitFor();await page.keyboard.press('Tab');assert.match(await page.locator('#prompt').inputValue(),/^\/reviewer-/);await page.close();
 });
 await check('A2-F4 mobile modal cycle includes owned toolbar',async()=>{
  for(const opener of ['#menu','#panel-toggle','#run-status-toggle']){const {page}=await fixture(browser,400,812);await page.locator(opener).click();for(const key of ['Tab','Shift+Tab']){const ids=new Set();for(let i=0;i<45;i++){await page.keyboard.press(key);ids.add(await page.evaluate(()=>document.activeElement.id));assert(!await page.evaluate(()=>document.activeElement.closest('[inert]')));}assert(ids.has('search-conversations'),[opener,key,[...ids]].join(' '));assert(ids.has('menu'));}await page.keyboard.press('Escape');await page.close();}
 });
 await check('A5-F1 tour repositions across desktop mobile round trip',async()=>{
  const {page}=await fixture(browser,1440,900);await page.evaluate(()=>window.keepHarnessTour.start());while(await page.locator('#tour-title').innerText()!=='Conversations by state')await page.locator('#tour-next').click();for(const size of [{width:390,height:844},{width:1440,height:900}]){await page.setViewportSize(size);await settle(page);await page.waitForTimeout(120);for(const b of await page.locator('#tour-card button:enabled').all())assert(await hit(b));await page.locator('#tour-next').click();}await page.close();
 });
 await check('A5-F2 completed run clears prior failure and usage',async()=>{
  const f=await fixture(browser,1280,720);await f.open('a');assert.match(await f.page.locator('#context-meter').innerText(),/did not finish/);f.state.completed=true;await f.open('b');assert.match(await f.page.locator('#context-meter').innerText(),/token usage not reported/i);assert.doesNotMatch(await f.page.locator('#context-meter').innerText(),/did not finish|cancelled/);f.state.usage={input_tokens:456,output_tokens:123};await f.open('a');f.state.usage=null;await f.open('b');assert.doesNotMatch(await f.page.locator('#context-meter').innerText(),/456|123/);await f.page.close();
 });
 await check('A5-F3 history refreshes project resources and roots',async()=>{
  const f=await fixture(browser,1440,900);f.state.roots=true;for(const [id,project] of [['b','q'],['a','p']]){await f.open(id);await f.page.waitForFunction(p=>document.querySelector('#workspace-resources').textContent.includes('reviewer-'+p)&&document.querySelector('#authorized-project-roots').textContent.includes('/synthetic/'+p),project);}await f.open('b');let release;f.state.resourceDelay=f.state.rootDelay=new Promise(resolve=>release=resolve);await f.open('a');await f.open('b');await f.page.waitForFunction(()=>document.querySelector('#workspace-resources').textContent.includes('reviewer-q')&&document.querySelector('#authorized-project-roots').textContent.includes('/synthetic/q'));release();await f.page.waitForTimeout(100);assert.match(await f.page.locator('#workspace-resources').innerText(),/reviewer-q/);assert.match(await f.page.locator('#authorized-project-roots').innerText(),/synthetic\/q/);await f.page.close();
 });
 await check('R6-V1 a prior conversation plan never shows on another run, even with empty spans',async()=>{
  const f=await fixture(browser,1440,900);f.state.emptySpans=true;f.state.plan=true;f.state.running=true;await f.open('a');await f.page.evaluate(()=>runConsole.openRun('a-job'));await f.page.waitForFunction(()=>document.querySelector('#console-run').value==='a-job');assert.equal(await f.page.locator('.run-plan-approval').count(),0,'a pending plan is read-only and lives in Needs you, not in the console');f.state.plan=false;await f.open('b');await f.page.evaluate(()=>runConsole.observe({type:'gate_required'}));await f.page.waitForFunction(()=>document.querySelector('#console-run').value==='b-job');assert.doesNotMatch(await f.page.locator('.run-console-body').innerText(),/Review synthetic report/,'Prior conversation plan must be cleared even when both span snapshots are empty');await f.page.close();
 });
 } finally {await browser.close();}
 if(failures.length){console.error(failures.join('\n'));process.exitCode=1;}
})();
