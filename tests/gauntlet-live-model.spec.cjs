// Real browser, synthetic server: a running turn keeps its model while follow-ups change.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict"),
  fs = require("node:fs/promises"),
  path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    let accept;
    const round = Number(process.env.GAUNTLET_ROUND || 1),
      page = await browser.newPage({
        viewport: { width: round % 2 ? 1280 : 390, height: 900 },
      }),
      posts = [],
      errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.route("http://live-model.test/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      let data = {};
      if (p.startsWith("/v1/")) {
        if (p === "/v1/projects") data = { projects: ["sem-projeto"] };
        if (p === "/v1/models")
          data = {
            models: [
              { id: "qwen-local", backend: "local", efforts: ["configured"] },
              {
                id: "deepseek-flash",
                backend: "deepseek",
                efforts: ["low", "high"],
              },
            ],
          };
        if (p === "/v1/conversations") data = { conversations: [] };
        if (p === "/v1/jobs" && route.request().method() === "POST") {
          posts.push(route.request().postDataJSON());
          if (posts.length === 2)
            await new Promise((resolve) => (accept = resolve));
          data = { job_id: "turn-" + posts.length };
        }
        if (p.endsWith("/events")) return;
        if (p === "/v1/conversations/turn-1")
          data = {
            turns: posts.map((request, i) => ({
              id: "turn-" + (i + 1),
              project: "sem-projeto",
              state: i ? "queued" : "running",
              request,
            })),
          };
        return route.fulfill({ json: data });
      }
      const file = p === "/" ? "index.html" : p.slice(1);
      return route.fulfill({
        body: await fs.readFile(
          path.join(
            __dirname,
            file.startsWith("assets/") ? "../tail_ui" : "../agent_service",
            file,
          ),
        ),
        contentType: file.endsWith(".js")
          ? "text/javascript"
          : file.endsWith(".css")
            ? "text/css"
            : file.endsWith(".svg")
              ? "image/svg+xml"
              : "text/html",
      });
    });
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.6"));
    await page.goto("http://live-model.test");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.fill("#prompt", "First message " + round);
    await page.click("#send");
    await page.waitForFunction(() => busy && !submitting);
    assert(
      await page.locator("#model-trigger").isEnabled(),
      "model picker must allow choosing the next queued turn while streaming",
    );
    await page.click("#model-trigger");
    const option = page.locator('#model-menu [data-value="deepseek-flash"]');
    const group = page
      .locator("#model-menu details")
      .filter({ has: page.locator('[data-value="deepseek-flash"]') });
    if ((await group.count()) && (await group.getAttribute("open")) === null)
      await group.locator("summary").click();
    if (process.env.GAUNTLET_SCREENSHOT)
      await page.screenshot({
        path: process.env.GAUNTLET_SCREENSHOT,
        fullPage: true,
      });
    await option.click();
    assert.match(
      await page.locator("#model-trigger").getAttribute("title"),
      /next message/,
    );
    await page.click("#effort-trigger");
    await page.locator('#effort-menu [data-value="high"]').click();
    const draft = "Continuation " + round + " 🐋\n" + "context ".repeat(round);
    await page.fill("#prompt", draft);
    await page.click("#send");
    await page.waitForFunction(() => submitting);
    await page.fill("#prompt", "New draft while sending " + round);
    accept();
    await page.waitForFunction(() => !submitting && parent === "turn-2");
    assert.equal(
      await page.inputValue("#prompt"),
      "New draft while sending " + round,
      "accepting a pending request must not erase newly typed text",
    );
    assert.equal(posts[0].model, "qwen-local");
    assert.equal(posts[1].model, "deepseek-flash");
    assert.equal(posts[1].effort, "high");
    assert.equal(posts[1].parent_job_id, "turn-1");
    assert.equal(await page.evaluate(() => job), "turn-1");
    await page.fill("#prompt", "Draft after switch " + round);
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.waitForFunction(() => busy && job === "turn-1");
    assert.equal(await page.evaluate(() => parent), "turn-2");
    assert.equal(
      await page.inputValue("#prompt"),
      "Draft after switch " + round,
    );
    assert(await page.locator("#model-trigger").isEnabled());
    await page.selectOption("#model", "qwen-local");
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.waitForFunction(() => busy);
    assert.equal(
      await page.inputValue("#model"),
      "qwen-local",
      "reload must preserve the unsent model choice instead of reverting to the latest turn",
    );
    assert.deepEqual(errors, []);
    console.log(
      "PASS live model round " +
        round +
        ": queued provider handoff, effort, Unicode draft, reload and active stream",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
