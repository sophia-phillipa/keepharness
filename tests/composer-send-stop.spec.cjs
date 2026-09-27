const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict"),
  fs = require("node:fs/promises"),
  path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage(),
      errors = [],
      requests = [],
      cancellations = [];
    let releaseFirst,
      releasePost,
      fail = false,
      finished = false;
    const firstDone = new Promise((resolve) => (releaseFirst = resolve));
    const postGate = new Promise((resolve) => (releasePost = resolve));
    await page.route("http://composer.test/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      if (p.startsWith("/v1/")) {
        let data = {};
        if (p.endsWith("/cancel")) cancellations.push(p);
        if (p === "/v1/projects") data = { projects: ["sem-projeto"] };
        if (p === "/v1/models")
          data = {
            models: [
              { id: "fixture", backend: "local", efforts: ["configured"] },
            ],
          };
        if (p === "/v1/conversations") data = { conversations: [] };
        if (p === "/v1/jobs" && route.request().method() === "POST") {
          requests.push(route.request().postDataJSON());
          await postGate;
          if (fail)
            return route.fulfill({ status: 429, json: { code: "queue_full" } });
          data = { job_id: "follow-" + requests.length };
        }
        if (p === "/v1/conversations/first")
          data = {
            turns: [
              {
                id: "first",
                project: "sem-projeto",
                state: finished ? "completed" : "running",
                request: {
                  backend: "local",
                  model: "fixture",
                  prompt: "Original",
                },
                result: finished ? { answer: "Original completed" } : null,
              },
              ...requests.slice(0, 2).map((request, i) => ({
                id: "follow-" + (i + 1),
                project: "sem-projeto",
                state: i ? "queued" : "running",
                request,
                result: null,
              })),
            ],
          };
        if (p === "/v1/jobs/first/events") {
          await firstDone;
          return route.fulfill({ body: "", contentType: "text/event-stream" });
        }
        if (p.includes("/events")) return; // Keep the follow-up stream open until the page closes.
        if (p === "/v1/jobs/first")
          data = {
            id: "first",
            state: "completed",
            result: { answer: "Original completed" },
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
            : "text/html",
      });
    });
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto("http://composer.test");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.evaluate(() => {
      job = parent = conversation = "first";
      active = assistant("first");
      void watch();
    });
    // Beginner: empty input offers stop.
    assert.equal(await page.locator("#cancel").isVisible(), true);
    // Writer: typing immediately offers enabled send.
    await page.fill("#prompt", "Continuation");
    assert.equal(await page.locator("#send").isVisible(), true);
    assert.equal(await page.locator("#send").isEnabled(), true);
    // Editor: deleting or whitespace restores stop.
    await page.fill("#prompt", "");
    assert.equal(await page.locator("#cancel").isVisible(), true);
    await page.fill("#prompt", "   ");
    assert.equal(await page.locator("#cancel").isVisible(), true);
    // Keyboard user: Enter submits; rapid repetition cannot duplicate.
    await page.fill("#prompt", "Continuation");
    await page.press("#prompt", "Enter");
    await page.press("#prompt", "Enter");
    await page.waitForFunction(() => submitting);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].parent_job_id, "first");
    releasePost();
    await page.waitForFunction(
      () => !submitting && !document.getElementById("prompt").value,
    );
    assert.equal(await page.locator("#cancel").isVisible(), true);
    assert.equal(
      await page.evaluate(() => job),
      "first",
      "original stream remains active",
    );
    // Multitasker: further follow-ups chain to the latest accepted message.
    await page.fill("#prompt", "Another");
    await page.click("#send");
    await page.waitForFunction(
      () => !submitting && !document.getElementById("prompt").value,
    );
    assert.equal(requests[1].parent_job_id, "follow-1");
    // Network failure: preserve the draft and the original execution.
    fail = true;
    await page.fill("#prompt", "Preserve");
    await page.click("#send");
    await page.waitForFunction(() => !submitting);
    assert.equal(await page.inputValue("#prompt"), "Preserve");
    assert.equal(await page.evaluate(() => busy), true);
    // Streaming user: finishing the first response starts watching the next.
    finished = true;
    releaseFirst();
    await page.waitForFunction(() => job === "follow-1");
    assert.match(
      await page.locator("#messages").innerText(),
      /Original completed/,
    );
    assert.equal(await page.inputValue("#prompt"), "Preserve");
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.waitForFunction(() => busy && job === "follow-1");
    assert.equal(await page.evaluate(() => parent), "follow-2");
    assert.equal(await page.inputValue("#prompt"), "Preserve");
    assert.equal(await page.evaluate(() => queuedTurns.length), 1);
    await page.fill("#prompt", "");
    await page.click("#cancel");
    await page.waitForFunction(() => !cancelling);
    assert.deepEqual(cancellations, ["/v1/jobs/follow-1/cancel"]);
    assert.deepEqual(errors, []);
    // Concurrency: completion of the original send cannot unlock a pending follow-up.
    const race = await browser.newPage();
    let posted = 0,
      finishOriginal,
      acceptFollowup;
    const originalGate = new Promise((resolve) => (finishOriginal = resolve)),
      followupGate = new Promise((resolve) => (acceptFollowup = resolve));
    await race.route("http://race.test/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      if (p.startsWith("/v1/")) {
        let data = {};
        if (p === "/v1/projects") data = { projects: ["sem-projeto"] };
        if (p === "/v1/models")
          data = {
            models: [
              { id: "fixture", backend: "local", efforts: ["configured"] },
            ],
          };
        if (p === "/v1/conversations") data = { conversations: [] };
        if (p === "/v1/jobs" && route.request().method() === "POST") {
          posted++;
          if (posted > 1) await followupGate;
          data = { job_id: posted === 1 ? "original" : "next" };
        }
        if (p === "/v1/jobs/original/events") {
          await originalGate;
          return route.fulfill({ body: "", contentType: "text/event-stream" });
        }
        if (p === "/v1/jobs/next/events") return;
        if (p === "/v1/jobs/original")
          data = { state: "completed", result: { answer: "Done" } };
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
            : "text/html",
      });
    });
    await race.goto("http://race.test");
    await race.locator("#startup-gate").waitFor({ state: "hidden" });
    await race.fill("#prompt", "First");
    await race.click("#send");
    await race.waitForFunction(() => job === "original" && !submitting);
    await race.fill("#prompt", "Next");
    await race.click("#send");
    await race.waitForFunction(() => submitting);
    finishOriginal();
    await race.waitForFunction(() => !busy);
    assert.equal(
      await race.evaluate(() => submitting),
      true,
      "original completion must not clear another request submission guard",
    );
    await race.press("#prompt", "Enter");
    assert.equal(posted, 2);
    acceptFollowup();
    await race.waitForFunction(() => job === "next" && busy && !submitting);
    console.log(
      "PASS: stop/send, erase, whitespace, Enter, duplicate guard, queued chaining, failure draft and stream continuity",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
