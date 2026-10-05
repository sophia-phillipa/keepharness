// Chat campaign batch 1 (C-01 project label, C-03 tool step names, C-04 active row, C-05 header pill, C-06 auto-scroll).
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises"),
  path = require("node:path");

const TRANSPARENT = "rgba(0, 0, 0, 0)";
const turn = {
  id: "job-1",
  project: "project-a",
  state: "completed",
  request: { prompt: "Read facts/alpha.txt", model: "fixture", backend: "local", effort: "low" },
  result: { answer: "Alpha.", model: "fixture", total_seconds: 5 },
};
const events = [
  { id: 1, type: "running", data: {} },
  {
    id: 2,
    type: "tool_start",
    data: { tool: "read_file", tool_id: "mcp-1", server: "harness_reader" },
  },
  { id: 3, type: "tool_end", data: { tool: "read_file", tool_id: "mcp-1", status: "completed" } },
  {
    id: 4,
    type: "tool_start",
    data: { tool: "mcp__harness_reader__list_dir<img src=x onerror=alert(1)>", tool_id: "mcp-2" },
  },
  { id: 5, type: "tool_end", data: { tool: "x", tool_id: "mcp-2", status: "completed" } },
  { id: 6, type: "tool_start", data: { tool: "mcp__harness_reader__list_dir", tool_id: "mcp-3" } },
  { id: 7, type: "tool_end", data: { tool: "x", tool_id: "mcp-3", status: "completed" } },
  // Protocol types, not tool names: Codex native sends the item type, Gemini the ACP kind.
  { id: 8, type: "tool_start", data: { tool: "fileChange", tool_id: "mcp-4" } },
  { id: 9, type: "tool_end", data: { tool: "x", tool_id: "mcp-4", status: "completed" } },
  { id: 10, type: "answer_delta", data: { text: "Alpha." } },
  { id: 11, type: "completed", data: {} },
];

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("dialog", (d) => {
      errors.push("dialog " + d.message());
      d.dismiss();
    });
    const origin = "http://panel.test";
    await page.route(origin + "/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      if (p.startsWith("/v1/")) {
        let data = {};
        if (p === "/v1/projects")
          data = { projects: ["sem-projeto", "project-a"], details: { "project-a": { label: "Project Alpha" } } };
        else if (p === "/v1/models")
          data = {
            models: [{ id: "fixture", name: "Fixture", backend: "local", efforts: ["low"] }],
            providers: { local: true },
            uploads_enabled: false,
          };
        else if (p === "/v1/version") data = { version: "test", build: "batch1" };
        else if (p === "/v1/conversations")
          data = {
            conversations: [
              { id: "c-project", title: "In a project", project: "project-a", state: "running" },
              { id: "c-chat", title: "Plain chat", project: "sem-projeto", state: "completed" },
            ],
          };
        else if (p.startsWith("/v1/conversations/")) data = { turns: [turn] };
        else if (p.endsWith("/events"))
          return route.fulfill({
            contentType: "text/event-stream",
            body: events.map((e) => "data: " + JSON.stringify(e) + "\n\n").join(""),
          });
        else if (p.startsWith("/v1/jobs/")) data = turn;
        return route.fulfill({ json: data });
      }
      const file = p === "/" ? "index.html" : p.slice(1);
      return route.fulfill({
        body: await fs.readFile(
          path.join(__dirname, file.startsWith("assets/") ? "../harness_ui" : "../agent_service", file),
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
    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
    await page.goto(origin);
    await page.locator("#startup-gate").waitFor({ state: "hidden" });

    // C-01: a new conversation without a project reads "No project", like the sidebar rows.
    assert.equal(await page.locator("#project").inputValue(), "sem-projeto");
    assert.equal(await page.locator("#project-button-label").innerText(), "No project");

    // C-04: the active row paints one background; the actions area adds no block of its own.
    const chatRow = page.locator(".conversation-row", { hasText: "Plain chat" });
    await chatRow.locator("button").first().click();
    await page.waitForFunction(() => document.querySelector(".conversation-row button.active"));
    await page.mouse.move(700, 500);
    for (const row of [chatRow, page.locator(".conversation-row", { hasText: "In a project" })]) {
      await row.locator("button").first().click();
      await page.waitForFunction(() => document.querySelectorAll("#messages .message-activity").length === 1);
      await page.mouse.move(700, 500);
      const paint = await row.evaluate((el) => {
        const rowColor = getComputedStyle(el).backgroundColor;
        return [...el.querySelectorAll(":scope > button, .conversation-actions, .conversation-actions > summary")]
          .map((node) => ({ node: node.tagName + "." + node.className, color: getComputedStyle(node).backgroundColor }))
          .filter((item) => item.color !== "rgba(0, 0, 0, 0)" && item.color !== rowColor);
      });
      assert.deepEqual(paint, [], "active row children must not paint over the row");
    }

    // C-03: inline steps name the tool; hostile names stay text.
    const steps = page.locator("#messages .message-activity").first();
    await steps.locator("summary").click();
    await steps.locator("li").first().waitFor();
    const text = await steps.innerText();
    assert.match(text, /Ran read_file/);
    assert.match(text, /Ran list_dir/, "an MCP tool is named by its last segment");
    assert.equal(text.match(/Ran tool/g)?.length, 2, "an odd name and a protocol type (fileChange) read as the generic step");
    assert.doesNotMatch(text, /fileChange/);
    assert.equal(await steps.locator("img").count(), 0);

    // C-04: hovering a non-active row paints the row once; the title button stays transparent.
    const idleRow = page.locator(".conversation-row", { hasText: "Plain chat" });
    await idleRow.locator("button").first().hover();
    const hoverPaint = await idleRow.evaluate((el) => ({
      row: getComputedStyle(el).backgroundColor,
      button: getComputedStyle(el.querySelector(":scope > button")).backgroundColor,
    }));
    assert.notEqual(hoverPaint.row, TRANSPARENT, "the hovered row is painted");
    assert.equal(hoverPaint.button, TRANSPARENT, "the hovered row's title button must not repaint it");
    await page.mouse.move(700, 500);

    // C-05: while a run streams behind queued follow-ups the header pill says Running.
    await page.evaluate(() =>
      applyActivitySnapshot({
        jobs: [
          { conversation_id: "c-project", state: "queued", id: "q2" },
          { conversation_id: "c-project", state: "queued", id: "q1" },
          { conversation_id: "c-project", state: "running", id: "r0" },
        ],
        needs_you: [],
        counts: {},
      }),
    );
    assert.equal(await page.locator("#conversation-state-pill").innerText(), "Running");

    // C-06: the list stays pinned to the bottom while large chunks arrive, and stops once the user scrolls up.
    const gap = () =>
      page.evaluate(() => {
        const box = document.getElementById("messages");
        return Math.round(box.scrollHeight - box.scrollTop - box.clientHeight);
      });
    const grow = (chunks) =>
      page.evaluate((n) => {
        // Synchronous chunks bigger than the old 250 px follow window, like a fast stream.
        for (let i = 0; i < n; i++) {
          const block = document.createElement("div");
          block.style.cssText = "height:600px;flex:none";
          document.getElementById("messages").append(block);
          scroll();
        }
      }, chunks);
    await grow(2);
    await page.evaluate(() => jumpToLatest());
    await page.waitForFunction(() => document.getElementById("latest-message").hidden);
    await grow(4);
    await page.waitForTimeout(400);
    assert.ok((await gap()) <= 2, "pinned view follows chunks larger than 250 px, gap " + (await gap()));
    await page.locator("#messages").hover();
    await page.mouse.wheel(0, -900);
    await page.waitForFunction(() => {
      const box = document.getElementById("messages");
      return box.scrollHeight - box.scrollTop - box.clientHeight > 400;
    });
    const top = await page.evaluate(() => document.getElementById("messages").scrollTop);
    await grow(2);
    await page.waitForTimeout(400);
    assert.equal(await page.evaluate(() => document.getElementById("messages").scrollTop), top, "scrolled-up view stays put");
    await page.locator("#latest-message").click();
    await grow(3);
    await page.waitForTimeout(400);
    assert.ok((await gap()) <= 2, "returning to the bottom pins again, gap " + (await gap()));

    assert.deepEqual(errors, []);
    console.log("PASS: chat campaign batch 1 (C-01 label, C-03 tool names, C-04 active row, C-05 pill, C-06 auto-scroll).");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
