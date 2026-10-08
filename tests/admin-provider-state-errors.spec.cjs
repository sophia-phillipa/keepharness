const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const { spawn } = require("node:child_process");
const { once } = require("node:events");

// Real DeepSeek state boundary, synthetic CLI/home, dynamically bound local HTTP port.
// Other admin APIs are browser fixtures; no model turn or account access is possible.
const backend = `
import asyncio, json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from control.provider_state import ProviderStateService
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        result = asyncio.run(ProviderStateService(Path(os.environ["FIXTURE_STATE"]), lambda: [], track_notices=False).read("deepseek", "sem-projeto"))
        data = result.body if hasattr(result, "body") else json.dumps(result).encode()
        self.send_response(getattr(result, "status_code", 200))
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)
server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
print(server.server_address[1], flush=True)
server.serve_forever()
`;

(async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "provider-errors-"));
  let child, browser;
  try {
    const home = path.join(root, "home"), stateDir = path.join(root, "state");
    const privateHome = path.join(stateDir, "providers", "deepseek");
    for (const folder of [path.join(home, ".codex"), path.join(home, ".claude"), privateHome, path.join(stateDir, "providers", "home")])
      await fs.mkdir(folder, { recursive: true });
    const config = path.join(privateHome, "config.toml");
    const original = "[plugins.private]\nenabled=true\n";
    await fs.writeFile(config, original);
    await fs.writeFile(path.join(privateHome, "fake-app-server.json"), JSON.stringify({ plugins: [{ id: "private", name: "Private" }] }));
    child = spawn(process.env.PYTHON || "python3", ["-c", backend], {
      cwd: path.join(__dirname, ".."),
      env: { ...process.env, HOME: home, CODEX_HOME: path.join(home, ".codex"), CLAUDE_CONFIG_DIR: path.join(home, ".claude"),
        FIXTURE_STATE: stateDir, TMPDIR: root, PYTHONDONTWRITEBYTECODE: "1",
        PATH: path.join(__dirname, "fixtures", "fake-codex") + path.delimiter + process.env.PATH },
      stdio: ["ignore", "pipe", "pipe"],
    });
    const port = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error("Fixture server did not start")), 10000);
      child.once("error", (error) => { clearTimeout(timer); reject(error); });
      child.once("exit", (code) => { clearTimeout(timer); reject(Error("Fixture server exited: " + code)); });
      child.stdout.once("data", (data) => { clearTimeout(timer); resolve(Number(data.toString().trim())); });
    });
    assert.ok(Number.isInteger(port) && port > 0);
    const url = "http://127.0.0.1:" + port;
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 390, height: 900 } });
    page.setDefaultTimeout(10000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    let failedProvider = null;
    const inventory = [
      { id: "codex", name: "Codex CLI", found: true },
      { id: "claude", name: "Claude Code", found: true },
      { id: "deepseek", name: "DeepSeek", found: true },
    ];
    const state = {
      settings: { services: {}, projects: [], logins: [], port: 8095, tailnet_port: 8095, uploads_enabled: false },
      inventory: { services: inventory, platform: "Linux", projects: [], network: { online: true } },
      authentication: {}, models: {}, integrations: {}, operations: [], credentials: {}, status: { running: false },
    };
    await page.route(url + "/**", async (route) => {
      const address = new URL(route.request().url());
      if (!address.pathname.startsWith("/api/")) {
        const file = address.pathname === "/" ? "index.html" : address.pathname.slice(1);
        return route.fulfill({ body: await fs.readFile(path.join(__dirname, file.startsWith("assets/") ? "../harness_ui" : "../control", file)),
          contentType: file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : "text/html" });
      }
      const name = address.pathname.slice(5);
      if (name === "provider-state") {
        const provider = address.searchParams.get("provider");
        if (provider === "deepseek") return route.continue();
        if (provider === failedProvider)
          return route.fulfill({ status: 422, json: { error: "provider_state_unreadable", message: "Fixture state cannot be read." } });
        return route.fulfill({ json: { snapshot: { provider, fingerprint: provider, items: [{ id: "plugin:" + provider, name: provider, kind: "plugin", scope: "user", enabled: true, writable: true, source: "fixture", reason: "" }], warnings: [] }, external_changes: [] } });
      }
      if (name === "integration-catalog") return route.fulfill({ json: { items: [], warnings: [] } });
      return route.fulfill({ json: name === "state" ? state : {} });
    });
    await page.goto(url + "/#plugins");
    await page.locator('[data-testid="plugins-mode"]').click();
    const privateRow = page.locator('[data-item-id="plugin:private"][data-testid="plugin-row"]');
    await privateRow.waitFor();
    const foreign = path.join(root, "foreign-config.toml");
    const foreignBytes = '[mcp_servers.owner_only]\ncommand="owner-command"\n';
    await fs.writeFile(foreign, foreignBytes);
    const cliLog = path.join(privateHome, "fake-app-server.log");
    const logBeforeRefusal = await fs.readFile(cliLog, "utf8");
    await fs.unlink(config);
    await fs.link(foreign, config);
    const refresh = async () => {
      const response = page.waitForResponse((res) => res.url().includes("/api/provider-state?provider=deepseek"));
      await page.getByRole("button", { name: "Refresh", exact: true }).click();
      const result = await response;
      await page.waitForFunction(() => document.querySelector('[data-testid="plugins-list"]')?.getAttribute("aria-busy") === "false");
      return result;
    };
    const refused = await refresh();
    assert.equal(refused.status(), 422);
    assert.equal((await refused.json()).error, "provider_state_unreadable");
    assert.equal(await fs.readFile(cliLog, "utf8"), logBeforeRefusal, "refusal starts no CLI process");
    assert.equal(await fs.readFile(foreign, "utf8"), foreignBytes);
    const note = page.locator('[data-testid="plugins-note"]');
    assert.equal(await note.isVisible(), true, "state refusal must stay visible without provider rows");
    assert.match(await note.innerText(), /DeepSeek: .*DeepSeek's private config is unsafe or unreadable\./);
    assert.equal(await note.getAttribute("aria-live"), "polite");
    assert.equal(await privateRow.count(), 0);
    for (const provider of ["codex", "claude"])
      assert.equal(await page.locator('[data-testid="plugin-row"][data-item-id="plugin:' + provider + '"]').count(), 1);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    assert.ok(!(await note.innerText()).includes(root), "only the API message is displayed");
    await fs.unlink(config);
    await fs.writeFile(config, original);
    await refresh();
    assert.equal(await note.isVisible(), false, "successful reread clears the refusal");
    await privateRow.waitFor();
    for (const [provider, label] of [["codex", "Codex"], ["claude", "Claude Code"]]) {
      failedProvider = provider;
      await refresh();
      assert.equal(await note.isVisible(), true);
      assert.ok((await note.innerText()).includes(label + ":"));
      assert.match(await note.innerText(), /Fixture state cannot be read\./);
      assert.equal(await privateRow.count(), 1, "healthy DeepSeek state remains visible");
      const healthy = provider === "codex" ? "claude" : "codex";
      assert.equal(await page.locator('[data-testid="plugin-row"][data-item-id="plugin:' + healthy + '"]').count(), 1);
    }
    assert.deepEqual(errors, []);
    console.log("PASS provider state error feedback: real hardlink refusal, recovery, all providers, 390px");
  } finally {
    if (browser) await browser.close();
    if (child && child.exitCode === null) {
      const exited = once(child, "exit");
      child.kill("SIGTERM");
      await exited;
    }
    await fs.rm(root, { recursive: true, force: true });
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
