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
    // D11: the owner turned Full access on; turned off, the menu hides it.
    let fullAccess = true;
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
            p.startsWith("/assets/") ? "harness_ui" : "agent_service",
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
                    id: "local-fixture",
                    name: "Fixture",
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
                providers: { local: true },
                uploads_enabled: false,
                full_access: fullAccess,
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

    await page.addInitScript(() =>
      localStorage.setItem("keepharness-tour-seen", "0.16.0"),
    );

    await page.goto("http://panel.test/");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    // Empty conversation: one isolation choice plus the access notice.
    assert.equal(await notice.isVisible(), true);
    assert.equal(await toggle.isVisible(), true);
    assert.equal(await isolation.innerText(), "Isolated conversation");
    assert.equal(await access.innerText(), "Access: Ask for approval");
    assert.equal(await toggle.isDisabled(), true);
    assert.equal(await isolation.innerText(), "Isolated conversation");
    await chooseAccess("full");
    assert.equal(await access.innerText(), "Access: Full access");

    // After the first message Mock 4 keeps fixed mode and access chips in the header.
    await page.locator("#prompt").fill("First message");
    await page.locator("#send").click();
    await idle();
    assert.equal(sent.at(-1).execution_mode, "scoped");
    assert.equal(sent.at(-1).access_mode, "full");
    assert.equal(
      await notice.isVisible(),
      true,
      "Local isolation requirement remains visible",
    );
    assert.equal(
      await page.locator("#header-execution-mode").isVisible(),
      true,
    );
    assert.equal(
      await page.locator("#header-execution-mode").innerText(),
      "Isolated conversation",
    );
    assert.equal(
      await page.locator("#header-access").innerText(),
      "Full access",
    );
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
    assert.equal(await page.locator("#header-access").innerText(), "Read only");
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
    assert.equal(
      await page.locator("#execution-mode-unavailable").isVisible(),
      true,
    );
    assert.match(
      await page.locator("#execution-mode-unavailable").innerText(),
      /Choose a different model or start a new conversation\./,
    );
    assert.equal(await page.locator("#send").isDisabled(), true);
    await page.locator("#model").selectOption("local-fixture", { force: true });
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
    assert.equal(
      await page.locator("#header-execution-mode").isVisible(),
      true,
    );
    assert.equal(
      await page.locator("#header-execution-mode").innerText(),
      "Isolated conversation",
    );
    assert.equal(await page.locator("#header-access").innerText(), "Read only");
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
    assert.equal(await isolation.innerText(), "Isolated conversation");

    // Nor does a reload of an empty conversation carry "Full access" over.
    await chooseAccess("full");
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    assert.equal(await page.locator("#access-mode").inputValue(), "ask");
    assert.equal(await access.innerText(), "Access: Ask for approval");

    // The menu copy describes what each mode really does (F-110).
    await page.click("#access-trigger");
    const copy = await page
      .locator("#access-menu [data-access] small:not(.access-native-mode)")
      .allInnerTexts();
    assert.deepEqual(copy, [
      "Codex asks when an action needs to leave its read-only sandbox; Claude Code uses its default permission mode. Native connectors and plugins follow the CLI configuration. DeepSeek asks before edits and connector calls; read-only commands can read any file your account can.",
      "Codex uses the sandbox shown above, with project-bounded writes when allowed; Claude Code accepts edits with its native permission rules. Native connectors and plugins follow the CLI configuration. DeepSeek asks before connector calls and actions beyond its workspace sandbox. Local models stay in their sandbox.",
      "Available once turned on in the admin. Uses the native settings shown above, within the provider's configured grants. Codex and DeepSeek can run without a sandbox; Claude Code can bypass permission prompts. Local models keep the permissions set in the admin.",
      "Codex uses a read-only sandbox with no approval escalation; Claude Code uses plan mode. Their connectors, plugins and tool permissions follow the native CLI configuration. Other providers keep their read-only restrictions.",
    ]);
    await page.keyboard.press("Escape");

    // D11: until the owner turns Full access on, the menu does not offer it, the keyboard
    // skips it, and a restored "full" (a conversation last run with it) falls back to Ask.
    fullAccess = false;
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.click("#access-trigger");
    assert.equal(
      await page.locator('#access-menu [data-access="full"]').isVisible(),
      false,
    );
    const reachable = [];
    for (let step = 0; step < 4; step++) {
      await page.keyboard.press("ArrowDown");
      reachable.push(
        await page.evaluate(() => document.activeElement.dataset.access),
      );
    }
    assert.equal(reachable.includes("full"), false, reachable.join());
    await page.keyboard.press("Escape");
    await page.evaluate(() => {
      document.getElementById("access-mode").value = "full";
      syncAccessMode();
    });
    assert.equal(await page.locator("#access-mode").inputValue(), "ask");
    assert.equal(await access.innerText(), "Access: Ask for approval");
    assert.deepEqual(errors, []);
    console.log(
      "PASS: session notices, fixed isolation after first message, per-conversation access, new conversation starts in Ask, Full access hidden until enabled",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
