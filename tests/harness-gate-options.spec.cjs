// Option cards use typed choices, terminal replay and recoverable human-session errors.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const posts = [];
    let denied = false;
    await page.route("http://gates.test/**", async route => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname.startsWith("/v1/")) {
        if (pathname.startsWith("/v1/approvals/")) {
          posts.push(route.request().postDataJSON());
          return route.fulfill(denied ? { status: 403, json: { code: "approval_session_required" } } : { json: { resolved: true, resolved_by: "owner" } });
        }
        const data = pathname === "/v1/projects" ? { projects: ["sem-projeto"] }
          : pathname === "/v1/models" ? { models: [{ id: "fixture", backend: "local", efforts: ["configured"] }], providers: { local: true } }
          : pathname === "/v1/conversations" ? { conversations: [] }
          : pathname === "/v1/version" ? { version: "fixture", build: "fixture" } : {};
        return route.fulfill({ json: data });
      }
      const file = pathname === "/" ? "index.html" : pathname.slice(1);
      return route.fulfill({ body: await fs.readFile(path.join(__dirname, file.startsWith("assets/") ? "../tail_ui" : "../agent_service", file)), contentType: file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : "text/html" });
    });
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.9"));
    await page.goto("http://gates.test");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    async function gate(id, sequence, multi = false) {
      await page.evaluate(({ id, sequence, multi }) => event({ id: sequence, type: "gate_required", data: { gate_id: id, question: "Choose a synthetic option?", multi_select: multi, options: [{ id: "alpha", label: "Alpha", description: "First option" }, { id: "beta", label: "Beta", description: "Second option" }] } }), { id, sequence, multi });
    }
    await gate("single", 1);
    assert(await page.locator("#gate-single button").isDisabled());
    await page.getByRole("radio", { name: "Alpha" }).check();
    await page.locator("#gate-single button").focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => document.getElementById("gate-single").dataset.state === "resolved");
    assert.deepEqual(posts, [{ choice: "alpha" }]);
    assert.equal(await page.locator("#prompt").evaluate(el => el === document.activeElement), true);
    assert.match(await page.locator("#gate-single").innerText(), /Answered by owner/);
    await gate("multi", 2, true);
    await page.locator("#gate-multi input").first().check();
    await page.locator("#gate-multi input").last().check();
    await page.locator("#gate-multi button").click();
    await page.waitForFunction(() => document.getElementById("gate-multi").dataset.state === "resolved");
    assert.deepEqual(posts[1], { choice: ["alpha", "beta"] });
    await gate("restart", 3);
    await page.locator("#gate-restart input").first().focus();
    await page.evaluate(() => event({ id: 4, type: "gate_invalidated", data: { gate_id: "restart" } }));
    assert.match(await page.locator("#gate-restart").innerText(), /ask again/);
    assert.equal(await page.locator("#gate-restart input:enabled").count(), 0);
    assert.equal(await page.locator("#prompt").evaluate(el => el === document.activeElement), true);
    await gate("remote", 5);
    await page.locator("#gate-remote input").first().check();
    await page.evaluate(() => event({ id: 6, type: "gate_resolved", data: { gate_id: "remote", choice: "beta", resolved_by: "other-tab" } }));
    assert.equal(await page.locator('#gate-remote input[value="alpha"]').isChecked(), false);
    assert.equal(await page.locator('#gate-remote input[value="beta"]').isChecked(), true);
    denied = true;
    await gate("human", 7);
    await page.locator("#gate-human input").first().check();
    await page.locator("#gate-human button").click();
    await page.waitForFunction(() => document.querySelector("#gate-human [role=status]").textContent.toLowerCase().includes("enroll"));
    assert.equal(await page.locator("#gate-human input:enabled").count(), 2);
    await page.setViewportSize({ width: 400, height: 844 });
    const bounds = await page.locator("#gate-human").boundingBox();
    assert(bounds.x >= 0 && bounds.x + bounds.width <= 401);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    console.log("PASS typed single/multiple choice, human authority recovery, restart invalidation, focus and 400px");
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
