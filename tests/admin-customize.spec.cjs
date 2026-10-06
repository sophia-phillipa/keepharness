const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

// Admin Plugins page shell (issue #20): Codex Settings > Plugins copy over the
// integration catalog, one row per installed plugin, provider badges, row menu.
const GITHUB = { id: "plugin:github@openai", name: "GitHub", kind: "plugin", status: "installed", description: "Triage pull requests and issues." };
const CATALOGS = {
  codex: {
    items: [
      GITHUB,
      { id: "plugin:slack@openai", name: "Slack", kind: "plugin", status: "installed", description: "Read channels." },
      { id: "plugin:sentry@official", name: "Sentry", kind: "plugin", status: "available", description: "Tracks errors." },
      { id: "mcp:drive", name: "Drive", kind: "mcp", status: "installed" },
    ],
    warnings: [],
  },
  claude: {
    items: [
      GITHUB,
      { id: "plugin:linear@official", name: "Linear", kind: "plugin", status: "installed" },
      { id: "account-app:claude.ai Gmail", name: "claude.ai Gmail", kind: "account-app", status: "connected" },
    ],
    warnings: [],
  },
};

const channel = (v) => ((v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
const luminance = ([r, g, b]) => 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
const contrast = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    await page.route("http://admin.test/**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith("/api/")) return route.fallback();
      const file = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
      return route.fulfill({
        body: await fs.readFile(path.join(__dirname, file.startsWith("assets/") ? "../harness_ui" : "../control", file)),
        contentType: file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : file.endsWith(".svg") ? "image/svg+xml" : "text/html",
      });
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const service = () => ({ added: false, enabled: false, models: [], projects: ["sem-projeto"], permissions: {}, mode: "scoped", integrations: [] });
    const state = {
      settings: { services: { codex: service(), claude: service() }, projects: [], logins: [], port: 8095, tailnet_port: 8095, uploads_enabled: false },
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
      if (name === "integration-catalog") {
        catalogCalls++;
        if (releaseCatalog) await releaseCatalog;
        return route.fulfill({ json: CATALOGS[route.request().postDataJSON().provider] });
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
    assert.equal(await panel.locator('[data-testid="plugins-list"]').getAttribute("aria-busy"), "true");
    assert.match(await loading.innerText(), /Loading plugins/);
    release();
    releaseCatalog = null;
    const rows = panel.locator('[data-testid="plugin-row"]');
    await rows.first().waitFor();

    assert.equal(await page.locator("#overview h1").innerText(), "Plugins");
    assert.equal(await page.locator("#overview .panel-description").innerText(), "Manage plugins, skills, and MCPs");
    assert.equal(await page.locator("#dashboard").isHidden(), true);
    assert.equal(await page.locator("#catalog-panel").isHidden(), true);
    assert.equal(await page.locator('[data-panel="plugins"]').getAttribute("aria-current"), "page");
    assert.deepEqual(await panel.locator('[data-testid^="plugins-chip-"]').allInnerTexts(), ["Plugins 3", "Apps 1", "MCPs 1", "Skills"]);
    assert.equal(await panel.locator('[data-testid="plugins-chip-plugins"]').getAttribute("aria-pressed"), "true");
    assert.equal(await panel.locator('[data-testid="plugins-add"], [aria-label*="Add"]').count(), 0, "no Add menu yet (D-034)");
    assert.equal(await panel.locator('input[type="checkbox"], [role="switch"]').count(), 0, "no enable switch yet (#21)");

    // One row per installed plugin, grouped by id, with provider badges.
    assert.deepEqual(await rows.locator('[data-testid="plugin-name"]').allInnerTexts(), ["GitHub", "Linear", "Slack"]);
    const github = rows.filter({ hasText: "GitHub" });
    assert.match(await github.locator('[data-testid="plugin-description"]').innerText(), /Triage pull requests/);
    assert.deepEqual(await github.locator(".pill").allInnerTexts(), ["Codex", "Claude Code"]);
    assert.deepEqual(await rows.filter({ hasText: "Slack" }).locator(".pill").allInnerTexts(), ["Codex"]);
    assert.equal(await page.getByText("Sentry").count(), 0, "available plugins are not installed rows");

    // The search follows the chip; the other chips have no content yet.
    const search = panel.locator('[data-testid="plugins-search"]');
    await search.fill("slack");
    assert.deepEqual(await rows.locator('[data-testid="plugin-name"]').allInnerTexts(), ["Slack"]);
    await search.fill("zzz");
    assert.match(await panel.locator('[data-testid="plugins-empty"]').innerText(), /No plugins match/);
    await search.fill("");
    await panel.locator('[data-testid="plugins-chip-apps"]').click();
    assert.equal(await panel.locator('[data-testid="plugins-chip-apps"]').getAttribute("aria-pressed"), "true");
    assert.equal(await rows.count(), 0);
    assert.match(await panel.locator('[data-testid="plugins-empty"]').innerText(), /Apps are not listed here yet/);
    await panel.locator('[data-testid="plugins-chip-plugins"]').click();
    assert.equal(await rows.count(), 3);

    // Refresh re-fetches both CLIs; the cache serves the first render.
    assert.equal(catalogCalls, 2);
    await panel.locator('[data-testid="plugins-refresh"]').click();
    await page.waitForFunction(() => document.querySelector('[data-testid="plugins-list"]')?.getAttribute("aria-busy") === "false");
    assert.equal(catalogCalls, 4);
    assert.deepEqual(
      apiCalls.filter((n) => !["state", "integration-catalog"].includes(n)),
      [],
      "rendering requests only /api/state and /api/integration-catalog (no dashboard polling)",
    );
    await page.waitForTimeout(3500);
    assert.equal(apiCalls.includes("dashboard"), false, "the 3 s dashboard poll skips #plugins");
    await page.locator("[data-panel=home]").click();
    await page.locator("[data-panel=plugins]").click();
    await rows.first().waitFor();
    assert.equal(catalogCalls, 4, "returning to the page reuses the integrationCatalogs cache");

    // Row menu: Escape closes it and returns focus; Uninstall confirms and posts.
    const menuButton = github.locator('[data-testid="plugin-menu-button"]');
    assert.equal(await menuButton.getAttribute("aria-label"), "GitHub actions");
    await menuButton.click();
    const menu = github.locator('[data-testid="plugin-menu"]');
    assert.deepEqual(await menu.getByRole("menuitem").allInnerTexts(), ["Uninstall from Codex", "Uninstall from Claude Code"]);
    await page.keyboard.press("Escape");
    assert.equal(await menu.count(), 0);
    assert.equal(await menuButton.evaluate((el) => el === document.activeElement), true, "focus returns to the menu button");
    await menuButton.click();
    page.once("dialog", (dialog) => dialog.dismiss());
    await menu.getByRole("menuitem", { name: "Uninstall from Claude Code" }).click();
    assert.deepEqual(posts, [], "a dismissed confirmation posts nothing");
    const slack = rows.filter({ hasText: "Slack" });
    await slack.locator('[data-testid="plugin-menu-button"]').click();
    page.once("dialog", (dialog) => {
      assert.match(dialog.message(), /Slack/);
      return dialog.accept();
    });
    await slack.getByRole("menuitem", { name: "Uninstall", exact: true }).click();
    await page.waitForFunction(() => document.querySelector("#operation-dialog")?.open);
    assert.deepEqual(posts, [{ provider: "codex", action: "plugin_remove", name: "slack@openai" }]);
    await page.evaluate(() => document.querySelector("#operation-dialog").close());

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
        const over = (top, bottom) => top.slice(0, 3).map((v, i) => v * top[3] + bottom[i] * (1 - top[3]));
        const background = (el) => {
          const stack = [];
          for (let node = el; node; node = node.parentElement) stack.push(rgba(getComputedStyle(node).backgroundColor));
          return stack.reverse().reduce((acc, layer) => over(layer, acc), [255, 255, 255]);
        };
        const channel = (v) => ((v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
        const lum = ([r, g, b]) => 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
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
          return [selector, (hi + 0.05) / (lo + 0.05), fg.map(Math.round).join(","), bg.map(Math.round).join(","), getComputedStyle(el).color, getComputedStyle(el).backgroundColor];
        });
        return pairs;
      }, palette);
      for (const [selector, ratio, fg, bg] of ratios[palette]) assert(ratio >= 4.5, palette + " " + selector + " contrast " + ratio + " (text " + fg + " on " + bg + ")");
    }
    console.log("plugins text contrast", JSON.stringify(ratios));
    await page.evaluate(() => HarnessTheme.apply("paper", false));

    // No horizontal overflow at the admin minimum width or on a phone.
    for (const width of [900, 390]) {
      await page.setViewportSize({ width, height: 900 });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
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
