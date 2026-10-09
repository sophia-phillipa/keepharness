const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const { mount, run, span } = require('./run-console-fixture.cjs');
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(8000);
    const calls = [];
    await mount(page, async (url, request) => {
      if (url.pathname === '/v1/temporary') return { json: { id: 'temporary-console' } };
      if (url.pathname === '/v1/activity') return { json: { jobs: [run], needs_you: [], counts: {}, providers: [] } };
      if (url.pathname.startsWith('/v1/jobs/')) {
        const scope = request.headers()['x-keepharness-temporary'] || '';
        calls.push({ path: url.pathname, scope });
        if ((url.pathname.includes('/run-a/') && scope) || (url.pathname.includes('/private-run/') && scope !== 'temporary-console'))
          return { status: 404, json: { code: 'job_not_found' } };
        if (url.pathname.endsWith('/spans')) return { json: { spans: [span] } };
        if (url.pathname.endsWith('/events')) return { json: { events: [{ id: 1, type: 'tool_start', data: { tool: 'Saved run tool' } }], has_more: false } };
      }
    });
    await page.keyboard.press('Control+Shift+N');
    await page.locator('#temporary-chat-notice').waitFor({ state: 'visible' });
    await page.locator('#run-status-toggle').click();
    await page.getByRole('tab', { name: 'Logs', exact: true }).click();
    await page.locator('#console-run').selectOption('run-a');
    await page.waitForFunction(() => document.querySelector('#console-run').value === 'run-a');
    await page.getByRole('tab', { name: 'Pipeline', exact: true }).click();
    assert(calls.filter(call => call.path.startsWith('/v1/jobs/run-a/')).every(call => call.scope === ''), 'saved run requests must not carry the temporary session header');
    await page.getByRole('button', { name: /Fixture agent/ }).waitFor();
    await page.getByRole('tab', { name: 'Logs', exact: true }).click();
    await page.locator('.run-log-row').waitFor();
    assert(calls.some(call => call.path === '/v1/jobs/run-a/spans'));
    assert(calls.some(call => call.path === '/v1/jobs/run-a/events'));
    assert(calls.filter(call => call.path.startsWith('/v1/jobs/run-a/')).every(call => call.scope === ''));
    await page.getByRole('tab', { name: 'Runs', exact: true }).click();
    await page.getByRole('button', { name: 'Tag work item' }).click();
    await page.getByLabel('Work item key', { exact: true }).fill('CASE-60');
    await page.getByRole('button', { name: 'Save work item' }).click();
    await page.getByRole('button', { name: 'Tag work item' }).waitFor();
    assert(calls.some(call => call.path === '/v1/jobs/run-a/work-item' && call.scope === ''));
    console.log('PASS Activity inspects saved spans and logs without temporary scope');
    await page.evaluate(() => {
      const target = document.createElement('div');
      target.id = 'private-answer-fixture';
      document.querySelector('#messages').append(target);
      window.runConsole.attachAnswer(target, 'private-run');
    });
    await page.locator('#private-answer-fixture').getByRole('button', { name: 'View run' }).click();
    await page.getByRole('button', { name: /Fixture agent/ }).waitFor();
    await page.getByRole('tab', { name: 'Logs', exact: true }).click();
    await page.locator('.run-log-row').waitFor();
    await page.locator('#console-run').selectOption('private-run');
    await page.locator('.run-log-row').waitFor();
    assert(calls.some(call => call.path === '/v1/jobs/private-run/spans'));
    assert(calls.some(call => call.path === '/v1/jobs/private-run/events'));
    assert(calls.filter(call => call.path.startsWith('/v1/jobs/private-run/')).every(call => call.scope === 'temporary-console'));
    console.log('PASS temporary answer inspection keeps its own scope');
    await page.evaluate(() => dispatchEvent(new PageTransitionEvent('pagehide')));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
