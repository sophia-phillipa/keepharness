const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

// Changes made outside KeepHarness (issue #43, D-039 item 8, D-041 item 9): the notices block on the
// Plugins page and on Settings > Providers, one toast per notice id per page session, and the ack.
const item = (id, name) => ({ id, name, kind: "plugin", status: "installed" });
const GITHUB = item("plugin:github@openai-curated", "GitHub");
const LINEAR = item("plugin:linear@official", "Linear");
const CATALOGS = {
  codex: { items: [GITHUB], warnings: [] },
  claude: { items: [LINEAR], warnings: [] },
};
const stateItem = (id, name, enabled) => ({
  id, kind: "plugin", name, scope: "user", enabled, source: "config.toml", writable: true, reason: "", affects: [],
});
const snapshot = (provider, items) => ({
  provider, engine: provider, project_root: null, items, fingerprint: "fp-" + provider, cli_version: "1.0.0", warnings: [],
});
const notice = (n, extra) => ({
  id: "n_" + String(n).padStart(16, "0"), item_id: GITHUB.id, name: "github", change: "changed", before: true, after: false,
  source: "config.toml", detected_at: "2026-10-08T10:42:00Z", ...extra,
});
const N1 = notice(1);
const N3 = notice(3, { item_id: LINEAR.id, name: "linear", change: "reverted", before: true, after: false, source: ".claude.json" });
const N4 = notice(4, { change: "added", before: null, after: true, name: '<img src=x onerror="window.__pwned=1">', source: "<b>evil</b>.toml" });
const N5 = notice(5, { item_id: "plugin:figma@openai-curated", name: "figma", change: "removed", before: true, after: null });

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
    const service = () => ({ added: true, enabled: true, models: ["m"], projects: ["sem-projeto"], mode: "native", integrations: [], permissions: {} });
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
      authentication: {}, models: {}, integrations: { codex: [], claude: [] }, operations: [], credentials: {}, status: { running: false },
    };
    // What the server holds as pending notices; an ack removes the ids it is given.
    const server = { codex: [N1], claude: [] };
    const snapshots = {
      codex: snapshot("codex", [stateItem(GITHUB.id, "github", false)]),
      claude: snapshot("claude", [stateItem(LINEAR.id, "linear", false)]),
    };
    const gets = [], acks = [];
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      const name = url.pathname.replace(/^.*\/api\//, "");
      const method = route.request().method();
      if (name === "provider-state" && method === "GET") {
        const provider = url.searchParams.get("provider");
        gets.push(url.search);
        return route.fulfill({ json: { snapshot: snapshots[provider], external_changes: server[provider] } });
      }
      if (name === "provider-state/notices:ack" && method === "POST") {
        const body = route.request().postDataJSON();
        acks.push({ body, header: route.request().headers()["x-harness-admin"] });
        server[body.provider] = server[body.provider].filter((n) => !body.notice_ids.includes(n.id));
        return route.fulfill({ json: {} });
      }
      if (name === "integration-catalog") return route.fulfill({ json: CATALOGS[route.request().postDataJSON().provider] });
      return route.fulfill({ json: name === "state" ? state : {} });
    });

    const pluginsBlock = page.locator("#plugins-notices");
    const providersBlock = page.locator("#provider-state-notices");
    const toast = page.locator("#th-toast");
    const shown = (block) => block.locator('[data-testid="provider-notice"]');
    const focusWindow = () => page.evaluate(() => window.dispatchEvent(new Event("focus")));
    const hideToast = () => page.evaluate(() => { document.getElementById("th-toast").hidden = true; });
    const go = (hash) => page.evaluate((h) => { location.hash = h; }, hash);

    // A single notice on the Plugins page: provider, item, change, file and time as text, a marker on its row, one toast.
    await page.goto("http://admin.test/#plugins");
    await shown(pluginsBlock).first().waitFor();
    assert.equal(await shown(pluginsBlock).count(), 1);
    assert.match(await shown(pluginsBlock).innerText(), /^Codex › github was turned off \(config\.toml, \d\d:\d\d\)\.\s*Dismiss$/);
    assert.equal(await pluginsBlock.getAttribute("role"), "region");
    assert.equal(await pluginsBlock.getAttribute("aria-label"), "Changes made outside KeepHarness");
    assert.equal(await pluginsBlock.locator("strong").innerText(), "Changed outside KeepHarness");
    assert.equal(await pluginsBlock.evaluate((el) => el === el.closest("#plugins-panel").firstElementChild), true, "the block sits at the top of the page");
    assert.match(await toast.innerText(), /^Changed outside KeepHarness: Codex › github was turned off \(config\.toml, \d\d:\d\d\)\./);
    const rows = page.locator('[data-testid="plugin-row"]');
    await rows.first().waitFor();
    const row = (name) => rows.filter({ has: page.locator('[data-testid="plugin-name"]', { hasText: new RegExp("^" + name + "$") }) });
    assert.equal(await row("GitHub").locator('[data-testid="plugin-changed"]').count(), 1, "the row of the changed item is marked");
    assert.equal(await row("Linear").locator('[data-testid="plugin-changed"]').count(), 0);
    assert.deepEqual(gets.sort(), ["?provider=claude&project_id=sem-projeto", "?provider=codex&project_id=sem-projeto"]);

    // Settings > Providers shows the same notice; the toast does not fire again for the same id.
    await hideToast();
    await go("#providers");
    await shown(providersBlock).first().waitFor();
    assert.match(await shown(providersBlock).innerText(), /^Codex › github was turned off/);
    assert.equal(await providersBlock.isVisible(), true);
    assert.equal(await toast.isHidden(), true, "no second toast for the same notice id");
    await go("#plugins");
    await shown(pluginsBlock).first().waitFor();
    assert.equal(await toast.isHidden(), true, "re-entering a screen does not toast again");

    // A new id arriving on return to the tab toasts once; the reverted wording names the CLI and the file.
    server.claude = [N3];
    const before = gets.length;
    await focusWindow();
    await shown(pluginsBlock).nth(1).waitFor();
    assert.equal(gets.length - before, 2, "focus re-reads both CLIs once");
    assert.equal(await pluginsBlock.locator("strong").innerText(), "2 changes outside KeepHarness");
    assert.match(await shown(pluginsBlock).nth(1).innerText(), /^Reverted: linear was turned back off by Claude Code \(\.claude\.json, \d\d:\d\d\)\./);
    assert.match(await toast.innerText(), /^Changed outside KeepHarness: Reverted: linear was turned back off by Claude Code/);
    await hideToast();
    const reads = gets.length;
    await focusWindow();
    while (gets.length < reads + 2) await page.waitForTimeout(50);
    await page.waitForTimeout(100);
    assert.equal(await toast.isHidden(), true, "known ids are not announced again");

    // Dismiss one: the ack carries that id for that provider and scope, with the admin header; both screens drop it.
    await page.getByRole("button", { name: /^Dismiss: Codex › github was turned off/ }).click();
    await page.waitForFunction(() => document.querySelectorAll('#plugins-notices [data-testid="provider-notice"]').length === 1);
    assert.deepEqual(acks, [{ body: { provider: "codex", project_id: "sem-projeto", notice_ids: [N1.id] }, header: "1" }]);
    assert.equal(await row("GitHub").locator('[data-testid="plugin-changed"]').count(), 0, "the marker goes with the notice");
    assert.equal(await row("Linear").locator('[data-testid="plugin-changed"]').count(), 1);
    assert.equal(await pluginsBlock.locator("strong").innerText(), "Changed outside KeepHarness");
    await go("#providers");
    await shown(providersBlock).first().waitFor();
    assert.equal(await shown(providersBlock).count(), 1);
    assert.match(await shown(providersBlock).innerText(), /^Reverted: linear was turned back off by Claude Code/);

    // Names and files are text, never HTML. Dismiss all acks every id of that provider.
    server.codex = [N4, N5];
    await focusWindow();
    await page.waitForFunction(() => document.querySelectorAll('#provider-state-notices [data-testid="provider-notice"]').length === 3);
    assert.equal(await providersBlock.locator("img, b").count(), 0);
    assert.equal(await page.evaluate(() => window.__pwned), undefined);
    assert.match(await providersBlock.innerText(), /Codex › <img src=x onerror="window\.__pwned=1"> was added \(<b>evil<\/b>\.toml, \d\d:\d\d\)\./);
    assert.match(await pluginsBlock.evaluate((el) => el.textContent), /<b>evil<\/b>\.toml/);
    assert.match(await shown(providersBlock).filter({ hasText: "figma" }).innerText(), /^Codex › figma was removed/);
    assert.equal(await providersBlock.getByRole("button", { name: "Dismiss all Claude Code" }).count(), 0, "a provider with one notice has no Dismiss all");
    await providersBlock.getByRole("button", { name: "Dismiss all Codex" }).click();
    await page.waitForFunction(() => document.querySelectorAll('#provider-state-notices [data-testid="provider-notice"]').length === 1);
    assert.deepEqual(acks.at(-1).body, { provider: "codex", project_id: "sem-projeto", notice_ids: [N4.id, N5.id] });
    assert.equal(await page.evaluate(() => document.activeElement.closest("#provider-state-notices") !== null), true, "focus stays in the notices block");

    // The last dismiss hides the block on both screens and the focus does not fall to <body>.
    await providersBlock.getByRole("button", { name: /^Dismiss: Reverted/ }).click();
    await providersBlock.waitFor({ state: "hidden" });
    assert.equal(await page.evaluate(() => document.activeElement === document.body), false);
    await go("#plugins");
    await rows.first().waitFor();
    assert.equal(await pluginsBlock.isHidden(), true);
    assert.equal(await page.locator('[data-testid="plugin-changed"]').count(), 0);

    // No polling: an idle page makes no further reads.
    const idle = gets.length;
    await page.waitForTimeout(4000);
    assert.equal(gets.length, idle);
    assert.deepEqual(errors, []);
    console.log("PASS admin provider notices");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
