// Regression: a restored local bookmark must use the canonical origin's panels.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");
const { spawn } = require("node:child_process");

(async () => {
  const root = path.resolve(__dirname, "..");
  const state = fs.mkdtempSync(path.join(os.tmpdir(), "tail-browser-entry-"));
  const reservation = net.createServer();
  await new Promise((resolve) => reservation.listen(0, "127.0.0.1", resolve));
  const port = reservation.address().port;
  await new Promise((resolve) => reservation.close(resolve));
  const local = `http://127.0.0.1:${port}`,
    canonical = `http://localhost:${port}`;
  const config = path.join(state, "runtime.json");
  fs.writeFileSync(
    config,
    JSON.stringify({
      state_dir: path.join(state, "runs"),
      bind: "127.0.0.1",
      port,
      clients: {},
      projects: {},
      services: {},
      origins: [canonical],
      browser_url: canonical + "/",
    }),
  );
  const server = spawn(
    process.env.PYTHON || "python3",
    ["-m", "agent_service.app"],
    {
      cwd: root,
      env: { ...process.env, TAIL_HARNESS_AGENT_CONFIG: config },
      stdio: ["ignore", "ignore", "pipe"],
    },
  );
  let logs = "",
    browser;
  server.stderr.on("data", (chunk) => (logs += chunk));
  const exited = new Promise((resolve) => server.once("exit", resolve));
  try {
    let ready = false;
    for (let attempt = 0; attempt < 80; attempt++) {
      if (server.exitCode !== null) throw new Error(logs);
      try {
        ready = (await fetch(local + "/ui.css")).ok;
      } catch {}
      if (ready) break;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    assert(ready, "fixture server readiness: " + logs);
    async function open(storageState) {
      browser = await chromium.launch();
      const context = await browser.newContext({
        viewport: { width: 1600, height: 950 },
        storageState,
      });
      await context.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.3"));
      await context.route("**/v1/**", (route) => {
        const endpoint = new URL(route.request().url()).pathname;
        const data =
          endpoint === "/v1/projects"
            ? { projects: ["sem-projeto"], details: {} }
            : endpoint === "/v1/models"
              ? {
                  models: [
                    {
                      id: "fixture",
                      name: "Fixture",
                      backend: "codex",
                      efforts: ["low"],
                    },
                  ],
                  providers: { codex: true },
                  uploads_enabled: false,
                }
              : endpoint === "/v1/conversations"
                ? { conversations: [] }
                : endpoint === "/v1/version"
                  ? { version: "fixture", build: "fixture" }
                  : {};
        return route.fulfill({ json: data });
      });
      return { context, page: await context.newPage() };
    }
    let { context, page } = await open();
    // Distinct origins really contain different preferences before the restart.
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.3"));
    await page.goto(local + "/ui.css");
    await page.evaluate(() =>
      localStorage.setItem("panel-order", "conversations-left"),
    );
    await page.goto(canonical);
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.click("#settings");
    await page.locator("[data-panel-order=conversations-right]").click();
    await page.click("#settings-close");
    const saved = await context.storageState();
    await browser.close();
    ({ context, page } = await open(saved));
    await page.goto(local);
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    assert.equal(new URL(page.url()).origin, canonical);
    assert.equal(
      await page.evaluate(() => localStorage.getItem("panel-order")),
      "conversations-right",
    );
    const positions = await page.evaluate(() => ({
      reversed: document.body.classList.contains("panel-order-reversed"),
      conversations: document.querySelector("#sidebar").getBoundingClientRect()
        .x,
      files: document.querySelector("#activity-panel").getBoundingClientRect()
        .x,
    }));
    assert(
      positions.reversed && positions.conversations > positions.files,
      "saved panel positions restored",
    );
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    assert.equal(
      new URL(page.url()).origin,
      canonical,
      "no redirect loop on canonical origin",
    );
    console.log(
      "PASS: old bookmark redirects through real backend and restores panels after browser restart",
    );
  } finally {
    if (browser) await browser.close();
    if (server.exitCode === null) server.kill("SIGTERM");
    await exited;
    fs.rmSync(state, { recursive: true, force: true });
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
