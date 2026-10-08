const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const net = require('node:net');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const markers = ['TEMPORARY-PRIVATE-PROMPT-60', 'TEMPORARY-PRIVATE-ATTACHMENT-60', 'OPERATOR_OK'];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function scan(root, active = false) {
  for (const entry of await fs.readdir(root, { withFileTypes: true })) {
    if (active && entry.name === 'temporary-chats') continue;
    const file = path.join(root, entry.name);
    if (entry.isDirectory()) await scan(file, active);
    else if (entry.isFile()) {
      const bytes = await fs.readFile(file);
      for (const marker of markers) assert(!bytes.includes(Buffer.from(marker)), `${marker} leaked into ${file}`);
    }
  }
}
(async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'temporary-chat-live-'));
  const socket = net.createServer();
  socket.listen(0, '127.0.0.1'); await once(socket, 'listening');
  const port = socket.address().port; await new Promise(resolve => socket.close(resolve));
  const origin = `http://127.0.0.1:${port}`;
  const log = await fs.open(path.join(root, 'server.log'), 'w');
  const server = spawn(process.env.PYTHON || 'python3', ['tests/fixtures/temporary_chat_server.py', '--root', root, '--port', String(port)], { stdio: ['ignore', log.fd, log.fd] });
  let browser;
  try {
    let ready = false;
    for (let i = 0; i < 100; i++) {
      if (server.exitCode !== null) throw Error(await fs.readFile(path.join(root, 'server.log'), 'utf8'));
      try { if ((await fetch(origin + '/v1/version')).ok) { ready = true; break; } } catch {}
      await delay(100);
    }
    assert(ready, 'fixture server ready');
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    page.setDefaultTimeout(15000);
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem('keepharness-tour-seen', '0.16.0'));
    await page.goto(origin);
    await page.locator('#startup-gate').waitFor({ state: 'hidden' });
    await page.getByRole('button', { name: 'New temporary chat', exact: true }).last().click();
    await page.locator('#temporary-chat-notice').waitFor({ state: 'visible' });
    await page.locator('#file').setInputFiles({ name: 'temporary-reference.txt', mimeType: 'text/plain', buffer: Buffer.from(markers[1]) });
    await page.waitForFunction(() => files.length === 1 && uploads === 0);
    await page.locator('#file').setInputFiles({ name: 'temporary-image.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1X8AAAAASUVORK5CYII=', 'base64') });
    await page.waitForFunction(() => files.length === 2 && uploads === 0);
    assert(await page.locator('#attachments img').count(), JSON.stringify(await page.evaluate(() => ({ files, status: document.getElementById('status').textContent })))) ;
    assert(await page.locator('#attachments img').evaluate(image => image.complete && image.naturalWidth > 0));
    await page.fill('#prompt', markers[0]);
    const sent = page.waitForResponse(response => new URL(response.url()).pathname === '/v1/jobs' && response.request().method() === 'POST');
    await page.click('#send');
    const response = await sent;
    assert.equal(response.status(), 202, await response.text());
    const { job_id: job } = await response.json();
    await page.getByText('OPERATOR_OK', { exact: false }).last().waitFor();
    assert(await page.locator('.message-image img').first().evaluate(image => image.complete && image.naturalWidth > 0));
    await page.waitForFunction(() => !busy && !submitting);
    console.log('PASS P3 file professional: upload and actual fake CLI answer');
    for (const theme of ['paper', 'amethyst']) {
      await page.evaluate(name => HarnessTheme.apply(name, false), theme);
      await page.setViewportSize({ width: 390, height: 844 });
      assert(await page.locator('#temporary-chat-notice').isVisible());
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      const box = await page.locator('#temporary-chat-notice').boundingBox();
      assert(box.x >= 0 && box.x + box.width <= 390 && box.y >= 0 && box.y + box.height <= 844);
      if (process.env.TEMPORARY_SCREENSHOT_DIR) {
        await fs.mkdir(process.env.TEMPORARY_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.TEMPORARY_SCREENSHOT_DIR, `temporary-${theme}-390.png`) });
      }
    }
    console.log('PASS P5/P7 mobile and UX: 390px, Paper/dark permanent notice');
    await page.evaluate(() => HarnessPrefs.flush());
    const stored = await page.evaluate(() => JSON.stringify({ session: { ...sessionStorage }, local: { ...localStorage }, history: window.history.state }));
    for (const marker of [...markers, job]) assert(!stored.includes(marker), 'no browser storage leak: ' + marker);
    for (const endpoint of ['/v1/history', '/v1/conversations', '/v1/activity', '/v1/ui-state']) {
      const value = await (await fetch(origin + endpoint)).text();
      for (const marker of [...markers, job]) assert(!value.includes(marker), 'no normal API leak: ' + endpoint);
    }
    await scan(root, true);
    console.log('PASS P6 engineer: no marker/ID in browser storage, normal APIs, durable DB/sessions/logs while active');
    page.once('dialog', dialog => dialog.dismiss());
    await page.locator('#close-temporary-chat').click();
    assert(await page.locator('#temporary-chat-notice').isVisible());
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#close-temporary-chat').click();
    await page.locator('#temporary-chat-notice').waitFor({ state: 'hidden' });
    for (let i = 0; i < 100; i++) {
      if (!(await fs.readdir(path.join(root, 'chat', 'temporary-chats'))).length) break;
      await delay(50);
    }
    assert.deepEqual(await fs.readdir(path.join(root, 'chat', 'temporary-chats')), []);
    await scan(root);
    console.log('PASS discard: uploads removed and no durable content after close');
    assert.deepEqual(errors, []);
    await browser.close(); browser = null;
    server.kill('SIGTERM'); await once(server, 'exit');
    await scan(root);
    console.log('PASS shutdown: no temporary content on disk');
  } finally {
    if (browser) await browser.close();
    if (server.exitCode === null) { server.kill('SIGTERM'); await once(server, 'exit'); }
    await log.close(); await fs.rm(root, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
