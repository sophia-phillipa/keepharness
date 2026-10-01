// Round-two synthetic browser regressions: actual rendering, keyboard and network boundaries.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const { mount, run, span } = require('./run-console-fixture.cjs');
const resource = { id: 'project/p/reviewer', resource_id: 'project/p/reviewer', revision: '1', name: 'reviewer', kind: 'agent', mode: 'delegated', scope: 'project', origin: 'codex', selectable: true };
const plan = { steps: [{ role: 'Reviewer', backend: 'codex', model: 'fixture-model-with-a-long-identity', effort: 'configured', task: 'Review synthetic report' }] };
async function fixture(browser, width = 1024, height = 768) {
  const page = await browser.newPage({ viewport: { width, height } });
  page.setDefaultTimeout(4000);
  await page.addInitScript(() => localStorage.setItem('activity-open', '1'));
  const state = { delay: null, running: false };
  const turn = id => ({ id: id + '-job', project: 'p', state: state.running ? 'running' : 'failed', workflow_checkpoint: true, request: { prompt: 'Review ' + id, backend: 'codex', model: 'fixture-model', effort: 'configured', execution_mode: 'native' }, result: state.running ? null : { answer: 'Synthetic answer', error: 'fixture' } });
  await mount(page, async (url, request) => {
    if (url.pathname === '/v1/projects') return { json: { projects: ['p', 'q'], details: { p: { label: 'Project P' }, q: { label: 'Project Q' } } } };
    if (url.pathname === '/v1/models') return { json: { models: [{ id: 'fixture-model', backend: 'codex', efforts: ['configured'] }], providers: { codex: true } } };
    if (url.pathname === '/v1/resources') return { json: { items: [resource], warnings: [] } };
    if (url.pathname === '/v1/activity') return { json: { counts: { running: 1 }, jobs: [{ ...run, project_id: 'p', backend: 'codex' }], needs_you: [], providers: [] } };
    if (url.pathname === '/v1/conversations') return { json: { conversations: ['a','b'].map(id => ({ id, title: id.toUpperCase() + ' report', state: 'failed', project: 'p', last_job_id: id + '-job' })) } };
    if (/^\/v1\/conversations\/(a|b|child)$/.test(url.pathname)) {
      if (state.delay) await state.delay;
      const id = url.pathname.split('/').at(-1);
      return { json: { title: id.toUpperCase() + ' report', turns: [turn(id)] } };
    }
    if (url.pathname.endsWith('/cancel')) { state.running = false; return { json: { cancelled: true } }; }
    if (/^\/v1\/jobs\/.*-job$/.test(url.pathname)) return { json: turn(url.pathname.split('/').at(-1).replace('-job','')) };
    if (url.pathname.endsWith('/spans')) return { json: { spans: ['Planner', 'Accessibility and interaction reviewer', 'Implementation and integration engineer'].map((name, i) => ({ ...span, name, span_id: 'span-' + i, attrs: { ...span.attrs, 'gen_ai.provider.name': 'codex', 'gen_ai.request.model': 'fixture-model', effort: 'medium' } })) } };
    if (url.pathname.endsWith('/events')) return { body: '', contentType: 'text/event-stream' };
  });
  async function open(id) { await page.locator('#history .conversation-row > button').filter({ hasText: id.toUpperCase() + ' report' }).click(); await page.waitForFunction(title => !loading && document.querySelector('#conversation-title').textContent === title, id.toUpperCase() + ' report'); }
  return { page, open, state };
}
const hit = locator => locator.evaluate(node => { const r = node.getBoundingClientRect(); const h = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); return h === node || node.contains(h); });
const settle = page => page.evaluate(() => Promise.all(document.getAnimations().filter(a => a.effect?.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {}))));
(async () => {
  const browser = await chromium.launch();
  const failures = [];
  async function check(name, fn) { if (process.env.ONLY && !process.env.ONLY.split(',').some(id => name.startsWith(id))) return; try { await fn(); console.log('PASS ' + name); } catch (error) { failures.push(name + ': ' + error.stack); console.error('FAIL ' + name + ': ' + error.message); } }
  try {
    await check('A1-F1 inline metadata remains contained and readable', async () => {
      const { page, open } = await fixture(browser); await open('a');
      await page.evaluate(plan => showMaestroPlan(plan), plan);
      const row = page.locator('.maestro-plan-card li'); await row.scrollIntoViewIfNeeded();
      const bounds = await row.evaluate(node => { const card = node.closest('.maestro-plan-card').getBoundingClientRect(); return [...node.children].map(child => { const r = child.getBoundingClientRect(); const range = document.createRange(); range.selectNodeContents(child); return { width: r.width, right: r.right, limit: card.right, fragments: range.getClientRects().length }; }); });
      assert(bounds.every(b => b.right <= b.limit && b.width > 20), JSON.stringify(bounds));
      assert(bounds[2].fragments <= 2, 'effort must not stack letters'); await row.evaluate(n => n.scrollIntoView({ block: 'center', behavior: 'instant' })); await settle(page); assert(await hit(row.locator('span').nth(2)), await row.locator('span').nth(2).evaluate(n => { const r=n.getBoundingClientRect(); return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.outerHTML; }));
      await page.close();
    });
    await check('A1-F2 clipped pipeline model discloses full identity', async () => {
      const { page } = await fixture(browser); await page.keyboard.press('Control+j'); const chip = page.locator('.run-span-row .backend-chip').first(); await chip.waitFor();
      assert.equal(await chip.getAttribute('title'), await chip.innerText()); await page.close();
    });
    await check('A1-F5 long-role pipeline cards fit before vertical scroll', async () => {
      const { page } = await fixture(browser); await page.keyboard.press('Control+j'); await page.locator('.run-span-row').nth(2).waitFor();
      await settle(page);
      const metrics = await page.locator('.run-span-row').evaluateAll(nodes => nodes.map(n => ({ bottom: n.getBoundingClientRect().bottom, limit: n.closest('.run-console-body').getBoundingClientRect().bottom, scroll: n.closest('.run-console-body').scrollTop })));
      assert(metrics.every(m => m.scroll === 0 && m.bottom <= m.limit), JSON.stringify(metrics));
      const token = page.locator('.run-span-row').nth(1).locator('.run-span-tokens');
      await page.locator('.run-span-list').evaluate(n => n.scrollLeft = 190);
      assert(await hit(token));
      assert(await token.evaluate(n => { const r = n.getBoundingClientRect(), h = document.elementFromPoint(r.x + r.width / 2, r.bottom - 1); return n === h || n.contains(h); }));
      await page.close();
    });
    await check('A1-F3 mobile tour exposes pane during step and after dismissal', async () => {
      const { page } = await fixture(browser, 400, 812); await page.evaluate(() => tailHarnessTour.start());
      for (let i = 0; i < 18 && await page.locator('#tour-title').innerText() !== 'Files and activity'; i++) await page.locator('#tour-next').click();
      assert.equal(await page.locator('#tour-title').innerText(), 'Files and activity'); await settle(page);
      assert(await page.locator('#run-console').isHidden());
      await page.keyboard.press('Escape'); await settle(page);
      for (const summary of await page.locator('#activity-panel .workspace-section > summary').all()) assert(await hit(summary));
      await page.close();
    });
    await check('A1-F4 stable hovered Next contrast in three themes', async () => {
      const { page } = await fixture(browser);
      for (const theme of ['porcelain','amethyst','petroleum']) {
        await page.evaluate(theme => { TailTheme.apply(theme); tailHarnessTour.start(); }, theme);
        await page.locator('#tour-next').hover(); await settle(page);
        const ratio = await page.locator('#tour-next').evaluate(n => { const s = getComputedStyle(n), canvas = document.createElement('canvas'), c = canvas.getContext('2d'); function lum(color) { c.fillStyle = color; c.fillRect(0,0,1,1); const values = [...c.getImageData(0,0,1,1).data].slice(0,3).map(v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }); return values[0]*.2126 + values[1]*.7152 + values[2]*.0722; } const a = lum(s.color), b = lum(s.backgroundColor); return (Math.max(a,b)+.05)/(Math.min(a,b)+.05); });
        assert(ratio >= 4.5, theme + ': ' + ratio); await page.keyboard.press('Escape');
      }
      await page.close();
    });
    await check('A2-F5 resize handle is 24px with uncovered edges', async () => {
      const { page } = await fixture(browser, 1280, 900); await page.keyboard.press('Control+j'); await settle(page);
      const handle = page.locator('#run-console-resize'); const box = await handle.boundingBox(); assert(box.width >= 24 && box.height >= 24, JSON.stringify(box));
      assert(await handle.evaluate(n => { const r = n.getBoundingClientRect(); return [r.top+1, r.bottom-1].every(y => { const h = document.elementFromPoint(r.x+r.width/2,y); return n===h || n.contains(h); }); })); await page.close();
    });
    await check('A2-F1 new drafts belong to the source project', async () => {
      const { page } = await fixture(browser, 1280, 900);
      await page.evaluate(() => { document.querySelector('#project-tree').open = true; expandedProjects.set('p', true); expandedProjects.set('q', true); renderProjects(); });
      await page.fill('#prompt', 'Exact P draft'); await page.locator('.project-group[data-project-id="q"]').evaluate(n => n.open = true); await page.locator('.project-group[data-project-id="q"] .project-new').click();
      await page.fill('#prompt', 'Exact Q draft'); await page.locator('.project-group[data-project-id="p"]').evaluate(n => n.open = true); await page.locator('.project-group[data-project-id="p"] .project-new').click();
      assert.equal(await page.inputValue('#prompt'), 'Exact P draft');
      await page.reload(); await page.waitForFunction(() => !loading && document.querySelector('#prompt').value === 'Exact P draft'); await page.locator('#project-tree').evaluate(n => n.open = true);
      await page.locator('.project-group[data-project-id="q"]').evaluate(n => n.open = true); await page.locator('.project-group[data-project-id="q"] .project-new').click(); assert.equal(await page.inputValue('#prompt'), 'Exact Q draft'); await page.evaluate(() => chooseProject('p')); assert.equal(await page.inputValue('#prompt'), 'Exact P draft'); await page.evaluate(() => chooseProject('q')); assert.equal(await page.inputValue('#prompt'), 'Exact Q draft'); await page.close();
    });
    await check('A5-F1 unavailable conversation composer prevents lost input', async () => {
      const { page, open, state } = await fixture(browser); await open('a'); await page.fill('#prompt', 'Saved A');
      let release; state.delay = new Promise(resolve => release = resolve);
      await page.locator('#history .conversation-row > button').filter({ hasText: 'B report' }).click();
      await page.waitForFunction(() => loading);
      const protectedInput = await page.locator('#prompt').evaluate(n => n.readOnly || n.disabled);
      release(); state.delay = null;
      assert(protectedInput, 'composer accepts input while its destination is unavailable');
      await page.waitForFunction(() => !loading); await page.fill('#prompt', 'New B'); await open('a'); assert.equal(await page.inputValue('#prompt'), 'Saved A'); await open('b'); assert.equal(await page.inputValue('#prompt'), 'New B'); await page.close();
    });
    await check('A2-F2 mobile sidebar owns visible keyboard focus', async () => {
      for (const reversed of [false, true]) {
        const { page } = await fixture(browser, 400, 844);
        await page.evaluate(reversed => { document.body.classList.toggle('panel-order-reversed', reversed); setPanelOpen(false); }, reversed);
        await page.locator('#menu').focus(); await page.keyboard.press('Enter'); await settle(page);
        for (let i = 0; i < 22; i++) { await page.keyboard.press(i < 11 ? 'Tab' : 'Shift+Tab'); assert(await page.evaluate(() => document.querySelector('#sidebar').contains(document.activeElement))); assert(await hit(page.locator(':focus'))); }
        await page.keyboard.press('Escape'); assert(await page.locator('#menu').evaluate(n => n === document.activeElement)); assert.equal(await page.locator('#menu').getAttribute('aria-expanded'), 'false');
        await page.setViewportSize({ width: 621, height: 844 }); assert.equal(await page.locator('#sidebar').getAttribute('aria-modal'), null); await page.close();
      }
    });
    await check('A2-F3 Attention owns Tab above workspace overlay', async () => {
      const { page } = await fixture(browser, 800, 844); await page.locator('#attention-bell').click();
      for (let i = 0; i < 12; i++) { await page.keyboard.press(i < 6 ? 'Tab' : 'Shift+Tab'); assert(await page.evaluate(() => document.querySelector('#attention-popover').contains(document.activeElement))); assert(await hit(page.locator(':focus'))); }
      await page.keyboard.press('Escape'); assert(await page.locator('#attention-popover').isHidden()); assert(await page.locator('#activity-panel').isVisible()); await page.close();
    });
    await check('A2-F4 pickers and dialogs own console shortcuts', async () => {
      const { page } = await fixture(browser, 1280, 900); await page.keyboard.press('Control+j');
      for (const id of ['model','access','effort']) { await page.locator('#' + id + '-trigger').click(); await page.keyboard.press('Escape'); assert(await page.locator('#run-console').isVisible(), id); assert.equal(await page.locator('.composer-menu:popover-open').count(), 0); }
      await page.locator('#attention-bell').click(); await page.locator('#attention-popover button').first().focus(); await page.keyboard.press('Escape'); assert(await page.locator('#attention-popover').isHidden()); assert(await page.locator('#run-console').isVisible());
      await page.evaluate(() => setQuotaOpen(true)); await page.keyboard.press('Escape'); assert(await page.locator('#quota-panel').isHidden()); assert(await page.locator('#run-console').isVisible());
      await page.keyboard.press('Control+j'); await page.locator('#search-conversations').click(); await page.keyboard.press('Control+j'); assert(await page.locator('#run-console').isHidden()); await page.close();
    });
    await check('A4-F1 code examples submit byte-preserved from composer', async () => {
      for (const prompt of ['Explain:\n```js\n//comment\n```', '```diff\n@@ -1 +1 @@\n-old\n+new\n```', 'What is 7 // 2 in Python?']) {
        const { page } = await fixture(browser, 1280, 900); const sent = [];
        await page.route('**/v1/jobs', async route => { sent.push(route.request().postDataJSON()); return route.fulfill({status: 422, json: {error: 'synthetic rejection after admission capture'}}); });
        await page.fill('#prompt', prompt); await page.locator('#send').click();
        await page.waitForFunction(() => !submitting);
        assert.equal(sent.length, 1); assert.equal(sent[0].prompt, prompt); await page.close();
      }
    });
    await check('A4-F2 repeated selection retains two ordered occurrence chips', async () => {
      const { page } = await fixture(browser, 1280, 900);
      await page.fill('#prompt', '/reviewer'); await page.locator('#resource-menu [data-resource-id]').click(); await page.keyboard.type('first /reviewer'); await page.locator('#resource-menu [data-resource-id]').click(); await page.keyboard.type('second');
      assert.equal(await page.locator('.resource-chip').count(), 2); assert.equal(await page.evaluate(() => resourceSelections.length), 2);
      assert.match(await page.locator('.resource-chain-preview').innerText(), /1 \/reviewer.*2 \/reviewer/);
      const input = await page.evaluate(() => ({prompt: document.querySelector('#prompt').value, selections: resourceSelections}));
      const normalized = require('node:child_process').execFileSync(process.env.PYTHON || require('node:path').join(__dirname, '../.venv/bin/python'), ['-c', 'import json,sys; from agent_service.invocations import normalize_chips; d=json.load(sys.stdin); print(json.dumps([v.to_dict() for v in normalize_chips(d["prompt"], d["selections"], [{"id":"project/p/reviewer","kind":"agent"}])]))'], {input: JSON.stringify(input), cwd: require('node:path').join(__dirname, '..'), encoding: 'utf8'});
      assert.deepEqual(JSON.parse(normalized).map(item => item.args), ['first ', 'second']);
      await page.locator('.resource-chip').nth(1).click(); assert.equal(await page.locator('.resource-chip').count(), 1); assert.equal(await page.inputValue('#prompt'), '/reviewer first second'); await page.close();
    });
    await check('A5-F2 Resume retry and reload reuse one accepted identity', async () => {
      const { page, open } = await fixture(browser); await open('a'); const keys = []; const children = new Map();
      await page.route('**/v1/jobs/a-job/resume', async route => { const key = route.request().headers()['idempotency-key']; keys.push(key); if (!children.has(key || keys.length)) children.set(key || keys.length, 'child'); if (keys.length === 1) return route.abort('failed'); return route.fulfill({ status: 202, json: { job_id: 'child', conversation_id: 'child' } }); });
      await page.getByRole('button', { name: 'Resume workflow', exact: true }).click(); await page.getByText(/Couldn't resume the workflow/).waitFor(); await page.reload(); await page.waitForFunction(() => !loading && document.querySelector('.workflow-recovery'));
      await page.getByRole('button', { name: 'Resume workflow', exact: true }).click(); await page.waitForFunction(() => document.querySelector('#conversation-title').textContent === 'CHILD report');
      assert(keys[0]); assert.equal(keys[0], keys[1]); assert.equal(children.size, 1); await page.close();
    });
    await check('A5-F3 search states current plan and loaded-file scope before searching', async () => {
      const { page } = await fixture(browser); await page.locator('#search-conversations').click(); assert.match(await page.locator('#conversation-search-dialog').innerText(), /current plan/i); assert.match(await page.locator('#conversation-search-dialog').innerText(), /loaded files/i); await page.close();
    });
    await check('A5-F4 cancel remains reachable with a follow-up draft', async () => {
      const { page, open, state } = await fixture(browser); state.running = true; await open('a'); await page.fill('#prompt', 'Keep this unsent follow-up');
      assert(await page.locator('#cancel').isVisible()); assert(await hit(page.locator('#cancel'))); await page.locator('#cancel').click(); await page.waitForFunction(() => !cancelling); assert.equal(state.running, false); assert.equal(await page.inputValue('#prompt'), 'Keep this unsent follow-up'); await page.close();
    });
    await check('A5-F5 terminal gate headings match live and replayed decisions', async () => {
      const { page, open } = await fixture(browser); await open('a');
      for (const [state, choice, expected] of [['resolved','yes','Answered'],['resolved','deny','Publication denied'],['resolved',undefined,'Publication decision recorded'],['expired',null,'Question expired'],['invalidated',null,'Question closed']]) {
        const data = { gate_id: 'test-' + state + choice, question: 'Continue?', options: [{id:'yes',label:'Yes'}], publish: choice === 'deny' || choice === undefined };
        await page.evaluate(({data,state,choice}) => { showGate(data); finishGate(data.gate_id,state,{choice}); }, {data,state,choice}); assert.equal(await page.locator('#gate-' + data.gate_id + ' h3').innerText(), expected);
        await page.evaluate(({data,state,choice}) => { document.getElementById('gate-' + data.gate_id).remove(); restoreGates([{...data,state,choice}]); }, {data,state,choice}); assert.equal(await page.locator('#gate-' + data.gate_id + ' h3').innerText(), expected);
      } await page.close();
    });
  } finally { await browser.close(); }
  if (failures.length) { console.error(failures.join('\n')); process.exitCode = 1; }
})();
