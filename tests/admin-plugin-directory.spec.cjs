const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs/promises");
const path = require("node:path");

const fixturePath = path.join(__dirname, "fixtures/plugin-directory.json");
const familyKey = (item) => String(item.name || item.id || "")
  .replace(/^(?:mcp|plugin):/i, "").split("@")[0].toLowerCase().replace(/[_ ]/g, "-");

const channel = (v) => ((v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
const luminance = ([r, g, b]) => 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);

(async () => {
  const fixture = JSON.parse(await fs.readFile(fixturePath, "utf8"));
  const rawSource = "https://example-user:EXAMPLE_TOKEN@example.test/source-probe?token=EXAMPLE_QUERY#EXAMPLE_FRAGMENT";
  const python = process.env.PYTHON || "python3";
  const sourceProbe = JSON.parse(execFileSync(python, ["-c", `
import json, os, subprocess, sys
revision = os.environ.get("PLUGIN_CATALOG_REV")
if revision:
    source = subprocess.check_output(["git", "show", revision + ":control/integration_catalog.py"], text=True)
    namespace = {"__name__": "control.integration_catalog", "__file__": "control/integration_catalog.py", "__package__": "control"}
    exec(compile(source, namespace["__file__"], "exec"), namespace)
    parse = namespace["_plugins"]
else:
    from control.integration_catalog import _plugins as parse
print(json.dumps(parse(json.dumps({"available": [{"id": "source-probe@official", "name": "Source Probe", "homepage": sys.argv[1]}]}))[0]))
`, rawSource], { cwd: path.join(__dirname, ".."), encoding: "utf8" }));
  const sourceJson = JSON.stringify(sourceProbe);
  for (const secret of ["EXAMPLE_TOKEN", "EXAMPLE_QUERY", "EXAMPLE_FRAGMENT"])
    assert.equal(sourceJson.includes(secret), false, "public integration-catalog JSON omits " + secret);
  assert.equal(sourceProbe.source, "https://example.test/source-probe");
  fixture.catalogs.codex.items.push(sourceProbe);
  assert.deepEqual(fixture.family_cases.map(({ item }) => familyKey(item)), fixture.family_cases.map(({ key }) => key));
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
    await page.route("http://admin.test/**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith("/api/")) return route.fallback();
      const file = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
      return route.fulfill({
        body: await fs.readFile(path.join(__dirname, file.startsWith("assets/") ? "../harness_ui" : "../control", file)),
        contentType: file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : file.endsWith(".svg") ? "image/svg+xml" : "text/html",
      });
    });
    const service = () => ({ added: true, enabled: true, models: ["m"], projects: ["sem-projeto"], mode: "native", integrations: [], permissions: {} });
    const state = {
      settings: { services: { codex: service(), claude: service() }, projects: [], logins: [], port: 8095, tailnet_port: 8095, uploads_enabled: false },
      inventory: { platform: "Linux", services: [
        { id: "codex", name: "Codex CLI", found: true },
        { id: "claude", name: "Claude Code", found: true },
      ], projects: [], network: { online: true } },
      authentication: {}, models: {}, integrations: { codex: [], claude: [] }, operations: [], credentials: {}, status: { running: false },
    };
    const stateItem = (id, name, extra = {}) => ({ id, kind: "plugin", name, scope: "user", enabled: true, source: "config.toml", writable: true, reason: "", affects: [], ...extra });
    const snapshots = {
      codex: { provider: "codex", fingerprint: "codex-fp", items: [
        stateItem("plugin:github@openai-curated", "github"),
        stateItem("plugin:slack@team-tools", "slack", { writable: false, reason: "Managed by the team profile." }),
        stateItem("plugin:discord@beta-market", "discord"),
        stateItem("plugin:documents@official", "documents@official"),
        stateItem("plugin:archive@official", "archive@official"),
      ], warnings: [] },
      claude: { provider: "claude", fingerprint: "claude-fp", items: [
        stateItem("plugin:linear@official", "linear", { enabled: false, source: "settings.json" }),
      ], warnings: [] },
    };
    let catalogReads = 0, unreadable = null;
    const posts = [];
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      const name = url.pathname.replace(/^.*\/api\//, "");
      if (name === "integration-catalog") {
        catalogReads++;
        return route.fulfill({ json: fixture.catalogs[route.request().postDataJSON().provider] });
      }
      if (name === "provider-state") {
        const provider = url.searchParams.get("provider");
        if (unreadable === provider) return route.fulfill({ status: 422, json: { error: "provider_state_unreadable", message: "Provider state is unreadable." } });
        return route.fulfill({ json: { snapshot: snapshots[provider], external_changes: [] } });
      }
      if (name === "integration") {
        posts.push(route.request().postDataJSON());
        return route.fulfill({ json: { id: "install-op" } });
      }
      return route.fulfill({ json: name === "state" ? state : {} });
    });

    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("http://admin.test/#plugins");
    const directory = page.locator('[data-testid="plugin-directory"]');
    await directory.waitFor();

    // P1 directory: marketplace sections are collapsible, cards are a two-column grid, and families merge across providers.
    const sections = directory.locator('[data-testid="plugin-marketplace"]');
    assert.deepEqual(await sections.locator("summary").allInnerTexts(), ["alpha-market", "anthropic", "beta-market", "official", "openai-curated", "personal-lab", "second-market", "team-tools"]);
    const githubCards = directory.locator('[data-family-key="github"]');
    assert.equal(await githubCards.count(), 2, "one shared family detail is projected into each source marketplace");
    const openaiCards = sections.filter({ has: page.getByText("openai-curated", { exact: true }) }).locator('[data-testid="plugin-card"]');
    assert.equal(await openaiCards.count(), 2);
    const boxes = await openaiCards.evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().toJSON()));
    assert(Math.abs(boxes[0].y - boxes[1].y) <= 2 && boxes[1].x > boxes[0].x, "cards use two columns");
    for (const [id, label] of [["plugin:documents@official", "Internal Documents"], ["plugin:archive@official", "Internal Archive"]]) {
      const exactCards = directory.locator('[data-item-id="' + id + '"]');
      assert.equal(await exactCards.count(), 1, id + " reconciles catalog and snapshot before family grouping");
      await exactCards.getByRole("button", { name: "View " + label + " details" }).click();
      assert.equal(await page.locator('[data-testid="plugin-detail"]').getByRole("switch", { name: label + " in Codex" }).getAttribute("data-item-id"), id);
      assert.equal(await page.locator('[data-testid="plugin-detail"]').getByRole("button", { name: "Install " + label + " in Codex" }).count(), 0, "an exact installed id is never offered for installation");
      await page.locator('[data-testid="plugin-detail-back"]').click();
    }
    const aliasCard = directory.locator('[data-item-id="plugin:different@official"]');
    await aliasCard.getByRole("button", { name: "View Documents details" }).click();
    assert.equal(await page.locator('[data-testid="plugin-detail"]').getByRole("switch", { name: "Documents in Codex" }).count(), 0,
      "a display-name collision cannot borrow a snapshot owned by another exact catalog id");
    await page.locator('[data-testid="plugin-detail-back"]').click();
    await directory.locator('[data-item-id="plugin:source-probe@official"]').getByRole("button", { name: "View Source Probe details" }).click();
    assert.match(await page.locator('[data-testid="plugin-detail"]').innerText(), /Source\s+https:\/\/example\.test\/source-probe/);
    for (const secret of ["EXAMPLE_TOKEN", "EXAMPLE_QUERY", "EXAMPLE_FRAGMENT"])
      assert.equal((await page.locator("body").innerText()).includes(secret), false, "plugin detail DOM omits " + secret);
    await page.locator('[data-testid="plugin-detail-back"]').click();
    await sections.filter({ has: page.getByText("official", { exact: true }) }).locator("summary").click();
    assert.equal(await directory.locator('[data-family-key="calendar"]').isHidden(), true, "section collapses");

    // Search covers all metadata, preserves Refresh, and expands the matching section.
    const search = page.locator('[data-testid="plugins-search"]');
    await search.fill("GitHub Issues");
    assert.deepEqual(await directory.locator('[data-testid="plugin-name"]').allInnerTexts(), ["GitHub", "GitHub"]);
    await search.fill("");
    assert.equal(catalogReads, 2);
    await page.locator('[data-testid="plugins-refresh"]').click();
    await page.waitForFunction(() => document.querySelector('[data-testid="plugins-list"]')?.getAttribute("aria-busy") === "false");
    assert.equal(catalogReads, 4);

    // Keyboard card navigation opens detail; breadcrumb Back restores the card and focus.
    const githubCard = directory.locator('[data-family-key="github"][data-marketplace="openai-curated"]');
    await githubCard.getByRole("button", { name: "View GitHub details" }).focus();
    await page.keyboard.press("Enter");
    const detail = page.locator('[data-testid="plugin-detail"]');
    await detail.waitFor();
    assert.match(await detail.innerText(), /GitHub[\s\S]*Triage pull requests[\s\S]*Version[\s\S]*2\.4\.0[\s\S]*Apps[\s\S]*GitHub Issues[\s\S]*Skills[\s\S]*Review pull requests[\s\S]*Developer[\s\S]*OpenAI[\s\S]*Source/);
    assert.equal(await detail.getByRole("button", { name: "Back to plugins" }).locator("svg").count(), 1, "breadcrumb has an icon");
    assert.equal(await detail.getByText("Try now").count(), 0);
    assert.equal(await detail.getByText("Copy link").count(), 0);
    await page.setViewportSize({ width: 390, height: 820 });
    const detailWidth = await detail.evaluate((node) => ({ client: node.clientWidth, scroll: node.scrollWidth }));
    assert(detailWidth.scroll <= detailWidth.client, "390px detail reflows long metadata: " + JSON.stringify(detailWidth));
    await page.setViewportSize({ width: 1400, height: 1000 });

    // Provider rows preserve every independent state and use install only for a backed available variant.
    const providerRow = (name) => detail.locator('[data-testid="plugin-provider-row"]').filter({ hasText: name });
    assert.equal(await providerRow("Codex").getByRole("switch").getAttribute("aria-checked"), "true");
    assert.equal(await providerRow("Claude Code").getByRole("button", { name: "Install GitHub in Claude Code" }).isEnabled(), true);
    await providerRow("Claude Code").getByRole("button", { name: "Install GitHub in Claude Code" }).click();
    assert.deepEqual(posts, [{ provider: "claude", action: "plugin_install", name: "github@anthropic" }]);
    await page.evaluate(() => document.querySelector("#operation-dialog")?.close());
    await detail.getByRole("button", { name: "Back to plugins" }).click();
    assert.equal(await githubCard.getByRole("button", { name: "View GitHub details" }).evaluate((el) => el === document.activeElement), true);
    const anthropicGithub = directory.locator('[data-family-key="github"][data-marketplace="anthropic"]');
    assert.match(await anthropicGithub.innerText(), /Work with repositories/);
    await anthropicGithub.getByRole("button", { name: "View GitHub details" }).click();
    assert.match(await detail.innerText(), /Work with repositories[\s\S]*Version[\s\S]*9\.0\.0[\s\S]*Apps[\s\S]*Claude GitHub[\s\S]*Developer[\s\S]*Anthropic[\s\S]*Source[\s\S]*anthropic-github/);
    assert.equal(await detail.getByText("2.4.0", { exact: true }).count(), 0, "detail does not mix another marketplace's version");
    await detail.getByRole("button", { name: "Back to plugins" }).click();
    assert.equal(await anthropicGithub.getByRole("button", { name: "View GitHub details" }).evaluate((el) => el === document.activeElement), true);

    await directory.locator('[data-family-key="linear"][data-marketplace="openai-curated"]').getByRole("button", { name: "View Linear details" }).click();
    assert.equal(await providerRow("Claude Code").getByRole("switch").getAttribute("aria-checked"), "false", "installed off remains independently switchable");
    assert.equal(await providerRow("Codex").getByRole("button", { name: "Install Linear in Codex" }).isEnabled(), true);
    await detail.getByRole("button", { name: "Back to plugins" }).click();
    await directory.locator('[data-family-key="slack"]').getByRole("button", { name: "View Slack details" }).click();
    assert.equal(await providerRow("Codex").getByRole("switch").isDisabled(), true);
    assert.match(await providerRow("Codex").innerText(), /Managed by the team profile/);
    assert.match(await providerRow("Claude Code").innerText(), /not offered/i);
    await detail.getByRole("button", { name: "Back to plugins" }).click();
    await directory.locator('[data-family-key="calendar"]').getByRole("button", { name: "View Calendar details" }).click();
    assert.match(await providerRow("Claude Code").innerText(), /catalog reports this plugin as installed/i, "catalog/state mismatch is not called absent");
    assert.equal(await detail.getByText("Version", { exact: true }).count(), 0, "unknown optional metadata is omitted");
    assert.equal(await detail.getByText("Apps", { exact: true }).count(), 0);
    await detail.getByRole("button", { name: "Back to plugins" }).click();
    await directory.locator('[data-family-key="figma"][data-marketplace="personal-lab"]').getByRole("button", { name: "View Figma details" }).click();
    assert.match(await providerRow("Codex").innerText(), /Multiple catalog entries/i, "same-provider duplicates never pick an install target");
    assert.equal(await providerRow("Codex").getByRole("button", { name: "Install Figma in Codex" }).count(), 0);
    assert.match(await providerRow("Claude Code").innerText(), /not offered/i);
    assert.equal(await detail.locator("b, img").count(), 0, "hostile catalog metadata stays text");
    assert.equal(await page.evaluate(() => window.__pwned), undefined);
    await detail.getByRole("button", { name: "Back to plugins" }).click();
    await page.locator('[data-testid="plugins-mode"]').click();
    const exactRow = (id) => page.locator('[data-testid="plugin-row"][data-item-id="' + id + '"]');
    assert.equal(await exactRow("plugin:discord@alpha-market").getByRole("switch").count(), 0, "catalog row cannot borrow a family sibling's state switch");
    assert.match(await exactRow("plugin:discord@alpha-market").innerText(), /catalog reports this plugin as installed/i);
    assert.equal(await exactRow("plugin:discord@beta-market").getByRole("switch").getAttribute("data-item-id"), "plugin:discord@beta-market");
    await page.locator('[data-testid="plugins-mode"]').click();
    await directory.locator('[data-family-key="figma"][data-marketplace="personal-lab"]').getByRole("button", { name: "View Figma details" }).click();

    // New controls keep AA text contrast in one light and one dark palette.
    for (const palette of ["paper", "graphite"]) {
      const ratios = await page.evaluate((theme) => {
        HarnessTheme.apply(theme, false);
        document.getAnimations().forEach((animation) => animation.finish());
        const ctx = document.createElement("canvas").getContext("2d");
        const rgb = (value) => { ctx.fillStyle = value; ctx.fillRect(0, 0, 1, 1); return [...ctx.getImageData(0, 0, 1, 1).data].slice(0, 3); };
        const lum = ([r, g, b]) => [r, g, b].map((v) => (v /= 255) <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((n, v, i) => n + v * [.2126, .7152, .0722][i], 0);
        return ['[data-testid="plugin-detail-title"]', '[data-testid="plugin-provider-row"]', '[data-testid="plugin-detail-back"]'].map((selector) => {
          const el = document.querySelector(selector), fg = lum(rgb(getComputedStyle(el).color)), bg = lum(rgb(getComputedStyle(el).backgroundColor === "rgba(0, 0, 0, 0)" ? getComputedStyle(document.body).backgroundColor : getComputedStyle(el).backgroundColor));
          return (Math.max(fg, bg) + .05) / (Math.min(fg, bg) + .05);
        });
      }, palette);
      for (const ratio of ratios) assert(ratio >= 4.5, `${palette} plugin detail contrast ${ratio}`);
    }
    await detail.getByRole("button", { name: "Back to plugins" }).click();
    unreadable = "claude";
    await page.locator('[data-testid="plugins-refresh"]').click();
    await page.waitForFunction(() => document.querySelector('[data-testid="plugins-list"]')?.getAttribute("aria-busy") === "false");
    await page.locator('[data-family-key="github"][data-marketplace="openai-curated"]').getByRole("button", { name: "View GitHub details" }).click();
    assert.match(await providerRow("Claude Code").innerText(), /Provider state is unreadable/, "one unreadable provider leaves an explanation");
    assert.equal(await providerRow("Claude Code").getByRole("button", { name: /Install/ }).count(), 0, "an unreadable provider offers no write");
    assert.equal(posts.length, 1, "unreadable recovery performs no install or uninstall write");
    await detail.getByRole("button", { name: "Back to plugins" }).click();
    await page.setViewportSize({ width: 390, height: 820 });
    assert((await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)) <= 0, "mobile directory has no horizontal overflow");
    assert.deepEqual(errors, []);
    for (const scenario of [
      "directory-marketplace-two-column-family-grouping",
      "public-source-api-and-dom-redaction",
      "provider-id-before-family-reconciliation",
      "search-metadata-and-refresh-recovery",
      "keyboard-detail-breadcrumb-focus-return",
      "provider-enabled-and-disabled-independent-switches",
      "provider-installed-readonly-reason",
      "provider-absent-explicit-install-exact-target",
      "provider-catalog-state-mismatch-and-unavailable-explanations",
      "hostile-and-optional-metadata",
      "coherent-origin-marketplace-metadata",
      "manage-exact-id-state-binding",
      "unreadable-provider-recovery",
      "paper-and-graphite-aa-contrast",
      "mobile-directory-overflow",
      "mobile-detail-long-metadata",
    ]) console.log("PASS scenario " + scenario);
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
