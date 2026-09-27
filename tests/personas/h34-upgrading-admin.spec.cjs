// H34 upgrading admin (P6): opens the panel on a persisted 0.5.0 state whose
// services predate the `mode` / `integrations` fields, then continues a legacy
// conversation that the server refuses with `execution_mode_unsupported`. The
// old state must render and save without breaking; the refusal must be guided.
// Mocked admin/harness (admin.test / harness.test); no real provider.
"use strict";
const assert = require("node:assert/strict");
const { mockAdmin, mockHarness, runPersona } = require("./_harness.cjs");

// A 0.5.0 service record: no `mode`, no `integrations`.
const legacyService = (models) => ({
  added: true,
  enabled: true,
  models,
  projects: ["sem-projeto"],
  permissions: {
    read: true,
    write: false,
    upload: true,
    shell: false,
    internet: false,
    hooks: false,
  },
});
const STATE = {
  settings: {
    services: {
      codex: legacyService(["fixture"]),
      claude: legacyService(["claude-sonnet-4-6"]),
    },
    projects: [],
    logins: [],
    port: 8095,
    tailnet_port: 8095,
    uploads_enabled: true,
  },
  inventory: {
    platform: "Linux",
    services: [
      { id: "codex", name: "Codex CLI", found: true },
      { id: "claude", name: "Claude Code", found: true },
    ],
    projects: [],
    network: { online: true },
  },
  authentication: { codex: true, claude: true },
  models: {
    codex: { fixture: ["low"] },
    claude: { "claude-sonnet-4-6": ["configured"] },
  },
  integrations: { codex: [], claude: [] },
  local_profiles: {},
  operations: [],
  credentials: {},
  status: { running: false, local_url: "http://127.0.0.1:8095/" },
};
const DASH = {
  json: {
    available: true,
    checked_at: 1,
    requests_per_second: 0,
    active: 0,
    queued: 0,
    input_tokens: null,
    output_tokens: null,
    measured_jobs: 0,
    latest_output_tokens_per_second: null,
    hardware: {
      cpu_percent: 0,
      memory_used: 100,
      memory_total: 1000,
      gpus: [],
    },
    recent: [],
  },
};

runPersona("H34", [
  {
    title: "H34-S1 a 0.5.0 state renders and saves without errors",
    async run(page) {
      const posts = [];
      await mockAdmin(page, STATE, {
        "GET /api/dashboard": DASH,
        "POST /api/settings": (route) => {
          posts.push(route.request().postDataJSON());
          return route.fulfill({ json: { saved: true } });
        },
      });
      await page.goto("http://admin.test/");
      await page.waitForLoadState("networkidle");
      await page.locator("[data-panel=providers]").click();

      // The legacy state renders both providers.
      const providers = await page.locator("#configured-providers").innerText();
      assert.match(providers, /Codex CLI/);
      assert.match(providers, /Claude Code/);

      // Editing and saving works and posts a well-formed settings object that
      // preserves the 0.5.0 models and permissions (the server normalizes the
      // missing `mode` / `integrations` on receipt).
      await page
        .locator("#configured-providers button", { hasText: /^Edit/ })
        .first()
        .click();
      await page.locator("#save").waitFor();
      await page.locator("#save").click();
      await page.locator("#dirty", { hasText: /Settings saved/ }).waitFor();
      assert.equal(posts.length, 1, "one settings POST");
      const saved = posts[0];
      assert(saved.services.codex, "codex service preserved");
      assert.deepEqual(saved.services.codex.models, ["fixture"], "models kept");
      assert.equal(
        saved.services.codex.permissions.read,
        true,
        "permissions kept",
      );
    },
  },
  {
    title:
      "H34-S2 continuing a legacy conversation refused with execution_mode_unsupported",
    async run(page) {
      const noConsoleErrors = (() => {
        page.removeAllListeners("console");
        const errors = [];
        page.on("console", (m) => {
          if (m.type() === "error" && !/Failed to load resource/.test(m.text()))
            errors.push(m.text());
        });
        return () => assert.deepEqual(errors, []);
      })();
      await mockHarness(page, {
        "GET /v1/models": {
          json: {
            uploads_enabled: false,
            providers: { claude: true },
            models: [
              {
                id: "claude-sonnet-4-6",
                backend: "claude",
                efforts: ["low"],
                permissions: { upload: false },
                execution_modes: ["native"],
              },
            ],
          },
        },
        "POST /v1/jobs": {
          status: 422,
          json: { code: "execution_mode_unsupported" },
        },
      });
      await page.goto("http://harness.test/");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
      await page.fill("#prompt", "continue the pre-0.6 conversation");
      await page.click("#send");
      await page.waitForFunction(() =>
        /Couldn't run/.test(document.querySelector("#status").textContent),
      );
      const status = await page.locator("#status").textContent();
      console.log("H34-S2 status:", status);
      // The draft is preserved so the user can retry after upgrading.
      assert.equal(
        await page.inputValue("#prompt"),
        "continue the pre-0.6 conversation",
      );
      // F-101 (F-07 UI side): a guided message instead of the raw code.
      assert.match(status, /start a new conversation/);
      assert.doesNotMatch(status, /execution_mode_unsupported/);
      noConsoleErrors();
    },
  },
]);
