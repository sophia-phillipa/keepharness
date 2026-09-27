// H37 low-vision reader at 400% zoom (P4): a 1280 px desktop window zoomed to
// 400% is a 320x640 CSS viewport with devicePixelRatio 4 (WCAG 1.4.10 Reflow).
// This headless run is authoritative for reflow because the live Chrome persona
// could not resize the owner's maximized window.
"use strict";
const assert = require("node:assert/strict");
const {
  mockHarness,
  mockAdmin,
  runPersona,
  visible,
  fits,
} = require("./_harness.cjs");

const ZOOM = { width: 320, height: 640 };
const URL_300 =
  "https://docs.example.test/reference/" +
  "a".repeat(40) +
  "/" +
  Array.from({ length: 22 }, (_, i) => "segment" + i).join("/") +
  "?query=" +
  "b".repeat(40);
const CODE_LINE =
  "const wide = [" +
  Array.from({ length: 30 }, (_, i) => `"item-${i}"`).join(", ") +
  "];";
const ANSWER = [
  "## Where to read more",
  "",
  "Full link: " + URL_300,
  "",
  "Named link: [the reference](" + URL_300 + ")",
  "",
  "```js",
  CODE_LINE,
  "```",
  "",
  "Inline `" + "x".repeat(120) + "` code.",
  "",
  "| column one | column two | column three | column four | column five |",
  "|---|---|---|---|---|",
  "| value | value | value | value | value |",
].join("\n");
const MODELS = [
  {
    id: "gpt-5.6-luna",
    backend: "codex",
    efforts: ["low"],
    permissions: { upload: true },
    execution_modes: ["native", "scoped"],
  },
];

// Browser zoom, not a phone: desktop user agent, no touch, devicePixelRatio 4.
async function zoom400(page) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Emulation.setDeviceMetricsOverride", {
    ...ZOOM,
    deviceScaleFactor: 4,
    mobile: false,
  });
}

// Elements that stick out of the viewport on the right (or left) and are not
// clipped by an ancestor that scrolls or hides overflow. WCAG 1.4.10 lets code
// blocks and data tables scroll on their own, so those containers count as clipping.
function sideOverflow() {
  const out = [];
  for (const el of document.body.querySelectorAll("*")) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    if (r.right <= innerWidth + 1 && r.left >= -1) continue;
    let clipped = false;
    for (
      let a = el.parentElement;
      a && a !== document.body;
      a = a.parentElement
    ) {
      const s = getComputedStyle(a);
      if (s.overflowX !== "visible" || s.display === "none") {
        const b = a.getBoundingClientRect();
        if (b.right <= innerWidth + 1 && b.left >= -1) clipped = true;
        break;
      }
    }
    if (clipped || el.closest("[hidden],dialog:not([open]),[inert]")) continue;
    const s = getComputedStyle(el);
    if (s.visibility !== "visible" || s.position === "fixed") continue;
    out.push(
      el.tagName.toLowerCase() +
        (el.id ? "#" + el.id : "") +
        (typeof el.className === "string" && el.className
          ? "." + el.className.split(" ")[0]
          : "") +
        " " +
        Math.round(r.left) +
        ".." +
        Math.round(r.right),
    );
  }
  return out;
}

const noPageScroll = (page) =>
  page.evaluate(
    () =>
      document.documentElement.scrollWidth <= innerWidth &&
      document.body.scrollWidth <= innerWidth,
  );

// Scrolled into view, inside the viewport, and not covered by another element
// (what a pointer click at the control's centre would actually hit).
const covering = (el) => {
  const r = el.getBoundingClientRect(),
    hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
  return !hit || el.contains(hit) || hit.contains(el)
    ? ""
    : hit.tagName.toLowerCase() +
        (hit.id ? "#" + hit.id : "") +
        " '" +
        hit.textContent.trim().slice(0, 30) +
        "'";
};
async function reachable(page, selector) {
  await page.locator(selector).scrollIntoViewIfNeeded();
  await visible(page, selector);
  await fits(page, selector);
  assert.equal(
    await page.locator(selector).evaluate(covering),
    "",
    selector + " is covered",
  );
}

// Admin-gauntlet state (Ax) with a local model profile so the folder picker opens.
function axState() {
  const permissions = {
    read: true,
    write: false,
    upload: true,
    shell: false,
    internet: false,
    hooks: false,
  };
  const service = (models, mode = "native") => ({
    added: true,
    enabled: true,
    models,
    projects: ["sem-projeto"],
    permissions: { ...permissions },
    mode,
    integrations: [],
  });
  return {
    settings: {
      services: {
        codex: service(["gpt-5.6-luna"], "scoped"),
        claude: service(["claude-sonnet-5"]),
        local: service(["qwen-local"]),
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
          models: ["qwen-local"],
          runtimes: [
            {
              id: "qwen-local",
              model_file: "/models/Qwen.gguf",
              runtime: "llama.cpp",
            },
          ],
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
    integrations: { codex: [], claude: [], local: [], deepseek: [] },
    local_profiles: {
      "/models/Qwen.gguf": {
        model_file: "/models/Qwen.gguf",
        binary: "/usr/bin/llama-server",
        performance: {},
        allowed_roots: [],
      },
    },
    operations: [],
    credentials: { deepseek: true },
    status: {
      running: true,
      local_url: "http://127.0.0.1:8095/",
      shared: false,
    },
  };
}

const FOLDERS = {
  path: "/server/a-very-long-folder-name-that-does-not-fit-in-a-narrow-window",
  parent: "/server",
  directories: Array.from({ length: 6 }, (_, i) => ({
    name: "Project folder with a long descriptive name " + i,
    path: "/server/long/Project folder with a long descriptive name " + i,
  })),
  truncated: false,
};

runPersona("H37", [
  {
    title: "H37-S1 answer with a 300-char URL and wide code reflows at 320 px",
    viewport: ZOOM,
    timeout: 8000,
    async run(page) {
      await zoom400(page);
      const s = await mockHarness(page, {
        "GET /v1/models": {
          json: {
            models: MODELS,
            providers: { codex: true },
            uploads_enabled: true,
          },
        },
        "/GET \\/v1\\/jobs\\/job-\\d+$/": {
          json: {
            id: "job-1",
            project: "sem-projeto",
            state: "completed",
            result: { answer: ANSWER },
          },
        },
      });
      await page.goto("http://harness.test");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
      assert.deepEqual(
        await page.evaluate(() => [innerWidth, devicePixelRatio]),
        [320, 4],
      );
      assert(
        await noPageScroll(page),
        "empty harness has no horizontal page scroll",
      );

      for (const id of ["#prompt", "#send", "#attach", "#model-trigger"])
        await reachable(page, id);
      await page.fill("#prompt", "Where do I read more? " + URL_300);
      assert(await noPageScroll(page), "a long draft does not widen the page");
      await page.click("#send");
      await page.waitForFunction(() =>
        /Where to read more/.test(
          document.getElementById("messages").innerText,
        ),
      );
      assert.equal(s.posts.length, 1);

      assert(await noPageScroll(page), "the answer does not widen the page");
      assert.deepEqual(await page.evaluate(sideOverflow), []);
      // The bare URL is plain text (answers do not autolink); it wraps inside
      // the message, and the named link stays inside the viewport.
      const bare = await page
        .locator("#messages p", { hasText: "Full link:" })
        .evaluate((p) => {
          const r = p.getBoundingClientRect();
          return {
            right: r.right,
            lines: Math.round(
              r.height / parseFloat(getComputedStyle(p).lineHeight),
            ),
          };
        });
      assert(bare.right <= 321, "bare URL paragraph stays inside the viewport");
      assert(bare.lines > 3, "the 300-char URL wraps over several lines");
      const link = await page
        .locator("#messages a", { hasText: "the reference" })
        .boundingBox();
      assert(
        link && link.x + link.width <= 321,
        "named link stays inside the viewport",
      );
      // The code block scrolls on its own instead of the page.
      const pre = await page
        .locator("#messages pre")
        .first()
        .evaluate((p) => {
          const box =
            p.scrollWidth > p.clientWidth ? p : p.querySelector("code");
          return {
            scrolls: box.scrollWidth > box.clientWidth,
            overflow: getComputedStyle(box).overflowX,
            right: p.getBoundingClientRect().right,
          };
        });
      assert(pre.scrolls, "wide code overflows its own box");
      assert(
        ["auto", "scroll"].includes(pre.overflow),
        "code box scrolls: " + pre.overflow,
      );
      assert(pre.right <= 321);
      await reachable(page, "#prompt");
      await reachable(page, "#model-trigger");
    },
  },
  {
    title: "H37-S1b sidebar, menus and settings fit at 400% zoom",
    viewport: ZOOM,
    timeout: 8000,
    async run(page) {
      await zoom400(page);
      await mockHarness(page, {
        "GET /v1/models": {
          json: {
            models: MODELS,
            providers: { codex: true },
            uploads_enabled: true,
          },
        },
        "GET /v1/conversations": {
          json: {
            conversations: [
              {
                id: "c1",
                title:
                  "A conversation title long enough to need an ellipsis at 320 px",
                project: "sem-projeto",
                state: "completed",
                execution: { mode: "native" },
              },
            ],
          },
        },
      });
      await page.goto("http://harness.test");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });

      if ((await page.getAttribute("#menu", "aria-expanded")) !== "true")
        await page.click("#menu");
      await visible(page, "#sidebar");
      assert(await noPageScroll(page), "open sidebar does not widen the page");
      assert.deepEqual(await page.evaluate(sideOverflow), []);
      await page.click("#menu");

      for (const menu of ["model", "access", "effort"]) {
        await page.click(`#${menu}-trigger`);
        await visible(page, `#${menu}-menu`);
        await fits(page, `#${menu}-menu`);
        await page.keyboard.press("Escape");
      }

      await page.click("#settings");
      await visible(page, "#settings-dialog");
      await fits(page, "#settings-dialog");
      await reachable(page, "#settings-close");
      assert(await noPageScroll(page));
      for (const section of ["agents", "skills"]) {
        await page.click(`[data-settings=${section}]`);
        assert.deepEqual(
          await page.evaluate(sideOverflow),
          [],
          section + " section",
        );
      }
      await page.click("#settings-close");

      await page.click("#search-conversations");
      await visible(page, "#conversation-search-dialog");
      await fits(page, "#conversation-search-dialog");
      await page.keyboard.press("Escape");
    },
  },
  {
    title: "H37-S2 admin import/export and folder picker dialogs fit at 320 px",
    viewport: ZOOM,
    timeout: 8000,
    async run(page) {
      await zoom400(page);
      await mockAdmin(page, axState(), {
        "GET /api/folders": { json: FOLDERS },
      });
      await page.goto("http://admin.test/");
      await page
        .locator("[data-panel=providers]")
        .waitFor({ state: "attached" });
      assert.deepEqual(
        await page.evaluate(() => [innerWidth, devicePixelRatio]),
        [320, 4],
      );
      assert(
        await noPageScroll(page),
        "admin home has no horizontal page scroll",
      );

      for (const panel of ["home", "providers", "runs"]) {
        await page.locator(`[data-panel=${panel}]`).scrollIntoViewIfNeeded();
        await page.click(`[data-panel=${panel}]`);
        assert(
          await noPageScroll(page),
          panel + " has no horizontal page scroll",
        );
        assert.deepEqual(await page.evaluate(sideOverflow), [], panel);
      }

      await page.click("[data-panel=providers]");
      await page.locator("#manage-network").scrollIntoViewIfNeeded();
      await page.click("#manage-network");
      await visible(page, "#network");
      await fits(page, "#network");
      for (const id of [
        "#network-close",
        "#export-settings",
        "#import-settings",
      ])
        await reachable(page, id);
      await page.click("#network-close");

      await page
        .locator("[data-configured-provider=local]")
        .getByRole("button", { name: /Edit/ })
        .click();
      await page
        .locator("#inspector-tabs")
        .getByText("Model and hardware")
        .click();
      await page.locator("#model-roots-add").scrollIntoViewIfNeeded();
      await page.click("#model-roots-add");
      await visible(page, "#folder-picker");
      await page.waitForFunction(
        () =>
          document.querySelectorAll("#folder-picker-list button").length === 6,
      );
      await fits(page, "#folder-picker");
      assert.deepEqual(await page.evaluate(sideOverflow), [], "folder picker");
      for (const id of [
        "#folder-picker-close",
        "#folder-picker-parent",
        "#folder-picker-use",
      ])
        await reachable(page, id);
      await reachable(page, "#folder-picker-list button:last-child");
      await page
        .locator(".folder-picker-create>summary")
        .scrollIntoViewIfNeeded();
      await page.click(".folder-picker-create>summary");
      // KNOWN BUG F-99: tabbing into "New folder name" scrolls it just into the
      // dialog, where the sticky ".folder-picker-footer" (admin.css ~1095) with
      // "Use this folder" paints over the focused field (WCAG 2.4.11 Focus Not
      // Obscured). The next Tab scrolls far enough; so does scrolling to the end.
      await page.keyboard.press("Tab");
      assert.equal(
        await page.evaluate(() => document.activeElement.id),
        "folder-picker-new-name",
      );
      assert.equal(
        await page.locator("#folder-picker-new-name").evaluate(covering),
        "button#folder-picker-use 'Use this folder'",
      );
      await page.keyboard.press("Tab");
      assert.equal(
        await page.evaluate(() => document.activeElement.id),
        "folder-picker-create",
      );
      assert.equal(
        await page.locator("#folder-picker-create").evaluate(covering),
        "",
      );
      await page
        .locator("#folder-picker")
        .evaluate((d) => (d.scrollTop = d.scrollHeight));
      for (const id of ["#folder-picker-new-name", "#folder-picker-create"]) {
        await visible(page, id);
        await fits(page, id);
        assert.equal(
          await page.locator(id).evaluate(covering),
          "",
          id + " after scrolling to the end",
        );
      }
      assert(await noPageScroll(page));
    },
  },
]);
