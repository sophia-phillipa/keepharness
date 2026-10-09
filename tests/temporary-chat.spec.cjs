const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const origin = process.env.HARNESS_URL;
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    page.setDefaultTimeout(8000);
    let closed = 0;
    let failNextTemporary = false;
    let originDeleted = false;
    await page.route('**/v1/**', route => {
      const request = route.request(), url = new URL(request.url());
      if (url.pathname === '/v1/temporary' && failNextTemporary) {
        failNextTemporary = false;
        return route.fulfill({ status: 500, json: { code: 'temporary_storage_unavailable' } });
      }
      if (originDeleted && url.pathname === '/v1/conversations/cx') return route.fulfill({ status: 404, json: { code: 'conversation_not_found', error: 'Conversation not found' } });
      const data = url.pathname === '/v1/projects' ? { projects: ['sem-projeto'], details: {} }
        : url.pathname === '/v1/models' ? { models: [{ id: 'fixture', name: 'Fixture', backend: 'local', efforts: ['low'], permissions: { upload: true }, temporary_chat: true }], providers: { local: true }, uploads_enabled: true }
        : url.pathname === '/v1/conversations' ? { conversations: ['cx', 'cy'].map(id => ({ id, title: 'Conversation ' + id, project: 'sem-projeto', state: 'completed', last_job_id: 't-' + id, execution: { backend: 'local', model: 'fixture' } })) }
        : /^\/v1\/conversations\/c[xy]$/.test(url.pathname) ? { title: 'Conversation', turns: [{ id: 't-' + url.pathname.slice(-2), project: 'sem-projeto', state: 'completed', request: { backend: 'local', model: 'fixture', prompt: 'Question', effort: 'configured' }, result: { answer: 'Answer' } }] }
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
    const entryBounds = await page.locator('#composer-temporary').boundingBox();
    assert(entryBounds.width >= 24 && entryBounds.height >= 24, 'temporary entry meets pointer-target minimum');
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
    await page.keyboard.press('Control+Shift+N');
    await page.locator('#temporary-chat-notice').waitFor({ state: 'visible' });
    await page.fill('#prompt', 'PRIVATE-MARKER-60-A');
    page.once('dialog', dialog => dialog.accept());
    await page.keyboard.press('Control+Shift+N');
    await page.waitForFunction(() => !temporaryStarting && temporarySession);
    assert.equal(await page.locator('#prompt').inputValue(), '');
    assert.equal(await page.evaluate(() => JSON.parse(sessionStorage.getItem('conversation-draft:new:sem-projeto')).draft), 'ordinary draft');
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#close-temporary-chat').click();
    assert.equal(await page.locator('#prompt').inputValue(), 'ordinary draft');
    assert.equal(await page.evaluate(() => JSON.parse(sessionStorage.getItem('conversation-draft:new:sem-projeto')).draft), 'ordinary draft');
    console.log('PASS temporary replacement preserves original ordinary draft in composer and storage');
    await page.keyboard.press('Control+Shift+N');
    await page.locator('#temporary-chat-notice').waitFor({ state: 'visible' });
    await page.fill('#prompt', 'PRIVATE-MARKER-60-A');
    failNextTemporary = true;
    page.once('dialog', dialog => dialog.accept());
    await page.keyboard.press('Control+Shift+N');
    await page.waitForFunction(() => !temporaryStarting && !temporarySession);
    assert.equal(await page.locator('#prompt').inputValue(), 'ordinary draft');
    await page.evaluate(() => saveView());
    assert.equal(await page.evaluate(() => JSON.parse(sessionStorage.getItem('conversation-draft:new:sem-projeto')).draft), 'ordinary draft');
    console.log('PASS failed temporary replacement restores ordinary draft before autosave resumes');
    const draftOf = key => page.evaluate(k => JSON.parse(sessionStorage.getItem(k))?.draft, key);
    const openConversation = async id => {
      await page.locator('#history .conversation-row > button', { hasText: 'Conversation ' + id }).click();
      await page.waitForFunction(i => conversation === i && !loading, id);
    };
    await page.fill('#prompt', 'HOME-DRAFT');
    await openConversation('cx');
    await page.fill('#prompt', 'X-DRAFT');
    await page.keyboard.press('Control+Shift+N');
    await page.locator('#temporary-chat-notice').waitFor({ state: 'visible' });
    await page.fill('#prompt', 'PRIVATE-MARKER-60-B');
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#close-temporary-chat').click();
    await page.waitForFunction(() => conversation === 'cx' && !loading);
    assert.equal(await page.locator('#prompt').inputValue(), 'X-DRAFT');
    await page.evaluate(() => saveView());
    assert.equal(await draftOf('conversation-draft:cx'), 'X-DRAFT');
    assert.equal(await draftOf('conversation-draft:new:sem-projeto'), 'HOME-DRAFT');
    console.log('PASS closing a temporary chat returns to its conversation with both drafts intact');
    await page.keyboard.press('Control+Shift+N');
    await page.locator('#temporary-chat-notice').waitFor({ state: 'visible' });
    await page.fill('#prompt', 'PRIVATE-MARKER-60-C');
    page.once('dialog', dialog => dialog.accept());
    await openConversation('cy');
    assert.equal(await page.locator('#prompt').inputValue(), '');
    assert.equal(await draftOf('conversation-draft:cx'), 'X-DRAFT');
    assert.equal(await draftOf('conversation-draft:new:sem-projeto'), 'HOME-DRAFT');
    assert.equal(await page.evaluate(() => JSON.stringify({ ...sessionStorage, ...localStorage }).includes('PRIVATE-MARKER-60')), false);
    console.log('PASS leaving a temporary chat for another conversation keeps every draft and stores nothing private');
    await openConversation('cx');
    originDeleted = true;
    await page.keyboard.press('Control+Shift+N');
    await page.locator('#temporary-chat-notice').waitFor({ state: 'visible' });
    await page.fill('#prompt', 'PRIVATE-MARKER-60-D');
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#close-temporary-chat').click();
    await page.locator('#temporary-chat-notice').waitFor({ state: 'hidden' });
    await page.waitForFunction(() => !loading);
    assert.match(await page.locator('#status').innerText(), /Couldn't open the conversation/);
    assert.equal(await draftOf('conversation-draft:new:sem-projeto'), 'HOME-DRAFT');
    assert.equal(await page.locator('#prompt').inputValue(), 'HOME-DRAFT');
    assert.equal(await page.evaluate(() => JSON.stringify({ ...sessionStorage, ...localStorage }).includes('PRIVATE-MARKER-60')), false);
    originDeleted = false;
    console.log('PASS closing a temporary chat whose origin conversation was deleted keeps the Home draft and the error');
    // The one-row entry must not push a visible composer note (draft limit) out of the viewport.
    await page.fill('#prompt', 'x'.repeat(130000));
    await page.locator('#draft-limit').waitFor({ state: 'visible' });
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 860 });
      const fit = await page.evaluate(() => {
        const row = document.querySelector('.composer-context'), inside = element => {
          const box = element.getBoundingClientRect();
          return box.left >= 0 && box.right <= innerWidth;
        };
        return { contained: row.scrollWidth <= row.clientWidth, note: inside(document.querySelector('#draft-limit')), button: inside(document.querySelector('#composer-temporary')) };
      });
      assert.deepEqual(fit, { contained: true, note: true, button: true }, 'composer context at ' + width + 'px');
    }
    await page.setViewportSize({ width: 1280, height: 860 });
    await page.fill('#prompt', '');
    console.log('PASS the composer context row keeps its note and temporary-chat button inside the viewport');
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
