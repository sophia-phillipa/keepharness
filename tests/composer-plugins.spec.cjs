// Connectors and plugins for this project and route (Sophia, 2026-10-03;
// Codex model: a Plugins chip in the composer sub-bar).
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    const queries = [];
    page.on("pageerror", (e) => console.error("PAGEERROR", e.message));
    await page.route("http://plugins.test/**", async (route) => {
      const url = new URL(route.request().url()),
        pathname = url.pathname;
      if (!pathname.startsWith("/v1/"))
        return route.fulfill({
          path: path.join(__dirname, "..", pathname.startsWith("/assets/") ? "tail_ui" : "agent_service", pathname === "/" ? "index.html" : pathname),
        });
      let data = {};
      if (pathname === "/v1/projects") data = { projects: ["sem-projeto", "alpha"], details: { alpha: { label: "Alpha" } } };
      else if (pathname === "/v1/models")
        data = {
          models: [{ id: "claude-sonnet-5-5", name: "Claude Sonnet 5.5", backend: "claude", efforts: ["configured"] }],
          providers: { claude: true },
          uploads_enabled: false,
        };
      else if (pathname === "/v1/conversations") data = { conversations: [] };
      else if (pathname === "/v1/version") data = { version: "fixture", build: "plugins" };
      else if (pathname === "/v1/integrations") {
        queries.push(Object.fromEntries(url.searchParams));
        data = {
          backend: "claude",
          execution_mode: "native",
          access_mode: url.searchParams.get("access_mode"),
          effective_note: "Each connector call asks for your approval.",
          window_days: 30,
          items: [
            { id: "mcp:github", kind: "mcp", name: "github", transport: "http", status: "configured", allowed: true, effective: true, reason: "", used: { count: 3, last_used: Date.now() / 1000 - 7200, tools: ["create_issue"] } },
            { id: "mcp:drive", kind: "mcp", name: "drive", transport: "stdio", status: "configured", allowed: true, effective: true, reason: "", used: null },
            { id: "plugin:superpowers@market", kind: "plugin", name: "superpowers@market", status: "installed", allowed: false, effective: false, reason: "Not allowed for this provider. Change it in Settings › System › Providers.", used: null },
          ],
          other_tools: [{ name: "Read", count: 12, last_used: Date.now() / 1000 - 60 }],
        };
      }
      return route.fulfill({ json: data });
    });
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.15"));
    await page.goto("http://plugins.test/");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });

    const chip = page.locator("#execution-mode-choice").getByRole("button", { name: "Plugins" });
    await chip.click();
    const menu = page.getByRole("dialog", { name: "Connectors and plugins" });
    await menu.getByText("Available in this conversation").waitFor();
    assert.equal(await chip.getAttribute("aria-expanded"), "true");
    assert.deepEqual(queries.at(-1), {
      project_id: "sem-projeto",
      backend: "claude",
      model: "claude-sonnet-5-5",
      execution_mode: "native",
      access_mode: "ask",
    });
    const text = await menu.innerText();
    assert.match(text, /Connectors and plugins · Claude Code/);
    assert.match(text, /Each connector call asks for your approval\./);
    assert.match(text, /github\s+Connector · http · used 3× · 2 h ago/);
    assert.match(text, /drive\s+Connector · stdio · not used here recently/);
    assert.match(text, /Installed, not available here[\s\S]*superpowers@market\s+Plugin · Not allowed for this provider/);
    assert.match(text, /Other tools used in this project \(30 days\)/);
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => document.getElementById("plugins-chip").getAttribute("aria-expanded") === "false");
    assert.equal(await menu.isVisible(), false);

    // The query follows the chosen project and access mode.
    await page.click("#project-button");
    await page.getByRole("option", { name: "Alpha" }).click();
    await page.click("#access-trigger");
    await page.locator('#access-menu [data-access="full"]').click();
    await chip.click();
    await menu.getByText("Available in this conversation").waitFor();
    assert.equal(queries.at(-1).project_id, "alpha");
    assert.equal(queries.at(-1).access_mode, "full");
    console.log("PASS composer Plugins chip shows connectors and plugins for the route");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
