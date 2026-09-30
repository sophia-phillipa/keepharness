const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const { mount, run, span } = require('./run-console-fixture.cjs');
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const calls = [];
    let eventCount = 601;
    await mount(page, async (url, request) => {
      calls.push(url.pathname + url.search);
      if (url.pathname === '/v1/activity') return { json: { counts: { running: 1, queued: 1, needs_you: 0 }, jobs: [run], needs_you: [], providers: [{ backend: 'local', model: 'fixture', state: 'busy', running: 1, queued: 1 }] } };
      if (url.pathname.endsWith('/spans')) return { json: { spans: [{ ...span, ...(url.searchParams.get('include_content') === 'true' ? { content: [{ request: { prompt: 'private prompt' } }, { name: 'tool_start', data: { input: 'private input' } }, { name: 'tool_end', data: { result: 'private output' } }] } : {}) }] } };
      if (url.pathname.endsWith('/events')) {
        const after = Number(url.searchParams.get('after') || 0);
        const events = Array.from({ length: Math.min(200, eventCount - after) }, (_, i) => ({ id: after + i + 1, timestamp: 1 + i, type: 'tool_start', data: { tool: 'Read', tool_call_id: String(after + i) } }));
        return { json: { events, next_after: events.at(-1)?.id || after, has_more: after + events.length < eventCount } };
      }
      if (url.pathname.endsWith('/work-item')) return { json: { ...run, work_item: request.postDataJSON().work_item } };
      if (url.pathname === '/v1/conversations/conversation-a') return { json: { turns: [{ id: run.job_id, project: 'sem-projeto', state: 'completed', request: { prompt: 'Fixture', model: 'fixture' }, result: { answer: 'Done' } }] } };
      if (url.pathname === '/v1/jobs/run-a') return { json: { id: 'run-a', state: 'completed', result: { answer: 'Done' } } };
    });
    await page.locator('#run-status-toggle').waitFor();
    assert.equal(await page.locator('#run-console').isVisible(), false);
    await page.waitForFunction(() => document.querySelector('#run-status-toggle').textContent.includes('1 running'));
    await page.locator('#prompt').focus();
    await page.keyboard.press('Control+j');
    await page.getByRole('tab', { name: 'Pipeline', exact: true }).waitFor();
    const heightBefore = (await page.locator('#run-console').boundingBox()).height;
    await page.getByRole('separator', { name: 'Resize run console' }).focus();
    await page.keyboard.press('ArrowUp');
    assert((await page.locator('#run-console').boundingBox()).height > heightBefore);
    assert.equal(await page.locator('#console-run').inputValue(), 'run-a', 'Pipeline binds the latest run automatically');
    await page.getByRole('button', { name: /Fixture agent/ }).click();
    assert.equal(calls.some(p => p.includes('include_content=true')), false);
    await page.getByRole('button', { name: 'Show content', exact: true }).click();
    await page.getByRole('tab', { name: 'Input', exact: true }).click();
    await page.getByText('private input', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Hide content', exact: true }).click();
    assert.equal(await page.getByText('private input', { exact: true }).count(), 0);
    await page.getByRole('tab', { name: 'Metrics', exact: true }).focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.getByRole('tab', { name: 'Attributes', exact: true }).evaluate(el => el === document.activeElement), true);
    await page.waitForTimeout(4200);
    assert.equal(await page.getByRole('tab', { name: 'Attributes', exact: true }).evaluate(el => el === document.activeElement), true);
    await page.getByRole('tab', { name: 'Timeline', exact: true }).click();
    await page.getByLabel('Timeline zoom').fill('2');
    assert.equal(await page.locator('.run-waterfall-bar').count(), 1);
    await page.getByRole('tab', { name: 'Logs', exact: true }).click();
    await page.waitForFunction(() => document.querySelectorAll('.run-log-row').length === 200);
    for (const expected of [400, 600, 601]) {
      await page.getByRole('button', { name: 'Load more events' }).click();
      await page.waitForFunction(n => document.querySelectorAll('.run-log-row').length === n, expected);
    }
    eventCount = 602;
    await page.evaluate(() => runConsole.observe({ type: 'tool_start', job_id: 'run-a' }));
    await page.getByRole('button', { name: 'Load more events' }).click();
    await page.waitForFunction(() => document.querySelectorAll('.run-log-row').length === 602);
    await page.getByLabel('Search logs').fill('"tool_call_id":"600"');
    assert.equal(await page.locator('.run-log-row').count(), 1);
    await page.getByRole('tab', { name: 'Runs', exact: true }).click();
    await page.getByLabel('Work item filter').fill('CASE-42');
    await page.getByRole('button', { name: 'Apply filters' }).click();
    await page.waitForFunction(() => document.querySelector('#run-status-toggle').textContent.includes('1 running'));
    await page.getByRole('button', { name: 'Tag work item' }).click();
    await page.getByLabel('Work item key').fill('CASE-43');
    assert.equal(await page.getByLabel('Work item key').getAttribute('maxlength'), '128');
    await page.waitForTimeout(4200);
    assert.equal(await page.getByLabel('Work item key').inputValue(), 'CASE-43');
    await page.getByRole('button', { name: 'Save work item' }).click();
    await page.getByRole('button', { name: 'run-a', exact: true }).click();
    await page.getByText('Done', { exact: true }).waitFor();
    assert(calls.includes('/v1/conversations/conversation-a'));
    await page.getByRole('tab', { name: 'Agents', exact: true }).click();
    await page.getByText(/busy · 1 running · 1 queued/).waitFor();
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#prompt').evaluate(el => el === document.activeElement), true);
    await page.getByRole('button', { name: 'View run', exact: true }).click();
    assert.equal(await page.locator('#run-console').isVisible(), true);
    await page.setViewportSize({ width: 400, height: 844 });
    const bounds = await page.locator('#run-console').boundingBox();
    assert(bounds.width <= 400 && bounds.height >= 300 && bounds.height <= 440);
    const topbar = await page.locator('#app-topbar').boundingBox();
    const strip = await page.locator('.run-status-strip').boundingBox();
    assert(topbar.y === 0 && bounds.y >= topbar.y + topbar.height,
      'the bounded mobile drawer preserves the top bar');
    assert(bounds.y + bounds.height <= strip.y + 1 && strip.y + strip.height <= 844,
      'the persistent status strip remains below the mobile drawer');
    assert.equal(await page.locator('.run-console-body').evaluate(el => getComputedStyle(el).overflowY), 'auto');
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.getByRole('button', { name: 'Collapse run console' }).click();
    assert.equal(await page.locator('#run-console').isVisible(), false);
    assert(calls.some(p => p.includes('work_item=CASE-42') && p.includes('project_id=sem-projeto')));
    assert.deepEqual(errors, []);
    console.log('PASS console collapsed/chat home, keyboard/focus, spans/content opt-in, timeline zoom, 601 events/search, filters/tagging, agents and 400px');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
