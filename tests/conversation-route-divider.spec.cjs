// Aggregator (Sophia, 2026-10-03): a conversation can change model or provider
// between turns and keep its context; a quiet divider marks each change.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");

const turn = (id, backend, model, prompt) => ({
  id,
  project: "sem-projeto",
  state: "completed",
  request: { backend, model, prompt, access_mode: "ask", effort: "medium" },
  result: { answer: "Answer to " + prompt },
});

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    const posted = [];
    page.on("pageerror", (e) => console.error("PAGEERROR", e.message));
    await page.route("http://route.test/**", async (route) => {
      const url = new URL(route.request().url()),
        pathname = url.pathname;
      if (!pathname.startsWith("/v1/"))
        return route.fulfill({
          path: path.join(
            __dirname,
            "..",
            pathname.startsWith("/assets/") ? "harness_ui" : "agent_service",
            pathname === "/" ? "index.html" : pathname,
          ),
        });
      const turns = [
        turn("t1", "codex", "gpt-6-astra", "Describe the photo"),
        turn("t2", "codex", "gpt-5.6-sol", "Reflect on it"),
        turn("t3", "claude", "claude-sonnet-5-5", "Summarize everything"),
      ];
      let data = {};
      if (pathname === "/v1/projects") data = { projects: ["sem-projeto"] };
      else if (pathname === "/v1/models")
        data = {
          models: [
            { id: "gpt-6-astra", name: "GPT-6 Astra", backend: "codex", efforts: ["medium"] },
            { id: "gpt-5.6-sol", name: "GPT-5.6 Sol", backend: "codex", efforts: ["medium"] },
            { id: "claude-sonnet-5-5", name: "Claude Sonnet 5.5", backend: "claude", efforts: ["medium"] },
          ],
          providers: { codex: true, claude: true },
          uploads_enabled: false,
        };
      else if (pathname === "/v1/conversations")
        data = {
          conversations: [
            { id: "mix", title: "Mixed models", project: "sem-projeto", state: "completed", last_job_id: "t3", updated_at: 1 },
          ],
        };
      else if (pathname === "/v1/conversations/mix") data = { title: "Mixed models", turns };
      else if (pathname === "/v1/version") data = { version: "fixture", build: "route-divider" };
      else if (pathname === "/v1/jobs" && route.request().method() === "POST") {
        const body = route.request().postDataJSON();
        posted.push(body);
        data = { job_id: "t4", backend: body.backend, model: body.model, execution_mode: "native" };
      } else if (pathname === "/v1/jobs/t4/events")
        return route.fulfill({ body: "", contentType: "text/event-stream" });
      else if (pathname === "/v1/jobs/t4") data = turn("t4", "codex", "gpt-6-astra", "Back to Astra");
      else if (/^\/v1\/jobs\/t[123]$/.test(pathname)) data = turns.find((t) => "/v1/jobs/" + t.id === pathname);
      return route.fulfill({ json: data });
    });
    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
    await page.goto("http://route.test/");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.locator("#history .conversation-row > button", { hasText: "Mixed models" }).click();
    await page.waitForFunction(() => document.querySelectorAll("#messages article.user").length === 3);

    const dividers = page.getByRole("note").filter({ hasText: /changed to|Switched to/ });
    assert.deepEqual(await dividers.allInnerTexts(), [
      "Model changed to GPT-5.6 Sol",
      "Switched to Claude Sonnet 5.5 · Claude Code — the conversation so far goes with it",
    ]);
    // Each divider sits right before the user message that used the new model.
    const order = await page.locator("#messages > *").evaluateAll((nodes) =>
      nodes.map((node) => (node.classList.contains("route-divider") ? "divider" : node.classList.contains("user") ? "user" : "other")),
    );
    assert.deepEqual(order.filter((kind) => kind !== "other"), ["user", "divider", "user", "divider", "user"]);

    // UX-R1-4: a visible one-line note before sending on another provider, hidden when it is the same one.
    const carryover = page.locator("#route-carryover");
    await page.locator("#model").selectOption("claude-sonnet-5-5");
    assert.equal(await carryover.isHidden(), true, "the last turn used Claude already");
    await page.locator("#model").selectOption("gpt-6-astra");
    assert.equal(
      await carryover.innerText(),
      "Next message goes to Codex · GPT-6 Astra. The conversation so far goes with it.",
    );
    await page.locator("#model").selectOption("claude-sonnet-5-5");
    assert.equal(await carryover.isHidden(), true);
    // Switching back to Codex in the same conversation adds one more divider.
    await page.locator("#model").selectOption("gpt-6-astra");
    await page.fill("#prompt", "Back to Astra");
    await page.locator("#send").click();
    await page.waitForFunction(() => document.querySelectorAll("#messages article.user").length === 4);
    assert.equal(posted.at(-1).backend, "codex");
    assert.equal(
      await dividers.last().innerText(),
      "Switched to GPT-6 Astra · Codex — the conversation so far goes with it",
    );
    assert.equal(await dividers.count(), 3);

    assert.equal(await carryover.isHidden(), true, "the sent turn is the new baseline");
    // A new conversation starts without dividers.
    await page.click("#new");
    assert.equal(await page.locator("#messages .route-divider").count(), 0);
    console.log("PASS model and provider switches are marked in the conversation");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
