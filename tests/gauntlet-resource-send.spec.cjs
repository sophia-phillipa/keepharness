// A slow quota lookup must not mix one prompt with another draft's resource refs.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict"),
  fs = require("node:fs/promises"),
  path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage(),
      posts = [];
    let delay = false,
      releaseQuota;
    await page.route("http://resource-send.test/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      let data = {};
      if (p.startsWith("/v1/")) {
        if (p === "/v1/projects") data = { projects: ["sem-projeto"] };
        if (p === "/v1/models")
          data = {
            models: [{ id: "gpt-6-astra", backend: "codex", efforts: ["low"] }],
          };
        if (p === "/v1/conversations") data = { conversations: [] };
        if (p === "/v1/usage") {
          if (delay) await new Promise((resolve) => (releaseQuota = resolve));
          data = { available: false };
        }
        if (p === "/v1/resources")
          data = {
            items: [
              {
                id: "reviewer",
                revision: "v1",
                kind: "agent",
                name: "reviewer",
                selectable: true,
                scope: "project",
                origin: "Codex",
              },
              {
                id: "writer",
                revision: "v2",
                kind: "agent",
                name: "writer",
                selectable: true,
                scope: "project",
                origin: "Codex",
              },
            ],
          };
        if (p === "/v1/jobs" && route.request().method() === "POST") {
          posts.push(route.request().postDataJSON());
          data = { job_id: "first" };
        }
        if (p.endsWith("/events")) return;
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
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.8"));
    await page.goto("http://resource-send.test");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.fill("#prompt", "@review");
    const reviewerOption = page.locator(
      '#resource-menu [role="option"][data-resource-id="reviewer"]',
    );
    assert.equal(await reviewerOption.getAttribute("role"), "option");
    await reviewerOption.click();
    const submittedDraft = "@reviewer   with  spaces  ";
    await page.keyboard.type("  with  spaces  ");
    assert.equal(await page.inputValue("#prompt"), submittedDraft);
    delay = true;
    await page.click("#send");
    await page.waitForFunction(() => submitting);
    await page.fill("#prompt", "@writer");
    const writerOption = page.locator(
      '#resource-menu [role="option"][data-resource-id="writer"]',
    );
    assert.equal(await writerOption.getAttribute("role"), "option");
    await writerOption.click();
    delay = false;
    releaseQuota();
    await page.waitForFunction(() => job === "first" && !submitting);
    assert.equal(posts.length, 1);
    assert.equal(posts[0].prompt, submittedDraft);
    assert.deepEqual(posts[0].resource_selections, [
      { id: "reviewer", revision: "v1", token: "@reviewer" },
    ]);
    assert.equal(await page.inputValue("#prompt"), "@writer ");
    assert.equal(
      await page.locator(".prompt-resource").textContent(),
      "@writer",
    );
    assert.deepEqual(await page.evaluate(() => resourceSelections), [
      { id: "writer", revision: "v2", token: "@writer" },
    ]);
    console.log(
      "PASS: quota delay snapshots submitted resources and preserves new draft selection",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
