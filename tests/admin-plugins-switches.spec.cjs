const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

// Plugins page row switches (issue #21, D-041): each provider pill on a row has its own switch that
// writes the CLI's real state through POST /api/provider-state with that provider's fingerprint.
const item = (id, name) => ({ id, name, kind: "plugin", status: "installed" });
const GITHUB = item("plugin:github@openai-curated", "GitHub");
const SLACK = item("plugin:slack@openai-curated", "Slack");
const LINEAR = item("plugin:linear@official", "Linear");
const NOTION = {
  ...item("plugin:notion@official", "Notion"),
  description: "Notes and docs.",
};
const CATALOGS = {
  codex: { items: [GITHUB, SLACK], warnings: [] },
  claude: { items: [GITHUB, LINEAR, NOTION], warnings: [] },
};
const stateItem = (id, name, extra) => ({
  id,
  kind: "plugin",
  name,
  scope: "user",
  enabled: true,
  source: "config.toml",
  writable: true,
  reason: "",
  affects: [],
  ...extra,
});
const snapshot = (provider, items, fingerprint) => ({
  provider,
  engine: provider,
  project_root: null,
  items,
  fingerprint,
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
      projects: ["sem-projeto"],
      mode: "native",
      integrations: [],
      permissions: {},
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
          { id: "gemini", name: "Gemini CLI", found: false },
        ],
        projects: [],
        network: { online: true },
      },
      authentication: {},
      models: {},
      integrations: { codex: [], claude: [] },
      operations: [],
      credentials: {},
      status: { running: false },
    };
    // The fake provider state: what each CLI really holds. A write bumps the fingerprint.
    const server = {
      codex: snapshot(
        "codex",
        [
          stateItem(GITHUB.id, "github"),
          stateItem(SLACK.id, "slack", {
            scope: "project",
            source: ".codex/config.toml",
            writable: false,
            reason: "The project layer is read-only in Codex.",
          }),
          stateItem("plugin:figma@openai-curated", "figma", { enabled: false }), // in the CLI, not in the catalog
          stateItem(NOTION.id, "notion"), // in the Codex snapshot only; the Claude catalog describes it
        ],
        "fp-codex-1",
      ),
      claude: snapshot(
        "claude",
        [
          stateItem(GITHUB.id, "github", {
            enabled: false,
            source: "settings.json",
          }),
        ],
        "fp-claude-1",
      ),
    };
    let readError = null,
      version = 1;
    const posts = [],
      gets = [],
      settingsCalls = [];
    const next = []; // queued write outcomes: { status, json } or a function run before answering
    let inFlight = 0,
      maxInFlight = 0;
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      const name = url.pathname.replace(/^.*\/api\//, "");
      const method = route.request().method();
      if (name === "settings") settingsCalls.push(method);
      if (name === "provider-state" && method === "GET") {
        const provider = url.searchParams.get("provider");
        gets.push(url.search);
        if (readError?.[provider]) return route.fulfill(readError[provider]);
        return route.fulfill({
          json: { snapshot: server[provider], external_changes: [] },
        });
      }
      if (name === "provider-state" && method === "POST") {
        const body = route.request().postDataJSON();
        posts.push(body);
        inFlight++;
        maxInFlight = Math.max(maxInFlight, inFlight);
        await new Promise((resolve) => setTimeout(resolve, 80));
        inFlight--;
        const outcome = next.shift();
        if (outcome) return route.fulfill(outcome());
        const current = server[body.provider];
        const found = current.items.find((x) => x.id === body.item_id);
        found.enabled = body.enabled;
        current.fingerprint = "fp-" + body.provider + "-" + ++version;
        return route.fulfill({ json: { snapshot: current } });
      }
      if (name === "integration-catalog")
        return route.fulfill({
          json: CATALOGS[route.request().postDataJSON().provider],
        });
      return route.fulfill({ json: name === "state" ? state : {} });
    });

    await page.goto("http://admin.test/#plugins");
    await page.locator('[data-testid="plugins-mode"]').click();
    const rows = page.locator('[data-testid="plugin-row"]');
    await rows.first().waitFor();
    const row = (name) =>
      rows.filter({
        has: page.locator('[data-testid="plugin-name"]', {
          hasText: new RegExp("^" + name + "$"),
        }),
      });
    const sw = (name, cli) =>
      page.getByRole("switch", { name: name + " in " + cli, exact: true });
    const on = async (name, cli) =>
      (await sw(name, cli).getAttribute("aria-checked")) === "true";
    const status = page.locator('[data-testid="plugins-status"]');
    const notes = (name) =>
      row(name).locator('[data-testid="plugin-note"]').allInnerTexts();

    // The page asked each CLI for its state, for the No project scope, in parallel.
    assert.deepEqual(gets.sort(), [
      "?provider=claude&project_id=sem-projeto",
      "?provider=codex&project_id=sem-projeto",
    ]);

    // Rows: catalog rows plus the plugin only the CLI loads; one switch per provider that has the item.
    assert.deepEqual(
      await rows.locator('[data-testid="plugin-name"]').allInnerTexts(),
      ["Figma", "GitHub", "Linear", "Notion", "Slack"],
    );
    // A plugin only the Codex snapshot lists, but the Claude catalog describes, keeps the catalog's description.
    assert.equal(
      await row("Notion")
        .locator('[data-testid="plugin-description"]')
        .innerText(),
      "Notes and docs.",
    );
    assert.equal(
      await row("GitHub").getByRole("switch").count(),
      2,
      "two providers on one row get two switches",
    );
    assert.equal(await on("GitHub", "Codex"), true);
    assert.equal(await on("GitHub", "Claude Code"), false);
    assert.equal(
      await sw("GitHub", "Codex").getAttribute("data-provider"),
      "codex",
    );
    assert.equal(
      await sw("GitHub", "Codex").getAttribute("data-item-id"),
      GITHUB.id,
    );
    assert.equal(
      await sw("Figma", "Codex").count(),
      1,
      "snapshot-only plugin appears as a row with a switch",
    );
    assert.equal(await on("Figma", "Codex"), false);

    // Scope, deciding file and the reason; a read-only layer disables the switch and the reason describes it.
    assert.deepEqual(await notes("GitHub"), [
      "Codex: User · config.toml",
      "Claude Code: User · settings.json",
    ]);
    assert.equal(await sw("Slack", "Codex").isDisabled(), true);
    const slackNote = await sw("Slack", "Codex").getAttribute(
      "aria-describedby",
    );
    assert.equal(
      await page.locator("#" + slackNote).innerText(),
      "Codex: Project · .codex/config.toml · The project layer is read-only in Codex.",
    );
    assert.equal(await sw("GitHub", "Codex").isDisabled(), false);

    // A catalog-only item has no switch and says so.
    assert.equal(
      await row("Linear").getByRole("switch").count(),
      0,
      "catalog-only row has no switch",
    );
    assert.deepEqual(await notes("Linear"), [
      "Claude Code: The catalog reports this plugin as installed, but provider state did not return a matching item.",
    ]);

    // Toggle: one POST with that provider's fingerprint, the row follows the returned snapshot.
    await sw("GitHub", "Codex").click();
    await page.waitForFunction(
      () =>
        document
          .querySelector(
            '[data-provider="codex"][data-item-id="plugin:github@openai-curated"]',
          )
          .getAttribute("aria-checked") === "false",
    );
    assert.deepEqual(posts, [
      {
        provider: "codex",
        project_id: "sem-projeto",
        item_id: GITHUB.id,
        scope: "user",
        enabled: false,
        fingerprint: "fp-codex-1",
      },
    ]);
    assert.equal(await status.innerText(), "GitHub is now off in Codex.");
    assert.equal(
      await on("GitHub", "Claude Code"),
      false,
      "the other provider's switch is untouched",
    );
    assert.equal(
      await sw("GitHub", "Codex").evaluate(
        (el) => el === document.activeElement,
      ),
      true,
      "focus returns to the switch",
    );

    // The second provider on the row writes with its own fingerprint, never to /api/settings.
    await sw("GitHub", "Claude Code").click();
    await page.waitForFunction(
      () =>
        document
          .querySelector(
            '[data-provider="claude"][data-item-id="plugin:github@openai-curated"]',
          )
          .getAttribute("aria-checked") === "true",
    );
    assert.deepEqual(posts[1], {
      provider: "claude",
      project_id: "sem-projeto",
      item_id: GITHUB.id,
      scope: "user",
      enabled: true,
      fingerprint: "fp-claude-1",
    });
    assert.equal(posts.length, 2);
    assert.deepEqual(
      settingsCalls,
      [],
      "switches never write to /api/settings",
    );

    // A click that arrives while another admin operation runs is ignored and leaves the status line alone.
    await page.evaluate(() => {
      working = true;
    });
    await sw("GitHub", "Codex").dispatchEvent("click");
    await page.evaluate(() => {
      working = false;
    });
    assert.equal(posts.length, 2, "no write while another operation runs");
    assert.equal(
      await status.innerText(),
      "GitHub is now on in Claude Code.",
      "the status line is not cleared by an ignored click",
    );

    // No concurrent posts under a double click.
    await sw("Figma", "Codex").dblclick();
    await page.waitForFunction(
      () =>
        document
          .querySelector(
            '[data-provider="codex"][data-item-id="plugin:figma@openai-curated"]',
          )
          .getAttribute("aria-checked") === "true",
    );
    assert.equal(maxInFlight, 1);
    assert.equal(posts.length, 3, "a double click writes once");
    assert.equal(
      posts[2].fingerprint,
      "fp-codex-2",
      "the fingerprint of the last answer",
    );

    // 409: the CLI changed behind the page. Rows reload from the returned snapshot and the status says what changed.
    next.push(() => {
      server.claude.items[0].enabled = false;
      server.claude.fingerprint = "fp-claude-ext";
      return {
        status: 409,
        json: {
          error: "provider_state_conflict",
          snapshot: server.claude,
          external_changes: [],
        },
      };
    });
    await sw("GitHub", "Claude Code").click();
    await page.waitForFunction(
      () =>
        document
          .querySelector(
            '[data-provider="claude"][data-item-id="plugin:github@openai-curated"]',
          )
          .getAttribute("aria-checked") === "false",
    );
    assert.equal(
      await status.innerText(),
      "Claude Code changed since this page loaded: GitHub is now off. Try again.",
    );
    // The next write uses the fingerprint from the 409 body.
    await sw("GitHub", "Claude Code").click();
    await page.waitForFunction(
      () =>
        document
          .querySelector(
            '[data-provider="claude"][data-item-id="plugin:github@openai-curated"]',
          )
          .getAttribute("aria-checked") === "true",
    );
    assert.equal(posts.at(-1).fingerprint, "fp-claude-ext");
    // 409 where the item disappeared: the snapshot-only row goes away.
    next.push(() => {
      server.codex.items = server.codex.items.filter(
        (x) => !x.id.includes("figma"),
      );
      server.codex.fingerprint = "fp-codex-ext";
      return {
        status: 409,
        json: {
          error: "provider_state_conflict",
          snapshot: server.codex,
          external_changes: [],
        },
      };
    });
    await sw("Figma", "Codex").click();
    await row("Figma").waitFor({ state: "detached" });
    assert.equal(
      await status.innerText(),
      "Codex changed since this page loaded: Figma was removed. Try again.",
    );
    assert.equal(
      await page.evaluate(
        () =>
          document.activeElement ===
          document.querySelector('[data-testid="plugins-list"]'),
      ),
      true,
      "focus falls back to the list when the row is gone",
    );
    // 409 where the switch is now read-only: focus moves to the row's menu button instead of <body>.
    next.push(() => {
      server.codex.items[0].writable = false;
      server.codex.fingerprint = "fp-codex-ro";
      return {
        status: 409,
        json: {
          error: "provider_state_conflict",
          snapshot: server.codex,
          external_changes: [],
        },
      };
    });
    await sw("GitHub", "Codex").click();
    await page.waitForFunction(
      () =>
        document.querySelector(
          '[data-provider="codex"][data-item-id="plugin:github@openai-curated"]',
        )?.disabled === true,
    );
    await page.waitForFunction(() => !document.body.hasAttribute("aria-busy")); // the admin lock is released
    assert.equal(
      await page
        .getByRole("button", { name: "GitHub actions" })
        .evaluate((el) => el === document.activeElement),
      true,
    );
    server.codex.items[0].writable = true;
    await page.getByRole("button", { name: "Refresh" }).click();
    await page.waitForFunction(
      () =>
        document.querySelector(
          '[data-provider="codex"][data-item-id="plugin:github@openai-curated"]',
        )?.disabled === false,
    );

    // 422 and 502: the message shows as text on the row and in the status, the switch keeps the server value.
    next.push(() => ({
      status: 422,
      json: {
        error: "provider_state_write_unsupported",
        message: "<b>Managed</b> by a profile.",
      },
    }));
    await sw("GitHub", "Codex").click();
    await page.waitForFunction(() =>
      /Managed/.test(
        document.querySelector('[data-testid="plugins-status"]').textContent,
      ),
    );
    assert.equal(
      await on("GitHub", "Codex"),
      false,
      "switch unchanged after 422",
    );
    assert.match(await status.innerText(), /<b>Managed<\/b> by a profile\./);
    assert.match((await notes("GitHub"))[0], /<b>Managed<\/b> by a profile\./);
    assert.equal(
      await row("GitHub").locator("b").count(),
      0,
      "server text is rendered as text, never as HTML",
    );
    assert.equal(
      await sw("GitHub", "Codex").evaluate(
        (el) => el === document.activeElement,
      ),
      true,
    );
    next.push(() => ({
      status: 502,
      json: {
        error: "provider_command_failed",
        provider_message: "codex plugin disable failed",
      },
    }));
    await sw("GitHub", "Claude Code").click();
    await page.waitForFunction(() =>
      /disable failed/.test(
        document.querySelector('[data-testid="plugins-status"]').textContent,
      ),
    );
    assert.equal(
      await on("GitHub", "Claude Code"),
      true,
      "switch unchanged after 502",
    );
    assert.match((await notes("GitHub"))[1], /codex plugin disable failed/);

    // A provider whose state cannot be read (DeepSeek-style 404): reason on its pills, no switch, others unaffected.
    readError = {
      claude: { status: 404, json: { error: "provider_unknown" } },
    };
    await page.reload();
    await page.locator('[data-testid="plugins-mode"]').click();
    await rows.first().waitFor();
    await page.waitForFunction(
      () => document.querySelectorAll('[data-testid="plugin-note"]').length > 0,
    );
    assert.equal(await row("Linear").getByRole("switch").count(), 0);
    assert.equal(
      await row("GitHub").getByRole("switch").count(),
      1,
      "only Codex keeps its switch",
    );
    assert.match(
      (await notes("GitHub"))[1],
      /^Claude Code: .*no state adapter/i,
    );
    assert.match((await notes("Linear"))[0], /no state adapter/i);

    // An unreadable state keeps the reason too.
    readError = {
      codex: {
        status: 422,
        json: {
          error: "provider_state_unreadable",
          message: "config.toml is not valid TOML.",
        },
      },
    };
    await page.reload();
    await page.locator('[data-testid="plugins-mode"]').click();
    await rows.first().waitFor();
    await page.waitForFunction(
      () => document.querySelectorAll('[data-testid="plugin-note"]').length > 0,
    );
    assert.match((await notes("Slack"))[0], /config\.toml is not valid TOML\./);
    assert.equal(await sw("Slack", "Codex").count(), 0);

    assert.deepEqual(settingsCalls, []);
    // DeepSeek rows remain independent of their Codex engine.
    readError = null;
    state.inventory.services.push({
      id: "deepseek",
      name: "DeepSeek",
      found: true,
    });
    server.deepseek = {
      ...snapshot(
        "deepseek",
        [
          stateItem("plugin:private", "Private"),
          stateItem("skill:shared", "Shared Skill", {
            kind: "skill",
            reason:
              "Shared Skills root: /private/.agents/skills. Content changes affect other providers using this root; this switch changes only this provider's config.",
          }),
          stateItem("app:mail", "Private Mail", {
            kind: "app",
            scope: "profile",
            writable: false,
            reason: "Profile app.",
          }),
          stateItem("hook:check", "Check Hook", {
            kind: "hook",
            scope: "profile",
            writable: false,
            reason: "Read-only hook.",
          }),
          stateItem("instructions:guide", "Project Guide", {
            kind: "instructions",
            writable: false,
            reason: "Read-only instructions.",
          }),
        ],
        "fp-deepseek-1",
      ),
      engine: "codex",
    };
    await page.reload();
    await page.locator('[data-testid="plugins-mode"]').click();
    await sw("Private", "DeepSeek").waitFor();
    await sw("Private", "DeepSeek").click();
    await page.waitForFunction(() => !document.body.hasAttribute("aria-busy"));
    assert.equal(posts.at(-1).provider, "deepseek");
    assert.equal(posts.at(-1).fingerprint, "fp-deepseek-1");
    await page.locator('[data-testid="plugins-chip-skills"]').click();
    assert.match(
      await row("Shared Skill").innerText(),
      /Content changes affect other providers using this root/,
    );
    assert.equal(await sw("Shared Skill", "DeepSeek").isEnabled(), true);
    await page.locator('[data-testid="plugins-chip-apps"]').click();
    assert.match(await row("Private Mail").innerText(), /Profile/);
    assert.equal(await sw("Private Mail", "DeepSeek").isEnabled(), false);
    await page.locator('[data-testid="plugins-chip-hooks"]').click();
    assert.match(await row("Check Hook").innerText(), /Profile/);
    assert.equal(await sw("Check Hook", "DeepSeek").isEnabled(), false);
    await page.locator('[data-testid="plugins-chip-instructions"]').click();
    assert.equal(await sw("Project Guide", "DeepSeek").isEnabled(), false);
    assert.equal(
      await row("Project Guide")
        .getByRole("button", { name: "Project Guide actions" })
        .isEnabled(),
      false,
    );
    assert.deepEqual(errors, []);
    console.log("PASS admin plugins switches");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
