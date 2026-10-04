// Recovery must not compete with background history refresh or require a page reload.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    let offline = false,
      modelsFail = false,
      loads = 0;
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.clock.install();
    await page.route("http://reconnect.test/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      if (p.startsWith("/v1/")) {
        if (offline) return route.abort("failed");
        if (modelsFail && p === "/v1/models")
          return route.fulfill({ status: 500, json: { code: "internal_error" } });
        let data = {};
        if (p === "/v1/projects") data = { projects: ["sem-projeto"] };
        if (p === "/v1/models")
          data = {
            models: [{ id: "test-local", backend: "local", efforts: ["low"] }],
            providers: { local: true },
          };
        if (p === "/v1/conversations") data = { conversations: [] };
        if (p === "/v1/version") data = { version: "test", build: "stable" };
        return route.fulfill({ json: data });
      }
      if (p === "/") loads++;
      const file = p === "/" ? "index.html" : p.slice(1);
      await route.fulfill({
        body: await fs.readFile(
          path.join(
            __dirname,
            file.startsWith("assets/") ? "../harness_ui" : "../agent_service",
            file,
          ),
        ),
        contentType: file.endsWith(".js")
          ? "text/javascript"
          : file.endsWith(".css")
            ? "text/css"
            : "text/html",
      });
    });
    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
    await page.goto("http://reconnect.test");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.fill("#prompt", "Preserve my draft");
    // Hold the recovery history response across a background timer tick.
    await page.evaluate(() => {
      window.originalJson = json;
      window.historyCalls = 0;
      json = async function (url, options) {
        const data = await originalJson(url, options);
        if (url === "/v1/conversations") {
          historyCalls++;
          if (window.holdHistory)
            await new Promise((resolve) => {
              window.releaseHistory = resolve;
            });
        }
        return data;
      };
      setReadiness(false);
      window.holdHistory = true;
      void initialize();
    });
    await page.waitForFunction(
      () => typeof window.releaseHistory === "function",
    );
    await page.clock.runFor(10000);
    assert.equal(
      await page.evaluate(() => historyCalls),
      1,
      "background polling must not supersede recovery history",
    );
    await page.evaluate(() => {
      holdHistory = false;
      releaseHistory();
    });
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    offline = true;
    await page.evaluate(() => {
      readinessRetryAt = 0;
      return probeReadiness();
    });
    await page.locator("#startup-gate").waitFor({ state: "visible" });
    offline = false;
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    await page
      .locator("#startup-gate")
      .waitFor({ state: "hidden", timeout: 3000 });
    offline = true;
    await page.evaluate(() => {
      readinessRetryAt = 0;
      return probeReadiness();
    });
    offline = false;
    await page.evaluate(() =>
      document.dispatchEvent(new Event("visibilitychange")),
    );
    await page
      .locator("#startup-gate")
      .waitFor({ state: "hidden", timeout: 3000 });
    assert.equal(
      await page.locator("#prompt").inputValue(),
      "Preserve my draft",
    );
    // A failed readiness probe keeps an open editor and its unsaved text (UX-R1-1).
    await page.click("#rail-space");
    const space = page.getByRole("dialog", { name: "Space" });
    await space.getByRole("button", { name: "New page" }).click();
    await space.getByLabel("Page title").fill("Unsaved title");
    await space.getByLabel("Page content (Markdown)").fill("Unsaved body");
    modelsFail = true;
    await page.evaluate(() => {
      readinessRetryAt = 0;
      return probeReadiness();
    });
    await page.locator("#startup-gate").waitFor({ state: "visible" });
    assert.equal(await space.isVisible(), true, "Space stays open when the probe fails");
    assert.equal(await space.getByLabel("Page title").inputValue(), "Unsaved title");
    modelsFail = false;
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    await page.locator("#startup-gate").waitFor({ state: "hidden", timeout: 3000 });
    assert.equal(await space.getByLabel("Page content (Markdown)").inputValue(), "Unsaved body");
    assert.equal(loads, 1);
    // QA-R2-3: an idle tab asks the server at most ~30 times a minute.
    await space.getByRole("button", { name: "Close Space" }).click();
    const idleRequests = [];
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.startsWith("/v1/")) idleRequests.push(request.url());
    });
    for (let second = 0; second < 60; second++) await page.clock.runFor(1000);
    assert(idleRequests.length <= 30, "an idle minute made " + idleRequests.length + " requests");
    assert.deepEqual(errors, []);
    console.log(
      "PASS: recovery owns history polling, online/foreground recovery, no reload, draft preserved, idle polling budget.",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
