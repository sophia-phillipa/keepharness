const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises"),
  path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
        viewport: { width: 1440, height: 1000 },
      }),
      errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const turn = (id) => ({
      id,
      project: "sem-projeto",
      state: "completed",
      request: {
        prompt: "Question " + id,
        model: "fixture",
        backend: "local",
        effort: "low",
      },
      result: {
        answer: "Answer " + id,
        model: "fixture",
        total_seconds: 12,
        metrics: { output_tokens: 11, debug: "RAW_METRIC_SENTINEL" },
        references: ["RAW_REFERENCE_SENTINEL"],
      },
    });
    const eventList = (id) => [
      { id: 1, type: "running", data: {} },
      { id: 2, type: "thinking", data: {} },
      {
        id: 3,
        type: "reasoning_delta",
        data: { text: "RAW_THOUGHT_SENTINEL" },
      },
      {
        id: 4,
        type: "tool_start",
        data: {
          tool: "commandExecution",
          tool_id: "cmd-" + id,
          command_name: "ls",
          command: "ls RAW_INPUT_SENTINEL",
        },
      },
      {
        id: 4,
        type: "tool_start",
        data: {
          tool: "commandExecution",
          tool_id: "cmd-" + id,
          command_name: "ls",
          command: "ls RAW_INPUT_SENTINEL",
        },
      },
      {
        id: 5,
        type: "tool_end",
        data: {
          tool: "commandExecution",
          tool_id: "cmd-" + id,
          command_name: "ls",
          status: "completed",
          result: "RAW_OUTPUT_SENTINEL",
        },
      },
      {
        id: 6,
        type: "plan_updated",
        data: { plan: [{ step: "Review completed", status: "completed" }] },
      },
      { id: 7, type: "answer_delta", data: { text: "Answer " + id } },
      { id: 8, type: "completed", data: {} },
    ];
    const origin = process.env.HARNESS_URL || "http://panel.test";
    await page.route(origin + "/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      let data = {};
      if (p.startsWith("/v1/")) {
        if (p === "/v1/projects")
          data = { projects: ["sem-projeto"], details: {} };
        if (p === "/v1/models")
          data = {
            models: [
              {
                id: "fixture",
                name: "Test model",
                backend: "local",
                efforts: ["low"],
              },
            ],
            providers: { local: true },
            uploads_enabled: false,
          };
        if (p === "/v1/version")
          data = { version: "test", build: "milestones" };
        if (p === "/v1/conversations")
          data = {
            conversations: [
              {
                id: "conversation",
                title: "Conversation with two answers",
                project: "sem-projeto",
                state: "completed",
              },
            ],
          };
        if (p === "/v1/conversations/conversation")
          data = { turns: [turn("one"), turn("two")] };
        if (p === "/v1/jobs" && route.request().method() === "POST")
          data = { job_id: "three" };
        if (p.startsWith("/v1/jobs/")) {
          const id = p.split("/")[3];
          data = turn(id);
          if (p.endsWith("/events"))
            return route.fulfill({
              contentType: "text/event-stream",
              body: eventList(id)
                .map((e) => "data: " + JSON.stringify(e) + "\n\n")
                .join(""),
            });
        }
        return route.fulfill({ json: data });
      }
      if (process.env.HARNESS_URL) return route.continue();
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
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.10.1"));
    await page.goto(origin);
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.locator(".conversation-row>button").first().click();
    await page.waitForFunction(
      () =>
        document.querySelectorAll("#messages .message-activity").length === 2 &&
        !document.querySelector("#send").hidden,
    );
    const old = page.locator("#messages .message-activity").first(),
      latest = page.locator("#messages .message-activity").nth(1);
    assert.equal(await old.evaluate((el) => el.open), false);
    assert.equal(await latest.evaluate((el) => el.open), false);
    await old.locator("summary").click();
    await old
      .getByText(/command ls/i)
      .first()
      .waitFor();
    assert.match(await old.innerText(), /Thinking|Reasoning/i);
    assert.equal(
      await old.locator("pre,code,details").count(),
      0,
      "Expanded activity contains only titles",
    );
    // Historical and live rendering must not echo inputs, outputs, raw reasoning or JSON metrics.
    async function noRaw() {
      assert.doesNotMatch(
        await page.locator("#messages").textContent(),
        /RAW_\w+_SENTINEL/,
      );
      assert.doesNotMatch(
        await page.locator("#activity-panel").textContent(),
        /RAW_\w+_SENTINEL/,
      );
    }
    await noRaw();
    await latest.locator("summary").click();
    await latest
      .getByText(/command ls/i)
      .first()
      .waitFor();
    assert.match(await latest.innerText(), /command ls/i);
    const titles = await latest.locator("li").allTextContents();
    assert.equal(
      titles.length,
      new Set(titles).size,
      "Repeated SSE IDs must not duplicate titles",
    );
    await page.fill("#prompt", "Third question");
    await page.click("#send");
    await page.waitForFunction(
      () =>
        document.querySelectorAll("#messages .message-activity").length === 3 &&
        !document.querySelector("#send").hidden,
    );
    const newest = page.locator("#messages .message-activity").last();
    assert.equal(await newest.evaluate((el) => el.open), false);
    await newest.locator("summary").click();
    assert.match(await newest.innerText(), /command ls/i);
    await noRaw();
    assert.match(await page.locator("#messages").innerText(), /Answer three/);
    assert.equal(
      await page.locator("#activity-panel .activity-result").count(),
      0,
    );
    assert.doesNotMatch(
      await page.locator("#activity-panel").innerText(),
      /Running command ls/i,
      "Sidebar is for main milestones",
    );
    await page.fill("#prompt", "Preserved draft");
    await page.evaluate(() => TailTheme.apply("arizona", false));
    if (await page.locator("#th-toast").isVisible())
      await page.locator("#th-toast button").click();
    await page.screenshot({
      path: "/tmp/tail-inline-milestones-desktop.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    if (await page.locator("#activity-panel").isVisible())
      await page.click("#panel-toggle");
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    assert.equal(await page.locator("#prompt").inputValue(), "Preserved draft");
    await page.screenshot({
      path: "/tmp/tail-inline-milestones-mobile.png",
      fullPage: true,
    });
    assert.deepEqual(errors, []);
    console.log(
      "PASS: collapsed inline milestones, safe command titles, historical/live replay, SSE deduplication, sidebar summary, no raw payloads, mobile and draft.",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
