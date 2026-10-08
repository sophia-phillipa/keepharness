const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

// Issue #44, D-039: the owner sees the CLI trust boundary in the conversation and
// Plugins page. These fixtures use synthetic project/provider state only.
const project = { id: "demo", label: "Demo project", root: "/fixture/demo" };
const service = () => ({ added: true, enabled: true, models: ["fixture"], projects: ["sem-projeto", "demo"], mode: "native", integrations: [], permissions: {} });
const snapshot = (provider) => ({
  provider,
  engine: provider,
  project_root: project.root,
  items: provider === "codex" ? [{
    id: "plugin:fixture@local", kind: "plugin", name: "fixture", scope: "project",
    enabled: true, source: ".codex/config.toml", writable: true, reason: "", affects: [],
  }] : [],
  fingerprint: "fp-" + provider,
  cli_version: "1.0.0",
  warnings: [],
});
const providerBody = (provider, trusted = false) => ({
  snapshot: snapshot(provider),
  external_changes: [],
  trust: { trusted, required: !trusted },
  mcp_approvals: [
    { server: "docs-local", approved: false },
    { server: "reviewed", approved: true },
  ],
});

function staticResponse(root, pathname) {
  const file = pathname === "/" ? "index.html" : pathname.slice(1);
  return fs.readFile(path.join(__dirname, file.startsWith("assets/") ? "../harness_ui" : root, file)).then((body) => ({
    body,
    contentType: file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : file.endsWith(".svg") ? "image/svg+xml" : "text/html",
  }));
}

function contrastRatio(colors) {
  const lum = (rgb) => {
    const values = rgb.match(/[\d.]+/g).slice(0, 3).map((value) => {
      const channel = Number(value) / 255;
      return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2];
  };
  const [foreground, background] = colors.map(lum);
  return (Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05);
}

(async () => {
  const browser = await chromium.launch();
  try {
    const errors = [];
    const admin = await browser.newPage({ viewport: { width: 1280, height: 850 } });
    admin.on("pageerror", (error) => errors.push("admin: " + error.message));
    const adminState = {
      settings: { services: { codex: service(), claude: service() }, projects: [project, { id: "other", label: "Other project", root: "/fixture/other" }], logins: [], port: 8095, tailnet_port: 8095, uploads_enabled: false },
      inventory: { platform: "Linux", services: [{ id: "codex", name: "Codex CLI", found: true }, { id: "claude", name: "Claude Code", found: true }], projects: [], network: { online: true } },
      authentication: {}, models: {}, integrations: { codex: [], claude: [] }, operations: [], credentials: {}, status: { running: false },
    };
    const adminBodies = { codex: providerBody("codex"), claude: providerBody("claude") };
    const adminReads = [], adminWrites = [];
    let failTrust = true, denyAdminApproval = true;
    await admin.route("http://admin.test/**", async (route) => {
      const url = new URL(route.request().url());
      if (!url.pathname.startsWith("/api/")) return route.fulfill(await staticResponse("../control", url.pathname));
      const name = url.pathname.slice(5), method = route.request().method();
      if (name === "provider-state" && method === "GET") {
        const provider = url.searchParams.get("provider");
        adminReads.push(Object.fromEntries(url.searchParams));
        return route.fulfill({ json: adminBodies[provider] });
      }
      if (name === "provider-state" && method === "POST") {
        const body = route.request().postDataJSON();
        adminWrites.push({ name, body });
        const item = adminBodies[body.provider].snapshot.items.find((entry) => entry.id === body.item_id);
        item.enabled = body.enabled;
        return route.fulfill({ json: { snapshot: adminBodies[body.provider].snapshot } });
      }
      if (name === "provider-state/trust" && method === "POST") {
        const body = route.request().postDataJSON();
        adminWrites.push({ name, body });
        if (failTrust) {
          failTrust = false;
          return route.fulfill({ status: 422, json: { error: "provider_state_validation_failed", message: "CLI state changed; review it and try again." } });
        }
        adminBodies.codex.trust = adminBodies.claude.trust = { trusted: true, required: false };
        return route.fulfill({ json: adminBodies[body.provider] });
      }
      if (name === "provider-state/mcp-approvals" && method === "POST") {
        const body = route.request().postDataJSON();
        adminWrites.push({ name, body });
        if (body.expected_project_root !== adminBodies.claude.snapshot.project_root)
          return route.fulfill({ status: 409, json: { error: "provider_state_conflict", message: "The project folder changed; review its trust prompt again." } });
        const item = adminBodies.claude.mcp_approvals.find((entry) => entry.server === body.server);
        if (body.approved && denyAdminApproval) denyAdminApproval = false;
        else item.approved = body.approved;
        return route.fulfill({ json: adminBodies.claude });
      }
      if (name === "integration-catalog") return route.fulfill({ json: { items: [{ id: "plugin:fixture@local", name: "Fixture", kind: "plugin", status: "installed" }], warnings: [] } });
      return route.fulfill({ json: name === "state" ? adminState : {} });
    });

    await admin.goto("http://admin.test/#plugins");
    await admin.getByLabel("Project for plugins").selectOption("demo");
    const adminTrust = admin.locator('[data-testid="project-trust"]');
    await adminTrust.waitFor();
    assert.match(await adminTrust.innerText(), /applies to both Codex and Claude Code/i); // P1 main: visible language needs no CLI knowledge.
    assert.match(await adminTrust.innerText(), /hooks and environment settings/i);
    assert.match(await adminTrust.innerText(), /versioned \.mcp\.json servers/i);
    assert(adminReads.some((read) => read.project_id === "demo" && read.provider === "claude"));

    await admin.evaluate(() => {
      globalThis.oldAdminTrust = document.querySelector('[data-testid="project-trust"] > button');
      globalThis.oldAdminApproval = document.querySelector('[data-testid="project-mcp-approval"] button');
    });
    await admin.getByLabel("Project for plugins").selectOption("other");
    await admin.evaluate(() => { oldAdminTrust.click(); oldAdminApproval.click(); });
    assert.equal(adminWrites.length, 0, "detached Plugins controls must not authorize the newly selected project");
    await admin.getByLabel("Project for plugins").selectOption("demo");
    await adminTrust.getByRole("button", { name: "Trust Demo project" }).waitFor();
    await admin.evaluate(() => { oldAdminTrust.click(); oldAdminApproval.click(); });
    assert.equal(adminWrites.length, 0, "returning to project A must not revive its old controls");

    for (const palette of ["paper", "graphite"]) {
      const pairs = await admin.evaluate((id) => {
        window.HarnessTheme.apply(id, false);
        const panel = document.querySelector('[data-testid="project-trust"]'),
          warning = panel.querySelector(".project-trust-copy p"),
          button = panel.querySelector(":scope > .button");
        return [
          [getComputedStyle(warning).color, getComputedStyle(panel).backgroundColor],
          [getComputedStyle(button).color, getComputedStyle(button).backgroundColor],
        ];
      }, palette);
      pairs.forEach((pair, index) => assert(contrastRatio(pair) >= 4.5, palette + " admin trust contrast " + index));
    }

    const trustButton = adminTrust.getByRole("button", { name: "Trust Demo project" });
    await trustButton.dblclick(); // P2 main: a rushed double click cannot duplicate a write.
    await admin.getByText(/CLI state changed; review it and try again/).waitFor();
    assert.equal(adminWrites.filter((write) => write.name.endsWith("trust")).length, 1);
    assert.equal(await trustButton.isEnabled(), true); // P1 error: validation failure has a clear retry.
    await trustButton.focus();
    await admin.keyboard.press("Enter"); // P4 main: keyboard-only acceptance.
    await adminTrust.getByRole("button", { name: "Trust Demo project" }).waitFor({ state: "detached" });
    assert.deepEqual(adminWrites.filter((write) => write.name.endsWith("trust")).at(-1).body, { provider: "codex", project_id: "demo", expected_project_root: "/fixture/demo" });

    const pending = admin.locator('[data-testid="project-mcp-approval"]', { hasText: "docs-local" });
    await pending.waitFor();
    assert.match(await pending.innerText(), /Project MCP server[\s\S]*Not approved/); // P3 main: server and state remain explicit.
    const approve = pending.getByRole("button", { name: "Approve docs-local" });
    await approve.focus();
    await admin.keyboard.press("Enter");
    await admin.getByText(/remains not approved because another CLI settings layer denies it/i).waitFor();
    assert.match(await pending.innerText(), /Not approved/, "deny-wins response is rendered instead of optimistic approval");
    await approve.click();
    await admin.waitForFunction(() => document.querySelector('[data-testid="project-mcp-approval"]')?.textContent.includes("Approved"));
    assert.deepEqual(adminWrites.at(-1), { name: "provider-state/mcp-approvals", body: { provider: "claude", project_id: "demo", expected_project_root: "/fixture/demo", server: "docs-local", approved: true } });
    assert.equal(await pending.getByRole("button", { name: "Revoke docs-local" }).evaluate((element) => element === document.activeElement), true); // P4 recovery: focus stays on the changed control.
    await admin.getByRole("switch", { name: "Fixture in Codex" }).click();
    assert.equal(adminWrites.at(-1).body.project_id, "demo", "plugin switches follow the selected project scope");
    assert.equal(await adminTrust.isVisible(), true, "a plugin switch response preserves trust and approval metadata");

    await admin.setViewportSize({ width: 390, height: 780 });
    assert.equal(await admin.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true); // P5 main: narrow layout does not overflow.
    await admin.reload(); // P5 recovery: server state survives reload.
    await admin.getByLabel("Project for plugins").selectOption("demo");
    await admin.waitForFunction(() => document.body.textContent.includes("docs-local"));
    assert.match(await admin.locator('[data-testid="project-mcp-approval"]', { hasText: "docs-local" }).innerText(), /Approved/);

    for (const palette of ["paper", "graphite"]) { // P7 main: approval text and controls remain readable in light and dark themes.
      const pairs = await admin.evaluate((id) => {
        window.HarnessTheme.apply(id, false);
        const row = document.querySelector('[data-testid="project-mcp-approval"]'),
          small = row.querySelector("small"),
          button = row.querySelector("button");
        return [
          [getComputedStyle(row).color, getComputedStyle(row).backgroundColor],
          [getComputedStyle(small).color, getComputedStyle(row).backgroundColor],
          [getComputedStyle(button).color, getComputedStyle(button).backgroundColor],
        ];
      }, palette);
      pairs.forEach((pair, index) => assert(contrastRatio(pair) >= 4.5, palette + " admin approval contrast " + index));
    }

    adminBodies.codex.snapshot.project_root = adminBodies.claude.snapshot.project_root = "/fixture/rebound-demo";
    await adminTrust.getByRole("button", { name: "Revoke docs-local" }).click();
    await admin.getByText(/project folder changed/i).waitFor();
    await adminTrust.getByRole("button", { name: "Revoke docs-local" }).click();
    await adminTrust.getByRole("button", { name: "Approve docs-local" }).waitFor();
    assert.equal(adminWrites.at(-1).body.expected_project_root, "/fixture/rebound-demo", "Plugins conflict recovery requires a fresh explicit click with the refreshed root");

    const chat = await browser.newPage({ viewport: { width: 1000, height: 800 } });
    chat.on("pageerror", (error) => errors.push("chat: " + error.message));
    await chat.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
    const chatBody = providerBody("claude");
    let chatRoot = "/fixture/demo";
    const chatWrites = [], chatReads = [];
    let failApproval = true, releaseConversation;
    const conversationStarted = new Promise((resolve) => { releaseConversation = resolve; });
    let finishConversation;
    const conversationReady = new Promise((resolve) => { finishConversation = resolve; });
    await chat.route("http://chat.test/**", async (route) => {
      const url = new URL(route.request().url()), pathname = url.pathname;
      if (!pathname.startsWith("/v1/")) return route.fulfill(await staticResponse("../agent_service", pathname));
      const method = route.request().method();
      if (pathname === "/v1/projects") return route.fulfill({ json: { projects: ["sem-projeto", "demo", "other"], details: { demo: { label: "Demo project" }, other: { label: "Other project" } } } });
      if (pathname === "/v1/models") return route.fulfill({ json: { models: [{ id: "claude-sonnet-5-5", name: "Claude Sonnet 5.5", backend: "claude", efforts: ["configured"], execution_modes: ["native"] }, { id: "codex-fixture", name: "Codex fixture", backend: "codex", efforts: ["configured"], execution_modes: ["native"] }], providers: { claude: true, codex: true }, uploads_enabled: false } });
      if (pathname === "/v1/conversations/existing-other") {
        releaseConversation();
        await conversationReady;
        return route.fulfill({ json: { title: "Existing other", turns: [{ id: "other-turn", project: "other", state: "completed", request: { backend: "claude", model: "claude-sonnet-5-5", prompt: "Existing turn" }, result: { answer: "Done" } }] } });
      }
      if (pathname === "/v1/conversations") return route.fulfill({ json: { conversations: [] } });
      if (pathname === "/v1/version") return route.fulfill({ json: { version: "fixture", build: "trust" } });
      if (pathname === "/v1/provider-state" && method === "GET") {
        chatReads.push(Object.fromEntries(url.searchParams));
        return route.fulfill({ json: url.searchParams.get("project_id") === "demo" ? { ...chatBody, snapshot: { ...snapshot(url.searchParams.get("provider")), project_root: chatRoot } } : { snapshot: snapshot("claude"), external_changes: [] } });
      }
      if (pathname === "/v1/provider-state/trust" && method === "POST") {
        const body = route.request().postDataJSON(); chatWrites.push({ pathname, body });
        chatBody.trust = { trusted: true, required: false };
        return route.fulfill({ json: chatBody });
      }
      if (pathname === "/v1/provider-state/mcp-approvals" && method === "POST") {
        const body = route.request().postDataJSON(); chatWrites.push({ pathname, body });
        if (failApproval) { failApproval = false; chatRoot = "/fixture/rebound-demo"; return route.fulfill({ status: 409, json: { error: "provider_state_conflict" } }); }
        if (body.expected_project_root !== chatRoot)
          return route.fulfill({ status: 409, json: { error: "provider_state_conflict" } });
        chatBody.mcp_approvals.find((item) => item.server === body.server).approved = body.approved;
        return route.fulfill({ json: chatBody });
      }
      if (pathname === "/v1/integrations") return route.fulfill({ json: { backend: "claude", items: [], elsewhere: [] } });
      return route.fulfill({ json: {} });
    });

    await chat.goto("http://chat.test/");
    await chat.locator("#startup-gate").waitFor({ state: "hidden" });
    await chat.click("#project-button");
    await chat.getByRole("option", { name: "Demo project" }).click();
    const conversationTrust = chat.locator("#project-trust-prompt");
    await conversationTrust.waitFor();
    assert.equal(await conversationTrust.getAttribute("role"), "region");
    assert.match(await conversationTrust.getAttribute("aria-label"), /Project trust and MCP approvals/);
    assert.match(await conversationTrust.innerText(), /applies to both Codex and Claude Code[\s\S]*hooks and environment settings[\s\S]*versioned \.mcp\.json servers/i);
    assert(chatReads.some((read) => read.provider === "claude" && read.project_id === "demo"));
    assert.equal(await chat.locator("#messages article").count(), 0, "trust prompt is UI state, not a synthetic turn"); // P6 main: conversation contract stays clean.
    await chat.evaluate(() => {
      globalThis.oldChatTrust = document.querySelector("#project-trust-prompt > button");
      globalThis.oldChatApproval = document.querySelector("#project-trust-prompt .project-mcp-approval button");
    });
    await chat.evaluate(() => { globalThis.pendingNavigation = load("existing-other"); });
    await conversationStarted;
    assert.equal(await conversationTrust.isVisible(), false, "navigation immediately clears the old prompt");
    await chat.evaluate(() => { oldChatTrust.click(); oldChatApproval.click(); });
    assert.equal(chatWrites.length, 0, "old controls cannot write while conversation navigation is pending");
    finishConversation();
    await chat.evaluate(() => pendingNavigation);
    assert.equal(await chat.locator("#project").inputValue(), "other");
    await chat.evaluate(() => { oldChatTrust.click(); oldChatApproval.click(); });
    assert.equal(chatWrites.length, 0, "opening an existing conversation must invalidate both old authorization controls");
    assert(!await conversationTrust.innerText().then((text) => text.includes("Trust Demo project")));
    assert(chatReads.some((read) => read.project_id === "other"), "existing conversation navigation refreshes trust");
    await chat.evaluate(() => newConversation("New Conversation", "demo"));
    await conversationTrust.getByRole("button", { name: "Trust Demo project" }).waitFor();
    await chat.evaluate(() => { oldChatTrust.click(); oldChatApproval.click(); });
    assert.equal(chatWrites.length, 0, "returning to a new draft in A must not revive the earlier conversation's controls");
    await chat.evaluate(() => {
      globalThis.oldProviderTrust = document.querySelector("#project-trust-prompt > button");
      globalThis.oldProviderApproval = document.querySelector("#project-trust-prompt .project-mcp-approval button");
    });
    await chat.locator("#model").selectOption("codex-fixture");
    await chat.evaluate(() => { oldProviderTrust.click(); oldProviderApproval.click(); });
    assert.equal(chatWrites.length, 0, "changing providers invalidates both rendered authorization controls");
    await conversationTrust.getByRole("button", { name: "Trust Demo project" }).waitFor();
    await chat.locator("#model").selectOption("claude-sonnet-5-5");
    await conversationTrust.getByRole("button", { name: "Trust Demo project" }).waitFor();


    for (const palette of ["paper", "graphite"]) {
      const pairs = await chat.evaluate((id) => {
        window.HarnessTheme.apply(id, false);
        const panel = document.getElementById("project-trust-prompt"),
          warning = panel.querySelector(".project-trust-copy p"),
          detail = panel.querySelector(".project-mcp-approval small"),
          buttons = [...panel.querySelectorAll("button")];
        return [
          [getComputedStyle(warning).color, getComputedStyle(panel).backgroundColor],
          [getComputedStyle(detail).color, getComputedStyle(panel).backgroundColor],
          ...buttons.map((button) => [getComputedStyle(button).color, getComputedStyle(button).backgroundColor]),
        ];
      }, palette);
      pairs.forEach((pair, index) => assert(contrastRatio(pair) >= 4.5, palette + " conversation trust contrast " + index));
    }
    await chat.locator("#prompt").fill("Keep this draft while trust changes");

    await conversationTrust.getByRole("button", { name: "Trust Demo project" }).click();
    await chat.waitForFunction(() => !document.querySelector("#project-trust-prompt")?.textContent.includes("Trust Demo project"));
    assert.deepEqual(chatWrites[0], { pathname: "/v1/provider-state/trust", body: { provider: "claude", project_id: "demo", expected_project_root: "/fixture/demo" } });
    const chatPending = conversationTrust.locator('[data-testid="project-mcp-approval"]', { hasText: "docs-local" });
    await chatPending.getByRole("button", { name: "Approve docs-local" }).click();
    await chat.getByText(/changed elsewhere|try again/i).waitFor(); // P2/P6 error: conflict is surfaced and the action can retry.
    assert.equal(await chatPending.getByRole("button", { name: "Approve docs-local" }).evaluate((element) => element === document.activeElement), true, "conflict recovery restores keyboard focus on the refreshed control");
    for (const palette of ["paper", "graphite"]) {
      const colors = await chat.evaluate((id) => {
        window.HarnessTheme.apply(id, false);
        const panel = document.getElementById("project-trust-prompt"), error = panel.querySelector(".project-trust-error");
        return [getComputedStyle(error).color, getComputedStyle(panel).backgroundColor];
      }, palette);
      assert(contrastRatio(colors) >= 4.5, palette + " conversation error contrast");
    }
    await chatPending.getByRole("button", { name: "Approve docs-local" }).click();
    await chat.waitForFunction(() => document.querySelector('#project-trust-prompt [data-testid="project-mcp-approval"]')?.textContent.includes("Approved"));
    assert.equal(chatWrites.filter((write) => write.pathname.endsWith("mcp-approvals")).length, 2);
    assert.equal(chatWrites.at(-1).body.expected_project_root, "/fixture/rebound-demo", "conversation conflict recovery refreshes the captured root before the next explicit click");
    assert.equal(await chat.locator("#prompt").inputValue(), "Keep this draft while trust changes", "trust actions preserve the draft"); // P2 recovery.
    assert.deepEqual(errors, []);

    console.log("PASS P1 beginner: visible trust meaning and validation retry");
    console.log("PASS P2 rushed user: duplicate guard, conflict retry, draft preservation");
    console.log("PASS P3 domain professional: named MCP state survives reload");
    console.log("PASS P4 accessibility: keyboard actions, region labels, focus continuity");
    console.log("PASS P5 mobile/network: narrow reflow and reload recovery");
    console.log("PASS P6 engineer: exact owner-route payloads and no synthetic turn");
    console.log("PASS P7 UI/UX: complete risk copy and >=4.5 theme contrast");
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
