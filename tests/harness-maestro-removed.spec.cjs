// The Maestro planner is gone from the UI; conversations an older build stored with it still open, read-only.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises'), os = require('node:os'), path = require('node:path');
const { spawn } = require('node:child_process');

(async () => {
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), 'maestro-removed-'));
  const proc = spawn(process.env.PYTHON || path.join(__dirname, '../.venv/bin/python'),
    ['-m', 'tests.maestro_browser_fixture', folder, 'legacy'],
    { cwd: path.join(__dirname, '..'), env: { ...process.env, HOME: folder }, stdio: ['ignore', 'pipe', 'pipe'] });
  let log = '', browser;
  proc.stderr.on('data', chunk => { log += chunk; });
  try {
    let ready;
    for (let i = 0; i < 100 && !ready; i++) {
      try {
        const candidate = JSON.parse(await fs.readFile(path.join(folder, 'ready.json')));
        if ((await fetch('http://127.0.0.1:' + candidate.port + '/v1/version')).ok) ready = candidate;
      } catch {}
      if (!ready && proc.exitCode !== null) throw Error(log);
      if (!ready) await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert(ready, log);
    const legacy = JSON.parse(await fs.readFile(path.join(folder, 'legacy.json')));
    const origin = 'http://127.0.0.1:' + ready.port;
    browser = await chromium.launch();
    const context = await browser.newContext();
    await context.addCookies([{ name: 'harness_session', value: ready.session, url: origin }]);
    const page = await context.newPage();
    page.setDefaultTimeout(8000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const version = (await fs.readFile(path.join(__dirname, '../agent_service/VERSION'), 'utf8')).trim();
    await page.addInitScript(value => localStorage.setItem('keepharness-tour-seen', value), version);
    await page.goto(origin);
    await page.locator('#startup-gate').waitFor({ state: 'hidden' });

    // (a) The model picker offers real providers only.
    await page.locator('#model-trigger').click();
    await page.locator('#model-menu details[data-provider="codex"]').waitFor();
    assert.equal(await page.locator('#model-menu details[data-provider="maestro"]').count(), 0);
    assert.doesNotMatch(await page.locator('#model-menu').innerText(), /maestro|auto plan/i);
    await page.keyboard.press('Escape');

    // (b) "/maestro" is not a built-in command; the declared agent still shows up for "/rev".
    await page.locator('#prompt').fill('/maestro');
    await page.waitForTimeout(300);
    assert.equal(await page.getByRole('option', { name: /maestro/i }).count(), 0);
    await page.locator('#prompt').fill('/rev');
    await page.getByRole('option', { name: /reviewer/ }).waitFor();
    await page.locator('#prompt').fill('');

    // (c) Settings no longer carries a plan-review policy.
    await page.locator('#settings').click();
    for (const section of ['models', 'general']) {
      const tab = page.locator('[data-settings=' + section + ']');
      if (await tab.count()) await tab.click();
    }
    assert.equal(await page.locator('#maestro-plan-policy').count(), 0);
    assert.doesNotMatch(await page.locator('#settings-dialog').innerText(), /Plan review for submitted runs|maestro/i);
    await page.locator('#settings-close').click();

    // (d) + (e) Conversations stored with backend "maestro" reopen on a real provider, plan card read-only.
    for (const [state, expected] of [['invalidated', /Not active/], ['resolved', /Approved/]]) {
      const job = legacy[state];
      const stored = await (await page.request.get(origin + '/v1/jobs/' + job)).json();
      assert.equal(stored.request.backend, 'maestro', 'the seeded conversation keeps its old backend');
      await page.evaluate(id => load(id), job);
      const card = page.locator('#gate-legacy-' + job);
      await card.waitFor();
      assert.match(await card.locator('.state-pill').innerText(), expected);
      assert.equal(await card.getByRole('button', { name: /approve|discard|edit/i }).count(), 0);
      assert.equal(await card.locator('button').count(), 0);
      assert.equal(await page.evaluate(() => selected().backend), 'codex');
      assert.doesNotMatch(await page.locator('#model-trigger').innerText(), /maestro|auto plan/i);
      assert.equal(await page.locator('.run-plan-actions').count(), 0);
    }
    // A live declared workflow card (no gate) reads Running until the run ends.
    await page.evaluate(() => showMaestroPlan({ steps: [{ role: 'Reviewer', backend: 'codex', model: 'gpt', task: 'Check the diff' }] }));
    const live = page.locator('.maestro-plan-card:not([id])');
    await live.waitFor();
    assert.match(await live.locator('.state-pill').innerText(), /Running/);
    assert.match(await live.locator('[role="status"]').innerText(), /The workflow is running these steps\./);
    assert.equal(await live.locator('button').count(), 0);
    await page.evaluate(() => renderPlanOutcome(document.querySelector('.maestro-plan-card:not([id])'), 'completed'));
    assert.match(await live.locator('.state-pill').innerText(), /Approved · Completed/);
    assert.deepEqual(errors, []);
    console.log('PASS Maestro is absent from the picker, "/" commands and Settings; old plan conversations open read-only');
  } finally {
    if (browser) await browser.close();
    proc.kill('SIGTERM');
    await new Promise(resolve => {
      if (proc.exitCode !== null) return resolve();
      proc.once('exit', resolve);
      setTimeout(() => proc.kill('SIGKILL'), 2000).unref();
    });
    await fs.rm(folder, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
