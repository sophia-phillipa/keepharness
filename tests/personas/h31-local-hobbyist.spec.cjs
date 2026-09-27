// H31 local-model hobbyist (P6/P5): mistypes the llama.cpp context size in the
// admin profile editor, then tries to use a local model whose server is down.
// Bad numbers get a validation message (never a 500) and the offline run ends
// with a clear failure instead of hanging. Mocked admin/harness (admin.test /
// harness.test); no real provider.
"use strict";
const assert = require("node:assert/strict");
const { mockAdmin, mockHarness, runPersona } = require("./_harness.cjs");

const PERMISSIONS = {
  read: true,
  write: false,
  upload: false,
  shell: false,
  internet: false,
  hooks: false,
};
const PROFILE = {
  model_file: "/models/qwen.gguf",
  binary: "/usr/bin/llama-server",
  description: "Desk GPU",
  performance: { threads: "8", "n-gpu-layers": "35", "ctx-size": "32768" },
  permissions: { ...PERMISSIONS },
  capabilities: { tools: true },
  allowed_roots: [],
};
const AX = {
  settings: {
    services: {
      local: {
        added: true,
        enabled: true,
        models: ["qwen"],
        projects: ["sem-projeto"],
        permissions: { ...PERMISSIONS },
        mode: "native",
        integrations: [],
      },
    },
    projects: [],
    logins: [],
    port: 8095,
    tailnet_port: 8095,
    uploads_enabled: false,
  },
  inventory: {
    platform: "Linux",
    services: [
      {
        id: "local",
        name: "Local model",
        found: true,
        models: ["qwen"],
        runtimes: [],
      },
    ],
    projects: [],
    network: { online: true },
  },
  authentication: { local: true },
  models: { local: { qwen: ["low"] } },
  integrations: { local: [] },
  local_profiles: { "/models/qwen.gguf": PROFILE },
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

// Mirror the ctx-size guard in control/local_models.py:validate_profile so the
// mocked 400 carries the message a real server would return.
function ctxSizeError(value) {
  const n = Number(value);
  if (!Number.isInteger(n)) return "Invalid numeric value for ctx-size.";
  if (n < 0 || n > 10000000)
    return "Value out of the allowed range for ctx-size.";
  return null;
}

// runPersona fails on any console error, including Chromium's "Failed to load
// resource" line for the 400s this scenario provokes on purpose. Swap its
// listener for one that tolerates only those.
function tolerateHttpErrors(page) {
  page.removeAllListeners("console");
  const errors = [];
  page.on("console", (m) => {
    if (m.type() === "error" && !/Failed to load resource/.test(m.text()))
      errors.push(m.text());
  });
  return () => assert.deepEqual(errors, []);
}

async function openLocalEditor(page) {
  await page.goto("http://admin.test/");
  await page.waitForLoadState("networkidle");
  await page.locator("[data-panel=providers]").click();
  await page
    .locator("#configured-providers button", { hasText: /^Edit/ })
    .first()
    .click();
  await page.locator("#local-models").waitFor();
  await page.locator("#hardware-editor-details summary").click();
  await page.locator("#profile-ctx-size").waitFor();
}

runPersona("H31", [
  {
    title: "H31-S1 invalid ctx-size gets a validation message, never a 500",
    async run(page) {
      const noConsoleErrors = tolerateHttpErrors(page);
      const posts = [];
      const calls = await mockAdmin(page, AX, {
        "GET /api/dashboard": DASH,
        "POST /api/local-profile": (route) => {
          const body = route.request().postDataJSON();
          posts.push(body);
          const message = ctxSizeError(body.performance["ctx-size"]);
          return message
            ? route.fulfill({ status: 400, json: { error: message } })
            : route.fulfill({ json: { ...PROFILE, ...body } });
        },
      });
      await openLocalEditor(page);

      for (const [value, expected] of [
        ["abc", "Invalid numeric value for ctx-size."],
        ["-1", "Value out of the allowed range for ctx-size."],
        ["999999999", "Value out of the allowed range for ctx-size."],
      ]) {
        await page.fill("#profile-ctx-size", value);
        // Editing renames the button ("Save changes to this profile"), so drive
        // it by its stable id.
        await page.locator("#profile-save").click();
        await page.locator("#feedback", { hasText: expected }).waitFor();
        assert.equal(
          posts.at(-1).performance["ctx-size"],
          value,
          "sent " + value,
        );
      }
      // The server was reached each time and never returned a 500-class error.
      assert.equal(posts.length, 3);
      assert(
        calls.every(
          (c) => c.path !== "/api/local-profile" || c.method === "POST",
        ),
      );
      noConsoleErrors();
    },
  },
  {
    title: "H31-S2 an offline local server yields guided errors, not a hang",
    async run(page) {
      // Admin: the readiness check reports the local server as unreachable.
      await mockAdmin(page, AX, {
        "GET /api/dashboard": DASH,
        "POST /api/check": { json: { authenticated: false, models: {} } },
      });
      await page.goto("http://admin.test/");
      await page.waitForLoadState("networkidle");
      await page.locator("[data-panel=providers]").click();
      await page
        .locator("#configured-providers button", { hasText: /^Edit/ })
        .first()
        .click();
      await page.locator("#local-models").waitFor();
      await page
        .getByRole("button", { name: "Check models", exact: true })
        .click();
      await page
        .locator("#feedback", { hasText: /The local server did not respond/ })
        .waitFor();

      // Harness: the run against the down local model ends in a clear failure.
      const s = await mockHarness(page, {
        "GET /v1/models": {
          json: {
            uploads_enabled: false,
            providers: { local: true },
            models: [
              {
                id: "qwen",
                backend: "local",
                efforts: ["low"],
                permissions: { upload: false },
                execution_modes: ["native"],
              },
            ],
          },
        },
        "POST /v1/jobs": (route) => {
          s.posts.push(route.request().postDataJSON());
          s.turns.push({
            id: "job-1",
            project: "sem-projeto",
            state: "failed",
            request: s.posts.at(-1),
            result: {
              error:
                "codex_execution_failed: error sending request to the local server (connection refused)",
              model: "qwen",
            },
          });
          return route.fulfill({ json: { job_id: "job-1" } });
        },
      });
      await page.goto("http://harness.test/");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
      await page.fill("#prompt", "Summarize the changelog");
      await page.click("#send");
      const answer = page.locator("#messages article.assistant").last();
      await answer.locator(".run-highlight", { hasText: /Failed/ }).waitFor();
      const bubble = await answer.locator(".chat-bubble").innerText();
      assert.match(bubble, /The run did not finish:/, "guided failure text");
      assert.match(await page.locator("#status").textContent(), /Failed run/);
      // No perpetual "Working…": the run reached a terminal state and the
      // composer is no longer busy (Cancel hidden, prompt editable, and Send
      // re-enables as soon as text is typed again).
      assert(await page.locator("#cancel").isHidden());
      assert(await page.locator("#prompt").isEnabled());
      await page.fill("#prompt", "retry");
      assert(await page.locator("#send").isEnabled());
    },
  },
]);
