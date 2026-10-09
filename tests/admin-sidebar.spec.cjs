const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
    });
    const fs = require("node:fs/promises"),
      path = require("node:path");
    await page.route("http://admin.test/**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith("/api/")) return route.fallback();
      const file = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
      return route.fulfill({
        body: await fs.readFile(
          path.join(
            __dirname,
            file.startsWith("assets/") ? "../harness_ui" : "../control",
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
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    let failSave = false,
      failCatalog = false,
      releaseCatalog = null,
      catalogCalls = 0;
    const service = () => ({
      added: false,
      enabled: false,
      models: [],
      projects: ["sem-projeto"],
      permissions: {
        read: false,
        write: false,
        upload: false,
        shell: false,
        internet: false,
        hooks: false,
      },
      mode: "scoped",
      integrations: [],
    });
    const state = {
      settings: {
        services: { codex: service(), claude: service() },
        projects: [],
        logins: [],
        port: 8095,
        tailnet_port: 8095,
        uploads_enabled: false,
      },
      inventory: {
        platform: "Linux",
        services: [
          { id: "codex", name: "Codex CLI", found: true },
          { id: "claude", name: "Claude Code", found: true },
        ],
        projects: [{ name: "Demo", path: "/workspace/demo" }],
        network: { online: true },
      },
      authentication: {},
      models: {},
      integrations: {
        codex: [
          { id: "mcp:drive", name: "Drive", kind: "mcp" },
          { id: "plugin:github@openai", name: "github@openai", kind: "plugin" },
        ],
        claude: [{ id: "mcp:linear", name: "Linear", kind: "mcp" }],
      },
      operations: [],
      credentials: {},
      status: { running: false },
    };
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith("/check")) {
        state.authentication.claude = true;
        return route.fulfill({
          json: { authenticated: true, models: { sonnet: ["configured"] } },
        });
      }
      if (url.pathname.endsWith("/integration-catalog")) {
        catalogCalls++;
        if (releaseCatalog) await releaseCatalog;
        if (failCatalog)
          return route.fulfill({
            status: 503,
            json: { error: "Catalog temporarily unavailable" },
          });
      }
      if (url.pathname.endsWith("/settings")) {
        if (failSave)
          return route.fulfill({
            status: 400,
            json: { error: "Simulated failure" },
          });
        state.settings = route.request().postDataJSON();
      }
      const result = url.pathname.endsWith("/integration-catalog")
        ? {
            items: [
              {
                id: "plugin:sentry@official",
                name: "Sentry",
                kind: "plugin",
                status: "available",
              },
              {
                id: "plugin:github@openai",
                name: "GitHub",
                kind: "plugin",
                status: "installed",
              },
            ],
            warnings: [],
          }
        : url.pathname.endsWith("/state")
          ? state
          : url.pathname.endsWith("/scan")
            ? state.inventory
            : {};
      await route.fulfill({ json: result });
    });

    state.settings.services.codex = {
      ...service(),
      added: true,
      models: ["fixture"],
    };
    state.settings.services.claude = {
      ...service(),
      added: true,
      models: ["sonnet"],
    };
    const now = Date.now() / 1000;
    let ticks = 0;
    await page.route("**/api/dashboard*", (route) =>
      route.fulfill({
        json: {
          available: true,
          checked_at: now + ticks++,
          requests_per_second: 0.2,
          active: 1,
          queued: 0,
          input_tokens: 100,
          output_tokens: 20,
          measured_jobs: 1,
          latest_output_tokens_per_second: 2,
          hardware: {
            cpu_percent: 25,
            memory_used: 10,
            memory_total: 20,
            gpus: [],
          },
          recent: [],
        },
      }),
    );
    await page.goto("http://admin.test/");
    await page.locator("#dashboard-metrics .metric-card").first().waitFor();
    assert(await page.locator("#inspector-empty").isVisible());
    assert(!(await page.locator("#dashboard").isVisible()));
    assert(!(await page.locator("#execution-page").isVisible()));
    assert.equal(
      await page.locator("[aria-current=page]").getAttribute("data-panel"),
      "home",
    );
    await page.locator("[data-panel=providers]").click();
    await page.locator("#dashboard").waitFor();
    const cards = await page
      .locator("#configured-providers .configured-card")
      .evaluateAll((es) =>
        es.map((e) => {
          const r = e.getBoundingClientRect();
          return { x: r.x, y: r.y, bottom: r.bottom };
        }),
      );
    assert.equal(cards[0].y, cards[1].y);
    assert(cards[1].x > cards[0].x);
    await page.getByRole("button", { name: "Edit Codex", exact: true }).click();
    assert(
      await page
        .locator("#provider-dialog")
        .evaluate((e) => e.matches(":modal")),
    );
    assert.equal(new URL(page.url()).hash, "#providers");
    await page.keyboard.press("Tab");
    assert(
      await page
        .locator("#provider-dialog")
        .evaluate((e) => e.contains(document.activeElement)),
    );
    await page.waitForTimeout(200);
    assert(
      await page
        .locator("#provider-dialog button svg")
        .evaluateAll((es) =>
          es
            .filter((e) => e.getClientRects().length)
            .every((e) => e.getBBox().width > 0),
        ),
      "Modal action icons are rendered",
    );
    await page.screenshot({ path: "/tmp/keepharness-provider-modal.png" });
    const draftToggle = page.getByRole("checkbox", {
      name: /fixture/i,
    });
    await draftToggle.click();
    await page
      .locator("#inspector-tabs")
      .getByText("Connectors", { exact: true })
      .click();
    await page.evaluate(() => (location.hash = "home"));
    await page.locator("#inspector-empty").waitFor();
    const previous = ticks;
    await page.waitForFunction(() =>
      document
        .querySelector("#dashboard-metrics")
        .textContent.includes("Requests"),
    );
    await page.waitForTimeout(3200);
    assert(
      ticks > previous,
      "Home keeps refreshing while provider draft is preserved",
    );
    await page.locator("[data-panel=runs]").focus();
    await page.keyboard.press("Enter");
    await page.locator("#execution-page").waitFor();
    assert(!(await page.locator("#inspector-empty").isVisible()));
    await page.goBack();
    await page.locator("#inspector-empty").waitFor();
    await page.locator("[data-panel=providers]").click();
    await page.locator("#provider-dialog").waitFor();
    page.once("dialog", (d) => d.dismiss());
    await page.keyboard.press("Escape");
    assert(await page.locator("#provider-dialog").evaluate((e) => e.open));
    page.once("dialog", (d) => d.accept());
    await page.keyboard.press("Escape");
    await page.waitForFunction(
      () => !document.querySelector("#provider-dialog").open,
    );
    await page.waitForFunction(
      () => document.activeElement.getAttribute("aria-label") === "Edit Codex",
    );
    for (const theme of ["violet-bordeaux", "arizona"]) {
      await page.evaluate((theme) => HarnessTheme.apply(theme, false), theme);
      for (const width of [320, 390, 768, 1440]) {
        await page.setViewportSize({ width, height: 1000 });
        const connectionIcon = await page
          .locator("[data-panel=connection] svg")
          .boundingBox();
        assert(
          connectionIcon &&
            connectionIcon.width > 0 &&
            connectionIcon.height > 0,
          "Connection remains visible in the narrow sidebar",
        );
        for (const section of ["home", "providers", "runs", "connection"]) {
          await page.locator("[data-panel=" + section + "]").click();
          await page.waitForFunction(
            (section) =>
              document.querySelector("[aria-current=page]")?.dataset.panel ===
              section,
            section,
          );
          assert(
            await page.evaluate(
              () => document.documentElement.scrollWidth <= innerWidth,
            ),
            "overflow " + theme + " " + width + " " + section,
          );
          const bounds = await page.locator(".sidebar").boundingBox();
          assert.equal(bounds.x, 0);
          assert.equal(
            await page
              .locator("[aria-current=page]")
              .getAttribute("data-panel"),
            section,
          );
        }
      }
    }
    await page.locator("[data-panel=home]").click();
    await page.locator("#inspector-empty").waitFor();
    await page.screenshot({
      path: "/tmp/keepharness-panel-sidebar.png",
      fullPage: true,
    });
    await page.locator("[data-panel=providers]").click();
    await page.locator("#dashboard").waitFor();
    await page.screenshot({
      path: "/tmp/keepharness-panel-providers.png",
      fullPage: true,
    });
    await page.goto("http://admin.test/#runs");
    await page.locator("#execution-page").waitFor();
    const missing = await page
      .locator("button")
      .evaluateAll((es) =>
        es
          .filter((e) => !e.title || !e.querySelector("svg,.provider-mark"))
          .map((e) => ({ id: e.id, text: e.textContent, title: e.title })),
      );
    assert.deepEqual(
      missing,
      [],
      "Every panel button has an action icon and explanatory tooltip",
    );
    assert.deepEqual(errors, []);
    console.log(
      "PASS sidebar: default Home, separate views, keyboard, history, deep links, preserved draft, polling, responsive themes",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
