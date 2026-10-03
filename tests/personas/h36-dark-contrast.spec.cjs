// H36 low-vision, high-contrast user (P4): OS dark preference, forced colors and
// reduced motion; WCAG AA text contrast in every theme of both UIs, status that is
// not colour-only, and a theme choice that persists per surface.
"use strict";
const assert = require("node:assert/strict");
const {
  mockHarness,
  mockAdmin,
  runPersona,
  visible,
} = require("./_harness.cjs");

const THEMES = [
  "violet-bordeaux",
  "porcelain",
  "mineral-rose",
  "amethyst",
  "petroleum",
  "arizona",
];
const LIGHT = THEMES.slice(0, 3);
const MODELS = [
  {
    id: "gpt-5.6-luna",
    backend: "codex",
    efforts: ["low"],
    permissions: { upload: true },
    execution_modes: ["native", "scoped"],
  },
  {
    id: "claude-sonnet-5",
    backend: "claude",
    efforts: ["low"],
    permissions: { upload: true },
    execution_modes: ["native", "scoped"],
  },
];
const ANSWER =
  "## Result\n\nSee [the guide](https://example.test/guide) and `inline code`.\n\n" +
  "```js\nconst x = 1;\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n> quoted";

// Admin-gauntlet state shape (Ax), trimmed to what the screens read.
function axState() {
  const permissions = {
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
    permissions: { ...permissions },
    mode: "native",
    integrations: [],
  });
  return {
    settings: {
      services: {
        codex: service(["gpt-5.6-luna"]),
        claude: service(["claude-sonnet-5"]),
        deepseek: service(["deepseek-flash"]),
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
          models: [],
          runtimes: [],
        },
        { id: "deepseek", name: "DeepSeek", found: true, api: true },
      ],
      projects: [{ name: "Demo", path: "/workspace/demo" }],
      network: { online: true },
    },
    authentication: { codex: true, claude: true, deepseek: true },
    models: {
      codex: { "gpt-5.6-luna": ["low", "medium"] },
      claude: { "claude-sonnet-5": ["low", "high"] },
      deepseek: { "deepseek-flash": ["configured"] },
    },
    integrations: { codex: [], claude: [], deepseek: [] },
    local_profiles: {},
    operations: [
      {
        id: "op-1",
        state: "failed",
        title: "Check account",
        output: "Login required.",
      },
      {
        id: "op-2",
        state: "completed",
        title: "Check account",
        output: "Done.",
      },
    ],
    credentials: { deepseek: true },
    status: {
      running: true,
      local_url: "http://127.0.0.1:8095/",
      shared: false,
    },
  };
}

// In-page WCAG 1.4.3 scan: every visible text node's colour against its
// composited background (4.5:1, or 3:1 for large text). Disabled controls are
// exempt, as WCAG allows. Returns "<element> '<text>' <ratio>" strings.
function contrastScan() {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  const parse = (c) => {
    const m = c.match(/^rgba?\(([^)]+)\)$/);
    if (m) {
      const p = m[1]
        .split(/[ ,/]+/)
        .filter(Boolean)
        .map(Number);
      return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
    }
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = c;
    ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2], d[3] / 255];
  };
  const over = (top, bottom) =>
    [0, 1, 2].map((i) => top[i] * top[3] + bottom[i] * (1 - top[3])).concat(1);
  const lum = (c) =>
    [0.2126, 0.7152, 0.0722].reduce((sum, w, i) => {
      const v = c[i] / 255;
      return (
        sum + w * (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
      );
    }, 0);
  const ratio = (a, b) => {
    const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  const background = (el) => {
    const layers = [];
    for (let e = el; e; e = e.parentElement) {
      const c = parse(getComputedStyle(e).backgroundColor);
      if (c[3] > 0) layers.push(c);
      if (c[3] >= 1) break;
    }
    return layers
      .reverse()
      .reduce((acc, c) => over(c, acc), [255, 255, 255, 1]);
  };
  const out = [];
  const seen = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode,
      el = node.parentElement;
    if (!node.textContent.trim() || !el || seen.has(el)) continue;
    seen.add(el);
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height || r.bottom < 0 || r.top > innerHeight) continue;
    if (
      el.closest(
        "[hidden],[aria-hidden=true],.visually-hidden,option,select,:disabled",
      )
    )
      continue;
    const s = getComputedStyle(el);
    if (s.visibility !== "visible" || Number(s.opacity) < 0.1) continue;
    const bg = background(el),
      cr = ratio(over(parse(s.color), bg), bg),
      size = parseFloat(s.fontSize),
      need =
        size >= 24 || (Number(s.fontWeight) >= 700 && size >= 18.66) ? 3 : 4.5;
    if (cr < need) {
      const name =
        el.tagName.toLowerCase() +
        (el.id ? "#" + el.id : "") +
        (typeof el.className === "string" && el.className
          ? "." + el.className.split(" ")[0]
          : "");
      out.push(
        `${name} '${node.textContent.trim().slice(0, 30)}' ${cr.toFixed(2)}`,
      );
    }
  }
  return out;
}

async function openHarness(page, over = {}) {
  const s = await mockHarness(page, {
    "GET /v1/models": {
      json: {
        models: MODELS,
        providers: { codex: true, claude: true },
        uploads_enabled: true,
      },
    },
    "/GET \\/v1\\/jobs\\/job-\\d+$/": (route) =>
      route.fulfill({
        json: {
          id: "job-1",
          project: "sem-projeto",
          state: "completed",
          result: { answer: ANSWER },
        },
      }),
    ...over,
  });
  await page.goto("http://harness.test");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  return s;
}

// Opens each harness surface a low-vision user reads and scans it.
async function scanHarness(page, theme) {
  await page.evaluate((t) => HarnessTheme.apply(t, false), theme);
  const found = [];
  const scan = async (where) =>
    found.push(
      ...(await page.evaluate(contrastScan)).map(
        (o) => `${theme} ${where}: ${o}`,
      ),
    );
  await scan("conversation");
  await page.locator(".skip-link").focus();
  await scan("skip link");
  await page.click("#settings");
  await visible(page, "#settings-dialog");
  await scan("settings");
  await page.keyboard.press("Escape");
  for (const menu of ["model", "access", "effort"]) {
    await page.click(`#${menu}-trigger`);
    await visible(page, `#${menu}-menu`);
    await scan(menu + " menu");
    await page.keyboard.press("Escape");
  }
  await page.click("#settings");
      await page.click("#settings-quota");
  await scan("quota panel");
  await page.keyboard.press("Escape");
      await page.keyboard.press("Escape");
  // The side panel remembers its view: "Activity" toggles the panel closed when
  // it already shows Activity, so open and switch only as needed.
  if ((await page.getAttribute("#panel-toggle", "aria-expanded")) !== "true")
    await page.click("#panel-toggle");
  if ((await page.getAttribute("#activity-toggle", "aria-expanded")) !== "true")
    await page.click("#activity-toggle");
  await visible(page, "#activity-view");
  await scan("activity panel");
  await page.click("#panel-toggle");
  return found;
}

async function scanAdmin(page, theme) {
  await page.evaluate((t) => HarnessTheme.apply(t, false), theme);
  const found = [];
  const scan = async (where) =>
    found.push(
      ...(await page.evaluate(contrastScan)).map(
        (o) => `${theme} ${where}: ${o}`,
      ),
    );
  for (const panel of ["home", "providers", "runs"]) {
    await page.click(`[data-panel=${panel}]`);
    await scan(panel);
  }
  await page.click("[data-panel=providers]");
  await page.click("#manage-network");
  await visible(page, "#network");
  await scan("import/export dialog");
  await page.click("#network-close");
  await page.click("#theme");
  await visible(page, "#appearance-dialog");
  await scan("appearance dialog");
  await page.click("#appearance-close");
  return found;
}

// A low-vision user starts from a completed answer.
async function answered(page, s) {
  await page.fill("#prompt", "Show me a formatted answer");
  await page.click("#send");
  await page.waitForFunction(() =>
    /Result/.test(document.getElementById("messages").innerText),
  );
  assert.equal(s.posts.length, 1);
}

runPersona("H36", [
  {
    title: "H36-S1 OS dark preference picks the initial theme",
    async run(page) {
      await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
      await openHarness(page);
      // F-97 fixed: with no saved theme, theme.js follows
      // matchMedia('(prefers-color-scheme: dark)') and picks the default dark
      // palette instead of always starting on the light default. Since the
      // Codex-style shell (2026-10-03) the default dark palette is "graphite".
      assert.equal(
        await page.evaluate(() => document.documentElement.dataset.palette),
        "graphite",
      );
      assert.equal(
        await page.evaluate(() => document.documentElement.dataset.theme),
        "dark",
      );
    },
  },
  {
    title: "H36-S1b harness text contrast in all six themes (dark OS)",
    timeout: 8000,
    async run(page) {
      await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
      const s = await openHarness(page);
      await answered(page, s);
      const found = [];
      for (const theme of THEMES)
        found.push(...(await scanHarness(page, theme)));
      // F-96 fixed: .skip-link:focus now sets color: var(--th-text), giving
      // the focused "Skip to message" link real contrast in every theme.
      assert.deepEqual(found, []);
    },
  },
  {
    title:
      "H36-S1c harness under forced colors: contrast, focus and non-colour status",
    timeout: 8000,
    async run(page) {
      await page.emulateMedia({
        colorScheme: "dark",
        forcedColors: "active",
        reducedMotion: "reduce",
      });
      const s = await openHarness(page);
      await answered(page, s);
      const found = [];
      for (const theme of THEMES)
        found.push(...(await scanHarness(page, theme)));
      assert.deepEqual(found, []);
      // Focus indicator of the message box with system colours.
      const ring = () =>
        page.evaluate(() => {
          const p = getComputedStyle(document.getElementById("prompt")),
            c = getComputedStyle(document.querySelector(".composer"));
          return [
            p.outlineStyle,
            p.boxShadow,
            c.outlineStyle,
            c.boxShadow,
            c.borderColor,
            c.borderWidth,
          ].join(" | ");
        });
      await page.locator("#prompt").blur();
      const idle = await ring();
      await page.locator("#prompt").focus();
      assert.equal(
        await page.evaluate(() => document.activeElement.id),
        "prompt",
      );
      // F-98 fixed: the forced-colors block now gives the focused composer its
      // own `outline: 2px solid Highlight`, so it no longer matches the idle
      // state (WCAG 2.4.7).
      assert.notEqual(await ring(), idle);
      // Status is carried by text, not only by colour.
      assert.match(await page.locator("#quota-short").innerText(), /\w/);
      const indicator = page.locator("#execution-mode-indicator");
      await indicator.waitFor({ state: "visible" });
      assert.equal(
        await indicator.getAttribute("aria-label"),
        "Native conversation · isolation off",
      );
      assert.equal(
        await indicator.getAttribute("title"),
        "Native conversation · isolation off",
      );
    },
  },
  {
    title: "H36-S1d error status is worded, not colour-only",
    async run(page) {
      page.removeAllListeners("console"); // 4xx fetches log "Failed to load resource"
      const unexpected = [];
      page.on("console", (m) => {
        if (m.type() === "error" && !/^Failed to load resource/.test(m.text()))
          unexpected.push(m.text());
      });
      await page.emulateMedia({ colorScheme: "dark", forcedColors: "active" });
      await openHarness(page, {
        "POST /v1/jobs": {
          status: 503,
          json: { code: "native_failed", retryable: true },
        },
      });
      await page.fill("#prompt", "fail please");
      await page.click("#send");
      const status = page.locator("#status.error");
      await status.waitFor({ state: "visible" });
      assert.equal(await status.getAttribute("role"), "alert");
      assert.match(
        await status.innerText(),
        /^Couldn't run: The AI service did not finish the run\./,
      );
      assert.deepEqual(await page.evaluate(contrastScan), []);
      assert.deepEqual(unexpected, []);
    },
  },
  {
    title:
      "H36-S1e admin text contrast in all six themes, normal and forced colors",
    timeout: 10000,
    async run(page) {
      await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
      await mockAdmin(page, axState());
      await page.goto("http://admin.test/");
      await visible(page, "[data-panel=providers]");
      const found = [];
      for (const theme of THEMES) found.push(...(await scanAdmin(page, theme)));
      await page.emulateMedia({ forcedColors: "active" });
      for (const theme of THEMES) found.push(...(await scanAdmin(page, theme)));
      assert.deepEqual(found, []);
      // Harness state in the top bar is worded ("● Harness active"), not a dot.
      assert.match(
        await page.locator("header, #main").first().innerText(),
        /Harness active/,
      );
    },
  },
  {
    title:
      "H36-S2 admin theme persists after F5 and stays independent of the harness",
    async run(page) {
      await page.emulateMedia({ colorScheme: "dark" });
      await mockAdmin(page, axState());
      await page.goto("http://admin.test/");
      await page.click("#theme");
      await visible(page, "#appearance-dialog");
      await page.click('#appearance-dialog [data-theme-choice="petroleum"]');
      assert.equal(
        await page.getAttribute(
          '#appearance-dialog [data-theme-choice="petroleum"]',
          "aria-pressed",
        ),
        "true",
      );
      await page.click("#appearance-close");
      await page.reload();
      assert.equal(
        await page.evaluate(() => document.documentElement.dataset.palette),
        "petroleum",
      );
      assert.equal(
        await page.evaluate(() => document.documentElement.dataset.theme),
        "dark",
      );
      assert.equal(
        await page.evaluate(() => HarnessTheme.key),
        "keepharness:theme:admin",
      );

      // A second admin tab follows the change live (storage event).
      const second = await page.context().newPage();
      await mockAdmin(second, axState());
      await second.goto("http://admin.test/");
      assert.equal(
        await second.evaluate(() => document.documentElement.dataset.palette),
        "petroleum",
      );
      await page.click("#theme");
      await page.click('#appearance-dialog [data-theme-choice="arizona"]');
      await second.waitForFunction(
        () => document.documentElement.dataset.palette === "arizona",
      );
      await second.close();

      // The harness keeps its own key, even when served from the admin's origin.
      const harness = await page.context().newPage();
      await mockHarness(harness, {
        "GET /v1/models": {
          json: { models: MODELS, providers: { codex: true } },
        },
      });
      await harness.goto("http://harness.test");
      // Default light palette of the Codex-style shell.
      assert.equal(
        await harness.evaluate(() => document.documentElement.dataset.palette),
        "paper",
      );
      assert.equal(
        await harness.evaluate(() => HarnessTheme.key),
        "keepharness:theme:harness",
      );
      await page.evaluate(() =>
        localStorage.setItem("keepharness:theme:harness", "porcelain"),
      );
      await page.reload();
      assert.equal(
        await page.evaluate(() => document.documentElement.dataset.palette),
        "arizona",
      );
      await harness.close();
    },
  },
]);
