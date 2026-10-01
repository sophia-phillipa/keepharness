const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

const root = path.join(__dirname, "../agent_service");
const markup = `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/tour.css"></head>
<body data-connection-ready="false">
  <header><button data-tour="top-search">Search runs, plans, files</button><span data-tour="quota-meters">Codex 80%</span><button data-tour="attention-bell">Attention</button><button data-tour="settings-admin">Settings</button><button id="take-tour" data-tour-action="start">Take the tour</button></header>
  <aside data-tour="sidebar-state-groups">Needs you · Running · Queued · Done</aside>
  <main><section data-tour="conversation-header">Conversation</section><section data-tour="composer">Composer</section><section data-tour="composer-controls">Access Model Effort</section></main>
  <footer data-tour="status-strip"><button id="run-status-toggle">Open console</button></footer>
  <section id="run-console" hidden><div data-tour="run-console-tabs">Pipeline Timeline Logs Runs Agents</div><div data-tour="span-detail">Show content</div></section>
  <button id="panel-toggle">Open files</button><aside id="activity-panel" hidden data-tour="right-pane">Files Activity</aside>
  <script>document.querySelector('#run-status-toggle').onclick=()=>document.querySelector('#run-console').hidden=false;document.querySelector('#panel-toggle').onclick=()=>document.querySelector('#activity-panel').hidden=false;</script>
  <script src="/tour.js" defer></script>
</body></html>`;

async function context(browser, options = {}) {
  const ctx = await browser.newContext(options);
  const page = await ctx.newPage();
  await page.route("http://tour.test/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === "/") return route.fulfill({ body: markup, contentType: "text/html" });
    const name = pathname.slice(1);
    return route.fulfill({
      body: await fs.readFile(path.join(root, name)),
      contentType: name.endsWith(".css") ? "text/css" : "text/javascript",
    });
  });
  return { ctx, page };
}

async function actualContext(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  const page = await ctx.newPage();
  await page.addInitScript(() => localStorage.removeItem("tail-harness-tour-seen"));
  await page.route("http://actual-tour.test/**", async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.startsWith("/v1/")) {
      if (pathname.endsWith("/events")) return route.abort();
      const data =
        pathname === "/v1/projects"
          ? { projects: ["sem-projeto"], details: { "sem-projeto": { label: "No project" } } }
          : pathname === "/v1/models"
            ? { models: [{ id: "fixture", name: "Fixture", backend: "local", efforts: ["low"] }], providers: { local: true }, uploads_enabled: false }
            : pathname === "/v1/conversations"
              ? { conversations: [] }
              : pathname === "/v1/version"
                ? { version: "0.13.8", build: "fixture" }
                : pathname === "/v1/resources"
                  ? { items: [], warnings: [] }
                  : pathname === "/v1/usage"
                    ? { available: false }
                    : pathname === "/v1/activity"
                      ? { jobs: [], providers: [], needs_you: [], counts: {} }
                      : pathname === "/v1/runs"
                        ? { runs: [] }
                        : {};
      return route.fulfill({ json: data });
    }
    const name = pathname === "/" ? "index.html" : pathname.slice(1);
    const base = name.startsWith("assets/") ? path.join(__dirname, "../tail_ui/assets") : root;
    const file = name.startsWith("assets/") ? name.slice("assets/".length) : name;
    return route.fulfill({
      body: await fs.readFile(path.join(base, file)),
      contentType: file.endsWith(".js")
        ? "text/javascript"
        : file.endsWith(".css")
          ? "text/css"
          : file.endsWith(".svg")
            ? "image/svg+xml"
            : "text/html",
    });
  });
  return { ctx, page };
}

(async () => {
  const browser = await chromium.launch();
  try {
    const first = await context(browser);
    const errors = [];
    first.page.on("pageerror", error => errors.push(error.message));
    await first.page.goto("http://tour.test/");
    assert.equal(await first.page.locator("#tour-card").count(), 0, "waits until the app is ready");
    await first.page.evaluate(() => { document.body.dataset.connectionReady = "true"; });
    await first.page.locator("#tour-card").waitFor();
    assert.match(await first.page.locator("#tour-title").innerText(), /Search/);
    assert.equal(await first.page.locator(".tour-spotlight").count(), 1);

    await first.page.getByRole("button", { name: "Next" }).click();
    assert.match(await first.page.locator("#tour-counter").innerText(), /^2 of /i);
    await first.page.getByRole("button", { name: "Back" }).click();
    assert.match(await first.page.locator("#tour-counter").innerText(), /^1 of /i);
    await first.page.keyboard.press("ArrowRight");
    assert.match(await first.page.locator("#tour-title").innerText(), /quota/i);
    await first.page.keyboard.press("ArrowLeft");
    assert.match(await first.page.locator("#tour-title").innerText(), /Search/);
    await first.page.keyboard.press("ArrowRight");

    await first.page.evaluate(() => {
      const outsider = document.createElement("button");
      outsider.id = "tour-focus-outsider";
      document.body.append(outsider);
      outsider.focus();
    });
    await first.page.waitForTimeout(0);
    assert.equal(await first.page.locator("#tour-card").evaluate(card => card.contains(document.activeElement)), true, "focus moved outside is recovered");

    const trapped = await first.page.evaluate(() => {
      document.querySelector("#tour-skip").focus();
      return document.activeElement.id;
    });
    assert.equal(trapped, "tour-skip");
    await first.page.keyboard.press("Shift+Tab");
    assert.equal(await first.page.evaluate(() => document.activeElement.id), "tour-next");

    await first.page.keyboard.press("Escape");
    assert.equal(await first.page.locator("#tour-card").count(), 0);
    assert.equal(await first.page.evaluate(() => localStorage.getItem("tail-harness-tour-seen")), "0.13.8");
    await first.page.reload();
    await first.page.evaluate(() => { document.body.dataset.connectionReady = "true"; });
    await first.page.waitForTimeout(150);
    assert.equal(await first.page.locator("#tour-card").count(), 0, "seen tour stays closed");
    await first.page.locator("#take-tour").click();
    await first.page.locator("#tour-card").waitFor();
    await first.page.getByRole("button", { name: "Skip tour" }).click();
    assert.equal(await first.page.locator("#tour-card").count(), 0);
    assert.deepEqual(errors, []);
    await first.ctx.close();

    const actual = await actualContext(browser);
    const actualErrors = [];
    actual.page.on("pageerror", error => actualErrors.push(error.message));
    await actual.page.goto("http://actual-tour.test/");
    await actual.page.locator("#startup-gate").waitFor({ state: "hidden" });
    await actual.page.locator("#tour-card").waitFor();
    assert.match(await actual.page.locator("#tour-title").innerText(), /Search/);
    await actual.page.keyboard.press("Escape");
    await actual.page.locator("#about").click();
    assert.equal(await actual.page.locator("#about-dialog").isVisible(), true);
    await actual.page.locator("#take-tour").click();
    assert.equal(await actual.page.locator("#about-dialog").isVisible(), false, "replay closes About before spotlighting the app");
    await actual.page.locator("#tour-card").waitFor();
    for (const palette of ["violet-bordeaux", "porcelain", "mineral-rose", "amethyst", "petroleum", "arizona"]) {
      const ratios = await actual.page.evaluate(theme => {
        TailTheme.apply(theme, false);
        const ratio = (foreground, background) => {
          const channel = value => {
            value /= 255;
            return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
          };
          const rgb = value => value.match(/[\d.]+/g).slice(0, 3).map(Number);
          const luminance = value => {
            const [r, g, b] = rgb(value).map(channel);
            return .2126 * r + .7152 * g + .0722 * b;
          };
          const [a, b] = [luminance(foreground), luminance(background)].sort((x, y) => y - x);
          return (a + .05) / (b + .05);
        };
        const card = getComputedStyle(document.querySelector("#tour-card"));
        const next = getComputedStyle(document.querySelector("#tour-next"));
        return { card: ratio(card.color, card.backgroundColor), action: ratio(next.color, next.backgroundColor) };
      }, palette);
      assert(ratios.card >= 4.5 && ratios.action >= 4.5, `${palette} tour contrast: ${JSON.stringify(ratios)}`);
    }
    for (let remaining = 14; remaining > 0; remaining--) {
      if (/Run console views/i.test(await actual.page.locator("#tour-title").innerText())) break;
      await actual.page.locator("#tour-next").click();
    }
    assert.match(await actual.page.locator("#tour-title").innerText(), /Run console views/i);
    assert.equal(await actual.page.locator("#run-console").isVisible(), true, "console step opens its drawer");
    await actual.page.keyboard.press("Escape");
    assert.equal(await actual.page.locator("#tour-card").count(), 0);
    assert.equal(await actual.page.locator("#run-console").isVisible(), true, "tour Escape is not handled again by the console");
    assert.equal(await actual.page.evaluate(() => document.activeElement?.id), "about", "replay restores focus to its visible opener");
    await actual.page.locator("#about").click();
    await actual.page.locator("#take-tour").click();
    await actual.page.locator("#tour-card").waitFor();
    await actual.page.evaluate(() => setReadiness(false, "Connection interrupted"));
    await actual.page.locator("#tour-card").waitFor({ state: "detached" });
    assert.equal(await actual.page.locator("#app-topbar, #sidebar, main, #activity-panel")
      .evaluateAll(nodes => nodes.every(node => node.inert)), true,
    "tour cleanup preserves the application's disconnected-state focus guard");
    assert.deepEqual(actualErrors, []);
    await actual.ctx.close();

    const readiness = await context(browser);
    await readiness.page.goto("http://tour.test/");
    await readiness.page.evaluate(() => { document.body.dataset.connectionReady = "true"; });
    await readiness.page.waitForTimeout(50);
    await readiness.page.evaluate(() => { document.body.dataset.connectionReady = "false"; });
    await readiness.page.waitForTimeout(300);
    assert.equal(await readiness.page.locator("#tour-card").count(), 0, "readiness is rechecked when the timer fires");
    await readiness.page.evaluate(() => {
      const dialog = document.createElement("dialog");
      dialog.id = "blocking-dialog";
      dialog.innerHTML = "<button>Close</button>";
      document.body.append(dialog);
      dialog.showModal();
      document.body.dataset.connectionReady = "true";
    });
    await readiness.page.waitForTimeout(300);
    assert.equal(await readiness.page.locator("#tour-card").count(), 0, "auto-start waits for an open modal");
    await readiness.page.evaluate(() => document.querySelector("#blocking-dialog").close());
    await readiness.page.locator("#tour-card").waitFor();
    await readiness.page.evaluate(() => { document.body.dataset.connectionReady = "false"; });
    await readiness.page.waitForTimeout(0);
    assert.equal(await readiness.page.locator("#tour-card").count(), 0, "connection loss suspends an active tour");
    assert.equal(await readiness.page.evaluate(() => localStorage.getItem("tail-harness-tour-seen")), null, "suspension does not mark the tour seen");
    await readiness.ctx.close();

    const missing = await context(browser);
    await missing.page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.8"));
    await missing.page.goto("http://tour.test/");
    await missing.page.evaluate(() => {
      document.querySelector('[data-tour="top-search"]').remove();
      document.body.dataset.connectionReady = "true";
      window.tailHarnessTour.start();
    });
    await missing.page.locator("#tour-card").waitFor();
    assert.match(await missing.page.locator("#tour-title").innerText(), /quota/i, "missing targets are skipped");
    await missing.ctx.close();

    const reduced = await context(browser, { reducedMotion: "reduce", viewport: { width: 400, height: 812 } });
    await reduced.page.goto("http://tour.test/");
    await reduced.page.evaluate(() => { document.body.dataset.connectionReady = "true"; });
    await reduced.page.locator("#tour-card").waitFor();
    const mobileTitles = [];
    for (let remaining = 14; remaining > 0; remaining--) {
      const title = await reduced.page.locator("#tour-title").innerText();
      mobileTitles.push(title);
      const [mobile, spotlight, pointer] = await Promise.all([
        reduced.page.locator("#tour-card").boundingBox(),
        reduced.page.locator(".tour-spotlight").boundingBox(),
        reduced.page.locator(".tour-pointer").boundingBox(),
      ]);
      for (const box of [mobile, spotlight, pointer])
        assert(box.x >= 0 && box.x + box.width <= 400 && box.y >= 0 && box.y + box.height <= 812, `${title} stays inside the mobile viewport`);
      if (/Settings, Admin/i.test(title)) break;
      await reduced.page.locator("#tour-next").click();
    }
    assert(mobileTitles.some(title => /Run console views/i.test(title)) && mobileTitles.some(title => /Files and activity/i.test(title)));
    assert.equal(await reduced.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.equal(await reduced.page.locator("#tour-root").evaluate(el => getComputedStyle(el).getPropertyValue("--tour-duration").trim()), "0ms");
    await reduced.ctx.close();

    console.log("PASS guided tour first-run, navigation, focus trap, skip/replay, missing targets, reduced motion and 400px docking");
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
