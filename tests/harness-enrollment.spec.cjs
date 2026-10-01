const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const readline = require('node:readline');
const assert = require('node:assert/strict');
const path = require('node:path');
(async () => {
  const server = spawn(process.env.PYTHON || path.join(__dirname, '../.venv/bin/python'), [path.join(__dirname, 'enrollment_fixture.py')], { stdio: ['ignore', 'pipe', 'inherit'] });
  const browser = await chromium.launch();
  try {
    const lines = readline.createInterface({ input: server.stdout });
    const [line] = await Promise.race([once(lines, 'line', { signal: AbortSignal.timeout(15000) }), once(server, 'exit').then(() => { throw new Error('Enrollment fixture exited before startup'); })]);
    const fixture = JSON.parse(line);
    const page = await browser.newPage();
    await page.goto(fixture.origin + '/approve-device?nonce=' + fixture.nonce);
    const posted = page.waitForResponse(response => response.request().method() === 'POST');
    await page.getByRole('button', { name: 'Enable approvals on this browser' }).click();
    assert.equal((await posted).status(), 303, 'native same-origin form enrolls without injected request headers');
    const response = await page.request.post(fixture.origin + '/v1/approvals/' + fixture.gate_id, { data: { choice: 'yes' } });
    assert.equal(response.status(), 200);
    const cookie = (await page.context().cookies()).find(item => item.name === 'harness_session');
    assert(cookie.httpOnly && cookie.sameSite === 'Strict');
    console.log('PASS: native browser enrollment and human gate resolution');
  } finally {
    await browser.close();
    if (server.exitCode === null && server.signalCode === null) {
      const exited = once(server, 'exit');
      server.kill('SIGTERM');
      await exited;
    }
  }
})().catch(error => { console.error(error); process.exit(1); });
