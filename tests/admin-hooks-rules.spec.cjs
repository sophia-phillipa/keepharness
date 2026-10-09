const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

// Seven simulated profiles; every response is synthetic, with no real CLI home.
const providers = ["codex", "claude", "deepseek"];
const names = ["Codex CLI", "Claude Code", "DeepSeek"];
const service = { added: false, enabled: false, models: [], projects: ["sem-projeto"], permissions: {}, integrations: [] };
const state = {
  settings: { services: Object.fromEntries(providers.map(p => [p, service])), projects: [{ id: "demo", label: "Demo", path: "/fake/project" }], logins: [], port: 8095 },
  inventory: { platform: "Linux", services: providers.map((id, i) => ({ id, name: names[i], found: true })), projects: [], network: { online: true } },
  authentication: {}, models: {}, integrations: {}, operations: [], credentials: {}, status: { running: false },
};
function items(provider) {
  return [
    { id: "hook:" + provider, kind: "hook", name: "Before tool", scope: "project", source: "/fake/project/." + provider + "/settings.json", enabled: false, writable: false,
      reason: "Project hooks disabled until trust", details: { event: "PreToolUse", matcher: "Bash", type: "command", command: "check --token=‹value› " + "x".repeat(180), timeout: 30, async: false, status: provider === "codex" ? "pending review" : "disabled until trust", env: { API_KEY: "‹value›" }, eventName: "PreToolUse", handlerType: "command", server: "audit", tool: "check", sourcePath: "/fake/hooks.json", statusMessage: "Review in CLI", timeoutSec: 30, additionalContextLimit: 1024, pluginId: "audit@local", enabled: false, isManaged: false, trustStatus: "untrusted", currentHash: "hash", key: "native-key", displayOrder: 1 } },
    { id: "instructions:" + provider, kind: "instructions", name: "Team rules", scope: "user", source: "/fake/home/" + provider + "/AGENTS.md", enabled: true, writable: false,
      details: { title: "Team rules", size_bytes: 42, preview: "# Team rules\nTreat <script>alert(1)</script> as text.", status: "active" } },
  ];
}
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
    page.setDefaultTimeout(5000);
    const errors = [], writes = [], catalogs = [];
    let broken = false, empty = false;
    page.on("pageerror", e => errors.push(e.message));
    await page.route("http://admin.test/**", async route => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith("/api/")) {
        const api = url.pathname.slice(5), provider = url.searchParams.get("provider");
        if (api === "state") return route.fulfill({ json: state });
        if (api === "integration-catalog") {
          catalogs.push(route.request().postDataJSON().provider);
          return route.fulfill({ json: { items: [], warnings: [] } });
        }
        if (route.request().method() !== "GET") writes.push(api);
        if (api === "provider-state") {
          if (broken && provider === "claude") return route.fulfill({ status: 503, json: { error: "fixture_unavailable" } });
          return route.fulfill({ json: { snapshot: { provider, project_root: "/fake/project", fingerprint: "fake", items: empty ? [] : items(provider), warnings: [] }, trust: { trusted: false, required: true }, external_changes: empty ? [] : [{ id: "n_" + provider, item_id: "hook:" + provider, name: "Before tool", change: "changed", before: false, after: false, source: "settings.json", detected_at: "2026-10-08T10:00:00Z" }] } });
        }
        return route.fulfill({ json: {} });
      }
      const file = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
      return route.fulfill({ body: await fs.readFile(path.join(__dirname, file.startsWith("assets/") ? "../harness_ui" : "../control", file)), contentType: file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : file.endsWith(".svg") ? "image/svg+xml" : "text/html" });
    });
    await page.goto("http://admin.test/#plugins");
    await page.waitForFunction(() => document.querySelector('[data-testid="plugins-list"]')?.getAttribute("aria-busy") === "false");
    const hooks = page.getByTestId("plugins-chip-hooks"), rules = page.getByTestId("plugins-chip-rules"), list = page.getByTestId("plugins-list");
    await hooks.click();
    assert.equal(await list.getByTestId("provider-resource-row").count(), 3);
    assert.match(await list.innerText(), /Configured hooks|PreToolUse/);
    console.log("PASS beginner-hooks-three-providers");
    assert.match(await list.innerText(), /Codex: Project · \/fake\/project/);
    assert.match(await list.innerText(), /pending review/);
    assert.match(await list.innerText(), /disabled until trust/);
    assert.equal(await list.getByRole("switch").count(), 0);
    assert(!catalogs.includes("deepseek"), "dsh has state, not a fictitious plugin catalog");
    assert.equal(await list.getByTestId("resource-changed").count(), 3);
    console.log("PASS engineer-native-fields-trust-readonly-external-notices");
    for (const text of ["PreToolUse", "Bash", "command", "30", "false", "API_KEY", "‹value›", "audit@local", "Review in CLI", "native-key", "1024", "untrusted", "audit", "check", "hash", "/fake/hooks.json"]) assert((await list.innerText()).includes(text), text);
    await rules.focus(); await page.keyboard.press("Enter");
    const preview = list.locator("details").first();
    assert.equal(await preview.getAttribute("open"), null);
    await preview.locator("summary").focus(); await page.keyboard.press("Enter");
    assert.match(await preview.innerText(), /Treat <script>/);
    assert.equal(await list.locator("script").count(), 0);
    assert.match(await list.innerText(), /42 bytes/);
    assert.match(await list.innerText(), /Previews are best effort/);
    console.log("PASS professional-rule-source-size-preview-text");
    assert.equal(await preview.locator("summary").evaluate(e => e === document.activeElement), true);
    await page.keyboard.press("Enter");
    assert.equal(await preview.getAttribute("open"), null);
    console.log("PASS accessibility-keyboard-preview");
    await hooks.click(); await rules.click(); await hooks.click();
    assert.equal(await list.getByTestId("provider-resource-row").count(), 3);
    await page.getByTestId("plugins-project").selectOption("demo");
    await page.waitForFunction(() => document.querySelectorAll('[data-testid="provider-resource-row"]').length === 3);
    await page.getByRole("button", { name: "Trust Demo", exact: true }).waitFor();
    assert.deepEqual(writes, []);
    console.log("PASS rushed-switch-sections-and-scope-no-writes");
    for (const theme of ["paper", "graphite"]) {
      await page.evaluate(theme => window.HarnessTheme.apply(theme, false), theme);
      for (const width of [1280, 390]) {
        await page.setViewportSize({ width, height: 1000 });
        for (const chip of [hooks, rules]) {
          await chip.click();
          assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${theme} ${width} overflow`);
        }
      }
    }
    console.log("PASS designer-paper-graphite-1280-390");
    broken = true;
    await page.getByTestId("plugins-refresh").click();
    await page.waitForFunction(() => document.querySelector('[data-testid="plugins-list"]')?.getAttribute("aria-busy") === "false");
    assert.match(await list.innerText(), /fixture_unavailable/);
    broken = false; empty = true;
    await page.getByTestId("plugins-refresh").click();
    await page.waitForFunction(() => document.querySelector('[data-testid="plugins-list"]')?.getAttribute("aria-busy") === "false");
    await hooks.click();
    assert.match(await list.innerText(), /No hooks found/);
    assert.match(await list.innerText(), /Configured hooks will appear here/);
    assert.deepEqual(errors, []);
    console.log("PASS mobile-read-failure-recovery-native-empty");
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
