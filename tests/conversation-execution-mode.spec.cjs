const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict"),
  path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
        viewport: { width: 1280, height: 900 },
      }),
      errors = [],
      sent = [];
    let fail = false;
    page.setDefaultTimeout(5000);
    page.on("pageerror", (e) => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem("activity-open", "0"));
    const turns = [];
    await page.route("http://panel.test/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      if (!p.startsWith("/v1/"))
        return route.fulfill({
          path: path.join(
            __dirname,
            "..",
            p.startsWith("/assets/") ? "tail_ui" : "agent_service",
            p === "/" ? "index.html" : p,
          ),
        });
      if (p === "/v1/jobs" && route.request().method() === "POST") {
        const data = route.request().postDataJSON();
        sent.push(data);
        if (fail)
          return route.fulfill({
            status: 500,
            json: { code: "fixture_failure" },
          });
        const id = "job-" + sent.length;
        turns.push({
          id,
          project: "p",
          state: "completed",
          request: data,
          result: { answer: "Done" },
        });
        return route.fulfill({ json: { job_id: id } });
      }
      if (p.endsWith("/events"))
        return route.fulfill({
          contentType: "text/event-stream",
          body:
            "id: 1\ndata: " +
            JSON.stringify({ id: 1, type: "completed", data: {} }) +
            "\n\n",
        });
      const data =
        p === "/v1/projects"
          ? { projects: ["p"], details: { p: { label: "Project" } } }
          : p === "/v1/models"
            ? {
                models: [
                  {
                    id: "claude-sonnet-4-6",
                    name: "Fixture",
                    backend: "claude",
                    efforts: ["low"],
                    execution_modes: ["native", "scoped"],
                  },
                  {
                    id: "local-fixture",
                    name: "Local fixture",
                    backend: "local",
                    efforts: ["low"],
                    execution_modes: ["scoped"],
                  },
                  {
                    id: "gemini-fixture",
                    name: "Gemini fixture",
                    backend: "gemini",
                    efforts: ["configured"],
                    execution_modes: ["native"],
                  },
                ],
                providers: { claude: true },
                uploads_enabled: false,
              }
            : p === "/v1/conversations"
              ? {
                  conversations: turns.length
                    ? [
                        {
                          id: turns[0].id,
                          title: "Test",
                          project: "p",
                          state: "completed",
                          execution_mode: turns[0].request.execution_mode,
                          last_job_id: turns.at(-1).id,
                        },
                      ]
                    : [],
                }
              : p.startsWith("/v1/conversations/")
                ? { execution_mode: turns[0]?.request.execution_mode, turns }
                : p.startsWith("/v1/jobs/")
                  ? turns.find((t) => t.id === p.split("/")[3]) || {}
                  : p === "/v1/version"
                    ? { version: "fixture", build: "fixture" }
                    : {};
      return route.fulfill({ json: data });
    });
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.2"));
    await page.goto("http://panel.test/");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    const toggle = page.getByRole("switch", { name: "Isolated conversation" });
    assert.equal(await toggle.getAttribute("aria-checked"), "false");
    assert.equal(
      await page.locator("#execution-mode-indicator").isVisible(),
      false,
    );
    await toggle.focus();
    await page.keyboard.press("Space");
    assert.equal(await toggle.getAttribute("aria-checked"), "true");
    const activeColor = await toggle.evaluate(
      (el) => getComputedStyle(el).backgroundColor,
    );
    assert.notEqual(activeColor, "rgba(0, 0, 0, 0)");
    // Exploratory: an explicit choice survives an incompatible model and reload.
    await page
      .locator("#model")
      .selectOption("gemini-fixture", { force: true });
    await page.locator("#prompt").fill("Draft 🐋 <test>");
    assert.equal(await toggle.getAttribute("aria-checked"), "true");
    assert.equal(await page.locator("#send").isDisabled(), true);
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    assert.equal(await toggle.getAttribute("aria-checked"), "true");
    assert.equal(await page.locator("#prompt").inputValue(), "Draft 🐋 <test>");
    assert.equal(await page.locator("#send").isDisabled(), true);
    assert.equal(sent.length, 0);
    await page
      .locator("#model")
      .selectOption("claude-sonnet-4-6", { force: true });
    assert.equal(await page.locator("#send").isEnabled(), true);
    await page.locator("#prompt").fill("Preserve draft");
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    assert.equal(await toggle.getAttribute("aria-checked"), "true");
    assert.equal(await page.locator("#prompt").inputValue(), "Preserve draft");
    fail = true;
    await page.locator("#send").click();
    await page.waitForFunction(() =>
      document.getElementById("status").textContent.includes("Couldn't"),
    );
    assert.equal(await toggle.isVisible(), true);
    assert.equal(await page.locator("#prompt").inputValue(), "Preserve draft");
    fail = false;
    await page.locator("#send").click();
    // F-58: the editable choice leaves the chat; the compact header records
    // the immutable mode without duplicating the conversation header.
    await toggle.waitFor({ state: "hidden" });
    assert.equal(
      await page.locator("#execution-mode-choice").isVisible(),
      false,
    );
    assert.equal(
      await page.locator("#header-execution-mode").innerText(),
      "Isolated conversation",
    );
    await page.locator("#header-execution-mode").click();
    assert.equal(await toggle.isVisible(), false, "started mode stays immutable");
    assert.equal(
      await page.locator("#execution-mode-label").innerText(),
      "Isolated conversation",
    );
    assert.equal(sent.at(-1).execution_mode, "scoped");
    assert.equal(
      await page
        .locator("#execution-mode-indicator")
        .getAttribute("data-isolated"),
      "true",
    );
    assert.equal(
      await page
        .locator("#execution-mode-indicator")
        .evaluate((el) => getComputedStyle(el).color),
      activeColor,
    );
    assert.equal(
      await page.locator("#execution-mode-indicator use").getAttribute("href"),
      await page.locator("#isolation-toggle use").getAttribute("href"),
    );
    await page.evaluate(() =>
      document.documentElement.style.setProperty(
        "--th-accent",
        "rgb(20, 90, 140)",
      ),
    );
    assert.equal(
      await page
        .locator("#execution-mode-indicator")
        .evaluate((el) => getComputedStyle(el).color),
      "rgb(20, 90, 140)",
    );
    assert.equal(
      await page
        .locator("#isolation-toggle")
        .evaluate((el) => getComputedStyle(el).backgroundColor),
      "rgb(20, 90, 140)",
    );
    await page.evaluate(() =>
      document.documentElement.style.removeProperty("--th-accent"),
    );
    await page.waitForFunction(() => !busy && !submitting);
    await page.locator("#prompt").fill("Follow up");
    await page.locator("#send").click();
    await page.waitForFunction(() => !busy && !submitting);
    assert.ok(sent.at(-1).parent_job_id);
    assert.equal(sent.at(-1).execution_mode, undefined);
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    assert.equal(await toggle.isVisible(), false);
    assert.equal(
      await page.locator("#header-execution-mode").innerText(),
      "Isolated conversation",
    );
    assert.equal(
      await page
        .locator("#execution-mode-indicator")
        .getAttribute("data-isolated"),
      "true",
    );
    await page.screenshot({ path: "/tmp/conversation-mode-started.png" });
    await page.locator("#new").click();
    assert.equal(await toggle.getAttribute("aria-checked"), "false");
    assert.equal(
      await page.locator("#execution-mode-indicator").isVisible(),
      false,
    );
    await page.screenshot({ path: "/tmp/conversation-mode-new.png" });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: "/tmp/conversation-mode-mobile.png" });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      true,
    );
    await page.locator("#model").selectOption("local-fixture", { force: true });
    await page.waitForFunction(
      () =>
        document.getElementById("execution-mode-label").textContent ===
        "Isolated conversation",
    );
    assert.equal(await toggle.isDisabled(), true);
    assert.match(
      await page.locator("#execution-mode-unavailable").innerText(),
      /requires isolation/,
    );
    await page
      .locator("#model")
      .selectOption("gemini-fixture", { force: true });
    await page.waitForFunction(
      () =>
        document.getElementById("execution-mode-label").textContent ===
        "Native conversation",
    );
    assert.equal(await toggle.isDisabled(), true);
    assert.deepEqual(errors, []);
    console.log(
      "PASS: native default, keyboard toggle, draft/reload/failure, locked mode, shared icon, continuation, reset, mobile",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
