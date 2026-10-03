// H38 i18n auditor (P7): every string a user can see or hear (text, aria-label,
// title, placeholder, alt) in both UIs, after opening each dialog and menu,
// checked for leftover Portuguese and raw protocol codes; ten server error codes
// forced through the composer; 100-character model and project names.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {
  mockHarness,
  mockAdmin,
  runPersona,
  visible,
} = require("./_harness.cjs");

const ROOT = path.join(__dirname, "..", "..");
const MODELS = [
  {
    id: "gpt-5.6-luna",
    backend: "codex",
    efforts: ["low", "medium"],
    permissions: { upload: true },
    execution_modes: ["native", "scoped"],
  },
  {
    id: "claude-sonnet-5",
    backend: "claude",
    efforts: ["low", "high"],
    permissions: { upload: true },
    execution_modes: ["native", "scoped"],
  },
  {
    id: "deepseek-flash",
    backend: "deepseek",
    efforts: ["configured"],
    permissions: { upload: false },
    execution_modes: ["native"],
  },
];
const PROJECTS = {
  json: {
    projects: ["sem-projeto", "demo"],
    details: {
      "sem-projeto": { label: "No project" },
      demo: { label: "Demo" },
    },
  },
};
const CONVERSATIONS = {
  json: {
    conversations: [
      {
        id: "c1",
        title: "Quarterly report",
        project: "demo",
        state: "completed",
        execution: { mode: "native" },
      },
      {
        id: "c2",
        title: "Loose idea",
        project: "sem-projeto",
        state: "completed",
        execution: { mode: "scoped" },
      },
    ],
  },
};
const DIRECTORIES = {
  json: {
    roots: [{ id: "home", label: "Local folders" }],
    root_id: "home",
    path: "",
    absolute_path: "/home/test-user",
    entries: [
      {
        name: "Work A",
        path: "Work A",
        absolute_path: "/home/test-user/Work A",
        type: "directory",
      },
    ],
    limited: false,
  },
};

// Leftover Portuguese: any Portuguese-only letter, or one of the stop words the
// repository's own conventions check scans source for (scripts/check_conventions.py).
// `sem-projeto` is a persisted protocol id, not text, so it is stripped first.
const PT_WORDS = fs
  .readFileSync(path.join(ROOT, "scripts", "check_conventions.py"), "utf8")
  .match(/^PT_WORDS = "([^"]+)"/m)[1]
  .split(" ");
const PORTUGUESE = new RegExp(
  "[\u00e3\u00f5\u00e7\u00c3\u00d5\u00c7]|\\b(" + PT_WORDS.join("|") + ")\\b",
  "i",
);
// A raw protocol code such as `model_not_allowed` shown to the user.
const SNAKE = /\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/;

// Every user-facing string in the document, hidden dialogs and templates included.
function collectStrings() {
  const out = new Set([document.title]);
  const walk = (root) => {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const n = walker.currentNode;
      if (n.parentElement?.closest("script,style")) continue;
      const t = n.textContent.replace(/\s+/g, " ").trim();
      if (t) out.add(t);
    }
    for (const el of root.querySelectorAll("*")) {
      for (const a of [
        "aria-label",
        "title",
        "placeholder",
        "alt",
        "aria-description",
        "aria-valuetext",
      ]) {
        const v = el.getAttribute(a);
        if (v && v.trim()) out.add(v.trim());
      }
      if (el.tagName === "TEMPLATE") walk(el.content);
    }
  };
  walk(document.documentElement);
  return [...out];
}

async function audit(page, where, sink) {
  for (const text of await page.evaluate(collectStrings)) {
    const plain = text.replace(/sem-projeto/g, "");
    if (PORTUGUESE.test(plain))
      sink.portuguese.add(where + ": " + text.slice(0, 120));
    const code = plain.match(SNAKE);
    if (code) sink.codes.add(code[0] + " ← " + text.slice(0, 120));
  }
}
const newSink = () => ({ portuguese: new Set(), codes: new Set() });

// Chromium logs every 4xx/5xx fetch as a console error; allow only that line.
function allowHttpErrors(page) {
  const unexpected = [];
  page.removeAllListeners("console");
  page.on("console", (m) => {
    if (m.type() === "error" && !/^Failed to load resource/.test(m.text()))
      unexpected.push(m.text());
  });
  return unexpected;
}

async function openHarness(page, over = {}) {
  const s = await mockHarness(page, {
    "GET /v1/models": {
      json: {
        models: MODELS,
        providers: { codex: true, claude: true, deepseek: true },
        uploads_enabled: true,
      },
    },
    "GET /v1/projects": PROJECTS,
    "GET /v1/conversations": CONVERSATIONS,
    "GET /v1/project-directories": DIRECTORIES,
    "GET /v1/project-folder": {
      json: {
        paths: ["/home/test-user/Work A"],
        project_ids: ["demo"],
        missing: false,
      },
    },
    ...over,
  });
  await page.goto("http://harness.test");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  return s;
}

// Admin-gauntlet state (Ax), trimmed to what the screens read.
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

// Ten codes the harness really returns on POST /v1/jobs (agent_service/services),
// half of them mapped in ui.js `userErrors`, half not.
const JOB_ERRORS = [
  [422, "model_not_allowed"],
  [422, "uploads_denied"],
  [503, "queue_full"],
  [422, "isolation_unavailable"],
  [502, "native_failed"],
  [422, "execution_mode_unsupported"],
  [422, "model_or_effort_unavailable"],
  [403, "service_project_denied"],
  [507, "job_storage_limit"],
  [422, "prompt_required"],
];

runPersona("H38", [
  {
    title: "H38-S1 harness: every dialog, menu and panel is English",
    timeout: 8000,
    async run(page) {
      const sink = newSink();
      const s = await openHarness(page);
      await audit(page, "start", sink);

      await page.click("#settings");
      for (const section of ["appearance", "agents", "skills"]) {
        await page.click(`[data-settings=${section}]`);
        await audit(page, "settings " + section, sink);
      }
      // "Connection / MCP" lives in the settings navigation.
      await page.click("#setup");
      await visible(page, "#setup-dialog");
      await audit(page, "setup", sink);
      await page.keyboard.press("Escape");
      if (await page.locator("#settings-dialog").evaluate((d) => d.open))
        await page.click("#settings-close");

      await page.click("#search-conversations");
      await page.fill("#conversation-search", "report");
      await audit(page, "search", sink);
      await page.keyboard.press("Escape");

      if (!(await page.locator("#project-tree").evaluate((el) => el.open)))
        await page.locator("#project-tree > summary").click();
      await page.click("#add-project");
      await visible(page, "#project-dialog");
      await page
        .locator("#project-directory-list .project-file-row")
        .filter({ hasText: "Work A" })
        .click();
      await page.click("#project-directory-add-current");
      await audit(page, "new project", sink);
      await page.keyboard.press("Escape");

      for (const action of ["Rename conversation", "Delete conversation"]) {
        await page
          .locator("#sidebar .conversation-actions summary")
          .first()
          .click();
        await page
          .locator("#sidebar .conversation-actions[open] button")
          .filter({ hasText: action })
          .click();
        await audit(page, action, sink);
        await page.keyboard.press("Escape");
      }
      await page
        .locator('#projects [aria-label^="Actions for project"]')
        .first()
        .click();
      await audit(page, "project actions", sink);
      await page
        .locator(".project-actions-menu:popover-open .project-delete-folder")
        .click();
      await visible(page, "#delete-project-folder-dialog");
      await page.waitForFunction(
        () =>
          document.getElementById("delete-project-folder-paths").children
            .length,
      );
      await audit(page, "delete project folder", sink);
      await page.keyboard.press("Escape");

      for (const menu of ["model", "access", "effort"]) {
        await page.click(`#${menu}-trigger`);
        await visible(page, `#${menu}-menu`);
        await audit(page, menu + " menu", sink);
        await page.keyboard.press("Escape");
      }
      await page.click("#settings");
      await page.click("#settings-quota");
      await audit(page, "quota", sink);
      await page.keyboard.press("Escape");
      await page.keyboard.press("Escape");

      await page.fill("#prompt", "Hello");
      await page.click("#send");
      await page.waitForFunction(() =>
        /Fixture response/.test(document.getElementById("messages").innerText),
      );
      assert.equal(s.posts.length, 1);
      if (
        (await page.getAttribute("#panel-toggle", "aria-expanded")) !== "true"
      )
        await page.click("#panel-toggle");
      for (const view of ["files", "activity"]) {
        if (
          (await page.getAttribute(`#${view}-toggle`, "aria-expanded")) !==
          "true"
        )
          await page.click(`#${view}-toggle`);
        await audit(page, view + " panel", sink);
      }
      assert.deepEqual([...sink.portuguese], []);
      assert.deepEqual([...sink.codes], []);
      // The detectors are not vacuous: a planted leftover is caught.
      await page.evaluate(() => {
        const p = document.createElement("p");
        p.title = "Rascunho removido"; // conventions: allow-pt
        p.textContent = "Failed with model_not_allowed";
        document.body.append(p);
      });
      const planted = newSink();
      await audit(page, "planted", planted);
      assert.equal(planted.portuguese.size, 1);
      assert.deepEqual(
        [...planted.codes],
        ["model_not_allowed ← Failed with model_not_allowed"],
      );
    },
  },
  {
    title: "H38-S1b ten server error codes reach the user as sentences",
    timeout: 8000,
    async run(page) {
      const unexpected = allowHttpErrors(page);
      let next = 0;
      await openHarness(page, {
        "POST /v1/jobs": (route) => {
          const [status, code] = JOB_ERRORS[next++];
          return route.fulfill({
            status,
            json: { code, message: code, retryable: false },
          });
        },
      });
      const shown = {};
      for (const [, code] of JOB_ERRORS) {
        await page.fill("#prompt", "Trigger " + code);
        await page.click("#send");
        await page.waitForFunction(() =>
          /Couldn't/.test(document.getElementById("status").textContent),
        );
        shown[code] = (await page.locator("#status").innerText()).trim();
        assert.equal(
          await page.locator("#prompt").inputValue(),
          "Trigger " + code,
          "draft kept",
        );
        await page.evaluate(
          () => (document.getElementById("status").textContent = ""),
        );
      }
      assert.equal(next, 10);
      for (const text of Object.values(shown))
        assert.doesNotMatch(text, PORTUGUESE);
      const raw = Object.entries(shown)
        .filter(([code, text]) => text.includes(code))
        .map(([code]) => code);
      // F-93: every code has a sentence; none reaches the user raw.
      assert.deepEqual(raw, []);
      assert.match(shown.model_not_allowed, /This model is not enabled/);
      assert.match(
        shown.execution_mode_unsupported,
        /start a new conversation/,
      );
      assert.deepEqual(unexpected, []);
    },
  },
  {
    title: "H38-S1c admin: every panel, wizard step and dialog is English",
    timeout: 10000,
    async run(page) {
      const sink = newSink(),
        state = axState();
      Object.assign(state.settings.services.deepseek, {
        added: false,
        enabled: false,
        models: [],
      });
      await mockAdmin(page, state, {
        "POST /api/scan": { json: state.inventory },
        "GET /api/folders": {
          json: {
            path: "/server",
            parent: "/",
            directories: [{ name: "Projects", path: "/server/Projects" }],
            truncated: false,
          },
        },
      });
      await page.goto("http://admin.test/");
      await visible(page, "[data-panel=providers]");
      for (const panel of ["home", "providers", "runs"]) {
        await page.click(`[data-panel=${panel}]`);
        await audit(page, panel, sink);
      }
      await page.click("[data-panel=providers]");
      await page.click("#manage-network");
      await audit(page, "import/export", sink);
      await page.click("#network-close");
      await page.click("#theme");
      for (const tab of await page
        .locator("#appearance-dialog .config-tabs button")
        .all()) {
        await tab.click();
        await audit(page, "appearance", sink);
      }
      await page.click("#appearance-close");

      await page.click("[data-panel=providers]");
      // DeepSeek is not added yet, so the wizard offers it with its key form.
      await page.click("#add-provider");
      await page.locator("#provider-dialog:not([hidden])").waitFor();
      await audit(page, "add provider", sink);
      await page.click("#provider-options [data-provider=deepseek]");
      await visible(page, "article[data-provider=deepseek]");
      await audit(page, "add DeepSeek", sink);
      await page.keyboard.press("Escape");
      await page.locator("#provider-dialog").waitFor({ state: "hidden" });
      await page
        .locator("[data-configured-provider=local]")
        .getByRole("button", { name: /Edit/ })
        .click();
      for (const tab of await page
        .locator("#inspector-tabs button, #inspector-tabs [role=tab]")
        .all()) {
        await tab.click();
        await audit(page, "local editor", sink);
      }
      await page
        .locator("#inspector-tabs")
        .getByText("Model and hardware")
        .click();
      await page.click("#model-roots-add");
      await visible(page, "#folder-picker");
      await page.waitForFunction(
        () =>
          document.querySelectorAll("#folder-picker-list button").length === 1,
      );
      await audit(page, "folder picker", sink);
      await page.click("#folder-picker-close");
      await page.keyboard.press("Escape");
      await page.locator("#provider-dialog").waitFor({ state: "hidden" });

      assert.deepEqual([...sink.portuguese], []);
      assert.deepEqual([...sink.codes], []);
    },
  },
  {
    title: "H38-S2 100-character model and project names do not clip controls",
    timeout: 8000,
    async run(page) {
      const longModel =
        "gpt-5.6-" +
        "extended-context-research-preview-".repeat(3).slice(0, 92);
      const longProject = (
        "Quarterly finance and compliance review for the northern region " +
        "x".repeat(100)
      ).slice(0, 100);
      assert.equal(longModel.length, 100);
      assert.equal(longProject.length, 100);
      const s = await mockHarness(page, {
        "GET /v1/models": {
          json: {
            models: [{ ...MODELS[0], id: longModel }, MODELS[1]],
            providers: { codex: true, claude: true },
            uploads_enabled: true,
          },
        },
        "GET /v1/projects": {
          json: {
            projects: ["sem-projeto", "long"],
            details: {
              "sem-projeto": { label: "No project" },
              long: { label: longProject },
            },
          },
        },
        "GET /v1/version": { json: { version: "0.6.0-rc.1", build: "b1" } },
      });
      await page.goto("http://harness.test");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
      assert.equal(await page.locator("#model").inputValue(), longModel);

      // A control may truncate, but only with an ellipsis and the full name on
      // hover; it may never grow wider than its container or the window.
      const clipped = () =>
        page.evaluate(() => {
          const out = [];
          for (const el of document.querySelectorAll(
            "button, summary, [role=option], #model-label, #conversation-title",
          )) {
            const r = el.getBoundingClientRect();
            if (!r.width || el.closest("[hidden],dialog:not([open])")) continue;
            const s = getComputedStyle(el);
            if (s.visibility !== "visible") continue;
            const name =
              (el.id ? "#" + el.id : el.tagName.toLowerCase()) +
              " '" +
              el.textContent.trim().slice(0, 24) +
              "'";
            if (r.right > innerWidth + 1) out.push(name + " leaves the window");
            const cut = [el, ...el.querySelectorAll("*")].some(
              (e) =>
                e.scrollWidth > e.clientWidth + 1 &&
                getComputedStyle(e).overflowX !== "visible",
            );
            const ellipsis = [el, ...el.querySelectorAll("*")].some(
              (e) => getComputedStyle(e).textOverflow === "ellipsis",
            );
            // The full name may sit on the control that wraps the label.
            const full = [
              el,
              el.closest("[title]"),
              ...el.querySelectorAll("[title]"),
            ].some(
              (e) =>
                e &&
                (e.getAttribute("title") || e.getAttribute("aria-label") || "")
                  .length >= Math.min(100, el.textContent.replace(/\s+/g, " ").trim().length),
            );
            if (cut && !(ellipsis && full))
              out.push(
                name +
                  (ellipsis
                    ? " has no full-name title"
                    : " is cut without ellipsis"),
              );
          }
          return out;
        });
      assert.deepEqual(await clipped(), [], "composer and sidebar");
      await page.click("#model-trigger");
      await visible(page, "#model-menu");
      assert.deepEqual(await clipped(), [], "model menu");
      await page.keyboard.press("Escape");
      const group = page.locator('#projects [data-project-id="long"]');
      if (!(await page.locator("#project-tree").evaluate((el) => el.open)))
        await page.locator("#project-tree > summary").click();
      await group.locator("summary > button").first().click();
      page.once("dialog", (d) => d.accept());
      await group.locator(".project-new").click();
      await page.waitForFunction(
        () => document.getElementById("project").value === "long",
      );
      assert.deepEqual(await clipped(), [], "long project selected");
      await page.fill("#prompt", "Hello");
      await page.click("#send");
      await page.waitForFunction(() =>
        /Fixture response/.test(document.getElementById("messages").innerText),
      );
      assert.equal(s.posts[0].model, longModel);
      assert.deepEqual(await clipped(), [], "after a turn");

      // Version labels agree: the live label follows /v1/version, and the static
      // fallback in index.html matches the packaged version.
      await page.waitForFunction(() =>
        /0\.6\.0-rc\.1/.test(document.getElementById("version").textContent),
      );
      assert.equal(
        await page.locator("#version").innerText(),
        "Release: 0.6.0-rc.1",
      );
      const packaged = fs
        .readFileSync(path.join(ROOT, "agent_service", "VERSION"), "utf8")
        .trim();
      const pyproject = fs
        .readFileSync(path.join(ROOT, "pyproject.toml"), "utf8")
        .match(/^version = "([^"]+)"/m)[1];
      const fallback = fs
        .readFileSync(path.join(ROOT, "agent_service", "index.html"), "utf8")
        .match(/id="version"[^>]*>Release: ([^<]+)</)[1];
      assert.deepEqual([fallback, pyproject], [packaged, packaged]);
    },
  },
]);
