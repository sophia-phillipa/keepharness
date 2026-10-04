// A skipped catalog hook leaves a notice in the turn instead of a raw status word.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await page.route("http://hooks.test/**", async (route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname.startsWith("/v1/")) {
        const data = pathname === "/v1/projects" ? { projects: ["sem-projeto"] }
          : pathname === "/v1/models" ? { models: [{ id: "fixture", backend: "local", efforts: ["configured"] }], providers: { local: true } }
          : pathname === "/v1/conversations" ? { conversations: [] }
          : pathname === "/v1/version" ? { version: "fixture", build: "fixture" } : {};
        return route.fulfill({ json: data });
      }
      const file = pathname === "/" ? "index.html" : pathname.slice(1);
      return route.fulfill({ body: await fs.readFile(path.join(__dirname, file.startsWith("assets/") ? "../harness_ui" : "../agent_service", file)),
        contentType: file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : "text/html" });
    });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
    await page.goto("http://hooks.test");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.evaluate(() => {
      job = "hook-turn";
      active = assistant(job, "fixture");
      event({ id: 1, type: "catalog_hook", data: { outcome: "skipped", reason: "hooks_not_trusted", hook: "fixture-hook" } });
      event({ id: 2, type: "catalog_hook", data: { outcome: "skipped", reason: "hooks_not_granted" } });
      event({ id: 3, type: "catalog_hook", data: { outcome: "skipped", reason: "unknown_reason" } });
      event({ id: 4, type: "catalog_hook", data: { outcome: "skipped", reason: "hooks_not_trusted", hook: "fixture-hook-2" } });
      event({ id: 5, type: "answer_delta", data: { text: "Hello" } });
    });
    await page.getByText("Hello", { exact: true }).waitFor();
    const notices = await page.locator(".run-notice").allInnerTexts();
    assert.equal(notices.length, 3); // a repeated reason does not stack a second notice
    assert.match(notices[0], /Re-trust the catalog in Admin/);
    assert.match(notices[1], /hook permission was not granted/);
    assert.match(notices[2], /hook was skipped/i);
    assert.doesNotMatch(await page.locator("#status").innerText(), /catalog_hook/);
    console.log("PASS H1: skipped-hook notices stay in the turn for both reasons and a generic fallback");
    assert.equal(errors.length, 0, errors.join("\n"));
    console.log("PASS H2: no browser exceptions");
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
