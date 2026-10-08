const assert = require('node:assert/strict');
const { mockHarness, runPersona } = require('./personas/_harness.cjs');
const scenarios = [];
for (const backend of ['codex', 'claude', 'deepseek', 'gemini']) {
  scenarios.push({ title: `${backend} exposes native permissions without isolation offers`, async run(page) {
    const model = { id: backend === 'claude' ? 'claude-sonnet-4-6' : backend + '-fixture', backend, efforts: ['low'], execution_modes: ['native', 'scoped'] };
    const state = await mockHarness(page, { 'GET /v1/models': { json: { models: [model], providers: { [backend]: true } } } });
    await page.goto('http://harness.test');
    await page.locator('#startup-gate').waitFor({ state: 'hidden' });
    await page.locator('#prompt').fill('Fresh native request');
    assert.equal(await page.locator('#isolation-toggle').isVisible(), false);
    assert.equal(await page.locator('#execution-mode-help').isVisible(), false);
    assert.equal(await page.locator('#execution-mode-indicator').isVisible(), false);
    assert.deepEqual(await page.evaluate(() => supportedExecutionModes()), ['native']);
    assert.equal(await page.locator('#access-mode option').count(), 4);
    const resourceModes = [];
    await page.route('**/v1/resources?**', route => {
      resourceModes.push(new URL(route.request().url()).searchParams.get('execution_mode'));
      return route.fulfill({ json: { items: [], warnings: [] } });
    });
    await page.evaluate(() => refreshWorkspaceResources());
    assert.deepEqual(resourceModes, ['native']);
    await page.locator('#send').click();
    await page.waitForFunction(() => job === 'job-1');
    assert.equal(state.posts[0].execution_mode, 'native');
    assert.equal(state.posts[0].parent_job_id, undefined);
    assert.equal(await page.locator('#execution-mode-indicator').isVisible(), false);
  } });
}
scenarios.push({ title: 'missing capabilities block Send and Enter until refresh', async run(page) {
  const model = { id: 'codex-fixture', backend: 'codex', efforts: ['low'] };
  const state = await mockHarness(page, { 'GET /v1/models': route => route.fulfill({ json: { models: [model], providers: { codex: true } } }) });
  await page.goto('http://harness.test');
  await page.locator('#startup-gate').waitFor({ state: 'hidden' });
  await page.locator('#prompt').fill('Keep this draft');
  assert.equal(await page.locator('#send').isDisabled(), true);
  assert.match(await page.locator('#execution-mode-unavailable').innerText(), /capabilities.*refresh/i);
  await page.locator('#prompt').press('Enter');
  assert.equal(state.posts.length, 0);
  model.execution_modes = ['native'];
  await page.evaluate(() => initialize());
  assert.equal(await page.locator('#send').isEnabled(), true);
  assert.equal(await page.locator('#prompt').inputValue(), 'Keep this draft');
} });
for (const backend of ['codex', 'claude']) for (const workflow of [false, true]) {
  scenarios.push({ title: `${backend} retired ${workflow ? 'workflow' : 'history'} keeps its label and keyboard New starts independent native context`, async run(page) {
    const model = { id: backend === 'claude' ? 'claude-sonnet-4-6' : backend + '-fixture', backend, efforts: ['low'], execution_modes: ['native'] };
    const turn = { id: 'retired-turn', project: 'sem-projeto', state: 'failed', request: { backend, model: model.id, execution_mode: 'scoped', prompt: 'Historical content', provider_session_id: 'old-session' }, result: { answer: 'Readable history' }, error: 'execution_mode_unsupported' };
    if (workflow) turn.workflow_checkpoint = { completed_steps: 1 };
    const state = await mockHarness(page, {
      'GET /v1/models': { json: { models: [model], providers: { [backend]: true } } },
      'GET /v1/conversations/retired': { json: { execution_mode: 'scoped', turns: [turn] } },
    });
    await page.goto('http://harness.test');
    await page.locator('#startup-gate').waitFor({ state: 'hidden' });
    await page.evaluate(() => navigate({ kind: 'conversation', id: 'retired' }));
    await page.waitForFunction(() => !loading && !policyPending);
    assert.equal(await page.locator('#header-execution-mode').innerText(), 'Isolated conversation');
    assert.equal(await page.locator('#execution-mode-unavailable').innerText(), 'Isolated Codex and Claude conversations are no longer supported. Start a new native conversation to continue.');
    assert.equal(await page.locator('#execution-mode-unavailable').getAttribute('role'), 'status');
    assert.equal(await page.getByText('Historical content', { exact: true }).isVisible(), true);
    assert.equal(await page.getByTestId('turn-retry').count(), 0);
    assert.equal(await page.getByRole('button', { name: 'Resume workflow', exact: true }).count(), 0);
    assert.equal(await page.locator('#send').isDisabled(), true);
    await page.locator('#new').focus();
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => !loading && !policyPending);
    assert.equal(await page.locator('#prompt').inputValue(), '');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'prompt');
    assert.equal(state.posts.length, 0);
    await page.locator('#prompt').fill('Explicit new content');
    await page.locator('#prompt').press('Enter');
    await page.waitForFunction(() => job === 'job-1');
    assert.equal(state.posts[0].execution_mode, 'native');
    assert.equal(state.posts[0].parent_job_id, undefined);
    assert.equal(state.posts[0].provider_session_id, undefined);
    assert.equal(state.posts[0].prompt, 'Explicit new content');
  } });
}
for (const theme of ['paper', 'graphite']) for (const origin of ['history', 'draft']) for (const entry of ['main', 'project', 'command']) {
  scenarios.push({ title: `390px ${theme} ${origin} ${entry} New keeps editor focus after drawer closes`, viewport: { width: 390, height: 844 }, async run(page) {
    const model = { id: 'codex-fixture', backend: 'codex', efforts: ['low'], execution_modes: ['native'] };
    const turn = { id: 'retired-turn', project: 'sem-projeto', state: 'completed', request: { backend: 'codex', model: model.id, execution_mode: 'scoped', prompt: 'Historical content', provider_session_id: 'old-session' }, result: { answer: 'Readable history' } };
    const state = await mockHarness(page, {
      'GET /v1/models': { json: { models: [model], providers: { codex: true } } },
      'GET /v1/projects': { json: { projects: ['sem-projeto', 'project-a'], details: { 'project-a': { label: 'Project Alpha' } } } },
      'GET /v1/conversations/retired': { json: { execution_mode: 'scoped', turns: [turn] } },
    });
    if (origin === 'draft') await page.addInitScript(() => sessionStorage.setItem('remote-view', JSON.stringify({ project: 'sem-projeto', composer_selection: { model: 'codex-fixture', effort: 'low' }, draft: 'Preserved unsent draft', draft_mode: { mode: 'scoped', modeChosen: true, retiredLock: true } })));
    await page.goto('http://harness.test');
    await page.locator('#startup-gate').waitFor({ state: 'hidden' });
    await page.evaluate(name => HarnessTheme.apply(name, false), theme);
    if (origin === 'history') await page.evaluate(() => navigate({ kind: 'conversation', id: 'retired' }));
    await page.waitForFunction(() => !loading && !policyPending);
    assert.equal(await page.locator('#send').isDisabled(), true);
    await page.locator('#menu').click();
    assert.equal(await page.locator('main').evaluate(el => el.inert), true);
    if (entry === 'command') {
      await page.keyboard.press('Control+k');
      await page.locator('#conversation-search').fill('New conversation');
      const result = page.locator('#conversation-search-dialog').getByRole('button', { name: /New conversation/i });
      await result.focus();
    } else {
      await page.locator(entry === 'main' ? '#new' : '.project-new').focus();
    }
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => !loading && !policyPending && !conversation && !draftMode.retiredLock);
    // Wait for rendering and the drawer transition before checking durable focus.
    await page.evaluate(async () => {
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      await Promise.all(document.getElementById('sidebar').getAnimations().map(animation => animation.finished.catch(() => {})));
    });
    assert.equal(await page.locator('#sidebar').evaluate(el => el.classList.contains('open')), false);
    assert.equal(await page.locator('main').evaluate(el => el.inert), false);
    assert.equal(await page.evaluate(() => document.activeElement.id), 'prompt');
    assert.equal(await page.locator('#prompt').inputValue(), origin === 'draft' ? 'Preserved unsent draft' : '');
    assert.equal(await page.evaluate(() => executionMode), 'native');
    assert.equal(state.posts.length, 0);
    await page.keyboard.type(' Fresh input');
    assert.match(await page.locator('#prompt').inputValue(), / Fresh input$/);
    assert.equal(state.posts.length, 0);
  } });
}
runPersona('cloud-mode-ui-retirement', scenarios).then(r => console.log(`cloud-mode-ui-retirement: ${r.filter(x => x.status === 'pass').length}/${r.length} passed`));
