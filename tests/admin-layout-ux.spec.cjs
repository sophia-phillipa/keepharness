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
      integrations: ["mcp:drive"],
    };
    state.settings.services.claude = {
      ...service(),
      added: true,
      models: ["sonnet"],
      integrations: ["mcp:linear"],
    };
    await page.goto("http://admin.test/");
    await page.locator("[data-panel=providers]").click();
    await page.getByRole("button", { name: "Edit Codex", exact: true }).click();
    const tabs = page.locator("#inspector-tabs");
    let release;
    releaseCatalog = new Promise((resolve) => (release = resolve));
    await tabs.getByText("Plugins", { exact: true }).click();
    await page
      .locator("#catalog-status")
      .filter({ hasText: "Searching" })
      .waitFor();
    assert(await page.locator("#catalog-refresh").isDisabled());
    await tabs.getByText("Model and hardware", { exact: true }).click();
    assert.equal(
      await page.locator("#projects .provider-connectors").count(),
      0,
    );
    await tabs.getByText("Plugins", { exact: true }).click();
    assert.equal(catalogCalls, 1);
    release();
    releaseCatalog = null;
    await page
      .getByRole("button", { name: "Install Sentry", exact: true })
      .waitFor();
    await tabs.getByText("Connectors", { exact: true }).click();
    const drive = page.getByRole("checkbox", { name: /Drive/ });
    await drive.uncheck();
    await page.locator("#wizard-back").click();
    assert(await page.locator("#provider-wizard").isVisible());
    await tabs.getByText("Plugins", { exact: true }).click();
    await tabs.getByText("Connectors", { exact: true }).click();
    assert(!(await drive.isChecked()));
    failSave = true;
    await page.click("#save");
    await page
      .locator("#feedback")
      .filter({ hasText: "Simulated failure" })
      .waitFor();
    await tabs.getByText("Connectors", { exact: true }).click();
    assert(!(await drive.isChecked()));
    failSave = false;
    await page.click("#save");
    await page.waitForFunction(() =>
      document.querySelector("#dirty").textContent.includes("saved"),
    );
    await page.reload();
    await page.getByRole("button", { name: "Edit Codex", exact: true }).click();
    await tabs.getByText("Plugins", { exact: true }).click();
    await tabs.getByText("Connectors", { exact: true }).click();
    assert(!(await drive.isChecked()));
    failCatalog = true;
    await page.click("#catalog-refresh");
    await page
      .locator("#catalog-status")
      .filter({ hasText: "temporarily unavailable" })
      .waitFor();
    assert.match(
      await page.locator("#catalog-status").innerText(),
      /Showing results from the last successful query/,
    );
    const catalogErrorText = await page.locator("#catalog-items").innerText();
    const errorLooksEmpty = /No options found/.test(catalogErrorText);
    await page.screenshot({
      path: "/tmp/tester-ux-catalog-error.png",
      fullPage: true,
    });
    await tabs.getByText("Plugins", { exact: true }).click();
    failCatalog = false;
    await page.click("#catalog-refresh");
    await page
      .getByRole("button", { name: "Install Sentry", exact: true })
      .waitFor();
    await page.locator("#integration-action").selectOption("plugin_install");
    const irrelevantPluginFields =
      (await page.locator("#integration-transport").isVisible()) ||
      (await page.locator("#integration-source").isVisible());
    await page.screenshot({
      path: "/tmp/tester-ux-plugin-form.png",
      fullPage: true,
    });
    await tabs.getByText("Connectors", { exact: true }).click();
    await page.locator("#integration-action").selectOption("connector_add");
    assert(await page.locator("#integration-source").isVisible());
    assert(await page.locator("#integration-transport").isVisible());
    await page.locator("#integration-transport").selectOption("stdio");
    assert.match(
      await page.locator("#integration-source").locator("..").innerText(),
      /command.*JSON/i,
    );
    assert.match(
      await page.locator("#integration-source").getAttribute("placeholder"),
      /program/,
    );
    await page.locator("#integration-action").selectOption("login");
    assert(!(await page.locator("#integration-source").isVisible()));
    await page.locator("#integration-action").selectOption("connector_remove");
    assert(!(await page.locator("#integration-transport").isVisible()));
    await tabs.getByText("Connectors", { exact: true }).click();
    await page.locator("#integration-action").selectOption("connector_add");
    assert(await page.locator("#integration-source").isVisible());

    await page.locator("#wizard-cancel").click();
    await page
      .getByRole("button", { name: "Edit Claude Code", exact: true })
      .click();
    await tabs.getByText("Plugins", { exact: true }).click();
    assert.equal(
      await page.locator("#integration-provider").inputValue(),
      "claude",
    );
    await tabs.getByText("Connectors", { exact: true }).click();
    assert(await page.getByRole("checkbox", { name: /Linear/ }).isChecked());
    assert.equal(
      await page.getByRole("checkbox", { name: /Drive/ }).count(),
      0,
    );
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
    );
    await page.screenshot({
      path: "/tmp/tester-ux-mobile.png",
      fullPage: true,
    });
    assert.deepEqual(errors, []);
    assert.equal(
      errorLooksEmpty,
      false,
      "UX-01: catalogue failure must not be presented as successful empty results",
    );
    assert.equal(
      irrelevantPluginFields,
      false,
      "UX-02: plugin install must not ask for MCP transport/source",
    );
    console.log(
      "PASS UX: P1 grouping/provider concepts; P2 slow and rapid tabs/back; P3 save-failure draft and persisted choices; P6 provider isolation; P7 mobile and recovery",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
