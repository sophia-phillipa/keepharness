const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

// Admin Plugins page shell (issue #20): Codex Settings > Plugins copy over the
// integration catalog, one row per installed plugin, provider badges, row menu.
const GITHUB = {
  id: "plugin:github@openai",
  name: "GitHub",
  kind: "plugin",
  status: "installed",
  description: "Triage pull requests and issues.",
};
const CATALOGS = {
  codex: {
    items: [
      GITHUB,
      {
        id: "plugin:slack@openai",
        name: "Slack",
        kind: "plugin",
        status: "installed",
        description: "Read channels.",
      },
      {
        id: "plugin:sentry@official",
        name: "Sentry",
        kind: "plugin",
        status: "available",
        description: "Tracks errors.",
      },
      { id: "mcp:drive", name: "Drive", kind: "mcp", status: "installed" },
    ],
    warnings: [],
  },
  claude: {
    items: [
      GITHUB,
      {
        id: "plugin:linear@official",
        name: "Linear",
        kind: "plugin",
        status: "installed",
      },
      {
        id: "account-app:claude.ai Gmail",
        name: "claude.ai Gmail",
        kind: "account-app",
        status: "connected",
      },
    ],
    warnings: [],
  },
};

const channel = (v) =>
  (v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
const luminance = ([r, g, b]) =>
  0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
const contrast = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

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
      added: false,
      enabled: false,
      models: [],
      projects: ["sem-projeto"],
      permissions: {},
      mode: "scoped",
      integrations: [],
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
    const apiCalls = [];
    const posts = [];
    let catalogInFlight = 0,
      maxCatalogInFlight = 0;
    let catalogCalls = 0,
      releaseCatalog = null;
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      const name = url.pathname.replace(/^.*\/api\//, "");
      apiCalls.push(name);
      if (route.request().method() === "POST" && name === "integration") {
        posts.push(route.request().postDataJSON());
        return route.fulfill({ json: { id: "op-1" } });
      }
      if (name === "provider-state") {
        const provider = url.searchParams.get("provider");
        const items = CATALOGS[provider].items
          .filter(
            (item) => item.kind === "plugin" && item.status === "installed",
          )
          .map((item) => ({
            id: item.id,
            name: item.name,
            kind: "plugin",
            scope: "user",
            enabled: true,
            source: provider === "codex" ? "config.toml" : "settings.json",
            writable: true,
            reason: "",
            affects: [],
          }));
        return route.fulfill({
          json: {
            snapshot: {
              provider,
              fingerprint: "fp-" + provider,
              items,
              warnings: [],
            },
            external_changes: [],
          },
        });
      }
      if (name === "integration-catalog") {
        catalogCalls++;
        catalogInFlight++;
        maxCatalogInFlight = Math.max(maxCatalogInFlight, catalogInFlight);
        if (releaseCatalog) await releaseCatalog;
        catalogInFlight--;
        return route.fulfill({
          json: CATALOGS[route.request().postDataJSON().provider],
        });
      }
      return route.fulfill({ json: name === "state" ? state : {} });
    });

    // Placeholder while the catalog is pending; nothing but /api/state and /api/integration-catalog.
    let release;
    releaseCatalog = new Promise((resolve) => (release = resolve));
    await page.goto("http://admin.test/#plugins");
    const panel = page.locator('[data-testid="plugins-panel"]');
    const loading = page.locator('[data-testid="plugins-loading"]');
    await loading.waitFor();
    assert.equal(
      await panel
        .locator('[data-testid="plugins-list"]')
        .getAttribute("aria-busy"),
      "true",
    );
    assert.match(await loading.innerText(), /Loading plugins/);
    release();
    releaseCatalog = null;
    await page.waitForFunction(
      () =>
        document
          .querySelector('[data-testid="plugins-list"]')
          ?.getAttribute("aria-busy") === "false",
    );
    await panel.locator('[data-testid="plugins-mode"]').click();
    const rows = panel.locator('[data-testid="plugin-row"]');
    await rows.first().waitFor();

    assert.equal(await page.locator("#overview h1").innerText(), "Plugins");
    assert.equal(
      await page.locator("#overview .panel-description").innerText(),
      "Manage plugins, skills, and MCPs",
    );
    assert.equal(await page.locator("#dashboard").isHidden(), true);
    assert.equal(await page.locator("#catalog-panel").isHidden(), true);
    assert.equal(
      await page.locator('[data-panel="plugins"]').getAttribute("aria-current"),
      "page",
    );
    assert.deepEqual(
      await panel.locator('[data-testid^="plugins-chip-"]').allInnerTexts(),
      ["Plugins 3", "Apps 1", "MCPs 1", "Skills"],
    );
    assert.equal(
      await panel
        .locator('[data-testid="plugins-chip-plugins"]')
        .getAttribute("aria-pressed"),
      "true",
    );
    // Each chip leads with one icon on the label's line, like the other admin buttons.
    for (const chip of await panel
      .locator('[data-testid^="plugins-chip-"]')
      .all()) {
      assert.equal(await chip.locator("svg").count(), 1, "one icon per chip");
      const [box, glyph] = [
        await chip.boundingBox(),
        await chip.locator("svg").boundingBox(),
      ];
      const label = await chip
        .locator("span")
        .first()
        .evaluate((el) => ({
          height: el.getBoundingClientRect().height,
          lineHeight: parseFloat(getComputedStyle(el).lineHeight),
        }));
      assert(
        label.height < 1.5 * label.lineHeight,
        "chip label stays one line: " +
          label.height +
          " vs line-height " +
          label.lineHeight,
      );
      assert(
        glyph.x - box.x < 24 &&
          Math.abs(glyph.y + glyph.height / 2 - (box.y + box.height / 2)) <= 2,
        "icon sits before the label on its line",
      );
    }
    assert.equal(
      maxCatalogInFlight,
      1,
      "catalogs are read one CLI at a time (the admin answers 429 to a second operation)",
    );
    assert.equal(
      await panel
        .locator('[data-testid="plugins-add"], [aria-label*="Add"]')
        .count(),
      0,
      "no Add menu yet (D-034)",
    );
    assert.equal(
      await panel.locator('[role="switch"]').count(),
      4,
      "management keeps one real provider switch per installed state item",
    );

    // One row per installed plugin, grouped by id, with provider badges.
    assert.deepEqual(
      await rows.locator('[data-testid="plugin-name"]').allInnerTexts(),
      ["GitHub", "Linear", "Slack"],
    );
    const github = rows.filter({ hasText: "GitHub" });
    assert.match(
      await github.locator('[data-testid="plugin-description"]').innerText(),
      /Triage pull requests/,
    );
    assert.deepEqual(await github.locator(".pill").allInnerTexts(), [
      "Codex",
      "Claude Code",
    ]);
    assert.deepEqual(
      await rows.filter({ hasText: "Slack" }).locator(".pill").allInnerTexts(),
      ["Codex"],
    );
    assert.equal(
      await page.getByText("Sentry").count(),
      0,
      "available plugins are not installed rows",
    );

    // The search follows the chip; the other chips have no content yet.
    const search = panel.locator('[data-testid="plugins-search"]');
    await search.fill("slack");
    assert.deepEqual(
      await rows.locator('[data-testid="plugin-name"]').allInnerTexts(),
      ["Slack"],
    );
    await search.fill("zzz");
    assert.match(
      await panel.locator('[data-testid="plugins-empty"]').innerText(),
      /No plugins match/,
    );
    await search.fill("");
    await panel.locator('[data-testid="plugins-chip-apps"]').click();
    assert.equal(
      await panel
        .locator('[data-testid="plugins-chip-apps"]')
        .getAttribute("aria-pressed"),
      "true",
    );
    assert.equal(await rows.count(), 0);
    assert.match(
      await panel.locator('[data-testid="plugins-empty"]').innerText(),
      /Apps are not listed here yet/,
    );
    await panel.locator('[data-testid="plugins-chip-plugins"]').click();
    assert.equal(await rows.count(), 3);

    // Refresh re-fetches both CLIs; the cache serves the first render.
    assert.equal(catalogCalls, 2);
    await panel.locator('[data-testid="plugins-refresh"]').click();
    await page.waitForFunction(
      () =>
        document
          .querySelector('[data-testid="plugins-list"]')
          ?.getAttribute("aria-busy") === "false",
    );
    assert.equal(catalogCalls, 4);
    assert.deepEqual(
      apiCalls.filter(
        (n) => !["state", "integration-catalog", "provider-state"].includes(n),
      ),
      [],
      "rendering requests only /api/state, /api/integration-catalog and /api/provider-state (no dashboard polling)",
    );
    assert.equal(
      apiCalls.filter((n) => n === "provider-state").length,
      4,
      "one state read per CLI on entry and again on Refresh",
    );
    await page.waitForTimeout(3500);
    assert.equal(
      apiCalls.includes("dashboard"),
      false,
      "the 3 s dashboard poll skips #plugins",
    );
    await page.locator("[data-panel=home]").click();
    await page.locator("[data-panel=plugins]").click();
    await rows.first().waitFor();
    assert.equal(
      catalogCalls,
      4,
      "returning to the page reuses the integrationCatalogs cache",
    );

    // Row menu: Escape closes it and returns focus; Uninstall confirms and posts.
    const menuButton = github.locator('[data-testid="plugin-menu-button"]');
    assert.equal(await menuButton.getAttribute("aria-label"), "GitHub actions");
    await menuButton.click();
    const menu = github.locator('[data-testid="plugin-menu"]');
    assert.deepEqual(await menu.getByRole("menuitem").allInnerTexts(), [
      "Uninstall from Codex",
      "Uninstall from Claude Code",
    ]);
    await page.keyboard.press("Escape");
    assert.equal(await menu.count(), 0);
    assert.equal(
      await menuButton.evaluate((el) => el === document.activeElement),
      true,
      "focus returns to the menu button",
    );
    await menuButton.click();
    page.once("dialog", (dialog) => dialog.dismiss());
    await menu
      .getByRole("menuitem", { name: "Uninstall from Claude Code" })
      .click();
    assert.deepEqual(posts, [], "a dismissed confirmation posts nothing");
    const slack = rows.filter({ hasText: "Slack" });
    await slack.locator('[data-testid="plugin-menu-button"]').click();
    page.once("dialog", (dialog) => {
      assert.match(dialog.message(), /Slack/);
      return dialog.accept();
    });
    await slack
      .getByRole("menuitem", { name: "Uninstall", exact: true })
      .click();
    await page.waitForFunction(
      () => document.querySelector("#operation-dialog")?.open,
    );
    assert.deepEqual(posts, [
      { provider: "codex", action: "plugin_remove", name: "slack@openai" },
    ]);
    await page.waitForFunction(() => !integrationCatalogs.has("codex"), null, {
      timeout: 5000,
    });
    await page.evaluate(() =>
      document.querySelector("#operation-dialog").close(),
    );
    await page.locator("[data-panel=home]").click();
    await page.locator("[data-panel=plugins]").click();
    for (let tries = 0; tries < 50 && catalogCalls < 5; tries++)
      await page.waitForTimeout(100);
    await page.waitForFunction(
      () =>
        document
          .querySelector('[data-testid="plugins-list"]')
          ?.getAttribute("aria-busy") === "false",
    );
    assert.equal(
      catalogCalls,
      5,
      "after an uninstall only that provider catalog is read again, got " +
        catalogCalls,
    );

    // Leaving the page closes an open row menu.
    await github.locator('[data-testid="plugin-menu-button"]').click();
    await page.locator("[data-panel=home]").click();
    await page.locator("[data-panel=plugins]").click();
    assert.equal(await page.locator('[data-testid="plugin-menu"]').count(), 0);

    // A Providers-page catalog read in flight holds back the Plugins page loads (one operation at a time).
    maxCatalogInFlight = 0;
    await page.evaluate(() => integrationCatalogs.clear());
    await page.locator("[data-panel=providers]").click();
    releaseCatalog = new Promise((resolve) => (release = resolve));
    await page.evaluate(() => {
      $("integration-provider").value = "codex";
      loadCatalog();
    });
    await page.waitForFunction(() => catalogPending.has("codex"));
    await page.locator("[data-panel=plugins]").click();
    await page.waitForTimeout(500);
    assert.equal(
      catalogInFlight,
      1,
      "Plugins waits for the Providers catalog read",
    );
    release();
    releaseCatalog = null;
    await page.waitForFunction(
      () =>
        document
          .querySelector('[data-testid="plugins-list"]')
          ?.getAttribute("aria-busy") === "false",
    );
    assert.equal(
      maxCatalogInFlight,
      1,
      "Providers and Plugins never have two catalog requests in flight",
    );
    assert.equal(
      await page.evaluate(() => integrationCatalogs.has("claude")),
      true,
      "Plugins still loaded the Claude catalog",
    );

    // Contrast >= 4.5 on a light (paper) and a dark (graphite) palette.
    const ratios = {};
    for (const palette of ["paper", "graphite"]) {
      ratios[palette] = await page.evaluate((id) => {
        HarnessTheme.apply(id, false);
        // Measure the settled palette, not a colour transition still running from the previous one.
        document.getAnimations().forEach((animation) => animation.finish());
        const ctx = document.createElement("canvas").getContext("2d");
        const rgba = (css) => {
          ctx.clearRect(0, 0, 1, 1);
          ctx.fillStyle = css;
          ctx.fillRect(0, 0, 1, 1);
          const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
          return [r, g, b, a / 255];
        };
        const over = (top, bottom) =>
          top.slice(0, 3).map((v, i) => v * top[3] + bottom[i] * (1 - top[3]));
        const background = (el) => {
          const stack = [];
          for (let node = el; node; node = node.parentElement)
            stack.push(rgba(getComputedStyle(node).backgroundColor));
          return stack
            .reverse()
            .reduce((acc, layer) => over(layer, acc), [255, 255, 255]);
        };
        const channel = (v) =>
          (v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
        const lum = ([r, g, b]) =>
          0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
        const panel = document.querySelector('[data-testid="plugins-panel"]');
        const pairs = [
          '[data-testid="plugin-name"]',
          '[data-testid="plugin-description"]',
          ".pill",
          '[data-testid="plugins-chip-plugins"]',
          '[data-testid="plugins-chip-apps"]',
          '[data-testid="plugins-search"]',
          '[data-testid="plugins-refresh"]',
          '[data-testid="plugin-menu-button"]',
        ].map((selector) => {
          const el = panel.querySelector(selector);
          const bg = background(el);
          const fg = over(rgba(getComputedStyle(el).color), bg);
          const [hi, lo] = [lum(fg), lum(bg)].sort((x, y) => y - x);
          return [
            selector,
            (hi + 0.05) / (lo + 0.05),
            fg.map(Math.round).join(","),
            bg.map(Math.round).join(","),
            getComputedStyle(el).color,
            getComputedStyle(el).backgroundColor,
          ];
        });
        return pairs;
      }, palette);
      for (const [selector, ratio, fg, bg] of ratios[palette])
        assert(
          ratio >= 4.5,
          palette +
            " " +
            selector +
            " contrast " +
            ratio +
            " (text " +
            fg +
            " on " +
            bg +
            ")",
        );
    }
    console.log("plugins text contrast", JSON.stringify(ratios));
    await page.evaluate(() => HarnessTheme.apply("paper", false));

    // No horizontal overflow at the admin minimum width or on a phone.
    for (const width of [900, 390]) {
      await page.setViewportSize({ width, height: 900 });
      const overflow = await page.evaluate(
        () =>
          document.documentElement.scrollWidth -
          document.documentElement.clientWidth,
      );
      assert(overflow <= 0, width + "px overflow " + overflow);
    }

    assert.deepEqual(errors, []);
    console.log("admin plugins page checks passed");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
