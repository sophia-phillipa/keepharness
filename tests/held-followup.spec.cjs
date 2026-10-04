// D16: after Stop, a queued follow-up waits with "Run queued message" and "Discard".
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict"),
  fs = require("node:fs/promises"),
  path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage(),
      errors = [],
      posts = [];
    await page.route("http://held.test/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      if (p.startsWith("/v1/")) {
        let data = {};
        if (route.request().method() === "POST") posts.push(p);
        if (p === "/v1/projects") data = { projects: ["sem-projeto"] };
        if (p === "/v1/models")
          data = { models: [{ id: "fixture", backend: "local", efforts: ["configured"] }] };
        if (p === "/v1/conversations") data = { conversations: [] };
        if (p.includes("/events")) return; // The held turn's stream stays open.
        return route.fulfill({ json: data });
      }
      const file = p === "/" ? "index.html" : p.slice(1);
      return route.fulfill({
        body: await fs.readFile(
          path.join(__dirname, file.startsWith("assets/") ? "../harness_ui" : "../agent_service", file),
        ),
        contentType: file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : "text/html",
      });
    });
    page.on("pageerror", (e) => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.15.0"));
    await page.goto("http://held.test");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    const hold = (id) =>
      page.evaluate((eventId) => {
        job = "next";
        active ||= assistant("next");
        event({ id: eventId, type: "queue_wait", data: { reason: "held_after_stop" } });
      }, id);
    await hold(1);
    const run = page.getByRole("button", { name: "Run queued message" }),
      discard = page.getByRole("button", { name: "Discard" });
    await run.waitFor();
    assert.equal(await discard.isVisible(), true);
    assert.equal(await page.locator("#status").innerText(), "Held after Stop");
    await run.click();
    await run.waitFor({ state: "detached" });
    assert.deepEqual(posts, ["/v1/jobs/next/run-queued"]);
    // A later event (the run starting) clears the choice; a new hold offers it again.
    await hold(2);
    await discard.click();
    await discard.waitFor({ state: "detached" });
    assert.deepEqual(posts, ["/v1/jobs/next/run-queued", "/v1/jobs/next/cancel"]);
    await hold(3);
    await run.waitFor();
    await page.evaluate(() => event({ id: 4, type: "running", data: {} }));
    await run.waitFor({ state: "detached" });
    assert.deepEqual(errors, []);
    console.log("PASS held follow-up actions");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
