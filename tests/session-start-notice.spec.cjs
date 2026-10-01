// F-58: the conversation's isolation and access mode are stated in a notice at
// the top of the chat; isolation is chosen only before the first message, and
// every new conversation starts in "Ask for approval".
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
      sent = [],
      turns = [];
    page.setDefaultTimeout(5000);
    page.on("pageerror", (e) => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem("activity-open", "0"));
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
      const conversations = turns.length
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
        : [];
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
              ? { conversations }
              : p.startsWith("/v1/conversations/")
                ? { execution_mode: turns[0]?.request.execution_mode, turns }
                : p.startsWith("/v1/jobs/")
                  ? turns.find((t) => t.id === p.split("/")[3]) || {}
                  : p === "/v1/version"
                    ? { version: "fixture", build: "fixture" }
                    : {};
      return route.fulfill({ json: data });
    });
    const notice = page.locator("#execution-mode-choice"),
      isolation = page.locator("#execution-mode-label"),
      access = page.locator("#access-mode-notice"),
      toggle = page.getByRole("switch", { name: "Isolated conversation" });
    const chooseAccess = async (mode) => {
      await page.click("#access-trigger");
      await page.locator(`#access-menu [data-access="${mode}"]`).click();
    };
    const idle = () => page.waitForFunction(() => !busy && !submitting);

    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.11.0"));

    await page.goto("http://panel.test/");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    // Empty conversation: one isolation choice plus the access notice.
    assert.equal(await notice.isVisible(), true);
    assert.equal(await toggle.isVisible(), true);
    assert.equal(await isolation.innerText(), "Native conversation");
    assert.equal(await access.innerText(), "Access: Ask for approval");
    await toggle.click();
    assert.equal(await isolation.innerText(), "Isolated conversation");
    await chooseAccess("full");
    assert.equal(await access.innerText(), "Access: Full access");

    // After the first message Mock 4 keeps fixed mode and access chips in the header.
    await page.locator("#prompt").fill("First message");
    await page.locator("#send").click();
    await idle();
    assert.equal(sent.at(-1).execution_mode, "scoped");
    assert.equal(sent.at(-1).access_mode, "full");
    assert.equal(await notice.isVisible(), false);
    assert.equal(await page.locator("#header-execution-mode").isVisible(), true);
    assert.equal(await page.locator("#header-execution-mode").innerText(), "Isolated conversation");
    assert.equal(await page.locator("#header-access").innerText(), "full");
    assert.equal(
      await toggle.isVisible(),
      false,
      "no isolation toggle in chat",
    );
    assert.equal(await page.getByRole("switch").count(), 0);
    assert.equal(await isolation.innerText(), "Isolated conversation");
    assert.equal(await access.innerText(), "Access: Full access");

    // The access mode can still change for this conversation; the notice follows.
    await chooseAccess("read_only");
    assert.equal(await access.innerText(), "Access: Read only");
    assert.equal(await page.locator("#header-access").innerText(), "read_only");
    await page.locator("#prompt").fill("Follow up");
    await page.locator("#send").click();
    await idle();
    assert.equal(sent.at(-1).access_mode, "read_only");
    assert.equal(sent.at(-1).execution_mode, undefined);

    // F-94: a model without this conversation's isolation offers a way out.
    await page
      .locator("#model")
      .selectOption("gemini-fixture", { force: true });
    await page.locator("#prompt").fill("Blocked draft");
    assert.equal(await page.locator("#execution-mode-unavailable").isVisible(), true);
    assert.match(
      await page.locator("#execution-mode-unavailable").innerText(),
      /Choose a different model or start a new conversation\./,
    );
    assert.equal(await page.locator("#send").isDisabled(), true);
    await page
      .locator("#model")
      .selectOption("claude-sonnet-4-6", { force: true });
    assert.equal(await page.locator("#send").isEnabled(), true);
    await page.locator("#prompt").fill("");

    // Reloading the conversation keeps its header chips, without a toggle.
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.waitForFunction(
      () =>
        document.getElementById("execution-mode-label").textContent ===
        "Isolated conversation",
    );
    assert.equal(await toggle.isVisible(), false);
    assert.equal(await page.locator("#header-execution-mode").isVisible(), true);
    assert.equal(await page.locator("#header-execution-mode").innerText(), "Isolated conversation");
    assert.equal(await page.locator("#header-access").innerText(), "read_only");
    assert.equal(await access.innerText(), "Access: Read only");

    // A new conversation never inherits the previous access mode.
    await page.locator("#new").click();
    assert.equal(await page.locator("#access-mode").inputValue(), "ask");
    assert.equal(
      await page.locator("#access-label").innerText(),
      "Ask for approval",
    );
    assert.equal(await access.innerText(), "Access: Ask for approval");
    assert.equal(await toggle.isVisible(), true);
    assert.equal(await isolation.innerText(), "Native conversation");

    // Nor does a reload of an empty conversation carry "Full access" over.
    await chooseAccess("full");
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    assert.equal(await page.locator("#access-mode").inputValue(), "ask");
    assert.equal(await access.innerText(), "Access: Ask for approval");

    // The menu copy describes what each mode really does (F-110).
    await page.click("#access-trigger");
    const copy = await page
      .locator("#access-menu [data-access] small")
      .allInnerTexts();
    assert.deepEqual(copy, [
      "Asks before every file change or command. Codex may still run commands that cannot change files.",
      "Edits and runs commands inside the project without asking; asks only to go beyond it.",
      "Runs without asking, within the permissions set by the administrator.",
      "Reads and searches only. Writing, commands and tests are off.",
    ]);
    await page.keyboard.press("Escape");
    assert.deepEqual(errors, []);
    console.log(
      "PASS: session notices, fixed isolation after first message, per-conversation access, new conversation starts in Ask",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
