const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

// Admin Plugins page chips for Apps, MCPs and Skills (issues #22 and #23). Rows come from
// GET /api/provider-state per project; every switch writes through POST /api/provider-state with
// that provider's fingerprint. The API is mocked here with fake paths only (/fake-home); the
// fake-home writes behind these posts are asserted in tests/test_provider_chip_writes.py.
const CATALOGS = {
  codex: { items: [], warnings: [] },
  claude: { items: [], warnings: [] },
};
const SHARED_NOTE =
  "Shared Skills root: /fake-home/.agents/skills. Content changes affect other providers using this root; this switch changes only this provider's config.";
const item = (id, kind, name, extra) => ({
  id: id,
  kind,
  name,
  scope: "user",
  enabled: true,
  source: "config.toml",
  writable: true,
  reason: "",
  affects: [],
  ...extra,
});
const snapshot = (provider, project, items) => ({
  provider,
  engine: provider,
  project_root: project === "alpha" ? "/fake-home/alpha" : null,
  items,
  fingerprint: "fp-" + provider + "|" + project + "-0",
  cli_version: "1.0.0",
  warnings: [],
});

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
    });
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
    const service = () => ({
      added: true,
      enabled: true,
      models: ["m"],
      projects: ["sem-projeto", "alpha"],
      mode: "native",
      integrations: [],
      permissions: {},
    });
    const state = {
      settings: {
        services: { codex: service(), claude: service() },
        projects: [{ id: "alpha", label: "Alpha", root: "/fake-home/alpha" }],
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
        projects: [{ id: "alpha", label: "Alpha", root: "/fake-home/alpha" }],
        network: { online: true },
      },
      authentication: {},
      models: {},
      integrations: { codex: [], claude: [] },
      operations: [],
      credentials: {},
      status: { running: false },
    };
    // What each CLI really holds, per project. A write flips the item and bumps the fingerprint.
    const server = {
      "codex|sem-projeto": snapshot("codex", "sem-projeto", [
        item("mcp:figma", "mcp", "figma"),
        item("app:mail", "app", "Mail"),
        item("skill:/fake-home/.codex/skills/lint/SKILL.md", "skill", "lint", {
          source: "/fake-home/.codex/skills/lint/SKILL.md",
        }),
        item(
          "skill:/fake-home/.agents/skills/shared/SKILL.md",
          "skill",
          "shared",
          {
            source: "/fake-home/.agents/skills/shared/SKILL.md",
            reason: SHARED_NOTE,
            affects: ["claude"],
          },
        ),
      ]),
      "codex|alpha": snapshot("codex", "alpha", []),
      "claude|sem-projeto": snapshot("claude", "sem-projeto", [
        item("mcp:linear", "mcp", "linear", { source: ".claude.json" }),
        item("skill:tool:review", "skill", "review", {
          source: "settings.json",
          writable: false,
          reason: "Part of plugin tool@mk",
          plugin: "tool@mk",
        }),
        item("skill:tool:bundled", "skill", "bundled", {
          source: "settings.json",
          writable: false,
          reason: "Shipped inside the tool@mk package",
          plugin: "tool@mk",
        }),
        item(
          "skill:/fake-home/.claude/skills/notes/SKILL.md",
          "skill",
          "notes",
          {
            source: "/fake-home/.claude/skills/notes/SKILL.md",
          },
        ),
      ]),
      "claude|alpha": snapshot("claude", "alpha", [
        item("mcp:docs", "mcp", "docs", {
          scope: "project",
          source: ".mcp.json",
        }),
        item(
          "skill:/fake-home/alpha/.claude/skills/deploy/SKILL.md",
          "skill",
          "deploy",
          {
            scope: "project",
            source: "/fake-home/alpha/.claude/skills/deploy/SKILL.md",
          },
        ),
      ]),
    };
    const posts = [],
      gets = [];
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      const name = url.pathname.replace(/^.*\/api\//, "");
      const method = route.request().method();
      if (name === "provider-state" && method === "GET") {
        const key =
          url.searchParams.get("provider") +
          "|" +
          url.searchParams.get("project_id");
        gets.push(url.search);
        return route.fulfill({
          json: { snapshot: server[key], external_changes: [] },
        });
      }
      if (name === "provider-state" && method === "POST") {
        const body = route.request().postDataJSON();
        posts.push(body);
        const key = body.provider + "|" + body.project_id;
        const current = server[key];
        const found = current.items.find((x) => x.id === body.item_id);
        found.enabled = body.enabled;
        current.fingerprint = "fp-" + key + "-" + posts.length;
        return route.fulfill({ json: { snapshot: current } });
      }
      if (name === "integration-catalog")
        return route.fulfill({
          json: CATALOGS[route.request().postDataJSON().provider],
        });
      return route.fulfill({ json: name === "state" ? state : {} });
    });

    await page.goto("http://admin.test/#plugins");
    const rows = page.locator('[data-testid="plugin-row"]');
    const row = (name) =>
      rows.filter({
        has: page.locator('[data-testid="plugin-name"]', {
          hasText: new RegExp("^" + name + "$", "i"),
        }),
      });
    const sw = (name, cli) =>
      page.getByRole("switch", {
        name: new RegExp("^" + name + " in " + cli + "$", "i"),
      });
    const on = async (name, cli) =>
      (await sw(name, cli).getAttribute("aria-checked")) === "true";
    const settle = () =>
      page.waitForFunction(() => !document.body.hasAttribute("aria-busy"));
    const fingerprint = (key) => server[key].fingerprint;
    const chip = (id) =>
      page.locator('[data-testid="plugins-chip-' + id + '"]');

    // Apps: the Codex app is listed and switches; Claude has no app kind, so no Claude switch.
    await chip("apps").click();
    await row("Mail").waitFor();
    assert.equal(await on("Mail", "Codex"), true);
    assert.equal(
      await page.getByRole("switch", { name: /in Claude Code$/ }).count(),
      0,
      "Claude has no app kind",
    );
    const mailFp = fingerprint("codex|sem-projeto");
    await sw("Mail", "Codex").click();
    await settle();
    assert.deepEqual(posts.at(-1), {
      provider: "codex",
      project_id: "sem-projeto",
      item_id: "app:mail",
      scope: "user",
      enabled: false,
      fingerprint: mailFp,
    });
    assert.equal(await on("Mail", "Codex"), false);

    // MCPs, Codex: the user-layer server switches through provider-state.
    await chip("mcps").click();
    await row("figma").waitFor();
    await sw("figma", "Codex").click();
    await settle();
    assert.equal(posts.at(-1).item_id, "mcp:figma");
    assert.equal(posts.at(-1).provider, "codex");
    assert.equal(await on("figma", "Codex"), false);

    // MCPs, Claude: per project. The No project list has the user server only.
    assert.equal(await row("docs").count(), 0);
    await row("linear").waitFor();
    assert.equal(await on("linear", "Claude Code"), true);

    // Skills, No project: User tab with a user skill switch, and a plugin skill with no switch.
    await chip("skills").click();
    assert.equal(
      await page
        .getByRole("tab", { name: "User", exact: true })
        .getAttribute("aria-selected"),
      "true",
    );
    await row("lint").waitFor();
    assert.equal(await row("deploy").count(), 0);
    const lintFp = fingerprint("codex|sem-projeto");
    await sw("lint", "Codex").click();
    await settle();
    assert.deepEqual(posts.at(-1), {
      provider: "codex",
      project_id: "sem-projeto",
      item_id: "skill:/fake-home/.codex/skills/lint/SKILL.md",
      scope: "user",
      enabled: false,
      fingerprint: lintFp,
    });
    await row("review").waitFor();
    assert.match(await row("review").innerText(), /Part of plugin tool@mk/);
    assert.equal(await row("review").getByRole("switch").count(), 0);
    await row("bundled").waitFor();
    assert.match(
      await row("bundled").innerText(),
      /Shipped inside the tool@mk package/,
    );
    assert.equal(await row("bundled").getByRole("switch").count(), 0);
    assert.match(
      await row("shared").innerText(),
      /Content changes affect other providers using this root/,
    );

    // Project: Claude MCP and project skill switches follow the selected project.
    await page
      .getByRole("combobox", { name: "Project for plugins" })
      .selectOption("alpha");
    await chip("mcps").click();
    await row("docs").waitFor();
    assert.equal(await row("linear").count(), 0);
    const docsFp = fingerprint("claude|alpha");
    await sw("docs", "Claude Code").click();
    await settle();
    assert.deepEqual(posts.at(-1), {
      provider: "claude",
      project_id: "alpha",
      item_id: "mcp:docs",
      scope: "project",
      enabled: false,
      fingerprint: docsFp,
    });
    assert.equal(await on("docs", "Claude Code"), false);

    await chip("skills").click();
    await page.getByRole("tab", { name: "Project", exact: true }).click();
    await row("deploy").waitFor();
    await sw("deploy", "Claude Code").click();
    await settle();
    assert.equal(posts.at(-1).project_id, "alpha");
    assert.equal(posts.at(-1).scope, "project");
    assert.equal(
      posts.at(-1).item_id,
      "skill:/fake-home/alpha/.claude/skills/deploy/SKILL.md",
    );
    assert.equal(await on("deploy", "Claude Code"), false);

    // Keyboard: arrows, Home and End move the selected tab; the list is its tab panel.
    const tab = (name) => page.getByRole("tab", { name, exact: true });
    const panel = page.locator("#plugins-list");
    assert.equal(await panel.getAttribute("role"), "tabpanel");
    assert.equal(
      await panel.getAttribute("aria-labelledby"),
      "plugins-scope-tab-project",
    );
    await tab("Project").focus();
    await page.keyboard.press("ArrowRight");
    assert.equal(await tab("User").getAttribute("aria-selected"), "true");
    assert.equal(
      await tab("User").evaluate((n) => n === document.activeElement),
      true,
    );
    assert.equal(await row("deploy").count(), 0);
    await page.keyboard.press("End");
    assert.equal(await tab("Project").getAttribute("aria-selected"), "true");
    await page.keyboard.press("Home");
    assert.equal(await tab("User").getAttribute("aria-selected"), "true");
    assert.equal(
      await tab("User").getAttribute("aria-controls"),
      "plugins-list",
    );

    assert.deepEqual(errors, []);
    console.log("PASS admin provider chips");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
