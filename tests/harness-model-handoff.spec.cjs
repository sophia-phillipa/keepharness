// Same conversation, different providers/models/efforts; no live inference.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage(),
      turns = [],
      errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const models = [
      { id: "deepseek-flash", backend: "deepseek", efforts: ["low", "high"] },
      { id: "qwen-local", backend: "local", efforts: ["configured"] },
      { id: "gpt-6-astra", backend: "codex", efforts: ["low", "high"] },
      { id: "gpt-5.6-terra", backend: "codex", efforts: ["low", "medium"] },
    ].map((m) => ({ ...m, permissions: { upload: true } }));
    await page.route("http://handoff.test/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      if (p.startsWith("/v1/")) {
        let data = {};
        if (p === "/v1/projects") data = { projects: ["sem-projeto"] };
        if (p === "/v1/models")
          data = {
            models,
            uploads_enabled: true,
            providers: { local: true, deepseek: true, codex: true },
          };
        if (p === "/v1/usage") data = { available: false };
        if (p === "/v1/conversations")
          data = {
            conversations: turns.length
              ? [
                  {
                    id: "job-1",
                    title: "Same task",
                    project: "sem-projeto",
                    state: "completed",
                  },
                ]
              : [],
          };
        if (p === "/v1/conversations/job-1")
          data = { title: "Same task", turns };
        if (p === "/v1/jobs" && route.request().method() === "POST") {
          const request = route.request().postDataJSON(),
            id = "job-" + (turns.length + 1);
          assert.equal(request.parent_job_id, turns.at(-1)?.id);
          turns.push({
            id,
            project: "sem-projeto",
            state: "completed",
            request,
            result: { answer: "Answer " + id },
          });
          data = { job_id: id };
        }
        if (/^\/v1\/jobs\/job-\d+$/.test(p))
          data = turns.find((t) => t.id === p.split("/").at(-1));
        if (p.endsWith("/events"))
          return route.fulfill({ body: "", contentType: "text/event-stream" });
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
    await page.goto("http://handoff.test");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    const choices = [
      ["deepseek-flash", "low"],
      ["qwen-local", "configured"],
      ["gpt-6-astra", "low"],
      ["gpt-6-astra", "high"],
      ["gpt-5.6-terra", "medium"],
      ["deepseek-flash", "high"],
    ];
    for (const [model, effort] of choices) {
      await page.selectOption("#model", model);
      await page.selectOption("#effort", effort);
      await page.fill("#prompt", "Continue " + model + " " + effort);
      await page.click("#send");
      await page.waitForFunction(() => !busy && !submitting);
      assert.equal(turns.at(-1).request.model, model);
      assert.equal(turns.at(-1).request.effort, effort);
      assert.equal(await page.evaluate(() => conversation), "job-1");
    }
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    assert.equal(await page.evaluate(() => parent), "job-6");
    assert.equal(await page.evaluate(() => conversation), "job-1");
    turns.at(-1).state = "failed";
    turns.at(-1).result = { error: "context_limit_exceeded" };
    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page
      .getByText("Couldn't prepare or process the context for this attempt.", {
        exact: false,
      })
      .waitFor();
    assert.equal(
      await page.getByText("To analyze the CSV", { exact: false }).count(),
      0,
    );
    assert.equal(await page.evaluate(() => parent), "job-6");
    await page.locator("#prompt").focus();
    await page.keyboard.type("Continue after the interruption");
    await page.locator("#send").focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => !busy && !submitting);
    assert.equal(turns.at(-1).request.parent_job_id, "job-6");
    await page.evaluate(() => {
      active = assistant("compact-fixture");
      event({ id: last + 1, type: "context_compacting", data: {} });
    });
    assert.match(
      await page.locator("#activity-state").textContent(),
      /Optimizing/,
    );
    await page.evaluate(() =>
      event({ id: last + 1, type: "context_compacted", data: {} }),
    );
    assert.match(
      await page.locator("#activity-state").textContent(),
      /Context optimized/,
    );
    assert.deepEqual(errors, []);
    console.log(
      "PASS: provider/model/effort changes preserve parent chain and conversation after reload.",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
