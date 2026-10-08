const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const origin = process.env.HARNESS_URL;
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    page.setDefaultTimeout(8000);
    let closed = 0;
    await page.route('**/v1/**', route => {
      const request = route.request(), url = new URL(request.url());
      const data = url.pathname === '/v1/projects' ? { projects: ['sem-projeto'], details: {} }
        : url.pathname === '/v1/models' ? { models: [{ id: 'fixture', name: 'Fixture', backend: 'local', efforts: ['low'], permissions: { upload: true }, temporary_chat: true }], providers: { local: true }, uploads_enabled: true }
        : url.pathname === '/v1/conversations' ? { conversations: [] }
        : url.pathname === '/v1/temporary' ? { id: 'temporary-fixture' }
        : url.pathname.startsWith('/v1/temporary/') ? (request.method() === 'DELETE' && ++closed, {})
        : url.pathname === '/v1/activity' ? { jobs: [], needs_you: [], counts: {}, providers: [] }
        : url.pathname === '/v1/version' ? { version: 'fixture', build: 'fixture' } : {};
      return route.fulfill({ json: data });
    });
    await page.addInitScript(() => localStorage.setItem('keepharness-tour-seen', '0.16.0'));
    await page.goto(origin);
    await page.locator('#startup-gate').waitFor({ state: 'hidden' });
    await page.fill('#prompt', 'ordinary draft');
    await page.locator('#new-temporary').click();
    await page.locator('#temporary-chat-notice').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#prompt').inputValue(), '');
    assert.equal(await page.locator('#prompt').evaluate(node => document.activeElement === node), true);
    console.log('PASS P1/P4 novice and keyboard user: visible New entry, labelled notice, composer focus');
    await page.fill('#prompt', 'PRIVATE-MARKER-60');
    assert.equal(await page.evaluate(() => JSON.stringify({ ...sessionStorage, ...localStorage }).includes('PRIVATE-MARKER-60')), false);
    assert.equal(await page.evaluate(() => JSON.stringify(window.history.state).includes('temporary-fixture')), false);
    await page.evaluate(() => event({ id: 1, type: 'gate_required', data: { gate_id: 'PRIVATE-MARKER-60-gate', question: 'Private choice?', options: [{ id: 'PRIVATE-MARKER-60-choice', label: 'Private option' }] } }));
    await page.getByRole('radio', { name: 'Private option' }).check();
    assert.equal(await page.evaluate(() => JSON.stringify({ ...sessionStorage, ...localStorage }).includes('PRIVATE-MARKER-60')), false);
    page.once('dialog', dialog => dialog.dismiss());
    await page.locator('#close-temporary-chat').click();
    assert.equal(await page.locator('#prompt').inputValue(), 'PRIVATE-MARKER-60');
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#close-temporary-chat').click();
    await page.locator('#temporary-chat-notice').waitFor({ state: 'hidden' });
    assert.equal(closed, 1);
    assert.equal(await page.locator('#prompt').inputValue(), 'ordinary draft');
    await page.keyboard.press('Control+,');
    await page.locator('#settings-dialog').waitFor({ state: 'visible' });
    await page.locator('#settings-close').click();
    await page.keyboard.press('Control+Shift+N');
    await page.locator('#temporary-chat-notice').waitFor({ state: 'visible' });
    await page.fill('#prompt', 'PRIVATE-MARKER-60');
    page.once('dialog', dialog => dialog.dismiss());
    await page.goBack();
    await page.waitForTimeout(100);
    assert(await page.locator('#temporary-chat-notice').isVisible());
    assert.equal(await page.locator('#prompt').inputValue(), 'PRIVATE-MARKER-60');
    page.once('dialog', dialog => dialog.accept());
    await page.goBack();
    await page.locator('#temporary-chat-notice').waitFor({ state: 'hidden' });
    await page.locator('#settings-dialog').waitFor({ state: 'visible' });
    await page.locator('#settings-close').click();
    await page.keyboard.press('Control+Shift+N');
    await page.locator('#temporary-chat-notice').waitFor({ state: 'visible' });
    console.log('PASS P2 power/rushed user: shortcut, browser Back cancel and discard');
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.locator('#temporary-chat-notice').isVisible(), true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.fill('#prompt', 'PRIVATE-MARKER-60');
    const reloadPrompt = page.waitForEvent('dialog');
    await page.evaluate(() => setTimeout(() => location.reload(), 0));
    const reloadDialog = await reloadPrompt;
    assert.equal(reloadDialog.type(), 'beforeunload');
    await reloadDialog.dismiss();
    assert(await page.locator('#temporary-chat-notice').isVisible());
    page.once('dialog', dialog => dialog.accept());
    await page.reload();
    await page.locator('#startup-gate').waitFor({ state: 'hidden' });
    assert.equal(await page.locator('#temporary-chat-notice').isVisible(), false);
    assert(!(await page.locator('#prompt').inputValue()).includes('PRIVATE-MARKER-60'));
    assert(!(await page.locator('body').innerText()).includes('PRIVATE-MARKER-60'));
    console.log('PASS reload: cancellation retains private draft; confirmed reload never restores it');
    await page.keyboard.press('Control+Shift+N');
    await page.locator('#temporary-chat-notice').waitFor({ state: 'visible' });
    await page.fill('#prompt', 'PRIVATE-MARKER-60');
    await page.evaluate(() => dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })));
    assert.equal(await page.locator('#temporary-chat-notice').isVisible(), false);
    assert.equal(await page.locator('#prompt').inputValue(), '');
    assert.equal(await page.evaluate(() => temporarySession), '');
    console.log('PASS private gate choices never persist; BFCache pagehide clears private state');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
