// D-033 (items 1, 2, 4): the right panel switch reads Activities | Files, the two views are exclusive,
// and Activities is a one-open-at-a-time accordion (Activity, Background tasks, Resources) whose open
// section fills the remaining height, scrolls inside, shows counts on collapsed headers, follows the
// WAI-ARIA accordion keys and is remembered in the workspace_sections preference.
//
// Runs against the shared test-ui.sh harness; only the model, resource and activity listings are
// stubbed so the counts have something to show. Every scenario starts from an empty preference store.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");

const BASE = process.env.HARNESS_URL;
assert(BASE, "HARNESS_URL is required (run through scripts/test-ui.sh)");
const SECTIONS = ["activity", "background-tasks", "resources"];
const LABELS = ["Activity", "Background tasks", "Resources"];
const RESOURCES = ["check", "inspect", "reviewer"].map((name, index) => ({
  id: "project/p/" + name, resource_id: "project/p/" + name, revision: "1", name,
  kind: ["command", "skill", "agent"][index], scope: "project", origin: "codex", selectable: true,
  description: "Inspect synthetic evidence", source: "project",
}));
const JOBS = ["alpha", "beta"].map((name, index) => ({
  job_id: "job-" + name, conversation_id: "conversation-" + name, project_id: "sem-projeto", state: "running",
  backend: "local", model: "fixture", created: index + 1, title: "Background " + name,
}));

async function resetPreferences() {
  const url = BASE + "/v1/ui-state", headers = { "Content-Type": "application/json" };
  const { values } = await (await fetch(url)).json();
  const keys = Object.keys(values);
  if (keys.length) await fetch(url, { method: "PATCH", headers, body: JSON.stringify({ values: Object.fromEntries(keys.map((key) => [key, null])) }) });
}
const storedPreference = async (key) => (await (await fetch(BASE + "/v1/ui-state")).json()).values[key];
const seedPreferences = (values) =>
  fetch(BASE + "/v1/ui-state", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ values }) });

async function openPage(browser, { view = "activity", palette = "paper", seed = {} } = {}) {
  await resetPreferences();
  await seedPreferences({ activity_open: true, right_panel_view: view, ...seed });
  const context = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  page.patches = [];
  page.on("pageerror", (error) => { throw error; });
  page.on("request", (request) => {
    if (request.method() === "PATCH" && new URL(request.url()).pathname === "/v1/ui-state") page.patches.push(request.postData());
  });
  await page.route("**/v1/**", (route) => {
    const { pathname } = new URL(route.request().url());
    if (pathname === "/v1/models") return route.fulfill({ json: { models: [{ id: "fixture", backend: "local", efforts: ["configured"] }], providers: { local: true } } });
    if (pathname === "/v1/resources") return route.fulfill({ json: { items: RESOURCES, warnings: [] } });
    if (pathname === "/v1/activity") return route.fulfill({ json: { jobs: JOBS, needs_you: [], counts: {}, providers: [] } });
    return route.continue();
  });
  await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
  await page.goto(BASE + "/");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  await page.evaluate((name) => window.HarnessTheme.apply(name), palette);
  await page.locator("#activity-panel").waitFor({ state: "visible" });
  return { context, page };
}
const head = (page, name) => page.locator("#workspace-" + name + "-head");
const openState = (page) =>
  page.evaluate((names) => names.map((name) => ({
    name,
    expanded: document.getElementById("workspace-" + name + "-head").getAttribute("aria-expanded"),
    disabled: document.getElementById("workspace-" + name + "-head").getAttribute("aria-disabled"),
    shown: !document.getElementById("workspace-" + name).hidden && document.getElementById("workspace-" + name).checkVisibility(),
  })), SECTIONS);
async function assertExactlyOneOpen(page, name, message) {
  const state = await openState(page);
  assert.deepEqual(state.filter((item) => item.expanded === "true").map((item) => item.name), [name], message + ": one expanded header " + JSON.stringify(state));
  assert.deepEqual(state.filter((item) => item.shown).map((item) => item.name), [name], message + ": one visible body " + JSON.stringify(state));
  for (const item of state) assert.equal(item.disabled, String(item.name === name), message + ": only the open header is aria-disabled " + item.name);
}

const scenarios = [];
const scenario = (name, work) => scenarios.push([name, work]);

scenario("the switch reads Activities | Files and the accordion headers keep their order", async (browser) => {
  const { context, page } = await openPage(browser);
  assert.deepEqual(await page.locator("#activity-panel .panel-view-controls button").allTextContents(), ["Activities", "Files"]);
  assert.deepEqual(await page.locator("#activities-view .accordion-head .accordion-label").allTextContents(), LABELS);
  assert.deepEqual(
    await page.locator("#activities-view .accordion-head").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("aria-controls"))),
    SECTIONS.map((name) => "workspace-" + name),
  );
  assert.equal(await page.locator("#activity-toggle").getAttribute("aria-controls"), "activities-view");
  await context.close();
});

scenario("Activities and Files are exclusive views and Files takes the whole panel", async (browser) => {
  const { context, page } = await openPage(browser);
  assert(await page.locator("#activities-view").isVisible());
  assert(await page.locator("#activity-view").isVisible(), "live run events still render into #activity-view");
  assert.equal(await page.locator("#files-view").isVisible(), false);
  assert.equal(await page.locator("#activity-toggle").getAttribute("aria-expanded"), "true");
  assert.equal(await page.locator("#files-toggle").getAttribute("aria-expanded"), "false");
  await page.locator("#files-toggle").click();
  assert(await page.locator("#files-view").isVisible());
  assert.equal(await page.locator("#activities-view").isVisible(), false);
  assert.equal(await page.locator("#files-toggle").getAttribute("aria-expanded"), "true");
  assert.equal(await page.locator("#activity-toggle").getAttribute("aria-expanded"), "false");
  const geometry = await page.evaluate(() => {
    const panel = document.getElementById("activity-panel"), style = getComputedStyle(panel);
    const floor = panel.getBoundingClientRect().bottom - parseFloat(style.paddingBottom);
    const files = document.getElementById("workspace-files").getBoundingClientRect();
    return { display: style.display, direction: style.flexDirection, gap: floor - files.bottom, files: files.height, overflow: panel.scrollHeight - panel.clientHeight };
  });
  assert.equal(geometry.display, "flex");
  assert.equal(geometry.direction, "column");
  assert(geometry.files >= 168, "the Files body is at least 168 px: " + JSON.stringify(geometry));
  assert(geometry.gap >= -1 && geometry.gap <= 24, "Files reaches the bottom of the panel (resize handle only): " + JSON.stringify(geometry));
  assert(geometry.overflow <= 1, "no outer scrollbar: " + JSON.stringify(geometry));
  await page.locator("#activity-toggle").click();
  assert(await page.locator("#activities-view").isVisible());
  assert.equal(await page.locator("#files-view").isVisible(), false);
  await context.close();
});

scenario("exactly one section is open; a header click switches and the open header is inert", async (browser) => {
  const { context, page } = await openPage(browser);
  await assertExactlyOneOpen(page, "activity", "initial");
  await head(page, "resources").click();
  await assertExactlyOneOpen(page, "resources", "after resources click");
  assert.equal(await page.locator("#workspace-resources .workspace-row").count(), 3);
  await head(page, "background-tasks").click();
  await assertExactlyOneOpen(page, "background-tasks", "after background tasks click");
  await head(page, "background-tasks").click({ force: true }); // aria-disabled: Playwright treats it as not enabled
  await assertExactlyOneOpen(page, "background-tasks", "clicking the open header does not collapse it");
  assert.equal(await page.locator("#workspace-background-tasks .workspace-row").count(), 2);
  await context.close();
});

scenario("keyboard: Enter and Space open, Up/Down/Home/End move focus and wrap", async (browser) => {
  const { context, page } = await openPage(browser);
  const focused = () => page.evaluate(() => document.activeElement.id);
  await head(page, "activity").focus();
  await page.keyboard.press("ArrowDown");
  assert.equal(await focused(), "workspace-background-tasks-head");
  await page.keyboard.press("ArrowDown");
  assert.equal(await focused(), "workspace-resources-head");
  await page.keyboard.press("ArrowDown");
  assert.equal(await focused(), "workspace-activity-head", "Down wraps from the last header to the first");
  await page.keyboard.press("ArrowUp");
  assert.equal(await focused(), "workspace-resources-head", "Up wraps from the first header to the last");
  await page.keyboard.press("Home");
  assert.equal(await focused(), "workspace-activity-head");
  await page.keyboard.press("End");
  assert.equal(await focused(), "workspace-resources-head");
  await assertExactlyOneOpen(page, "activity", "moving focus opens nothing");
  await page.keyboard.press("Enter");
  await assertExactlyOneOpen(page, "resources", "Enter opens the focused header");
  assert.equal(await focused(), "workspace-resources-head", "focus stays on the header");
  await page.keyboard.press("Home");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Space");
  await assertExactlyOneOpen(page, "background-tasks", "Space opens the focused header");
  await context.close();
});

scenario("collapsed headers show their count", async (browser) => {
  const { context, page } = await openPage(browser);
  await page.evaluate(() => {
    for (let index = 0; index < 2; index++) {
      const item = document.createElement("li");
      item.dataset.state = "done";
      item.textContent = "Event " + index;
      document.getElementById("activity-events").append(item);
    }
  });
  await page.waitForFunction(() => document.getElementById("workspace-activity-count").textContent === "2");
  await head(page, "resources").click();
  await page.waitForFunction(() => document.getElementById("workspace-resources-count").textContent === "3");
  const collapsed = async (name) => ({ shown: await page.locator("#workspace-" + name + "-count").isVisible(), text: await page.locator("#workspace-" + name + "-count").textContent(), inHead: await head(page, name).locator("#workspace-" + name + "-count").count() });
  assert.deepEqual(await collapsed("activity"), { shown: true, text: "2", inHead: 1 });
  assert.deepEqual(await collapsed("background-tasks"), { shown: true, text: "2", inHead: 1 });
  assert.match(await head(page, "activity").innerText(), /Activity\s*2/);
  assert.equal(await page.locator("#workspace-activity").isVisible(), false);
  await context.close();
});

scenario("the open section fills the height, scrolls inside and the panel never scrolls", async (browser) => {
  const { context, page } = await openPage(browser);
  const measure = () => page.evaluate((names) => {
    const panel = document.getElementById("activity-panel"), view = document.getElementById("activities-view").getBoundingClientRect();
    const floor = panel.getBoundingClientRect().bottom - parseFloat(getComputedStyle(panel).paddingBottom);
    const open = names.map((name) => document.getElementById("workspace-" + name)).find((node) => node.checkVisibility());
    const body = open.getBoundingClientRect(), item = open.closest(".accordion-item").getBoundingClientRect(), last = [...document.querySelectorAll(".accordion-item")].at(-1).getBoundingClientRect(), heads = [...document.querySelectorAll(".accordion-head")].map((node) => node.getBoundingClientRect());
    return {
      open: open.id, viewGap: floor - view.bottom, bodyGap: item.bottom - body.bottom, itemsGap: view.bottom - last.bottom, body: body.height,
      outerOverflow: panel.scrollHeight - panel.clientHeight, headsInside: heads.every((box) => box.top >= view.top - 1 && box.bottom <= view.bottom + 1),
      bodyOverflowY: getComputedStyle(open).overflowY,
    };
  }, SECTIONS);
  for (const name of SECTIONS) {
    await head(page, name).click({ force: name === "activity" }); // the first header starts open (aria-disabled)
    const box = await measure();
    assert.equal(box.open, "workspace-" + name);
    assert(Math.abs(box.viewGap) <= 24, name + " accordion ends at the panel bottom: " + JSON.stringify(box));
    assert(box.bodyGap >= -1 && box.bodyGap <= 2, name + " body fills the accordion: " + JSON.stringify(box));
    assert(box.itemsGap >= -1 && box.itemsGap <= 2, name + " the headers leave no unused space: " + JSON.stringify(box));
    assert(box.body > 150, name + " body gets the remaining height: " + JSON.stringify(box));
    assert(box.outerOverflow <= 1, name + " does not overflow the panel: " + JSON.stringify(box));
    assert(box.headsInside, name + ": all three headers stay visible");
  }
  await head(page, "activity").click({ force: !(await head(page, "activity").isEnabled()) });
  await page.evaluate(() => {
    for (let index = 0; index < 80; index++) {
      const item = document.createElement("li");
      item.dataset.state = "done";
      item.textContent = "Milestone " + index;
      document.getElementById("activity-events").append(item);
    }
  });
  const scrolling = await page.evaluate(() => {
    const body = document.getElementById("workspace-activity"), panel = document.getElementById("activity-panel");
    body.scrollTop = 500;
    return { body: body.scrollHeight - body.clientHeight, moved: body.scrollTop, outer: panel.scrollHeight - panel.clientHeight, panelTop: panel.scrollTop };
  });
  assert(scrolling.body > 200 && scrolling.moved > 0, "the long run scrolls inside its section: " + JSON.stringify(scrolling));
  assert(scrolling.outer <= 1 && scrolling.panelTop === 0, "the panel itself does not scroll: " + JSON.stringify(scrolling));
  await context.close();
});

scenario("a resource chip opens the panel on the Resources section", async (browser) => {
  const { context, page } = await openPage(browser, { view: "files" });
  await page.waitForFunction(() => document.getElementById("workspace-resources-count").textContent === "3");
  assert.equal(await page.locator("#activities-view").isVisible(), false);
  await page.evaluate(() => {
    const chip = document.createElement("a");
    chip.className = "prose-chip";
    chip.id = "test-chip";
    chip.href = "#";
    chip.dataset.resourceId = "project/p/inspect";
    chip.textContent = "inspect";
    document.body.append(chip);
  });
  await page.locator("#test-chip").click({ force: true });
  assert(await page.locator("#activities-view").isVisible());
  assert.equal(await page.locator("#files-view").isVisible(), false);
  await assertExactlyOneOpen(page, "resources", "resource chip");
  assert.equal(await page.evaluate(() => document.activeElement.dataset.resourceId), "project/p/inspect", "the resource row takes focus");
  await context.close();
});

scenario("the open section is remembered in workspace_sections across a reload", async (browser) => {
  const { context, page } = await openPage(browser);
  await head(page, "background-tasks").click();
  await page.waitForFunction(() => window.HarnessPrefs.get("workspace_sections", {})["background-tasks"]?.open === true);
  await page.evaluate(() => window.HarnessPrefs.flush());
  const patch = page.patches.find((body) => body.includes("workspace_sections") && body.includes("background-tasks"));
  assert(patch, "a PATCH /v1/ui-state carried workspace_sections: " + JSON.stringify(page.patches));
  const stored = await storedPreference("workspace_sections");
  assert.equal(stored["background-tasks"].open, true);
  assert.equal(stored.activity.open, false);
  assert.equal(stored.resources.open, false);
  assert.equal(await storedPreference("right_panel_view"), "activity", "the view preference keeps its values");
  await page.reload();
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  await assertExactlyOneOpen(page, "background-tasks", "after reload");
  await page.locator("#files-toggle").click();
  await page.evaluate(() => window.HarnessPrefs.flush());
  assert.equal(await storedPreference("right_panel_view"), "files");
  assert.equal(await page.evaluate(() => Object.keys(localStorage).filter((key) => /workspace|panel-view|accordion/.test(key)).length), 0, "no new localStorage key");
  await context.close();
});

for (const palette of ["paper", "graphite"]) {
  scenario("accordion text meets WCAG AA contrast in " + palette, async (browser) => {
    const { context, page } = await openPage(browser, { palette });
    await head(page, "resources").click();
    await page.waitForFunction(() => document.getElementById("workspace-resources-count").textContent === "3");
    const failures = await page.evaluate(() => {
      const probe = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
      const rgba = (css) => { probe.clearRect(0, 0, 1, 1); probe.fillStyle = "#000"; probe.fillStyle = css; probe.fillRect(0, 0, 1, 1); const [r, g, b, a] = probe.getImageData(0, 0, 1, 1).data; return [r, g, b, a / 255]; };
      const background = (node) => {
        let color = [255, 255, 255, 1];
        const layers = [];
        for (let cursor = node; cursor; cursor = cursor.parentElement) layers.push(rgba(getComputedStyle(cursor).backgroundColor));
        for (const layer of layers.reverse()) color = color.map((value, index) => (index < 3 ? layer[index] * layer[3] + value * (1 - layer[3]) : 1));
        return color;
      };
      const luminance = ([r, g, b]) => { const [x, y, z] = [r, g, b].map((v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * x + 0.7152 * y + 0.0722 * z; };
      const bad = [];
      const nodes = [...document.querySelectorAll(".accordion-head .accordion-label, .accordion-head .workspace-count, #workspace-resources .workspace-item-name, #workspace-resources .workspace-source, #activity-state")];
      for (const node of nodes) {
        if (!node.checkVisibility()) continue;
        const a = luminance(rgba(getComputedStyle(node).color)), b = luminance(background(node));
        const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
        if (ratio < 4.5) bad.push((node.id || node.className) + " " + ratio.toFixed(2));
      }
      return bad;
    });
    assert.deepEqual(failures, []);
    await context.close();
  });
}

(async () => {
  const browser = await chromium.launch();
  const failed = [];
  try {
    for (const [name, work] of scenarios) {
      try {
        await work(browser);
        console.log("PASS " + name);
      } catch (error) {
        failed.push(name);
        console.error("FAIL " + name + ": " + (error.stack || error));
      }
    }
  } finally {
    await browser.close();
  }
  if (failed.length) process.exit(1);
})();
