// Round-one layout and accessibility regressions use synthetic routes and real hit tests.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const { mount, run, span } = require('./run-console-fixture.cjs');
const { handleHitZones, assertHitAreas } = require('./support/handle-hit-zone.cjs');
const resources = ['command', 'skill', 'agent'].map((kind, index) => ({ id: 'project/p/' + kind, resource_id: 'project/p/' + kind, revision: '1', name: ['check', 'inspect', 'reviewer'][index], kind, scope: 'project', origin: 'codex', selectable: true, description: 'Inspect synthetic evidence', source: '/fixture/' + kind }));
async function fixture(browser, width = 1024, plan = false) {
  const page = await browser.newPage({ viewport: { width, height: 812 } });
  page.setDefaultTimeout(5000);
  await page.addInitScript(() => localStorage.setItem('activity-open', '1'));
  await mount(page, async url => {
    if (url.pathname === '/v1/models') return { json: { models: [{ id: 'fixture', backend: 'codex', efforts: ['medium'], execution_modes: ['native', 'scoped'] }], providers: { codex: true } } };
    if (url.pathname === '/v1/resources') return { json: { items: resources, warnings: [] } };
    if (url.pathname === '/v1/activity') return { json: { counts: { running: 1, queued: 0, needs_you: plan ? 1 : 0 }, jobs: [{ ...run, backend: 'codex' }], needs_you: plan ? [{ job_id: run.job_id, gate_id: 'plan', kind: 'gate', approval_kind: 'maestro_plan', plan: { steps: [{ role: 'Reviewer', backend: 'codex', model: 'fixture', task: 'Review' }] } }] : [], providers: [] } };
    if (url.pathname.endsWith('/spans')) return { json: { spans: Array.from({ length: 4 }, (_, i) => ({ ...span, span_id: 'span-' + i, name: 'Step ' + i })) } };
    if (url.pathname.endsWith('/events')) return { json: { events: [], has_more: false } };
  });
  return page;
}
async function unobscured(locator) {
  return locator.evaluate(node => { const r = node.getBoundingClientRect(); const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); return !!hit && (node === hit || node.contains(hit)); });
}
(async () => {
  const browser = await chromium.launch();
  const failures = [];
  async function check(name, test) { try { await test(); console.log('PASS ' + name); } catch (error) { failures.push(name + ': ' + error.stack); console.error('FAIL ' + name + ': ' + error.message); } }
  try {
    await check('A1-F1 palette options and preview occupy separate visible regions', async () => {
      for (const width of [1280, 1024, 400]) {
        const page = await fixture(browser, width);
        await page.fill('#prompt', '/');
        await page.locator('#resource-preview strong').waitFor();
        assert(await page.locator('#resource-preview').isVisible(), 'preview visible at ' + width);
        for (const option of await page.locator('#resource-menu [role=option]').all()) {
          await option.scrollIntoViewIfNeeded();
          assert(await unobscured(option.locator('strong')), 'option label uncovered at ' + width);
        }
        await page.locator('#resource-menu [role=option]').first().focus();
        await page.keyboard.press('ArrowDown');
        const selected = await page.locator('#resource-menu [aria-selected=true]').innerText();
        assert.match(selected, new RegExp((await page.locator('#resource-preview strong').innerText()).split(' · ').at(-1)));
        assert(await unobscured(page.locator('#resource-preview strong')), 'selected preview stays visible');
        await page.close();
      }
    });
    await check('A1-F2 default console contains complete pipeline cards', async () => {
      const page = await fixture(browser);
      await page.setViewportSize({ width: 1024, height: 768 });
      await page.keyboard.press('Control+j');
      await page.locator('.run-span-row').first().waitFor();
      await page.waitForFunction(() => { const card = document.querySelector('.run-span-row'); return card && card.getBoundingClientRect().bottom <= card.closest('.run-console-body').getBoundingClientRect().bottom; }, null, { timeout: 3000 });
      const bounds = await page.locator('.run-span-tokens').first().evaluate(node => { const card = node.closest('.run-span-row').getBoundingClientRect(), body = node.closest('.run-console-body').getBoundingClientRect(); return { bottom: card.bottom, limit: body.bottom }; });
      assert(bounds.bottom <= bounds.limit, JSON.stringify(bounds));
      assert(await unobscured(page.locator('.run-span-tokens').first()));
      await page.close();
    });
    await check('A1-F3 plan heading and instructions do not overlap', async () => {
      const page = await fixture(browser, 1024, true);
      await page.keyboard.press('Control+j');
      await page.locator('#needs-you-toggle').click();
      await page.locator('.needs-you-card').waitFor();
      await page.locator('.run-span-row').nth(3).waitFor();
      const boxes = await page.evaluate(() => { const node = document.querySelector('.needs-you-card'); return ['h3', 'p'].map(selector => { const range = document.createRange(); range.selectNodeContents(node.querySelector(selector)); return range.getBoundingClientRect().toJSON(); }); });
      assert(boxes[0].width > 0);
      assert(boxes[0].right <= boxes[1].left || boxes[0].bottom <= boxes[1].top || boxes[1].bottom <= boxes[0].top, JSON.stringify(boxes));
      await page.close();
    });
    await check('A1-F4 mobile tour leaves composer, controls and status visible', async () => {
      const page = await fixture(browser, 400);
      await page.evaluate(() => keepHarnessTour.start());
      const seen = new Set();
      while (await page.locator('#tour-root').count()) {
        const title = await page.locator('#tour-title').innerText();
        if (['Write and route work', 'Live status and the Run console'].includes(title)) {
          seen.add(title);
          await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
          await page.waitForFunction(() => [...document.querySelectorAll('#tour-card, .tour-spotlight')].every(node => node.getAnimations().length === 0));
          const [card, spot] = await page.locator('#tour-card, .tour-spotlight').evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().toJSON()));
          const overlap = Math.max(0, Math.min(card.right, spot.right) - Math.max(card.left, spot.left)) * Math.max(0, Math.min(card.bottom, spot.bottom) - Math.max(card.top, spot.top));
          assert.equal(overlap, 0, title);
        }
        assert(await unobscured(page.locator('#tour-next')));
        await page.locator('#tour-next').click();
      }
      assert.equal(seen.size, 2);
      await page.close();
    });
    await check('A1-F5 origin icons inherit the theme foreground', async () => {
      const page = await fixture(browser, 1280);
      for (const theme of ['porcelain', 'amethyst', 'petroleum']) {
        await page.evaluate(theme => HarnessTheme.apply(theme), theme);
        await page.fill('#prompt', '/');
        await page.locator('.resource-origin-icon svg').first().waitFor();
        const colors = await page.locator('.resource-origin-icon svg').first().evaluate(node => ({ fill: getComputedStyle(node).fill, color: getComputedStyle(node).color }));
        assert.equal(colors.fill, colors.color, theme);
      }
      await page.close();
    });
    await check('A2-F1 mobile panel contains visible keyboard focus and restores it', async () => {
      const page = await fixture(browser, 400);
      await page.locator('#panel-toggle').focus();
      await page.keyboard.press('Enter');
      for (const key of ['Tab', 'Tab', 'Tab', 'Shift+Tab', 'Shift+Tab', 'Shift+Tab']) {
        assert(await page.evaluate(() => document.querySelector('#activity-panel').contains(document.activeElement)));
        assert(await unobscured(page.locator(':focus')));
        await page.keyboard.press(key);
      }
      await page.keyboard.press('Escape');
      assert.equal(await page.locator(':focus').getAttribute('id'), 'panel-toggle');
      await page.close();
    });
    await check('A2-F1 docked panel updates focus and semantics at overlay breakpoints', async () => {
      const page = await fixture(browser, 1440);
      await page.locator('#prompt').focus();
      await page.setViewportSize({ width: 800, height: 812 });
      await page.waitForFunction(() => { const panel = document.getElementById('activity-panel'); return panel.getAttribute('aria-modal') === 'true' && panel.contains(document.activeElement); });
      assert.equal(await page.locator('#activity-panel').getAttribute('aria-modal'), 'true');
      assert(await page.evaluate(() => document.querySelector('#activity-panel').contains(document.activeElement)));
      assert(await unobscured(page.locator(':focus')));
      await page.setViewportSize({ width: 1440, height: 812 });
      await page.waitForFunction(() => { const panel = document.getElementById('activity-panel'); return panel.getAttribute('aria-modal') === null && panel.getAttribute('role') === 'complementary'; });
      assert.equal(await page.locator('#activity-panel').getAttribute('aria-modal'), null);
      await page.close();
    });
    await check('A2-F2 Attention owns Escape and clears disclosure through inbox', async () => {
      const page = await fixture(browser, 1440);
      await page.locator('#attention-bell').click();
      await page.locator('[data-attention-filter]').first().focus();
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('#attention-popover').isVisible(), false);
      assert(await page.locator('#activity-panel').isVisible());
      assert.equal(await page.locator(':focus').getAttribute('id'), 'attention-bell');
      await page.locator('#attention-bell').click();
      await page.locator('#attention-open-inbox').click();
      assert.equal(await page.locator('#attention-bell').getAttribute('aria-expanded'), 'false');
      await page.close();
    });
    await check('A2-F3 resize handles expose 24px hit regions', async () => {
      for (const width of [1440, 400]) {
        const page = await fixture(browser, width);
        if (!(await page.locator('#activity-panel').isVisible())) await page.locator('#panel-toggle').click();
        // D-033: the accordion headers of the Activities view own their whole hit area (no resize handle shares it).
        await page.locator('#activity-toggle').click();
        for (const head of await page.locator('#activity-panel .accordion-head').all()) {
          await head.scrollIntoViewIfNeeded();
          assert(await head.evaluate(node => { const r = node.getBoundingClientRect(); return [2, r.height - 2].every(y => node.contains(document.elementFromPoint(r.x + r.width / 2, r.y + y))); }), 'accordion headers do not share resize hit areas');
        }
        // The Files section and its row handle live in the Files view.
        await page.locator('#files-toggle').click();
        // The column handle is drawn as a 6 px strip (WP-16 L64) but keeps a 24 px hit area in free space (WCAG 2.5.8).
        assertHitAreas(await handleHitZones(page, '#activity-panel-resize'));
        for (const handle of await page.locator('#activity-panel-resize').all()) assert(await unobscured(handle));
        for (const summary of await page.locator('#activity-panel .workspace-section > summary').all()) {
          await summary.scrollIntoViewIfNeeded();
          assert(await summary.evaluate(node => { const r = node.getBoundingClientRect(); return [2, r.height - 2].every(y => node.contains(document.elementFromPoint(r.x + r.width / 2, r.y + y))); }), 'section summaries do not share resize hit areas');
        }
        // D-033: Files fills its view instead of carrying a row resize handle.
        assert.equal(await page.locator('#workspace-files-resize').count(), 0);
        const gap = await page.evaluate(() => { const panel = document.getElementById('activity-panel'); return panel.getBoundingClientRect().bottom - parseFloat(getComputedStyle(panel).paddingBottom) - document.getElementById('workspace-files').getBoundingClientRect().bottom; });
        assert(gap >= -1 && gap <= 24, 'Files reaches the bottom of the panel: ' + gap);
        await page.close();
      }
    });
    await check('A2-F4 sidebar disclosure follows responsive visibility', async () => {
      const page = await fixture(browser, 1440);
      for (const width of [400, 1440, 400, 1440]) {
        await page.setViewportSize({ width, height: 844 });
        await page.waitForFunction(() => document.getElementById('menu').getAttribute('aria-expanded') === String(document.getElementById('sidebar').checkVisibility()));
        assert.equal(await page.locator('#menu').getAttribute('aria-expanded'), String(await page.locator('#sidebar').isVisible()));
      }
      await page.close();
    });
    await check('A2-F5 tour restores focus after its desktop opener becomes hidden', async () => {
      for (const exit of ['Escape', 'Skip']) {
        const page = await fixture(browser, 1000);
        await page.keyboard.press("Control+,");await page.locator('#about').click();
        await page.locator('#about-dialog [data-tour-action=start]').click();
        await page.setViewportSize({ width: 400, height: 844 });
        await page.waitForFunction(() => document.getElementById('activity-panel').hidden && !document.getElementById('about').checkVisibility());
        if (exit === 'Escape') await page.keyboard.press('Escape'); else await page.locator('#tour-skip').click();
        assert.equal(await page.locator(':focus').getAttribute('id'), 'prompt');
        assert(await unobscured(page.locator(':focus')));
        await page.close();
      }
    });
    assert.deepEqual(failures, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
