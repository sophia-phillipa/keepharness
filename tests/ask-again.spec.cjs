// D37: "Ask again" re-sends the question of the newest answer as a new turn.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const { mount } = require('./run-console-fixture.cjs');
const turn = (id, prompt, answer) => ({ id, project: 'sem-projeto', state: 'completed', request: { backend: 'local', model: 'fixture', prompt, effort: 'configured' }, result: { answer } });
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    const errors = [], posted = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await mount(page, async (url, request) => {
      if (url.pathname === '/v1/conversations') return { json: { conversations: [{ id: 'c1', title: 'Pairs', project: 'sem-projeto', state: 'completed', last_job_id: 't2', execution: { backend: 'local', model: 'fixture' } }] } };
      if (url.pathname === '/v1/conversations/c1') return { json: { title: 'Pairs', execution_mode: 'scoped', turns: [turn('t1', 'First question', 'First answer'), turn('t2', 'Second question', 'Second answer')] } };
      if (url.pathname === '/v1/jobs' && request.method() === 'POST') {
        posted.push(request.postDataJSON());
        return { json: { job_id: 't3', backend: 'local', model: 'fixture', execution_mode: 'scoped' } };
      }
      if (url.pathname.endsWith('/events')) return { body: '', headers: { 'content-type': 'text/event-stream' } };
    });
    await page.locator('#history .conversation-row > button', { hasText: 'Pairs' }).click();
    await page.waitForFunction(() => document.querySelectorAll('#messages article.assistant').length === 2);
    const buttons = page.getByTestId('ask-again');
    assert.equal(await buttons.count(), 2);
    assert.equal(await buttons.first().isHidden(), true, 'only the newest answer offers it');
    assert.equal(await buttons.last().isVisible(), true);
    assert.equal(await buttons.last().getAttribute('aria-label'), 'Ask again');

    // A draft in the composer is never overwritten.
    await page.fill('#prompt', 'half-written thought');
    await buttons.last().click();
    assert.equal(await page.locator('#prompt').inputValue(), 'half-written thought');
    assert.deepEqual(posted, []);
    await page.fill('#prompt', '');

    await buttons.last().click();
    await page.waitForFunction(() => document.querySelectorAll('#messages article.user').length === 3);
    assert.equal(posted.length, 1);
    assert.equal(posted[0].prompt, 'Second question');
    assert.equal(posted[0].parent_job_id, 't2');
    assert.deepEqual(errors, []);
    console.log('PASS: Ask again on the newest answer re-sends its question, keeps a draft safe');
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exit(1); });
