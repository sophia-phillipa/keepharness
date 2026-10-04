const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const { mount } = require('./run-console-fixture.cjs');

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    let latest = false, currentPending = false;
    let holdActivity = false, releaseActivity, releaseConversation;
    const activityReady = new Promise(resolve => { releaseActivity = resolve; });
    const conversationReady = new Promise(resolve => { releaseConversation = resolve; });
    const calls = [];
    const jobs = () => [
      { job_id: 'a1', conversation_id: 'a', project_id: 'p', state: 'running', created: 1 },
      { job_id: 'b1', conversation_id: 'b', project_id: 'q', state: 'running', created: 2 },
      ...(latest ? [{ job_id: 'a2', conversation_id: 'a', project_id: 'p', state: 'running', created: 3 }] : []),
    ];
    const gate = (id, conversation, project) => ({ gate_id: 'gate-' + id, job_id: id,
      conversation_id: conversation, project_id: project, kind: 'gate', approval_kind: 'maestro_plan',
      question: 'Approve ' + id, timeout_at: Date.now() / 1000 + 300,
      plan: { steps: [{ role: 'reviewer', backend: 'local', model: 'fixture', effort: 'configured', task: 'Task for ' + id, reason: 'Review' }] },
      options: [{ id: 'approve', label: 'Approve' }, { id: 'deny', label: 'Discard' }] });
    await mount(page, async url => {
      calls.push(url.pathname + url.search);
      if (url.pathname === '/v1/projects') return { json: { projects: ['p', 'q'], details: { p: { label: 'Project P' }, q: { label: 'Project Q' } } } };
      if (url.pathname === '/v1/conversations') return { json: { conversations: [
        { id: 'a', project: 'p', title: 'Conversation A', state: 'running', updated: Date.now() / 1000 },
        { id: 'b', project: 'q', title: 'Conversation B', state: 'running', updated: Date.now() / 1000 },
      ] } };
      if (/\/v1\/conversations\/[ab]$/.test(url.pathname)) {
        const id = url.pathname.at(-1);
        if (id === 'a') await conversationReady;
        return { json: { title: 'Conversation ' + id.toUpperCase(), turns: [{ id: id + '1', project: id === 'a' ? 'p' : 'q', state: 'completed', request: { prompt: id, model: 'fixture' }, result: { answer: 'Previous response' } }] } };
      }
      if (/\/v1\/jobs\/[ab][12]$/.test(url.pathname)) return { json: { state: 'completed', result: { answer: 'Previous response' } } };
      if (url.pathname.endsWith('/spans')) return { json: { spans: [] } };
      if (url.pathname === '/v1/activity') {
        if (holdActivity) await activityReady;
        const needs = [gate('b1', 'b', 'q'), ...(currentPending ? [gate('a2', 'a', 'p')] : [])];
        const scoped = url.searchParams.get('project_id');
        return { json: { jobs: jobs().filter(j => !scoped || j.project_id === scoped),
          needs_you: needs.filter(j => !scoped || j.project_id === scoped),
          counts: { running: jobs().length, queued: 0, needs_you: needs.filter(j => !scoped || j.project_id === scoped).length }, providers: [] } };
      }
    });
    await page.waitForFunction(() => document.querySelector('#console-run').value === 'b1');
    await page.locator('#sidebar .conversation-title').filter({ hasText: 'Conversation A' }).click();
    holdActivity = true;
    const pendingActivity = page.waitForRequest(request => new URL(request.url()).pathname === '/v1/activity');
    await page.keyboard.press('Control+j');
    await pendingActivity;
    releaseConversation();
    await page.waitForFunction(() => !loading && conversation === 'a');
    holdActivity = false;
    releaseActivity();
    await page.waitForFunction(() => document.querySelector('#console-run').value === 'a1');
    assert.doesNotMatch(await page.locator('#run-console').innerText(), /Task for b1/, 'another conversation plan must never appear on this run');
    assert.match(await page.locator('#run-status-toggle').innerText(), /1 needs you/);
    assert(calls.includes('/v1/activity'), 'the global attention summary is not filtered by selected project');
    latest = true;
    currentPending = true;
    await page.evaluate(() => runConsole.observe({ type: 'gate_required' }));
    await page.waitForFunction(() => document.querySelector('#console-run').value === 'a2');
    await page.locator('#needs-you-toggle').click();
    const plan = page.locator('.needs-you-card').filter({ hasText: 'Task for a2' });
    await plan.waitFor();
    assert.match(await plan.innerText(), /This plan can no longer be approved\./);
    assert.equal(await plan.getByRole('button').count(), 0, 'an old plan card is read-only');
    await page.keyboard.press('Escape');
    await page.getByRole('tab', { name: 'Runs', exact: true }).click();
    await page.locator('#console-project').selectOption('p');
    await page.getByRole('button', { name: 'Apply filters' }).click();
    await page.getByRole('button', { name: 'b1', exact: true }).waitFor({ state: 'hidden' });
    await page.waitForFunction(() => /2 needs you/.test(document.querySelector('#run-status-toggle').textContent));
    assert.equal(await page.getByRole('button', { name: 'b1', exact: true }).count(), 0, 'Runs filters only its own table');
    assert.match(await page.locator('#sidebar').innerText(), /Conversation B/, 'other project remains in global sidebar');
    console.log('PASS global activity, project filters, latest-run following and conversation-bound read-only plans');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
