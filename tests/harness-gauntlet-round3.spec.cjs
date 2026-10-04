// Round-three synthetic browser regressions: actual rendering, keyboard and network boundaries.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
async function capture(page, name) { if (!process.env.EVAL_OUTPUT) return; const folder = path.join(process.env.EVAL_OUTPUT, 'round3'); await fs.mkdir(folder, { recursive: true }); await page.screenshot({ path: path.join(folder, name + '.png') }); }
const { mount, run, span } = require('./run-console-fixture.cjs');
const resource = { id: 'project/p/reviewer', resource_id: 'project/p/reviewer', revision: '1', name: 'reviewer', kind: 'agent', mode: 'delegated', scope: 'project', origin: 'codex', selectable: true };
const plan = { steps: [{ role: 'Reviewer', backend: 'codex', model: 'fixture-model-with-a-long-identity', effort: 'configured', task: 'Review synthetic report' }] };
async function fixture(browser, width = 1024, height = 768) {
  const page = await browser.newPage({ viewport: { width, height } });
  page.setDefaultTimeout(4000);
  await page.addInitScript(() => localStorage.setItem('activity-open', '1'));
  const state = { delay: null, running: false, plan: false, checkpointCount: 0, posts: [], approvalResponse: null };
  const turn = id => ({ id: id + '-job', project: 'p', state: state.running ? 'running' : 'failed', workflow_checkpoint: true, workflow_completed_steps: state.checkpointCount, gates: state.plan ? [{ gate_id: 'plan', kind: 'maestro_plan', state: 'pending', plan }] : [], request: { prompt: 'Review ' + id, backend: 'codex', model: 'fixture-model', effort: 'configured', execution_mode: 'native' }, result: state.running ? null : { answer: 'Synthetic answer', error: 'fixture' } });
  await mount(page, async (url, request) => {
    if (request.method() === 'POST') { state.posts.push({ path: url.pathname, data: request.postDataJSON() }); if (state.approvalResponse) await state.approvalResponse; return { status: state.approvalResponse ? 200 : 422, json: state.approvalResponse ? {resolved:true} : {error:'synthetic_stop'} }; }
    if (url.pathname === '/v1/projects') return { json: { projects: ['p', 'q'], details: { p: { label: 'Project P' }, q: { label: 'Project Q' } } } };
    if (url.pathname === '/v1/models') return { json: { models: [{ id: 'fixture-model', backend: 'codex', efforts: ['configured'] }, { id: plan.steps[0].model, backend: 'codex', efforts: ['configured'] }], providers: { codex: true } } };
    if (url.pathname === '/v1/resources') return { json: { items: [resource], warnings: [] } };
    if (url.pathname === '/v1/activity') return { json: { counts: { running: 1 }, jobs: [{ ...run, job_id: 'a-job', conversation_id: 'a', project_id: 'p', backend: 'codex' }], needs_you: state.plan ? [{ job_id:'a-job', conversation_id:'a', gate_id:'plan', kind:'maestro_plan', plan }] : [], providers: [] } };
    if (url.pathname === '/v1/conversations') return { json: { conversations: ['a','b'].map(id => ({ id, title: id.toUpperCase() + ' report', state: 'failed', project: 'p', last_job_id: id + '-job' })) } };
    if (/^\/v1\/conversations\/(a|b|child)$/.test(url.pathname)) {
      if (state.delay) await state.delay;
      const id = url.pathname.split('/').at(-1);
      return { json: { title: id.toUpperCase() + ' report', turns: [turn(id)] } };
    }
    if (url.pathname.endsWith('/cancel')) { state.running = false; return { json: { cancelled: true } }; }
    if (/^\/v1\/jobs\/.*-job$/.test(url.pathname)) return { json: turn(url.pathname.split('/').at(-1).replace('-job','')) };
    if (url.pathname.endsWith('/spans')) return { json: { spans: ['Planner', 'Accessibility and interaction reviewer', 'Implementation and integration engineer'].map((name, i) => ({ ...span, start_ts: Date.now()/1000-12, name, span_id: 'span-' + i, attrs: { ...span.attrs, 'gen_ai.provider.name': 'codex', 'gen_ai.request.model': 'fixture-model', effort: 'medium' } })) } };
    if (url.pathname.endsWith('/events')) return { body: '', contentType: 'text/event-stream' };
  });
  async function open(id) { if (width <= 620) await page.locator('#menu').click(); await page.locator('#sidebar .conversation-row > button').filter({ hasText: id.toUpperCase() + ' report' }).click(); await page.waitForFunction(title => !loading && document.querySelector('#conversation-title').textContent === title, id.toUpperCase() + ' report'); }
  return { page, open, state };
}
const hit = locator => locator.evaluate(node => { const r = node.getBoundingClientRect(); const h = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); return h === node || node.contains(h); });
const settle = page => page.evaluate(() => Promise.all(document.getAnimations().filter(a => a.effect?.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {}))));
(async () => {
  const browser = await chromium.launch();
  const failures = [];
  async function check(name, fn) { if (process.env.ONLY && !process.env.ONLY.split(',').some(id => name.startsWith(id))) return; try { await fn(); console.log('PASS ' + name); } catch (error) { failures.push(name + ': ' + error.stack); console.error('FAIL ' + name + ': ' + error.message); } }
  async function pendingPlan(width=1024, height=768) { const f=await fixture(browser,width,height); f.state.plan=true; f.state.running=true; await f.open('a'); await f.page.evaluate(()=>runConsole.openRun('a-job')); await f.page.keyboard.press('Control+j'); return f; }
  try {
    await check('A1-F1 plan labels have separate hit targets', async () => {
      for (const theme of ['porcelain','amethyst','petroleum']) {
        const {page,state}=await pendingPlan(); await page.evaluate(t=>HarnessTheme.apply(t),theme);
        const actions=page.locator('.maestro-plan-actions'); await actions.evaluate(n=>n.scrollIntoView({block:'center',behavior:'instant'})); await settle(page);
        const points=await actions.locator('button').evaluateAll(nodes=>nodes.map(n=>{const range=document.createRange();range.selectNodeContents(n);const r=range.getBoundingClientRect(),box=n.getBoundingClientRect(); return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,box:box.toJSON(),hit:[r.left+1,r.right-1].every(x=>n.contains(document.elementFromPoint(x,r.top+r.height/2)))};}));
        assert(points.every(p=>p.left>=p.box.left && p.right<=p.box.right && p.hit), JSON.stringify(points));
        assert(points[0].right<=points[1].left || points[0].bottom<=points[1].top, JSON.stringify(points));await capture(page,'plan-actions-'+theme);
        await actions.locator('button').first().click(); assert.equal(state.posts[0].data.choice,'approve');
        await actions.locator('button').last().click(); await page.getByRole('textbox',{name:'Task for step 1'}).waitFor(); await page.close();
      }
    });
    await check('A1-F2 pending approval and descriptive pipeline fit', async () => {
      for (const [width,height] of [[1440,900],[1280,720],[1024,768],[400,812]]) {
        const {page}=await pendingPlan(width,height); await page.keyboard.press('Control+j'); await page.locator('.run-span-row').nth(2).waitFor(); await settle(page);
        for (const maximize of [false,true,false]) {
          if (width>700 && maximize !== (await page.locator('#run-console-maximize').innerText()==='Restore')) await page.locator('#run-console-maximize').click();
          const boxes=await page.evaluate(()=>[...document.querySelectorAll('.run-span-row')].map(n=>({bottom:n.getBoundingClientRect().bottom,limit:n.closest('.run-console-body').getBoundingClientRect().bottom,scroll:n.closest('.run-console-body').scrollTop})));
          assert.equal(boxes.length,3);
          assert(boxes.every(b=>b.scroll===0 && b.bottom<=b.limit),width+': '+JSON.stringify(boxes));
          await page.locator('.run-span-list').evaluate(n=>n.scrollLeft=185); assert(await page.evaluate(()=>{const node=document.querySelectorAll('.run-span-row')[1].querySelector('.run-span-tokens'),r=node.getBoundingClientRect();return node.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}));await capture(page,'pipeline-'+width+'-'+maximize);
        }
        await page.close();
      }
    });
    await check('A1-F3 running follow-up does not put Send over Access', async () => {
      for (const theme of ['porcelain','amethyst','petroleum']) {
        const {page,state,open}=await fixture(browser);state.running=true;await open('a'); await page.evaluate(t=>HarnessTheme.apply(t),theme); await page.fill('#prompt','Please also review spacing.');
        const boxes=await page.locator('#access-trigger,#send,#cancel').evaluateAll(nodes=>nodes.filter(n=>n.checkVisibility()).map(n=>({id:n.id,...n.getBoundingClientRect().toJSON()})));
        for(let i=0;i<boxes.length;i++) for(let j=i+1;j<boxes.length;j++) assert(Math.min(boxes[i].right,boxes[j].right)<=Math.max(boxes[i].left,boxes[j].left) || Math.min(boxes[i].bottom,boxes[j].bottom)<=Math.max(boxes[i].top,boxes[j].top),JSON.stringify(boxes));
        assert(await hit(page.locator('#access-trigger')));await capture(page,'composer-'+theme); const b=await page.locator('#access-trigger').boundingBox(); await page.mouse.click(b.x+b.width/2,b.y+b.height/2); assert.equal(state.posts.length,0); await page.close();
      }
    });
    await check('A2-F1 storage quota retains latest draft and recovers', async () => {
      const {page,open}=await fixture(browser);await open('a');await page.fill('#prompt','Initial saved draft');
      const errors=await page.evaluate(()=>{let errors=0,i=0;for(const size of [1000000,100000,10000,1000,100,1]){try{for(let j=0;j<10000;j++)sessionStorage.setItem('quota-'+i++,'x'.repeat(size));}catch(e){if(e.name==='QuotaExceededError')errors++;else throw e;}}return errors;});assert(errors>0);
      const exact='Modified draft that must survive switching.';await page.fill('#prompt',exact);await open('b');await open('a');assert.equal(await page.inputValue('#prompt'),exact);
      await page.evaluate(()=>{for(const key of Object.keys(sessionStorage))if(key.startsWith('quota-'))sessionStorage.removeItem(key);});await page.fill('#prompt',exact+' Recovered.');await open('b');await open('a');assert.equal(await page.inputValue('#prompt'),exact+' Recovered.');await page.close();
    });
    await check('A2-F2 mobile console contains forward and reverse focus', async () => {
      for(const width of [400,640]){const {page}=await fixture(browser,width,812);await page.fill('#prompt','Visible draft');await page.keyboard.press('Control+j');await settle(page);
        for(const key of [...Array(14).fill('Shift+Tab'),...Array(14).fill('Tab')]){await page.keyboard.press(key);assert(await page.evaluate(selector => { const panel=document.querySelector(selector), active=document.activeElement; const surfaces=[panel,...(panel.getAttribute('aria-owns')||'').split(/\s+/).filter(Boolean).map(id=>document.getElementById(id))]; return !active.closest('[inert]') && surfaces.some(surface=>surface?.contains(active)); }, '#run-console'));assert(await hit(page.locator(':focus')),await page.locator(':focus').evaluate(n=>{const r=n.getBoundingClientRect();return JSON.stringify({html:n.outerHTML,rect:r.toJSON(),hit:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.outerHTML});}));}
        await page.keyboard.press('Escape');assert(await hit(page.locator(':focus')),await page.locator(':focus').evaluate(n=>{const r=n.getBoundingClientRect();return JSON.stringify({html:n.outerHTML,rect:r.toJSON(),hit:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.outerHTML});}));await page.close();}
    });
    await check('A2-F3 composer shortcut never types behind sidebar', async () => {
      const {page}=await fixture(browser,400,812);await page.locator('#menu').click();await page.keyboard.press('Control+/');await page.keyboard.type('Visible typing');assert(await hit(page.locator(':focus')));assert(await page.evaluate(()=>!document.querySelector('#sidebar').getAttribute('aria-modal') || document.querySelector('#sidebar').contains(document.activeElement)));await page.close();
    });
    await check('A2-F4 remote authorization resolution restores focus and announces', async () => {
      const {page,state,open}=await fixture(browser);state.running=true;await open('a');
      await page.evaluate(()=>event({id:101,type:'approval_required',data:{approval_id:'remote',request:{tool_name:'shell',input:{command:'synthetic'}}}}));const allow=page.locator('#approval-remote button').first();await allow.focus();
      await page.evaluate(()=>event({id:102,type:'approval_resolved',data:{approval_id:'remote',approved:true}}));assert(await hit(page.locator(':focus')));assert.match(await page.locator('#status').innerText(),/approval.*(resolved|recorded|approved)/i);await page.close();
    });
    await check('A2-F5 Escape follows mobile sidebar focus ownership', async () => {
      for(const reversed of [false,true]){const {page}=await fixture(browser,400,812);await page.evaluate(r=>document.body.classList.toggle('panel-order-reversed',r),reversed);await page.locator('#panel-toggle').click();await page.locator('#menu').click();await page.locator('#new').focus();await page.keyboard.press('Escape');assert(!(await page.locator('#sidebar').getAttribute('class')).split(' ').includes('open'));assert(await page.locator('#activity-panel').isVisible());assert(await hit(page.locator(':focus')));await page.close();}
    });
    await check('A2-F3 console shortcut respects active mobile modal', async () => {
      for (const panel of ['sidebar','activity-panel']) { const {page}=await fixture(browser,400,812);await page.locator(panel==='sidebar'?'#menu':'#panel-toggle').click();await page.evaluate(()=>document.addEventListener('keydown',e=>{if(e.ctrlKey&&e.key==='j')window.consoleShortcutPrevented=e.defaultPrevented;}));await page.keyboard.press('Control+j');assert.equal(await page.evaluate(()=>window.consoleShortcutPrevented),true);assert(await page.locator('#'+panel).evaluate(n=>n.contains(document.activeElement)));assert(await hit(page.locator(':focus')));assert(await page.locator('#run-console').isHidden());await page.close(); }
    });
    await check('A2-F6 mobile sidebar dialog is named', async () => {const {page}=await fixture(browser,400,812);await page.locator('#menu').click();await page.getByRole('dialog',{name:'Conversations',exact:true}).waitFor();await page.close();});
    await check('A5-F1 New retains selected invocation semantics', async () => {
      const {page,state,open}=await fixture(browser);await open('a');await page.fill('#prompt','/reviewer');await page.locator('#resource-menu [data-resource-id="project/p/reviewer"]').click();await page.keyboard.type(' inspect synthetic draft');const draft=await page.inputValue('#prompt');await page.locator('#new').click();assert.equal(await page.inputValue('#prompt'),draft);assert.equal(await page.locator('.resource-chip').count(),1);await page.locator('#send').click();await page.waitForFunction(()=>!submitting);assert.deepEqual(state.posts.at(-1).data.resource_selections.map(r=>({id:r.id,revision:r.revision})),[{id:resource.id,revision:resource.revision}]);await page.close();
    });
    await check('A5-F2 plan edits survive tabs hide and telemetry', async () => {
      const {page,state}=await pendingPlan();await page.locator('.maestro-plan-actions button').last().click();const editor=page.getByRole('textbox',{name:'Task for step 1'});await editor.waitFor();const exact='Only summarize; do not rewrite the synthetic report';await editor.fill(exact);
      await page.getByRole('tab',{name:'Logs',exact:true}).click();await page.getByRole('tab',{name:'Pipeline',exact:true}).click();assert.equal(await editor.inputValue(),exact);
      await page.getByRole('button',{name:'Hide editor',exact:true}).focus();await page.keyboard.press('Enter');assert.equal(await page.locator(':focus').innerText(),'Edit plan');await settle(page);assert(await hit(page.locator(':focus')),JSON.stringify(await page.locator(':focus').evaluate(n=>({text:n.textContent,rect:n.getBoundingClientRect().toJSON(),scroll:n.closest('.run-console-body').scrollTop}))));await page.keyboard.press('Enter');assert.equal(await page.locator(':focus').innerText(),'Hide editor');await settle(page);assert(await hit(page.locator(':focus')),JSON.stringify(await page.locator(':focus').evaluate(n=>({text:n.textContent,rect:n.getBoundingClientRect().toJSON(),scroll:n.closest('.run-console-body').scrollTop}))));assert.equal(await editor.inputValue(),exact);
      await page.waitForTimeout(2200);assert.equal(await editor.inputValue(),exact);await page.locator('.run-plan-actions button').filter({hasText:'Run with edits'}).click();assert.equal(state.posts.at(-1).data.plan.steps[0].task,exact);await page.close();
    });
    await check('A5-F3 late approval cannot replace terminal outcome', async () => {
      for(const outcome of ['completed','failed']){const {page,state}=await pendingPlan();let release;state.approvalResponse=new Promise(r=>release=r);await page.locator('.maestro-plan-actions button').first().click();await page.waitForFunction(()=>document.querySelector('.maestro-plan-actions button').disabled);
        await page.evaluate(outcome=>{event({id:101,type:'gate_resolved',data:{gate_id:'plan',choice:'approve'}});result(job,controller,{state:outcome,result:{answer:'Synthetic terminal answer'}});},outcome);
        release();await page.waitForTimeout(100);assert.match(await page.locator('.maestro-plan-card .state-pill').innerText(),new RegExp(outcome,'i'));await page.close();}
    });
    await check('A5-F4 publication expiry and invalidation retain identity', async () => {
      const {page,open}=await fixture(browser);await open('a');for(const state of ['expired','invalidated']){await page.evaluate(state=>{showGate({gate_id:state,kind:'publish',publish:true,effect_id:'effect-'+state,operation:'jira.create_issue',destination:'TEST',artifact_preview:'Synthetic evidence',options:[{id:'approve',label:'Approve'},{id:'deny',label:'Deny'}]});finishGate(state,state);},state);const gate=page.locator('#gate-'+state);assert.match(await gate.innerText(),/Publication (approval )?(expired|closed)/);assert.match(await gate.innerText(),/fresh approval/i);assert.match(await gate.innerText(),/Synthetic evidence/);assert.equal(await gate.locator('button:enabled').count(),0);}await page.close();
    });
    await check('A5-F5 recovery distinguishes zero and completed checkpoints', async () => {
      const {page,state,open}=await fixture(browser);await open('a');assert.match(await page.locator('.workflow-recovery').innerText(),/no completed|first step/i);state.checkpointCount=1;await open('b');assert.match(await page.locator('.workflow-recovery').innerText(),/1 completed step/);assert.equal(await page.getByRole('button',{name:'Resume workflow',exact:true}).count(),1);await page.close();
    });
    assert.deepEqual(failures,[]);
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});
