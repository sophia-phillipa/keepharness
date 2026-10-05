// Workflow discovery in the "/" palette stays fully visible at desktop and mobile sizes.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const { mount, run } = require('./run-console-fixture.cjs');
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    page.setDefaultTimeout(5000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await mount(page, async url => {
      if (url.pathname === '/v1/resources') return { json: { items: [{
        id: 'project/sem-projeto/workflows/review.json', resource_id: 'project/sem-projeto/workflows/review.json',
        revision: 'revision-one', kind: 'workflow', mode: 'delegated', name: 'review', group: 'Workflows',
        description: 'Review evidence in two sequential steps', scope: 'project', origin: 'project', selectable: true,
      }], warnings: [] } };
      if (url.pathname === '/v1/activity') return { json: { jobs: [run], needs_you: [], providers: [] } };
    });
    for (const [width, height] of [[1440, 900], [400, 812]]) {
      await page.setViewportSize({ width, height });
      await page.locator('#prompt').fill('/rev');
      await page.locator('#resource-menu').waitFor({ state: 'visible' });
      assert.match(await page.locator('#resource-menu').innerText(), /Workflows/i);
      const option = page.getByRole('option').filter({ hasText: 'review' }).first();
      await option.scrollIntoViewIfNeeded();
      const box = await option.boundingBox();
      assert(box && box.x >= 0 && box.x + box.width <= width && box.y >= 0 && box.y + box.height <= height);
      await page.locator('#prompt').press('Escape');
    }
    assert.deepEqual(errors, []);
    console.log('PASS workflow palette at desktop/mobile sizes');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
