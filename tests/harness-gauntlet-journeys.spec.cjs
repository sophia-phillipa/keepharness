// Round-one draft, terminal state, approved plan and workflow recovery journeys.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const { mount } = require('./run-console-fixture.cjs');
const plan = { steps: [{ role: 'Reviewer', backend: 'codex', model: 'fixture', effort: 'medium', task: 'Review synthetic evidence' }] };
async function setup(browser, outcome = 'completed', checkpoint = true) {
  const page = await browser.newPage();
  page.setDefaultTimeout(6000);
  const states = { a: 'completed', b: outcome, child: 'completed' };
  const requests = [];
  const turn = id => ({ id: id + '-job', project: 'sem-projeto', state: states[id], workflow_checkpoint: id === 'b' && checkpoint, request: { prompt: 'Review ' + id, backend: id === 'b' ? 'maestro' : 'codex', model: 'fixture', effort: 'medium', execution_mode: 'native' }, result: { answer: 'Synthetic answer', ...(states[id] === 'failed' ? { error: 'synthetic_execution_failed' } : {}) }, gates: id === 'b' && checkpoint ? [{ gate_id: 'approved-plan', kind: 'maestro_plan', state: 'resolved', choice: 'approve', plan }] : [] });
  await mount(page, async (url, request) => {
    if (url.pathname === '/v1/models') return { json: { models: [{ id: 'fixture', backend: 'codex', efforts: ['medium'], execution_modes: ['native', 'scoped'] }], providers: { codex: true } } };
    if (url.pathname === '/v1/conversations') return { json: { conversations: ['a', 'b'].map(id => ({ id, title: id.toUpperCase() + ' report', state: states[id], project: 'sem-projeto', last_job_id: id + '-job' })) } };
    const conversation = /^\/v1\/conversations\/(a|b|child-job)$/.exec(url.pathname);
    if (conversation) return { json: { title: conversation[1].replace('-job', '').toUpperCase() + ' report', turns: [turn(conversation[1].replace('-job', ''))] } };
    if (url.pathname === '/v1/jobs/b-job/resume') { requests.push(request.postDataJSON()); return { status: 202, json: { job_id: 'child-job', status_url: '/v1/jobs/child-job', events_url: '/v1/jobs/child-job/events', execution_mode: 'native' } }; }
    const job = /^\/v1\/jobs\/(a|b|child)-job$/.exec(url.pathname);
    if (job) return { json: turn(job[1]) };
    if (url.pathname.endsWith('/events')) return { body: '', contentType: 'text/event-stream' };
    if (url.pathname.endsWith('/spans')) return { json: { spans: [] } };
    if (url.pathname === '/v1/resources') return { json: { items: [{ id: 'project/p/skill', resource_id: 'project/p/skill', revision: '1', token: '/check', kind: 'skill', name: 'check', origin: 'codex', scope: 'project', selectable: true }], warnings: [] } };
  });
  async function open(id) {
    await page.locator('#history .conversation-row > button').filter({ hasText: id.toUpperCase() + ' report' }).click();
    await page.waitForFunction(title => document.querySelector('#conversation-title').textContent === title && !loading, id.toUpperCase() + ' report');
  }
  return { page, open, requests, states, turn };
}
(async () => {
  const browser = await chromium.launch();
  const failures = [];
  async function check(name, test) { try { await test(); console.log('PASS ' + name); } catch (error) { failures.push(name + ': ' + error.stack); console.error('FAIL ' + name + ': ' + error.message); } }
  try {
    await check('A5-F1 each conversation retains exact draft and resource selection across switching and reload', async () => {
      const { page, open } = await setup(browser);
      await open('b');
      await page.fill('#prompt', '/check');
      await page.locator('#resource-menu [data-resource-id="project/p/skill"]').click();
      await page.keyboard.type(' draft with  two spaces\nsecond line');
      const draft = await page.inputValue('#prompt');
      await open('a');
      assert.equal(await page.inputValue('#prompt'), '');
      await page.fill('#prompt', 'Independent A draft');
      await open('b');
      assert.equal(await page.inputValue('#prompt'), draft);
      assert.equal(await page.locator('.resource-chip').count(), 1);
      await page.reload();
      await page.waitForFunction(() => !loading && document.querySelector('#prompt').value.startsWith('/check'));
      assert.equal(await page.inputValue('#prompt'), draft);
      await open('a');
      assert.equal(await page.inputValue('#prompt'), 'Independent A draft');
      await page.close();
    });
    await check('A5-F3 terminal outcomes remain distinct in header and history', async () => {
      for (const state of ['completed', 'failed', 'cancelled', 'interrupted']) {
        const { page, open } = await setup(browser, state);
        await open('b');
        const label = state[0].toUpperCase() + state.slice(1);
        assert.equal(await page.locator('#conversation-state-pill').innerText(), label);
        assert.match(await page.locator('#history .conversation-row').filter({ hasText: 'B report' }).innerText(), new RegExp(label));
        await page.reload();
        await page.waitForFunction(() => !loading && document.querySelector('#conversation-title').textContent === 'B report');
        assert.equal(await page.locator('#conversation-state-pill').innerText(), label);
        await page.close();
      }
    });
    await check('A5-F4 approved terminal plans never replay pending or running wording', async () => {
      for (const state of ['completed', 'failed']) {
        const { page, open, turn } = await setup(browser, state);
        await open('b');
        const card = page.locator('.maestro-plan-card');
        assert.doesNotMatch(await card.innerText(), /Awaiting your approval|running|can start/i);
        assert.match(await card.innerText(), /Approved/);
        assert.match(await card.innerText(), new RegExp(state, 'i'));
        await page.evaluate(({ plan, snapshot }) => { showMaestroPlan({ ...plan, gate_id: 'approved-plan' }); finishGate('approved-plan', 'resolved', { choice: 'approve' }); return result(job, controller, snapshot); }, { plan, snapshot: turn('b') });
        assert.doesNotMatch(await card.innerText(), /Awaiting your approval|running|can start/i);
        await page.close();
      }
    });
    await check('A5-F5 failed workflow exposes a visible resume action with checkpoint explanation', async () => {
      const { page, open, requests } = await setup(browser, 'failed');
      await open('b');
      await open('a');
      await open('b');
      const resume = page.getByRole('button', { name: 'Resume workflow', exact: true });
      await resume.waitFor();
      assert.match(await resume.locator('..').innerText(), /completed steps|checkpoint/i);
      await resume.click();
      await page.waitForFunction(() => document.querySelector('#conversation-title').textContent === 'CHILD report');
      assert.deepEqual(requests, [{}]);
      await page.close();
    });
    await check('A5-F1 new conversation draft survives navigation into history', async () => {
      const { page, open } = await setup(browser);
      await page.fill('#prompt', '/check');
      await page.locator('#resource-menu [data-resource-id="project/p/skill"]').click();
      await page.keyboard.type(' New draft');
      const draft = await page.inputValue('#prompt');
      await open('a');
      await page.locator('#new').click();
      assert.equal(await page.inputValue('#prompt'), draft);
      assert.equal(await page.locator('.resource-chip').count(), 1);
      await page.close();
    });
    await check('A5-F5 cancellation before a checkpoint does not offer unusable recovery', async () => {
      const { page, open } = await setup(browser, 'cancelled', false);
      await open('b');
      assert.equal(await page.getByRole('button', { name: 'Resume workflow', exact: true }).count(), 0);
      await page.close();
    });
    assert.deepEqual(failures, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
