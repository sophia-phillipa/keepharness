// H40 chaos monkey (P6): 300 seeded clicks and keys on the harness (Mx) and on
// the admin panel (Ax) must never raise a pageerror or console.error.
// Seed: GAUNTLET_ROUND (default 1). A failure reports the last actions, so the
// same seed replays the same sequence against the same DOM.
"use strict";
const assert = require("node:assert/strict");
const { mockHarness, mockAdmin, runPersona } = require("./_harness.cjs");

const SEED = Number(process.env.GAUNTLET_ROUND || 1);
const STEPS = Number(process.env.CHAOS_STEPS || 300);

// Mx fixture ("claude-fx-5" stands for fx-claude: HarnessUI.selectableModel hides other Claude ids).
const MX = {
  "GET /v1/models": {
    json: {
      models: [
        {
          id: "claude-fx-5",
          backend: "claude",
          efforts: ["low", "high"],
          permissions: { upload: true },
          execution_modes: ["native", "scoped"],
        },
        {
          id: "fx-codex",
          backend: "codex",
          efforts: ["low", "medium"],
          permissions: { upload: true },
          execution_modes: ["native", "scoped"],
        },
      ],
      providers: { claude: true, codex: true },
      uploads_enabled: true,
    },
  },
  "GET /v1/projects": {
    json: {
      projects: ["sem-projeto", "demo"],
      details: { demo: { label: "Demo" } },
    },
  },
  "GET /v1/conversations": {
    json: {
      conversations: ["Alpha", "Beta", "Gamma"].map((t, i) => ({
        id: "job-" + (i + 1),
        title: t,
        project: i ? "demo" : "sem-projeto",
        state: "completed",
        execution: { backend: "codex", model: "fx-codex" },
      })),
    },
  },
};

// Ax fixture: a trimmed admin-gauntlet state.
const PERMISSIONS = {
  read: true,
  write: false,
  upload: true,
  shell: false,
  internet: false,
  hooks: false,
};
const service = (models) => ({
  added: true,
  enabled: true,
  models,
  projects: ["sem-projeto"],
  permissions: { ...PERMISSIONS },
  mode: "native",
  integrations: [],
});
const AX = {
  settings: {
    services: {
      codex: service(["fixture"]),
      claude: service(["claude-fx-5"]),
      local: service(["qwen"]),
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
  authentication: { codex: true, claude: true, local: true },
  models: {
    codex: { fixture: ["low"] },
    claude: { "claude-fx-5": ["configured"] },
    local: { qwen: ["low"] },
  },
  integrations: { codex: [], claude: [], local: [] },
  local_profiles: {},
  operations: [],
  credentials: {},
  status: { running: false, local_url: "http://127.0.0.1:8095/" },
};
const AX_OVER = {
  "GET /api/dashboard": {
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
  },
  "GET /api/scan": { json: AX.inventory },
  "POST /api/scan": { json: AX.inventory },
  "POST /api/check": {
    json: { authenticated: true, models: { fixture: ["low"] } },
  },
  "GET /api/integration-catalog": { json: { items: [], warnings: [] } },
  "POST /api/integration-catalog": { json: { items: [], warnings: [] } },
};

// mulberry32: tiny deterministic PRNG.
function rng(seed) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const TARGETS =
  "button, a[href], input, select, textarea, summary, [role=option], [role=treeitem], [role=switch], [tabindex='0']";
const KEYS = [
  "Tab",
  "Shift+Tab",
  "Escape",
  "Enter",
  "ArrowDown",
  "ArrowUp",
  "ArrowLeft",
  "ArrowRight",
  "Home",
  "End",
  " ",
  "Control+k",
  "Control+/",
  "Backspace",
];
const WORDS = [
  "hi",
  "@",
  "/",
  "@@x",
  "//y",
  "olá 👩‍👩‍👧",
  "مرحبا",
  "<b>x</b>",
  "a".repeat(40),
  "\n",
];

async function chaos(page, origin, seed) {
  const rand = rng(seed),
    pick = (list) => list[Math.floor(rand() * list.length)],
    trace = [],
    errors = [];
  page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
  page.on(
    "console",
    (m) => m.type() === "error" && errors.push("console: " + m.text()),
  );
  page.on("dialog", (d) => d.dismiss().catch(() => {}));
  page.context().on("page", (p) => p.close().catch(() => {})); // target=_blank links
  for (let step = 0; step < STEPS; step++) {
    if (!page.url().startsWith(origin)) await page.goto(origin).catch(() => {});
    const roll = rand();
    let action;
    if (roll < 0.55) {
      const count = await page.locator(TARGETS).count();
      const index = Math.floor(rand() * Math.max(count, 1));
      const el = page.locator(TARGETS).nth(index);
      const label = await el
        .evaluate(
          (e) =>
            e.tagName.toLowerCase() +
            (e.id ? "#" + e.id : "") +
            ":" +
            (e.textContent || e.value || "").trim().slice(0, 24),
        )
        .catch(() => "gone");
      action = "click " + index + " " + label;
      await el.click({ timeout: 250 }).catch(() => {});
    } else if (roll < 0.85) {
      action = "key " + pick(KEYS);
      await page.keyboard.press(action.slice(4)).catch(() => {});
    } else {
      const word = pick(WORDS);
      action = "type " + JSON.stringify(word);
      await page.keyboard.type(word).catch(() => {});
    }
    trace.push(step + ": " + action);
    await page.waitForTimeout(15);
    if (errors.length) break;
  }
  await page.waitForTimeout(300);
  assert.deepEqual(
    errors,
    [],
    "seed " + seed + " trace:\n" + trace.slice(-12).join("\n"),
  );
  return trace.length;
}

runPersona("H40", [
  {
    title: "H40-S1 300 seeded actions on the harness raise no errors",
    timeout: 3000,
    async run(page) {
      const s = await mockHarness(page, MX);
      s.events.push(
        { id: 1, type: "answer_delta", data: { text: "chaos " } },
        {
          id: 2,
          type: "approval_required",
          data: {
            approval_id: "a1",
            request: { command: "ls" },
            can_remember: true,
          },
        },
        { id: 3, type: "answer_delta", data: { text: "reply" } },
      );
      await page.goto("http://harness.test");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
      const steps = await chaos(page, "http://harness.test", SEED);
      assert.equal(steps, STEPS);
      console.log(
        "H40-S1 seed " +
          SEED +
          ": " +
          steps +
          " actions, " +
          s.posts.length +
          " POST /v1/jobs",
      );
    },
  },
  {
    title: "H40-S2 300 seeded actions on the admin panel raise no errors",
    timeout: 3000,
    async run(page) {
      const calls = await mockAdmin(page, AX, AX_OVER);
      await page.goto("http://admin.test");
      await page.waitForLoadState("networkidle");
      const steps = await chaos(page, "http://admin.test", SEED + 1000);
      assert.equal(steps, STEPS);
      console.log(
        "H40-S2 seed " +
          (SEED + 1000) +
          ": " +
          steps +
          " actions, " +
          calls.length +
          " admin API calls",
      );
    },
  },
]);
