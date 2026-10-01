// Conversational agents stay visible until the person ends the persona on a later turn.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const posts = [];
    await page.route("http://persona.test/**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith("/v1/")) {
        let data = {};
        if (url.pathname === "/v1/projects") data = { projects: ["p"], details: {} };
        if (url.pathname === "/v1/models")
          data = {
            models: [
              {
                id: "claude-sonnet-4-6",
                backend: "claude",
                efforts: ["low"],
                execution_modes: ["native"],
              },
            ],
            providers: { claude: true },
          };
        if (url.pathname === "/v1/usage") data = { available: false };
        if (url.pathname === "/v1/conversations/persona-history")
          data = {
            title: "Persona history",
            execution_mode: "native",
            turns: [
              {
                id: "persona-history-turn",
                project: "p",
                state: "completed",
                request: {
                  prompt: "/demo--advisor Earlier question",
                  backend: "claude",
                  model: "claude-sonnet-4-6",
                  effort: "low",
                  invocations: [
                    {
                      kind: "agent",
                      resource_id: "catalog/demo/agents/advisor.md",
                      mode: "conversational",
                    },
                  ],
                  resource_selections: [
                    {
                      id: "catalog/demo/agents/advisor.md",
                      revision: "a1",
                      token: "/demo--advisor",
                    },
                  ],
                },
                attachments: [],
                result: { answer: "Earlier answer", metrics: {} },
                gates: [
                  {
                    gate_id: "history-gate",
                    question: "Which tone?",
                    options: [
                      { id: "brief", label: "Brief", description: "Use fewer words" },
                      { id: "full", label: "Full", description: "Include detail" },
                    ],
                    multi_select: false,
                    state: "resolved",
                    choice: "brief",
                    resolved_by: "local",
                  },
                ],
              },
            ],
          };
        if (url.pathname === "/v1/conversations/ordinary-history")
          data = {
            title: "Ordinary history",
            execution_mode: "native",
            turns: [
              {
                id: "ordinary-history-turn",
                project: "p",
                state: "completed",
                request: {
                  prompt: "Ordinary question",
                  backend: "claude",
                  model: "claude-sonnet-4-6",
                  effort: "low",
                },
                attachments: [],
                result: { answer: "Ordinary answer", metrics: {} },
                gates: [],
              },
            ],
          };
        if (url.pathname === "/v1/conversations") data = { conversations: [] };
        if (url.pathname === "/v1/jobs" && route.request().method() === "POST") {
          posts.push(route.request().postDataJSON());
          data = { job_id: "release-job" };
        }
        if (url.pathname === "/v1/jobs/release-job/events")
          return route.fulfill({ contentType: "text/event-stream", body: "" });
        if (url.pathname === "/v1/jobs/release-job")
          data = {
            id: "release-job",
            project: "p",
            state: "completed",
            request: posts.at(-1),
            result: { answer: "Released", metrics: {} },
          };
        return route.fulfill({ json: data });
      }
      const file = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
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

    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.12"));

    await page.goto("http://persona.test");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.evaluate(() => {
      parent = "persona-turn";
      event({
        id: 50,
        type: "invocation_started",
        data: {
          role: "demo--advisor",
          invocation: {
            kind: "agent",
            resource_id: "catalog/demo/agents/advisor.md",
            mode: "conversational",
          },
        },
      });
    });
    const control = page.locator("#persona-control");
    await control.waitFor();
    assert.match(await control.innerText(), /Agent conversation: demo--advisor/);
    await page.click(".persona-end");
    assert.match(await control.innerText(), /Ending after your next message/);
    await page.evaluate(() =>
      event({
        id: 51,
        type: "invocation_started",
        data: {
          role: "demo--advisor",
          invocation: {
            resource_id: "catalog/demo/agents/advisor.md",
            mode: "conversational",
          },
        },
      }),
    );
    assert.match(await control.innerText(), /Ending after your next message/);

    const prompt = "Keep both spaces  ";
    await page.fill("#prompt", prompt);
    await page.click("#send");
    await page.waitForFunction(() => !submitting && !busy);
    assert.equal(posts.length, 1);
    assert.equal(posts[0].prompt, prompt);
    assert.equal(posts[0].parent_job_id, "persona-turn");
    assert.equal(posts[0].release_persona, true);
    assert(await control.isHidden());

    await page.evaluate(() =>
      event({
        id: 52,
        type: "invocation_started",
        data: {
          role: "demo--advisor",
          invocation: {
            resource_id: "catalog/demo/agents/advisor.md",
            mode: "conversational",
          },
        },
      }),
    );
    assert(await control.isVisible());
    await page.click("#new");
    assert(await control.isHidden());

    await page.evaluate(() => load("persona-history"));
    await page.waitForFunction(() => conversation === "persona-history" && !loading);
    assert.match(await control.innerText(), /Agent conversation: demo--advisor/);
    assert.equal(
      await page.locator(".message-resource-chip").innerText(),
      "/demo--advisor",
    );
    const restoredGate = page.locator("#gate-history-gate");
    assert.equal(await restoredGate.getAttribute("data-state"), "resolved");
    assert(await restoredGate.locator('input[value="brief"]').isChecked());
    assert(await restoredGate.locator('input[value="brief"]').isDisabled());

    await page.evaluate(() => load("ordinary-history"));
    await page.waitForFunction(() => conversation === "ordinary-history" && !loading);
    assert(await control.isHidden());
    console.log("PASS: conversational persona release, history restore, gates and resource chips");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
