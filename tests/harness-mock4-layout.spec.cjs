const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");

(async () => {
  const browser = await chromium.launch();
  try {
    const origin = "http://mock4-layout.test";
    let approvedPayload = null;
    let planPending = true;
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.addInitScript(() => {
      localStorage.setItem("tail-harness-tour-seen", "0.11.0");
      localStorage.setItem("activity-open", "1");
    });
    await page.route(origin + "/**", async (route) => {
      const url = new URL(route.request().url());
      if (!url.pathname.startsWith("/v1/")) {
        const file = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
        return route.fulfill({
          path: path.join(__dirname, "..", file.startsWith("assets/") ? "tail_ui" : "agent_service", file),
        });
      }
      const method = route.request().method();
      const data = url.pathname === "/v1/projects"
        ? { projects: ["p"], details: { p: { label: "Tail Harness" } } }
        : url.pathname === "/v1/models"
          ? { models: [{ id: "fixture", name: "Fixture", backend: "codex", efforts: ["medium"], execution_modes: ["native", "scoped"] }], providers: { codex: true } }
          : url.pathname === "/v1/conversations"
            ? { conversations: [
                { id: "c1", title: "Approval needed", project: "p", state: "running", last_job_id: "j1", execution: { backend: "codex", model: "fixture" }, updated_at: Date.now() / 1000 },
                { id: "c2", title: "Running task", project: "p", state: "running", last_job_id: "j2", execution: { backend: "codex", model: "fixture" } },
                { id: "c3", title: "Queued task", project: "p", state: "queued", wait_reason: "Provider busy", execution: { backend: "codex", model: "fixture" } },
                { id: "c4", title: "Done task", project: "p", state: "completed", execution: { backend: "codex", model: "fixture" } },
              ] }
            : url.pathname === "/v1/conversations/c1"
              ? { title: "Approval needed", execution_mode: "scoped", turns: [{ id: "j1", project: "p", state: "running", request: { backend: "codex", model: "fixture", prompt: "Export the report", access_mode: "ask", effort: "medium" }, result: {}, gates: [{ gate_id: "plan-gate", kind: "maestro_plan", state: planPending ? "pending" : "resolved", choice: planPending ? undefined : "approve", plan: { steps: [{ role: "reviewer", backend: "codex", model: "fixture", effort: "medium", task: "Review" }] } }] }] }
              : url.pathname === "/v1/jobs/j1"
                ? { id: "j1", project: "p", state: "running", request: { backend: "codex", model: "fixture" }, result: {} }
                : url.pathname === "/v1/activity"
                  ? { counts: { running: 2, queued: 1, needs_you: planPending ? 1 : 0 }, jobs: [{ job_id: "j1", conversation_id: "c1", project_id: "p", state: "running", backend: "codex", model: "fixture" }], providers: [{ backend: "codex", model: "fixture", state: "ready", running: 2, queued: 1 }], needs_you: planPending ? [{ gate_id: "plan-gate", kind: "gate", approval_kind: "maestro_plan", conversation_id: "c1", job_id: "j1", plan: { steps: [{ role: "reviewer", backend: "codex", model: "fixture", effort: "medium", task: "Review" }] } }] : [] }
                  : url.pathname === "/v1/jobs/j1/spans"
                    ? { spans: [{ span_id: "s1", name: "Planner", start_ts: 1, end_ts: 2, status: "completed", attrs: { backend: "codex", model: "fixture", effort: "medium", "gen_ai.usage.input_tokens": 10, "gen_ai.usage.output_tokens": 5 } }] }
                    : url.pathname === "/v1/project-files"
                      ? { roots: [], entries: [] }
                      : url.pathname === "/v1/version"
                        ? { version: "0.11.0", build: "fixture" }
                        : url.pathname === "/v1/catalog"
                          ? { agents: [], skills: [], warnings: [] }
                          : url.pathname === "/v1/usage"
                            ? { available: false }
                            : {};
      if (url.pathname === "/v1/approvals/plan-gate" && method === "POST") {
        approvedPayload = route.request().postDataJSON();
        planPending = false;
        return route.fulfill({ json: { state: "resolved", choice: "approve" } });
      }
      return route.fulfill({ json: data });
    });
    await page.goto(origin);
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.locator("#history .conversation-row button").first().click();
    await page.waitForFunction(() => document.getElementById("conversation-title")?.textContent === "Approval needed");

    const regions = await page.locator("body > #app-topbar, body > #sidebar, body > main, body > #activity-panel").evaluateAll((nodes) => nodes.map((node) => node.id || node.tagName.toLowerCase()));
    assert.deepEqual(regions, ["app-topbar", "sidebar", "main", "activity-panel"]);
    const boxes = await page.locator("#sidebar, main, #activity-panel").evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect()));
    assert(boxes[0].right <= boxes[1].left + 1 && boxes[1].right <= boxes[2].left + 1);
    assert(boxes[0].width >= 299, "desktop sidebar keeps the approved 300px region");
    assert(await page.locator('[data-tour="top-search"]').isVisible());
    assert(await page.locator('[data-tour="attention-bell"]').isVisible());
    for (const label of ["Needs you", "Running", "Queued", "Done today"])
      assert(await page.getByRole("heading", { name: new RegExp(label, "i") }).count());
    await page.waitForFunction(() => /2 running.*1 queued.*1 needs you/i.test(document.getElementById("run-status-toggle")?.textContent || ""));
    assert.match(await page.locator("#run-status-toggle").innerText(), /2 running.*1 queued.*1 needs you/i);
    assert.equal(await page.locator("#conversation-state-pill").innerText(), "Awaiting approval");
    const strip = await page.locator(".run-status-strip").boundingBox();
    assert(strip.x <= 1 && strip.width >= 1439, "status strip spans the viewport");
    assert.equal(await page.locator("#console-run").inputValue(), "j1");
    assert.equal(await page.locator("#console-run").isVisible(), false);
    await page.keyboard.press("Control+j");
    assert(await page.getByRole("heading", { name: "RUN CONSOLE" }).isVisible());
    for (const tab of ["Pipeline", "Timeline", "Logs", "Runs", "Agents"])
      assert(await page.getByRole("tab", { name: new RegExp(tab) }).isVisible());
    const cardStyle = await page.locator(".run-span-row").first().evaluate((node) => ({
      border: getComputedStyle(node).borderTopWidth,
      layout: getComputedStyle(node.parentElement).display,
      chip: !!node.querySelector(".backend-chip"),
    }));
    assert.deepEqual(cardStyle, { border: "3px", layout: "flex", chip: true });
    await page.keyboard.press("Control+j");
    const plan = page.locator(".maestro-plan-card");
    assert(await plan.isVisible());
    await plan.getByRole("button", { name: "Edit plan in Run console" }).click();
    assert(await page.locator(".run-plan-approval textarea").isVisible());
    await page.keyboard.press("Control+j");
    await plan.getByRole("button", { name: "Approve plan & run" }).click();
    assert.deepEqual(approvedPayload, { choice: "approve", plan: { steps: [{ role: "reviewer", backend: "codex", model: "fixture", effort: "medium", task: "Review" }] } });
    await page.waitForFunction(() => document.querySelector(".maestro-plan-card")?.dataset.state === "running");
    await page.evaluate(() => finishGate("plan-gate", "resolved", { choice: "approve" }));
    await page.waitForFunction(() => document.querySelector(".maestro-plan-card")?.dataset.state === "resolved");
    assert.equal(await plan.getAttribute("data-state"), "resolved");
    assert.equal(await plan.getByRole("button", { name: "Approve plan & run" }).isDisabled(), true);

    for (const theme of ["violet-bordeaux", "porcelain", "mineral-rose", "amethyst", "petroleum", "arizona"]) {
      await page.evaluate((value) => window.TailTheme.apply(value), theme);
      assert.equal(await page.locator("html").getAttribute("data-palette"), theme);
      assert.equal(await page.locator("main").isVisible(), true);
    }
    await page.setViewportSize({ width: 400, height: 812 });
    await page.locator("#activity-panel").waitFor({ state: "hidden" });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    const mobileMain = await page.locator("main").boundingBox();
    assert(mobileMain && mobileMain.x === 0 && mobileMain.width >= 399, "mobile chat uses the full viewport");
    assert.equal(await page.locator("#activity-panel").isVisible(), false, "mobile returns to chat when crossing the breakpoint");
    for (const control of ["#menu", "#search-conversations", "#attention-bell", "#settings", "#panel-toggle", "#model-trigger"]) {
      const box = await page.locator(control).boundingBox();
      assert(box && box.x >= 0 && box.x + box.width <= 400, control + " stays fully reachable");
    }
    console.log("PASS: mock-4 regions, grouped sidebar, status, auto-bind, themes and 400px layout");
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
