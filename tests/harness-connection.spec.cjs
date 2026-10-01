// Feature-only browser regression; serves the workspace assets without a model/server.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
        viewport: { width: 1280, height: 900 },
      }),
      errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    let mode = "offline",
      requests = 0,
      submissions = 0,
      extraModel = false;
    const projects = ["sem-projeto"];
    await page.route("http://panel.test/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      if (p.startsWith("/v1/")) {
        requests++;
        if (p === "/v1/jobs" && route.request().method() === "POST")
          submissions++;
        if (mode === "offline") return route.abort("failed");
        if (mode === "limited")
          return route.fulfill({
            status: 429,
            headers: { "Retry-After": "10" },
            json: { code: "rate_limit" },
          });
        if (mode === "auth")
          return route.fulfill({
            status: 401,
            json: { code: "authentication_required" },
          });
        if (mode === "partial" && p === "/v1/conversations")
          return route.fulfill({ status: 503, json: { code: "unavailable" } });
        let data = {};
        if (p === "/v1/projects") {
          if (route.request().method() === "POST") {
            const payload = route.request().postDataJSON();
            assert.deepEqual(payload.paths, ["/workspace/demo"]);
            projects.push("demo");
            return route.fulfill({ status: 201, json: { project_id: "demo" } });
          }
          data = { projects, details: { demo: { label: "Demo" } } };
        }
        if (p === "/v1/project-directories")
          data = {
            roots: [{ id: "home", label: "Test folders" }],
            root_id: "home",
            path: "",
            absolute_path: "/workspace/demo",
            entries: [],
            limited: false,
          };
        if (p === "/v1/models")
          data = {
            models:
              mode === "empty"
                ? []
                : [
                    { id: "fixture-local", backend: "local", efforts: ["low"] },
                    ...(extraModel
                      ? [
                          {
                            id: "new-provider",
                            backend: "deepseek",
                            efforts: ["configured"],
                          },
                        ]
                      : []),
                  ],
            providers: { local: true },
          };
        if (p === "/v1/conversations") data = { conversations: [] };
        if (p === "/v1/version")
          data = { version: "test", build: "connection-test" };
        if (p === "/v1/catalog")
          data = { agents: [], skills: [], warnings: [] };
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
    const gate = page.locator("#startup-gate");
    async function blocked() {
      await gate.waitFor({ state: "visible", timeout: 25000 });
      assert(await page.locator("main").evaluate((el) => el.inert));
      assert.equal(
        await page.locator(".composer-menu:popover-open").count(),
        0,
      );
      await page.keyboard.press("Escape");
      await page.keyboard.press("Control+/");
      await page.keyboard.press("Tab");
      assert(await gate.isVisible());
      assert(
        !(await page.evaluate(
          () =>
            !!document.activeElement.closest("main, #sidebar, #activity-panel"),
        )),
      );
    }
    async function ready() {
      await gate.waitFor({ state: "hidden", timeout: 20000 });
      assert(!(await page.locator("main").evaluate((el) => el.inert)));
    }
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.11.1"));
    await page.goto("http://panel.test");
    await blocked();
    const before = requests;
    mode = "partial";
    await page.waitForTimeout(6500);
    await blocked();
    assert(requests > before);
    // Optional browser persistence must never keep a healthy server behind the gate.
    await page.evaluate(() => {
      window.originalSetItem = Storage.prototype.setItem;
      Storage.prototype.setItem = function (key, value) {
        if (key === "conversation-activity")
          throw new DOMException("Storage full", "QuotaExceededError");
        return window.originalSetItem.call(this, key, value);
      };
    });
    mode = "ready";
    await ready();
    await page.evaluate(() => {
      Storage.prototype.setItem = window.originalSetItem;
    });
    await page.fill("#prompt", "Preserved draft");
    // Background tabs must not spend the shared identity's polling budget.
    await page.evaluate(() =>
      Object.defineProperty(document, "hidden", {
        configurable: true,
        get: () => true,
      }),
    );
    await page.waitForTimeout(500);
    const quietRequests = requests;
    await page.waitForTimeout(11000);
    assert.equal(requests, quietRequests);
    await page.evaluate(() => delete document.hidden);
    mode = "limited";
    const beforeLimit = requests;
    await page.waitForFunction(
      () =>
        document
          .querySelector("#status")
          .textContent.includes("Too many requests"),
      {},
      { timeout: 25000 },
    );
    assert(
      await gate.isHidden(),
      "429 is throttling, not a disconnected server",
    );
    assert.equal(await page.locator("#prompt").inputValue(), "Preserved draft");
    mode = "ready";
    await page.waitForTimeout(11000);
    assert(requests > beforeLimit);
    extraModel = true;
    await page.waitForFunction(
      () =>
        [...document.querySelector("#model").options].some(
          (o) => o.value === "new-provider",
        ),
      {},
      { timeout: 25000 },
    );
    assert.equal(await page.locator("#model").inputValue(), "fixture-local");
    if (!(await page.locator("#project-tree").evaluate((el) => el.open)))
      await page.locator("#project-tree > summary").click();
    await page.click("#add-project");
    await page.fill("#project-name", "Demo");
    await page
      .locator("#project-directory-list .project-file-row")
      .filter({ hasText: "Test folders" })
      .click();
    await page.click("#project-directory-add-current");
    await page.click("#project-create");
    await page.locator("#project-dialog").waitFor({ state: "hidden" });
    assert.equal(await page.locator("#project").inputValue(), "demo");
    assert.equal(await page.locator("#prompt").inputValue(), "Preserved draft");
    // A previously opened modal must not escape the disconnected screen.
    await page.evaluate(() =>
      document.querySelector("#settings-dialog").showModal(),
    );
    mode = "offline";
    await blocked();
    assert(!(await page.locator("#settings-dialog").evaluate((el) => el.open)));
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      );
      for (const theme of ["violet-bordeaux", "amethyst"]) {
        await page.evaluate((t) => TailTheme.apply(t, false), theme);
        await page.screenshot({
          path: "/tmp/tail-harness-connection-" + width + "-" + theme + ".png",
          fullPage: true,
        });
      }
    }
    await page.screenshot({
      path: "/tmp/tail-harness-connection-wait.png",
      fullPage: true,
    });
    mode = "ready";
    await ready();
    assert.equal(await page.locator("#prompt").inputValue(), "Preserved draft");
    assert.equal(submissions, 0);
    if (await page.locator("#th-toast").isVisible())
      await page.locator("#th-toast button").click();
    if (await page.locator("#activity-panel").isVisible())
      await page
        .locator(
          "#files-toggle[aria-expanded=true],#activity-toggle[aria-expanded=true]",
        )
        .click();
    await page.click("#model-trigger");
    await page.locator("#model-menu").waitFor({ state: "visible" });
    mode = "auth";
    await blocked();
    await page.locator("#vpn-login-token").fill("test-key");
    assert.equal(
      await page.locator("#vpn-login-token").inputValue(),
      "test-key",
    );
    // A configured server without models is connected, and keeps setup reachable.
    mode = "empty";
    await ready();
    assert(await page.locator("#send").isDisabled());
    assert(await page.locator("#model-availability").isVisible());
    assert.deepEqual(errors, []);
    console.log(
      "PASS: initial outage, partial initialization, automatic recovery, outage with open modal, keyboard blocking, mobile layout, draft preservation, authentication and empty models.",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
