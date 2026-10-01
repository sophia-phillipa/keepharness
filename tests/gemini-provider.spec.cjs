const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises"),
  path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
        viewport: { width: 1280, height: 900 },
      }),
      errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const spec = {
      added: false,
      enabled: false,
      models: [],
      projects: ["sem-projeto"],
      permissions: {},
      mode: "native",
      integrations: [],
    };
    const state = {
      settings: {
        services: { gemini: spec },
        projects: [],
        logins: [],
        port: 8095,
        tailnet_port: 8095,
      },
      inventory: {
        services: [
          {
            id: "gemini",
            name: "Gemini CLI",
            found: true,
            credential_present: false,
          },
        ],
        projects: [],
        network: {},
      },
      authentication: { gemini: false },
      models: {},
      integrations: { gemini: [] },
      operations: [],
      credentials: {},
      status: { running: false },
    };
    let checked = false,
      logins = 0;
    await page.route("http://gemini-admin.test/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      if (p.startsWith("/api/")) {
        let data = {};
        if (p === "/api/state") data = state;
        if (p === "/api/scan") data = state.inventory;
        if (p === "/api/check") {
          data = checked
            ? {
                authenticated: true,
                models: { "auto-gemini-3": ["configured"] },
              }
            : {
                authenticated: false,
                models: {},
                error: "gemini_login_required",
              };
          checked = true;
        }
        if (p === "/api/provider-login") {
          logins++;
          data = { id: "login-" + logins, state: "running" };
          state.operations = [
            {
              id: "old-failed",
              state: "failed",
              output: "OLD-OPERATION-SHOULD-NOT-APPEAR",
            },
            {
              id: data.id,
              state: logins === 1 ? "failed" : "completed",
              output:
                logins === 1
                  ? "Traceback (most recent call last): private stack"
                  : "Login complete.",
            },
          ];
        }
        if (p === "/api/settings") {
          state.settings = route.request().postDataJSON();
          data = { saved: true };
        }
        return route.fulfill({ json: data });
      }
      const f = p === "/" ? "index.html" : p.slice(1);
      return route.fulfill({
        body: await fs.readFile(
          path.join(
            __dirname,
            f.startsWith("assets/") ? "../tail_ui" : "../control",
            f,
          ),
        ),
        contentType: f.endsWith(".js")
          ? "text/javascript"
          : f.endsWith(".css")
            ? "text/css"
            : f.endsWith(".svg")
              ? "image/svg+xml"
              : "text/html",
      });
    });
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.13"));
    await page.goto("http://gemini-admin.test/#providers");
    assert.equal(
      await page.locator("[data-configured-provider=gemini]").count(),
      0,
    );
    await page.click("#add-provider");
    assert.equal(
      await page.locator("#provider-options [data-provider=gemini]").count(),
      0,
    );
    state.settings.services.gemini = {
      ...spec,
      added: true,
      models: ["auto-gemini-3"],
    };
    await page.reload();
    assert.equal(
      await page.locator("[data-configured-provider=gemini]").count(),
      0,
    );
    assert.equal(
      await page.locator("#provider-cards [data-provider=gemini]").count(),
      0,
    );
    assert.deepEqual(errors, []);
    const chat = await browser.newPage();
    chat.on("pageerror", (e) => errors.push(e.message));
    await chat.route("http://gemini-chat.test/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      if (p.startsWith("/v1/")) {
        let data = {};
        if (p === "/v1/projects") data = { projects: ["sem-projeto"] };
        if (p === "/v1/models")
          data = {
            models: [
              {
                id: "auto-gemini-3",
                backend: "gemini",
                efforts: ["configured"],
                permissions: { upload: true },
              },
            ],
            providers: { gemini: true },
            uploads_enabled: true,
          };
        if (p === "/v1/conversations") data = { conversations: [] };
        if (p === "/v1/version") data = { version: "test", build: "one" };
        return route.fulfill({ json: data });
      }
      const f = p === "/" ? "index.html" : p.slice(1);
      return route.fulfill({
        body: await fs.readFile(
          path.join(
            __dirname,
            f.startsWith("assets/") ? "../tail_ui" : "../agent_service",
            f,
          ),
        ),
        contentType: f.endsWith(".js")
          ? "text/javascript"
          : f.endsWith(".css")
            ? "text/css"
            : f.endsWith(".svg")
              ? "image/svg+xml"
              : "text/html",
      });
    });
    await chat.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.13"));
    await chat.goto("http://gemini-chat.test");
    await chat.locator("#startup-gate").waitFor({ state: "hidden" });
    assert.equal(await chat.locator("#model-trigger-icon").innerText(), "✦");
    assert.match(await chat.locator("#model-note").innerText(), /Gemini CLI/);
    assert(!/ChatGPT/.test(await chat.locator("#model-note").innerText()));
    assert.deepEqual(errors, []);
    console.log(
      "PASS: partial Gemini hidden from discovery and configured cards; existing chat identity preserved",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
