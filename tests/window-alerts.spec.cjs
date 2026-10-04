// D35: needs-you count in the window title and OS notifications while the window is unfocused.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const { mount } = require('./run-console-fixture.cjs');
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.addInitScript(() => {
      window.shown = [];
      window.focused = false;
      window.permission = 'granted';
      window.Notification = class {
        static get permission() { return window.permission; }
        static requestPermission() { window.asked = (window.asked || 0) + 1; return Promise.resolve('granted'); }
        constructor(title, options) { window.shown.push(`${title}|${options.body}`); }
      };
      document.hasFocus = () => window.focused;
    });
    await mount(page, async () => null);
    const snapshot = (jobs, needs = []) =>
      page.evaluate(([j, n]) => applyActivitySnapshot({ jobs: j, needs_you: n, counts: {} }), [jobs, needs]);
    const now = () => Date.now() / 1000;
    const job = (state, extra = {}) => ({ job_id: 'job-1', conversation_id: 'c1', title: 'Quarterly report', state, created: now(), ...extra });
    const shown = () => page.evaluate(() => window.shown);
    const base = await page.title();

    await snapshot([job('running')]);
    assert.deepEqual(await shown(), [], 'the first snapshot only primes');
    await snapshot([job('running')], [{ ...job('running'), kind: 'approval', approval_id: 'a1' }]);
    assert.equal(await page.title(), `(1) ${base}`);
    assert.deepEqual(await shown(), ['KeepHarness|Needs you: Quarterly report']);
    await snapshot([job('running')], [{ ...job('running'), kind: 'approval', approval_id: 'a1' }]);
    assert.equal((await shown()).length, 1, 'the same request is announced once');
    await snapshot([job('completed')]);
    assert.equal(await page.title(), base, 'the count leaves the title when nothing needs the user');
    assert.deepEqual((await shown()).slice(1), ['KeepHarness|Finished: Quarterly report']);
    await snapshot([job('completed')]);
    assert.equal((await shown()).length, 2, 'a finished run is announced once');
    await snapshot([job('completed'), job('failed', { job_id: 'job-2', title: 'Nightly import' })]);
    assert.deepEqual((await shown()).slice(2), ['KeepHarness|Failed: Nightly import']);
    await snapshot([job('completed'), job('failed', { job_id: 'old', title: 'Old', created: 1 })]);
    assert.equal((await shown()).length, 3, 'a job that merely scrolled into the list is not announced');

    // A focused window, or a missing permission, stays silent.
    await page.evaluate(() => { window.focused = true; });
    await snapshot([job('failed', { job_id: 'job-3', title: 'Focused' })]);
    await page.evaluate(() => { window.focused = false; window.permission = 'denied'; });
    await snapshot([job('failed', { job_id: 'job-4', title: 'Denied' })]);
    assert.equal((await shown()).length, 3);

    // The permission is requested once, with the first message.
    await page.evaluate(() => { window.permission = 'default'; });
    await page.locator('#prompt').fill('hello');
    await page.locator('#prompt').press('Enter');
    assert.equal(await page.evaluate(() => window.asked), 1);
    assert.deepEqual(errors, []);
    console.log('PASS: window title count, notifications only when unfocused and granted, once per event, permission asked with the first message');
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exit(1); });
