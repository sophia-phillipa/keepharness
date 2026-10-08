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
    // WP4: tools connected on another provider, per backend; `noElsewhere` models nothing to connect.
    const provider = (backend) => ({ backend, allowed: true, effective_capable: true });
    const elsewhereByBackend = {
      claude: [
        { key: "github", label: "Github", here: "enable", providers: [provider("codex")] },
        { key: "notion", label: "Notion", here: "absent", providers: [provider("codex"), provider("deepseek")] },
      ],
      codex: [
        { key: "linear", label: "Linear", here: "absent", providers: [provider("claude")] },
        { key: "slack", label: "Slack", here: "enable", providers: [provider("claude")] },
        { key: "figma", label: "Figma", here: "absent", providers: [provider("deepseek")] },
      ],
    };
    let noElsewhere = false,
      noModels = false,
      gate = null; // holds the /v1/integrations answers of one backend until released
    const turn = { id: "t1", project: "sem-projeto", state: "completed", request: { backend: "claude", model: "claude-sonnet-5-5", prompt: "Hi", access_mode: "ask", effort: "configured" }, result: { answer: "Hello" } };
    const serve = async (route) => {
      const url = new URL(route.request().url()),
        pathname = url.pathname;
      if (pathname.startsWith("/admin-fixture")) return route.fulfill({ contentType: "text/html", body: "<title>admin</title>" });
      if (!pathname.startsWith("/v1/"))
        return route.fulfill({
          path: path.join(__dirname, "..", pathname.startsWith("/assets/") ? "harness_ui" : "agent_service", pathname === "/" ? "index.html" : pathname),
        });
      let data = {};
      if (pathname === "/v1/projects") data = { projects: ["sem-projeto", "alpha"], details: { alpha: { label: "Alpha" } } };
      else if (pathname === "/v1/models")
        data = {
          // On a local host the admin is framed in Settings > System.
          ...(url.hostname === "127.0.0.1" ? { admin_url: "http://127.0.0.1:18700/admin-fixture/" } : {}),
          models: noModels ? [] : [
            { id: "claude-sonnet-5-5", name: "Claude Sonnet 5.5", backend: "claude", efforts: ["configured"], execution_modes: ["native"] },
            { id: "gpt-6-astra", name: "GPT-6 Astra", backend: "codex", efforts: ["configured"], execution_modes: ["native"] },
          ],
          providers: { claude: true, codex: true },
          uploads_enabled: false,
          full_access: true, // the owner turned Full access on (D11)
        };
      else if (pathname === "/v1/conversations")
        data = { conversations: [{ id: "c1", title: "Plugins talk", project: "sem-projeto", state: "completed", last_job_id: "t1", updated_at: 1 }] };
      else if (pathname === "/v1/conversations/c1") data = { title: "Plugins talk", turns: [turn] };
      else if (pathname === "/v1/jobs/t1") data = turn;
      else if (pathname === "/v1/version") data = { version: "fixture", build: "plugins" };
      else if (pathname === "/v1/integrations") {
        queries.push(Object.fromEntries(url.searchParams));
        if (gate && gate.backend === url.searchParams.get("backend")) await gate.promise;
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
          elsewhere: noElsewhere ? [] : elsewhereByBackend[url.searchParams.get("backend")] || [],
        };
      }
      return route.fulfill({ json: data });
    };
    await page.route("http://plugins.test/**", serve);
    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
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
    // A row opens the connector's detail (Codex plugin page); Back returns to the list.
    await menu.getByRole("button", { name: /^github/ }).click();
    const detail = await menu.innerText();
    assert.match(detail, /github\s+Connector \(MCP server\) · http/);
    assert.match(detail, /In this conversation\s+Available/);
    assert.match(detail, /Allowed for this provider\s+Yes/);
    assert.match(detail, /Used in this project\s+3× · last 2 h ago/);
    assert.match(detail, /Tools used\s+create_issue/);
    assert.match(detail, /Approvals\s+Each connector call asks for your approval\./);
    await menu.getByRole("button", { name: "Connectors and plugins" }).click();
    assert(await menu.getByRole("button", { name: /^github/ }).evaluate((el) => el === document.activeElement));
    await menu.getByRole("button", { name: /^superpowers/ }).click();
    assert.match(await menu.innerText(), /In this conversation\s+Not allowed for this provider/);
    await menu.getByRole("button", { name: "Connectors and plugins" }).click();
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

    // WP4 (a): tools connected on another provider, with the wording of the contract.
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.waitForFunction(() => document.getElementById("plugins-chip").dataset.elsewhere === "true");
    // WP4 (b): the chip carries a dot and says so in its accessible name.
    assert.match(await chip.getAttribute("aria-label"), /tools available on other providers/);
    assert.notEqual(await chip.evaluate((el) => getComputedStyle(el, "::after").content), "none");
    await chip.click();
    const other = menu.locator('[data-testid="elsewhere-section"]');
    await other.waitFor();
    assert.equal(await other.getByRole("heading").innerText(), "On other providers");
    const rows = other.locator('[data-testid="elsewhere-row"]');
    assert.deepEqual(await rows.allInnerTexts(), [
      "Github\nInstalled on Claude Code, turned off for KeepHarness\nEnable",
      "Notion\nNot connected on Claude Code (connected on Codex, DeepSeek)\nOpen Plugins",
    ]);
    assert.ok(
      (await menu.innerText()).indexOf("Installed, not available here") < (await menu.innerText()).indexOf("On other providers"),
      "the route reason comes first",
    );
    // No enable endpoint exists on the harness API: the button opens Settings (Plugins here: the admin nav is hidden on this host).
    const apiCalls = [];
    page.on("request", (r) => r.method() !== "GET" && apiCalls.push(r.method() + " " + r.url()));
    await rows.nth(0).getByRole("button", { name: "Enable" }).click();
    await page.locator('button[data-settings="plugins"][aria-pressed="true"]').waitFor();
    assert.equal(await menu.isVisible(), false);
    assert.deepEqual(apiCalls, []);
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await chip.click();
    await menu.locator('[data-testid="elsewhere-row"]').nth(1).getByRole("button", { name: "Open Plugins" }).click();
    await page.locator('button[data-settings="plugins"][aria-pressed="true"]').waitFor();

    // Text contrast of the new rows: >= 4.5 on one light (paper) and one dark (graphite) palette.
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await chip.click();
    await other.waitFor();
    const ratios = {};
    for (const palette of ["paper", "graphite"]) {
      ratios[palette] = await page.evaluate((id) => {
        document.documentElement.dataset.palette = id;
        const lum = (rgb) => {
          const [r, g, b] = rgb.match(/[\d.]+/g).slice(0, 3).map((v) => {
            const c = Number(v) / 255;
            return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
          });
          return 0.2126 * r + 0.7152 * g + 0.0722 * b;
        };
        const bg = lum(getComputedStyle(document.getElementById("plugins-menu")).backgroundColor);
        return [".integration-row strong", ".integration-row small", ".plugins-action", "h3"].map((sel) => {
          const fg = lum(getComputedStyle(document.querySelector('[data-testid="elsewhere-section"] ' + sel)).color);
          return (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05);
        });
      }, palette);
      for (const ratio of ratios[palette]) assert(ratio >= 4.5, palette + " contrast " + ratio);
    }
    console.log("elsewhere text contrast", JSON.stringify(ratios));
    await page.keyboard.press("Escape");

    // WP4 (c): switching provider names the tools that do not follow the conversation.
    await page.locator("#history .conversation-row > button", { hasText: "Plugins talk" }).click();
    await page.waitForFunction(() => document.querySelectorAll("#messages article.user").length === 1);
    const carryover = page.locator("#route-carryover");
    await page.locator("#model").selectOption("gpt-6-astra");
    await page.waitForFunction(() => document.querySelectorAll("#route-carryover .route-carryover-tool").length === 2);
    assert.equal(
      await carryover.innerText(),
      "Next message goes to Codex · GPT-6 Astra. The conversation so far goes with it.\n" +
        "Linear is connected on Claude Code but not on Codex\n" +
        "Slack is installed on Codex but not enabled - enable it in Settings › System › Providers",
    );
    await page.locator("#model").selectOption("claude-sonnet-5-5");
    assert.equal(await carryover.isHidden(), true);

    // A slow answer for the route left behind must not overwrite the route now shown.
    const routeShown = () => page.waitForFunction(() => elsewhereView.key !== "" && elsewhereView.key === routeKey(resourceEngine()));
    let release;
    gate = { backend: "claude", promise: new Promise((resolve) => (release = resolve)) };
    await chip.click(); // asks for Claude and waits at the gate
    await page.keyboard.press("Escape");
    await page.locator("#model").selectOption("gpt-6-astra");
    await routeShown();
    await page.waitForFunction(() => document.querySelectorAll("#route-carryover .route-carryover-tool").length === 2);
    const late = page.waitForResponse((r) => r.url().includes("/v1/integrations") && r.url().includes("backend=claude"));
    gate = null;
    release();
    await late;
    await page.evaluate(() => new Promise((resolve) => setTimeout(resolve, 100)));
    assert.equal(await page.evaluate(() => elsewhereView.key === routeKey(resourceEngine())), true, "the late Claude answer was dropped");
    assert.equal(await page.locator("#route-carryover .route-carryover-tool").count(), 2);
    assert.equal(await chip.getAttribute("data-elsewhere"), "true");

    // N route switches ask for at most N lists.
    const before = queries.length,
      switches = ["claude-sonnet-5-5", "gpt-6-astra", "claude-sonnet-5-5", "gpt-6-astra"];
    for (const id of switches) {
      await page.locator("#model").selectOption(id);
      await routeShown();
    }
    assert.ok(queries.length - before <= switches.length, "asked " + (queries.length - before) + " times for " + switches.length + " switches");

    // No model: the dot and the accessible name go away instead of describing the old route.
    noModels = true;
    await page.click("#project-button");
    await page.getByRole("option", { name: "Alpha" }).click();
    await page.waitForFunction(() => !document.getElementById("plugins-chip").dataset.elsewhere);
    assert.equal(await chip.getAttribute("aria-label"), null);
    assert.equal(await page.evaluate(() => elsewhereView.key), "");
    noModels = false;

    // Enable with Settings > System reachable (a local host): it opens Providers, from the keyboard too.
    const local = await (await browser.newContext({ viewport: { width: 1280, height: 860 } })).newPage();
    await local.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
    await local.route("http://127.0.0.1:18700/**", serve);
    const localWrites = [];
    local.on("request", (r) => r.method() !== "GET" && localWrites.push(r.method() + " " + r.url()));
    await local.goto("http://127.0.0.1:18700/");
    await local.locator("#startup-gate").waitFor({ state: "hidden" });
    const localChip = local.locator("#execution-mode-choice").getByRole("button", { name: "Plugins" });
    const localMenu = local.getByRole("dialog", { name: "Connectors and plugins" });
    const providers = local.locator('button[data-admin-section="providers"][aria-pressed="true"]');
    await local.waitForFunction(() => document.getElementById("plugins-chip").dataset.elsewhere === "true");
    assert.equal(await local.locator("#settings-system-nav").evaluate((el) => el.hidden), false);
    await localChip.click();
    await localMenu.getByRole("button", { name: "Enable" }).click();
    await providers.waitFor();
    assert.equal(await localMenu.isVisible(), false);
    await local.keyboard.press("Escape");
    await localChip.click();
    await localMenu.locator('[data-testid="elsewhere-row"]').first().waitFor();
    for (let i = 0; i < 40 && (await local.evaluate(() => document.activeElement.textContent)) !== "Enable"; i++)
      await local.keyboard.press("Tab");
    assert.equal(await local.evaluate(() => document.activeElement.textContent), "Enable", "Enable is reachable with Tab");
    await local.keyboard.press("Enter");
    await providers.waitFor();
    await local.waitForFunction(() => document.activeElement.closest("#settings-dialog"));
    assert.deepEqual(localWrites, []);

    // Nothing to connect elsewhere: no dot, no section, no carry-over line.
    noElsewhere = true;
    const bare = await (await browser.newContext({ viewport: { width: 1280, height: 860 } })).newPage();
    await bare.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
    await bare.route("http://plugins.test/**", serve);
    await bare.goto("http://plugins.test/");
    await bare.locator("#startup-gate").waitFor({ state: "hidden" });
    const bareChip = bare.locator("#execution-mode-choice").getByRole("button", { name: "Plugins" });
    await bareChip.click();
    const bareMenu = bare.getByRole("dialog", { name: "Connectors and plugins" });
    await bareMenu.getByText("Available in this conversation").waitFor();
    assert.equal(await bareChip.getAttribute("data-elsewhere"), null);
    assert.equal(await bareChip.getAttribute("aria-label"), null);
    assert.equal(await bareMenu.locator('[data-testid="elsewhere-section"]').count(), 0);
    await bare.keyboard.press("Escape");
    await bare.locator("#history .conversation-row > button", { hasText: "Plugins talk" }).click();
    await bare.waitForFunction(() => document.querySelectorAll("#messages article.user").length === 1);
    await bare.locator("#model").selectOption("gpt-6-astra");
    assert.equal(
      await bare.locator("#route-carryover").innerText(),
      "Next message goes to Codex · GPT-6 Astra. The conversation so far goes with it.",
    );
    console.log("PASS composer Plugins chip shows connectors and plugins for the route");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
