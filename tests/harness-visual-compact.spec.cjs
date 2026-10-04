const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { mount, run, span } = require("./run-console-fixture.cjs");
const {
  VIEWPORTS,
  THEMES,
  STATES,
  outputDirectory,
  mountVisual,
  selectState,
  geometry,
  contrastScan,
} = require("./visual/harness-visual-helpers.cjs");

async function fixtureReply(url) {
  if (url.pathname === "/v1/activity") {
    return { json: { counts: { running: 1, queued: 0, needs_you: 0 }, jobs: [run], providers: [], needs_you: [] } };
  }
  if (url.pathname === "/v1/jobs/run-a/spans") return { json: { spans: [span] } };
  if (url.pathname === "/v1/jobs/run-a") return { json: { ...run, request: {} } };
  return null;
}

async function newVisualPage(browser, viewport = { width: 1440, height: 900 }) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  await mountVisual(page);
  if (viewport.width <= 700) {
    await page.evaluate(() => setPanelOpen(false, false));
    await page.locator("#activity-panel").waitFor({ state: "hidden" });
  }
  return { context, page };
}

function assertGeometry(result, label) {
  assert(result.scrollWidth <= result.viewportWidth, `${label}: page scrollWidth ${result.scrollWidth} exceeds ${result.viewportWidth}`);
  assert.deepEqual(result.failures, [], `${label}: key controls must be fully visible and uncovered`);
  assert.deepEqual(result.targetFailures, [], `${label}: visible pointer targets must be at least 24px`);
  assert.deepEqual(result.overflow, [], `${label}: titles and chips must contain overflow`);
}

async function assertPaletteReachability(page, label) {
  const options = page.locator('#resource-menu [role="option"]');
  const count = await options.count();
  assert(count > 0, `${label}: palette must contain an option`);
  for (let index = 0; index < count; index++) {
    const option = options.nth(index);
    await option.evaluate(node => node.scrollIntoView({ block: "nearest", behavior: "instant" }));
    const result = await option.evaluate(node => {
      const rect = node.getBoundingClientRect();
      const menu = node.closest(".resource-options").getBoundingClientRect();
      const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
      return {
        withinMenu: rect.top >= menu.top - 1 && rect.bottom <= menu.bottom + 1,
        reachable: !!hit && (hit === node || node.contains(hit)),
      };
    });
    assert(result.withinMenu, `${label}: palette option ${index + 1}/${count} must scroll fully into the menu`);
    assert(result.reachable, `${label}: palette option ${index + 1}/${count} must be uncovered after scrolling`);
  }
  await page.locator("#resource-menu .resource-options").evaluate(node => { node.scrollTop = 0; });
}

async function captureMatrix(browser, directory, summary) {
  for (const viewport of VIEWPORTS) {
    for (const theme of THEMES) {
      const { context, page } = await newVisualPage(browser, viewport);
      try {
        await page.evaluate(value => window.HarnessTheme.apply(value), theme);
        assert.equal(await page.locator("html").getAttribute("data-palette"), theme);
        if (viewport.width === 1440 && theme === THEMES[0]) {
          // Codex-style shell (2026-10-03): the top bar is a 50 px icon rail and the
          // files/activity panel starts closed, so open it before measuring its width.
          if (await page.locator("#activity-panel").isHidden()) await page.click("#panel-toggle");
          const baseline = await page.evaluate(() => {
            const box = selector => document.querySelector(selector).getBoundingClientRect();
            return {
              rootFont: parseFloat(getComputedStyle(document.documentElement).fontSize),
              topbar: box("#app-topbar").width,
              sidebar: box("#sidebar").width,
              activity: box("#activity-panel").width,
              runningRow: box("#sidebar .conversation-row").height,
            };
          });
          assert.equal(baseline.rootFont, 15, "global font scale must match the approved baseline");
          assert(Math.abs(baseline.topbar - 50) <= .75, `rail baseline drifted: ${baseline.topbar}`);
          assert(Math.abs(baseline.sidebar - 300) <= .75, `sidebar baseline drifted: ${baseline.sidebar}`);
          assert(Math.abs(baseline.activity - 390) <= .75, `activity pane baseline drifted: ${baseline.activity}`);
          assert(Math.abs(baseline.runningRow - 47) <= 2, `conversation row baseline drifted: ${baseline.runningRow}`);
          summary.baselineScale = baseline;
        }
        for (const state of STATES) {
          await selectState(page, state);
          const label = `${viewport.width}x${viewport.height}/${theme}/${state}`;
          if (["slash-palette", "workflow-palette"].includes(state)) await assertPaletteReachability(page, label);
          const measured = await geometry(page, state);
          const filename = `${viewport.width}x${viewport.height}-${theme}-${state}.png`;
          await page.screenshot({ path: path.join(directory, filename), fullPage: false });
          assertGeometry(measured, label);
          if (state === "console-open" && viewport.width >= 1280) {
            assert.equal(measured.stepCard.scrollOffset, 0, `${label}: complete step card must be initially visible without console scrolling`);
            assert(measured.stepCard.initialTop >= measured.stepCard.bodyTop - 1 && measured.stepCard.initialBottom <= measured.stepCard.bodyBottom + 1, `${label}: step card must fit with actions and composer at desktop/tablet widths`);
          }
          if (state === "workflow-palette") {
            const workflow = page.locator('#resource-menu [data-resource-kind="workflow"]');
            assert.equal(await workflow.count(), 1, `${label}: filtered palette must expose one workflow`);
            assert.match(await workflow.innerText(), /Workflow · Review a release in two sequential steps/);
            assert.equal(await workflow.locator("xpath=ancestor::section[1]/h3").textContent(), "Workflows · Project · project");
            assert.match(await page.locator("#resource-preview").innerText(), /release-review[\s\S]*Review a release in two sequential steps/);
          }
          if (state === "plan-card-readonly") {
            const card = page.locator(".maestro-plan-card");
            assert.match(await card.innerText(), /Not active[\s\S]*This plan can no longer be approved/);
            assert.equal(await card.locator("button").count(), 0, `${label}: an old plan card offers no action`);
          }
          const contrast = viewport.width === 1440 ? await page.evaluate(contrastScan) : [];
          assert.deepEqual(contrast, [], `${label}: WCAG AA contrast failures`);
          summary.screenshots.push({ viewport, theme, state, filename, geometry: measured, contrast });
        }
      } finally {
        await context.close();
      }
    }
  }
  assert.equal(summary.screenshots.length, 168, "visual matrix must contain 4 × 6 × 7 screenshots");
  assert.equal(new Set(summary.screenshots.map(item => item.filename)).size, 168, "every matrix screenshot has a distinct filename");
}

async function runScenario(summary, details, action) {
  const entry = { ...details, status: "passed", evidence: "browser assertion" };
  try {
    await action();
  } catch (error) {
    entry.status = "failed";
    entry.evidence = error.message;
  }
  summary.profiles.push(entry);
}

async function runProfileMatrix(browser, summary) {
  {
    const { context, page } = await newVisualPage(browser);
    try {
      await runScenario(summary, { id: "P1-S1", profile: "Beginner", familiarity: "visible labels only", goal: "open execution details", expected: "the visible status control opens a complete console" }, async () => {
        await page.locator("#run-status-toggle").click();
        assert(await page.locator("#run-console").isVisible());
        assert.equal(await page.locator("#run-tab-pipeline").getAttribute("aria-selected"), "true");
      });
      await runScenario(summary, { id: "P1-R1", profile: "Beginner", familiarity: "visible labels only", goal: "recover from opening the wrong surface", expected: "Collapse closes the console and returns focus" }, async () => {
        await page.locator(".run-console-close").click();
        assert.equal(await page.locator("#run-console").isHidden(), true);
        assert.equal(await page.evaluate(() => document.activeElement?.id), "run-status-toggle");
      });
    } finally { await context.close(); }
  }
  {
    const { context, page } = await newVisualPage(browser, { width: 1280, height: 720 });
    try {
      await page.keyboard.press("Control+j");
      const original = await page.locator("#run-console").boundingBox();
      await runScenario(summary, { id: "P2-S1", profile: "Rushed user", familiarity: "works quickly", goal: "inspect then restore console", expected: "a rapid maximize/restore pair does not drift" }, async () => {
        const maximize = page.locator("#run-console-maximize");
        await maximize.click();
        await maximize.click();
        const restored = await page.locator("#run-console").boundingBox();
        assert(Math.abs(restored.height - original.height) <= 2, `rapid maximize/restore drifted from ${original.height}px to ${restored.height}px`);
        assert.equal(await maximize.getAttribute("aria-pressed"), "false");
      });
      await runScenario(summary, { id: "P2-R1", profile: "Rushed user", familiarity: "works quickly", goal: "recover after rapid toggles", expected: "the unsent draft survives two shortcut toggles" }, async () => {
        await page.locator("#prompt").fill("Keep this unsent draft while I inspect the run");
        await page.keyboard.press("Control+j");
        await page.keyboard.press("Control+j");
        assert.equal(await page.locator("#prompt").inputValue(), "Keep this unsent draft while I inspect the run");
      });
    } finally { await context.close(); }
  }
  {
    const { context, page } = await newVisualPage(browser);
    try {
      await runScenario(summary, { id: "P3-S1", profile: "Domain professional", familiarity: "uses long projects and names", goal: "recognize the active work", expected: "the long title stays contained and remains available as a tooltip" }, async () => {
        const title = page.locator("#conversation-title");
        assert.equal(await title.getAttribute("title"), await title.innerText());
        const box = await title.boundingBox();
        assert(box.x + box.width <= 1440);
      });
      await runScenario(summary, { id: "P3-R1", profile: "Domain professional", familiarity: "uses long projects and names", goal: "continue after reopening", expected: "section open state and size persist across reload" }, async () => {
        const section = page.locator('[data-workspace-section="resources"]');
        const handle = page.locator("#workspace-resources-resize");
        await handle.focus();
        await page.keyboard.press("ArrowDown");
        const height = await page.locator("#workspace-resources").evaluate(node => node.getBoundingClientRect().height);
        await section.locator("summary").click();
        await page.waitForFunction(() => JSON.parse(localStorage.getItem("workspace-section-resources") || "null")?.open === false);
        await page.reload();
        await page.locator("#startup-gate").waitFor({ state: "hidden" });
        assert.equal(await section.getAttribute("open"), null);
        assert.equal(await page.locator("#workspace-resources").evaluate(node => node.style.height), `${height}px`);
      });
    } finally { await context.close(); }
  }
  {
    const { context, page } = await newVisualPage(browser, { width: 1280, height: 720 });
    try {
      await page.keyboard.press("Control+j");
      await runScenario(summary, { id: "P4-S1", profile: "Keyboard and low-vision user", familiarity: "keyboard navigation", goal: "resize and maximize without a pointer", expected: "arrows resize by 30px and Enter maximizes/restores with ARIA state" }, async () => {
        const resize = page.locator("#run-console-resize");
        await resize.focus();
        const before = Number(await resize.getAttribute("aria-valuenow"));
        await page.keyboard.press("ArrowUp");
        assert.equal(Number(await resize.getAttribute("aria-valuenow")), before + 30);
        await page.keyboard.press("ArrowDown");
        assert.equal(Number(await resize.getAttribute("aria-valuenow")), before);
        const maximize = page.locator("#run-console-maximize");
        const compact = await page.locator("#run-console").boundingBox();
        await maximize.focus(); await page.keyboard.press("Enter");
        assert.equal(await maximize.getAttribute("aria-pressed"), "true");
        const expanded = await page.locator("#run-console").boundingBox();
        assert(expanded.height > compact.height, `keyboard maximize did not grow console: ${compact.height}→${expanded.height}`);
        await page.keyboard.press("Enter");
        assert.equal(await maximize.getAttribute("aria-pressed"), "false");
        const restored = await page.locator("#run-console").boundingBox();
        assert(Math.abs(restored.height - compact.height) <= 2, `keyboard restore drifted: ${compact.height}→${restored.height}`);
      });
      await runScenario(summary, { id: "P4-R1", profile: "Keyboard and low-vision user", familiarity: "keyboard navigation", goal: "leave the console", expected: "Escape collapses the console and exposes the chat" }, async () => {
        await page.keyboard.press("Escape");
        assert.equal(await page.locator("#run-console").isHidden(), true);
        assert(await page.locator("#prompt").isVisible());
      });
    } finally { await context.close(); }
  }
  {
    const { context, page } = await newVisualPage(browser, { width: 400, height: 812 });
    try {
      await runScenario(summary, { id: "P5-S1", profile: "Mobile user", familiarity: "narrow screen", goal: "write and inspect work", expected: "mobile has no horizontal page scroll and key controls remain reachable" }, async () => {
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        for (const selector of ["#menu", "#panel-toggle", "#model-trigger", ".composer-submit button:not([hidden])", "#run-status-toggle"]) {
          const locator = page.locator(selector);
          const box = await locator.boundingBox();
          assert(box && box.x >= 0 && box.x + box.width <= 400, `${selector} is outside 400px viewport: ${JSON.stringify(box)}`);
          assert(box.y >= 0 && box.y + box.height <= 812, `${selector} is outside 812px viewport: ${JSON.stringify(box)}`);
          assert(await locator.evaluate(node => {
            const rect = node.getBoundingClientRect(), hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
            return !!hit && (hit === node || node.contains(hit) || (node.matches(":disabled") && hit.contains(node)));
          }), `${selector} is covered at its center`);
        }
      });
      await runScenario(summary, { id: "P5-R1", profile: "Mobile user", familiarity: "narrow screen", goal: "recover after rotation", expected: "draft and compact layout survive 400→1024 resize" }, async () => {
        await page.locator("#prompt").fill("Draft survives a network-orientation interruption");
        await page.setViewportSize({ width: 1024, height: 768 });
        assert.equal(await page.locator("#prompt").inputValue(), "Draft survives a network-orientation interruption");
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      });
    } finally { await context.close(); }
  }
  {
    const context = await browser.newContext({ viewport: { width: 400, height: 812 } });
    const page = await context.newPage();
    try {
      await mountVisual(page, { emptyFiles: true });
      await runScenario(summary, { id: "P5-E1", profile: "Mobile user", familiarity: "narrow screen", goal: "understand an empty authorized-folder state", expected: "the empty state is explicit and the four workspace sections remain reachable" }, async () => {
        const empty = page.locator("#authorized-project-roots");
        assert(await empty.isVisible());
        assert.match(await empty.innerText(), /No project root is authorized/i);
        assert.equal(await page.locator(".workspace-section > summary").count(), 4);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      });
    } finally { await context.close(); }
  }
  {
    const { context, page } = await newVisualPage(browser, { width: 1280, height: 720 });
    try {
      await page.keyboard.press("Control+j");
      await runScenario(summary, { id: "P6-S1", profile: "Software and harness engineer", familiarity: "contracts and persistence", goal: "inspect workspace sources", expected: "four sections expose stable IDs, counts, resources, tasks, and isolated scrolling" }, async () => {
        for (const name of ["files", "background-tasks", "resources", "activity"]) {
          assert.equal(await page.locator(`[data-workspace-section="${name}"]`).count(), 1);
          assert.equal(await page.locator(`#workspace-${name}`).count(), 1);
          assert.equal(await page.locator(`#workspace-${name}-count`).count(), 1);
          assert.equal(await page.locator(`#workspace-${name}-resize`).getAttribute("aria-controls"), `workspace-${name}`);
        }
        assert.equal(await page.locator("#workspace-resources .workspace-row").count(), 4);
        const pinned = page.locator('#workspace-resources [data-resource-id="catalog/demo/commands/build.md"]');
        assert.match(await pinned.innerText(), /catalog · demo[\s\S]*Pinned · 0123456789ab/i);
        assert.equal(await page.locator("#workspace-background-tasks .workspace-row").count(), 2);
        const pane = await page.locator("#activity-panel").evaluate(node => ({ overflow: getComputedStyle(node).overflowY, scrollHeight: node.scrollHeight, clientHeight: node.clientHeight }));
        assert.equal(pane.overflow, "auto");
        assert(pane.scrollHeight <= pane.clientHeight + 1, `outer pane must not add a second scrollbar: ${JSON.stringify(pane)}`);
        for (const summaryNode of await page.locator(".workspace-section > summary").all()) {
          const box = await summaryNode.boundingBox(); assert(box && box.y >= 0 && box.y + box.height <= 720);
        }
      });
      await runScenario(summary, { id: "P6-R1", profile: "Software and harness engineer", familiarity: "contracts and persistence", goal: "recover from stale stored geometry", expected: "an excessive persisted console height is clamped to available space" }, async () => {
        await page.evaluate(() => localStorage.setItem("run-console-height", "99999"));
        await page.reload(); await page.locator("#startup-gate").waitFor({ state: "hidden" }); await page.keyboard.press("Control+j");
        const box = await page.locator("#run-console").boundingBox();
        assert(box.y + box.height <= 692 && box.y >= 0);
      });
    } finally { await context.close(); }
  }
  {
    const { context, page } = await newVisualPage(browser);
    try {
      await runScenario(summary, { id: "P7-S1", profile: "UI/UX specialist", familiarity: "interaction and hierarchy", goal: "discover project tools", expected: "slash palette groups resources and shows a preview without clipping" }, async () => {
        await page.locator("#prompt").fill("/");
        await page.locator('#resource-menu [role="option"]').first().waitFor();
        assert.equal(await page.locator("#resource-menu .resource-group").count() >= 2, true);
        assert(await page.locator("#resource-preview").isVisible());
        const pinned = page.locator('#resource-menu [data-resource-id="catalog/demo/commands/build.md"]');
        assert.match(await pinned.innerText(), /Pinned · 0123456789ab/);
        await pinned.focus();
        assert.match(await page.locator("#resource-preview").innerText(), /Pinned commit 0123456789ab/);
        assertGeometry(await geometry(page, "slash-palette"), "P7-S1");
      });
      await runScenario(summary, { id: "P7-R1", profile: "UI/UX specialist", familiarity: "interaction and hierarchy", goal: "dismiss help and resume writing", expected: "Escape closes the palette and returns focus to the composer" }, async () => {
        await page.keyboard.press("Escape");
        assert.equal(await page.locator("#resource-menu").evaluate(node => node.matches(":popover-open")), false);
        assert.equal(await page.evaluate(() => document.activeElement?.id), "prompt");
      });
    } finally { await context.close(); }
  }
}

(async () => {
  const browser = await chromium.launch();
  const output = outputDirectory();
  const summary = { generatedAt: new Date().toISOString(), outputDirectory: output.directory, temporary: output.temporary, screenshots: [], profiles: [] };
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
    await mount(page, fixtureReply);
    await page.keyboard.press("Control+j");
    await page.locator("#run-console").waitFor({ state: "visible" });

    const resize = page.locator("#run-console-resize");
    const maximize = page.locator("#run-console-maximize");
    assert.equal(await resize.getAttribute("role"), "separator", "console exposes a keyboard-operable resize control");
    assert.equal(await resize.getAttribute("aria-orientation"), "horizontal");
    assert.equal(await maximize.getAttribute("aria-controls"), "run-console");

    const restored = await page.locator("#run-console").boundingBox();
    const restoredBody = await page.locator("#run-console-panel").boundingBox();
    assert(Math.abs(restored.height - 340) <= 1, `default console preserves the approved 340px height at 720px; got ${restored.height}px`);
    assert(restoredBody.height >= 288, `default console preserves baseline useful body height; got ${restoredBody.height}px`);
    await maximize.click();
    const expanded = await page.locator("#run-console").boundingBox();
    const useful = await page.locator("#run-console-panel").boundingBox();
    assert(expanded.height > restored.height, "maximize increases console height");
    assert(useful.height >= 300, `maximized console keeps at least 300px useful height; got ${useful.height}px`);

    await maximize.click();
    const afterRestore = await page.locator("#run-console").boundingBox();
    assert(Math.abs(afterRestore.height - restored.height) <= 2, "maximize restores the previous compact height");
    await page.close();

    if (!process.env.VISUAL_PROFILES_ONLY) await captureMatrix(browser, output.directory, summary);
    await runProfileMatrix(browser, summary);
    const failedProfiles = summary.profiles.filter(item => item.status !== "passed");
    assert.equal(summary.profiles.length, 15, "profile matrix must contain 14 main/recovery scenarios plus one empty-state scenario");
    assert.deepEqual(failedProfiles, [], "all 15 profile scenarios must pass");
    fs.writeFileSync(path.join(output.directory, "summary.json"), JSON.stringify(summary, null, 2));
    console.log(`PASS: compact geometry, ${summary.screenshots.length} screenshots, ${summary.profiles.length} profile scenarios; evidence ${output.directory}`);
  } finally {
    if (summary.screenshots.length || summary.profiles.length) fs.writeFileSync(path.join(output.directory, "summary.json"), JSON.stringify(summary, null, 2));
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
