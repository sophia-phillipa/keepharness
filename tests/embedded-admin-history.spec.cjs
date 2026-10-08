// #58: the actual embedded settings receiver must not add iframe entries between shell views.
const assert = require("node:assert/strict");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const path = require("node:path");
const origin = "http://localhost:18990";
const admin = origin + "/admin";

(async () => {
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.route(origin + "/**", route => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === "/admin/") return route.fulfill({ contentType: "text/html", body: `
        <!doctype html><html data-surface="admin" data-embedded="1"><body>
        <nav><a data-panel="providers" href="#providers">Providers</a><a data-panel="runs" href="#runs">Runs</a></nav>
        <section id="providers"><label><input id="full-access" type="checkbox"><strong>Allow Full access</strong></label></section>
        <section id="execution-page">Runs</section>
        <script>
          window.settingsSearchIndexState = { lastGoodIndex: [], refreshing: false, lastError: null };
          function renderPanel() {
            document.querySelector('#providers').hidden = location.hash !== '#providers';
            document.querySelector('#execution-page').hidden = location.hash !== '#runs';
          }
          addEventListener('hashchange', renderPanel); renderPanel();
        </script><script src="/assets/settings-search.js"></script></body></html>` });
      if (!pathname.startsWith("/v1/")) return route.fulfill({ path: path.join(__dirname, "..", pathname.startsWith("/assets/") ? "harness_ui" : "agent_service", pathname === "/" ? "index.html" : pathname) });
      return route.fulfill({ json: {
        "/v1/projects": { projects: ["sem-projeto"], details: {} },
        "/v1/conversations": { conversations: [] },
        "/v1/catalog": { agents: [], skills: [], warnings: [] },
        "/v1/version": { version: "fixture", build: "iframe-history" },
      }[pathname] || {} });
    });
    page.setDefaultTimeout(5000);
    const step = async direction => { await page.locator("#settings-search").focus(); await page.keyboard.press(direction < 0 ? "Control+[" : "Control+]"); };
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
    await page.route(origin + "/v1/models**", route => route.fulfill({ json: {
      models: [{ id: "fixture", backend: "codex", efforts: ["low"], execution_modes: ["native"] }],
      providers: { codex: true }, admin_url: admin + "/",
    } }));
    await page.goto(origin);
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.keyboard.press("Control+,");
    await page.locator('[data-admin-section="providers"]').click();
    const frame = page.frameLocator("#admin-frame");
    await frame.locator("#full-access").waitFor();
    // Authenticate the actual admin's search index via its real message listener.
    await page.fill("#settings-search", "Full access");
    await page.locator('#settings-search-results [data-preference="full-access"]').waitFor();
    await page.locator("#settings-search-clear").click();
    const before = await page.evaluate(() => viewIndex);
    await page.locator('[data-admin-section="runs"]').click();
    await frame.locator("#execution-page").waitFor();
    await page.keyboard.press("Control+[");
    await frame.locator("#full-access").waitFor();
    assert.equal(await page.evaluate(() => viewIndex), before, "shell view index follows Back with embedded admin");
    assert.equal(await page.locator('[data-settings][aria-pressed="true"]').getAttribute("data-admin-section"), "providers");
    await frame.locator("#full-access").waitFor();
    assert.equal(await page.getByTestId("nav-forward").isEnabled(), true);
    await page.keyboard.press("Control+]");
    await page.waitForFunction(index => viewIndex === index + 1, before);
    assert.equal(await page.locator('[data-settings][aria-pressed="true"]').getAttribute("data-admin-section"), "runs");
    await frame.locator("#execution-page").waitFor();
    await page.evaluate(() => window.history.back());
    await page.waitForFunction(index => viewIndex === index, before);
    await frame.locator("#full-access").waitFor();
    console.log("PASS actual embedded receiver preserves shell and native Back/Forward");

    // Before search authenticates the frame, shell navigation replaces its document URL.
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.keyboard.press("Control+,");
    await page.locator('[data-admin-section="providers"]').click();
    await frame.locator("#full-access").waitFor();
    assert.equal(await page.locator("#admin-frame").getAttribute("data-settings-search-ready"), null);
    const unreadyIndex = await page.evaluate(() => viewIndex);
    await page.locator('[data-admin-section="runs"]').click();
    await frame.locator("#execution-page").waitFor();
    await step(-1);
    await frame.locator("#full-access").waitFor();
    assert.equal(await page.evaluate(() => viewIndex), unreadyIndex);
    await step(1);
    await frame.locator("#execution-page").waitFor();
    assert.equal(await page.evaluate(() => viewIndex), unreadyIndex + 1);
    console.log("PASS unready embedded document navigation preserves shell history");

    const length = await page.evaluate(() => window.history.length);
    await frame.locator('[data-panel="providers"]').click();
    await frame.locator("#full-access").waitFor();
    await frame.locator('[data-panel="runs"]').click();
    await frame.locator("#execution-page").waitFor();
    assert.equal(await page.evaluate(() => window.history.length), length);
    await step(-1);
    await frame.locator("#full-access").waitFor();
    assert.equal(await page.evaluate(() => viewIndex), unreadyIndex);
    console.log("PASS embedded section links add no joint browser history entries");

    await page.fill("#settings-search", "Full access");
    await page.locator('#settings-search-results [data-preference="full-access"]').waitFor();
    await page.locator("#settings-search-clear").click();
    await page.locator('[data-admin-section="runs"]').click();
    await frame.locator("#execution-page").waitFor();
    const focusIndex = await page.evaluate(() => viewIndex);
    await page.fill("#settings-search", "Full access");
    await page.locator('#settings-search-results [data-preference="full-access"]').click();
    await page.waitForFunction(() => document.querySelector("#admin-frame").contentDocument.activeElement?.id === "full-access");
    assert.equal(await page.evaluate(() => viewIndex), focusIndex + 1);
    await step(-1);
    await frame.locator("#execution-page").waitFor();
    assert.equal(await page.evaluate(() => viewIndex), focusIndex);
    await page.evaluate(() => window.history.forward());
    await frame.locator("#full-access").waitFor();
    assert.equal(await page.evaluate(() => viewIndex), focusIndex + 1);
    console.log("PASS actual settings-focus receiver retains Back/Forward navigation");
    // A not-yet-ready frame retries its current section, not its initial src.
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.keyboard.press("Control+,");
    await page.locator('[data-admin-section="providers"]').click();
    await frame.locator("#full-access").waitFor();
    const retryBase = await page.evaluate(() => viewIndex);
    await page.locator('[data-admin-section="runs"]').click();
    await frame.locator("#execution-page").waitFor();
    await frame.locator("html").evaluate(() => { window.settingsSearchIndexState.refreshing = true; });
    await page.fill("#settings-search", "Full access");
    await page.locator("#settings-search-retry").waitFor();
    const retryLength = await page.evaluate(() => window.history.length);
    await page.locator("#settings-search-retry").click();
    await page.locator('#settings-search-results [data-preference="full-access"]').waitFor();
    assert.equal(await frame.locator("#execution-page").isVisible(), true, "Retry retains Runs after unready navigation from Providers");
    assert.equal(await page.locator('[data-settings][aria-pressed="true"]').getAttribute("data-admin-section"), "runs");
    assert.equal(await page.evaluate(() => window.history.length), retryLength);
    await step(-1);
    await frame.locator("#full-access").waitFor();
    assert.equal(await page.evaluate(() => viewIndex), retryBase);
    await step(1);
    await frame.locator("#execution-page").waitFor();
    assert.equal(await page.evaluate(() => viewIndex), retryBase + 1);
    console.log("PASS unready Retry retains the current section and shell history");
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
