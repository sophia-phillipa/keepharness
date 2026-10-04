// D15: a paused scheduled task and a run that skipped an approval show in "Needs you".
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const { mount } = require('./run-console-fixture.cjs');

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const alerts = [
      { schedule_id: 's1', title: 'Nightly digest', project_id: 'sem-projeto', reason: 'paused', job_id: null,
        message: 'Paused after 3 failed runs in a row (last error: cli_missing).' },
      { schedule_id: 's2', title: 'Morning radar', project_id: 'sem-projeto', reason: 'needs_you', job_id: 'run-9',
        message: 'The last run skipped an action that needed your approval.' },
    ];
    await mount(page, async url => {
      if (url.pathname === '/v1/activity')
        return { json: { jobs: [], needs_you: [], providers: [], schedule_alerts: alerts,
          counts: { running: 0, queued: 0, needs_you: 0 } } };
    });
    await page.waitForFunction(() => document.getElementById('attention-count')?.textContent === '2');
    await page.click('#attention-bell');
    await page.click('#attention-open-inbox');
    const paused = page.locator('.schedule-alert').filter({ hasText: 'Scheduled task: Nightly digest' });
    await paused.waitFor();
    assert.match(await paused.innerText(), /Paused after 3 failed runs/);
    assert.equal(await paused.getByRole('button').count(), 0, 'a paused task has no run to open');
    const flagged = page.locator('.schedule-alert').filter({ hasText: 'Scheduled task: Morning radar' });
    assert.equal(await flagged.getByRole('button', { name: 'Open run' }).count(), 1);
    assert.deepEqual(errors, []);
    console.log('PASS scheduled-task attention items');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
