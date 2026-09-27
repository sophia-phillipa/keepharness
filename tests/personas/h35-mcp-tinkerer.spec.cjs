// H35 MCP tinkerer (P6/P7): registers connectors from the admin panel with
// malformed stdio commands and a non-HTTPS URL, then watches an operation fail.
// Bad command input is rejected client-side with no request; a failed operation
// is shown with a Retry control. Mocked admin (admin.test); no real provider.
"use strict";
const assert = require("node:assert/strict");
const { mockAdmin, runPersona } = require("./_harness.cjs");

const svc = (models) => ({
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
  mode: "native",
  integrations: [],
});
const AX = {
  settings: {
    services: { codex: svc(["fixture"]), claude: svc(["claude-sonnet-4-6"]) },
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

function tolerateHttpErrors(page) {
  page.removeAllListeners("console");
  const errors = [];
  page.on("console", (m) => {
    if (m.type() === "error" && !/Failed to load resource/.test(m.text()))
      errors.push(m.text());
  });
  return () => assert.deepEqual(errors, []);
}

async function openConnectors(page) {
  await page.goto("http://admin.test/");
  await page.waitForLoadState("networkidle");
  await page.locator("[data-panel=providers]").click();
  await page
    .locator("#configured-providers button", { hasText: /^Edit/ })
    .first()
    .click();
  await page
    .locator("#inspector-tabs")
    .getByText("Connectors", { exact: true })
    .click();
  await page.locator("#integration-run").waitFor();
}

runPersona("H35", [
  {
    title: "H35-S1 malformed connector input is rejected before any request",
    async run(page) {
      const done = tolerateHttpErrors(page);
      const posts = [];
      await mockAdmin(page, AX, {
        "GET /api/dashboard": DASH,
        "POST /api/integration": (route) => {
          posts.push(route.request().postDataJSON());
          return route.fulfill({
            status: 400,
            json: {
              error:
                "Use the MCP server's HTTPS URL, with no credentials in the URL.",
            },
          });
        },
      });
      await openConnectors(page);

      await page.locator("#integration-action").selectOption("connector_add");
      await page.locator("#integration-transport").selectOption("stdio");
      await page.fill("#integration-name", "drive");

      // Not JSON at all: rejected with the JSON-list hint, no request sent.
      await page.fill("#integration-source", "node server.js");
      await page.locator("#integration-run").click();
      await page
        .locator("#feedback", {
          hasText: /Enter the command as a valid JSON list/,
        })
        .waitFor();
      assert.equal(posts.length, 0, "malformed JSON is not sent");

      // Valid JSON but not a list of strings: rejected, still no request.
      await page.fill("#integration-source", '["node", 3]');
      await page.locator("#integration-run").click();
      await page
        .locator("#feedback", {
          hasText:
            /Enter the command as a JSON list of strings, starting with the program to run/,
        })
        .waitFor();
      assert.equal(posts.length, 0, "non-string command is not sent");

      // A non-HTTPS remote URL is not caught client-side, so it reaches the
      // server, which refuses it with a guided HTTPS message.
      await page.locator("#integration-transport").selectOption("http");
      await page.fill("#integration-source", "http://mcp.example/mcp");
      await page.locator("#integration-run").click();
      await page
        .getByText(
          "Use the MCP server's HTTPS URL, with no credentials in the URL.",
        )
        .first()
        .waitFor();
      assert.equal(posts.length, 1, "only the HTTPS check reaches the server");
      assert.equal(posts[0].transport, "http");

      done();
    },
  },
  {
    title: "H35-S2 a failed connector operation is shown with a Retry action",
    async run(page) {
      const done = tolerateHttpErrors(page);
      const failed = {
        id: "op123456",
        state: "failed",
        kind: "integration",
        provider: "codex",
        output:
          "codex mcp add drive -- npx server\nError: connector handshake failed",
      };
      await mockAdmin(page, AX, {
        "GET /api/dashboard": DASH,
        // The launch returns a running job; the next state poll shows it failed.
        "POST /api/integration": { json: { id: failed.id, state: "running" } },
        "GET /api/state": (route) =>
          route.fulfill({ json: { ...AX, operations: [failed] } }),
      });
      await openConnectors(page);
      await page.locator("#integration-action").selectOption("connector_add");
      await page.locator("#integration-transport").selectOption("stdio");
      await page.fill("#integration-name", "drive");
      await page.fill("#integration-source", '["npx", "server"]');
      await page.locator("#integration-run").click();

      // The operations panel surfaces the failure and offers Retry.
      await page.locator("#operations", { hasText: /Failed/ }).waitFor();
      await page.locator("#operation-retry:not([hidden])").waitFor();
      assert.match(
        await page.locator("#operations").innerText(),
        /connector handshake failed/,
        "the failure output is shown",
      );
      done();
    },
  },
]);
