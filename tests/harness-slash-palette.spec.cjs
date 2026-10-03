// One slash palette: fuzzy search, grouped metadata, chips, preview and mobile bounds.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await page.route("http://slash-palette.test/**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith("/v1/")) {
        let data = {};
        if (url.pathname === "/v1/projects")
          data = { projects: ["p"], details: { p: { label: "Project" } } };
        if (url.pathname === "/v1/models")
          data = {
            models: [{ id: "gpt-6-astra", backend: "codex", efforts: ["medium"] }],
            providers: { codex: true },
          };
        if (url.pathname === "/v1/usage") data = { available: false };
        if (url.pathname === "/v1/conversations") data = { conversations: [] };
        if (url.pathname === "/v1/resources")
          data = {
            items: [
              {
                id: "catalog/demo/agents/reviewer.toml",
                resource_id: "catalog/demo/agents/reviewer.toml",
                revision: "a1",
                kind: "agent",
                name: "demo--reviewer",
                description: "Review implementation evidence",
                scope: "catalog",
                origin: "demo",
                group: "Agents",
                catalog_commit: "0123456789abcdef0123456789abcdef01234567",
                catalog_pinned: true,
                catalog_dirty: false,
                source: "/catalog/agents/reviewer.toml",
                argument_hint: "<change>",
                selectable: true,
                preflight_hint: "Ready to delegate.",
              },
              {
                id: "project/p/.agents/skills/check/SKILL.md",
                revision: "s1",
                kind: "skill",
                name: "check",
                description: "Check a change",
                scope: "project",
                origin: "agents",
                group: "Skills",
                source: "/project/.agents/skills/check/SKILL.md",
                selectable: true,
              },
              {
                id: "catalog/demo/commands/install.md",
                revision: "c1",
                kind: "command",
                name: "install",
                description: "Install catalog resources",
                scope: "catalog",
                origin: "demo",
                group: "Maintenance",
                source: "/catalog/commands/install.md",
                selectable: true,
              },
              {
                id: "catalog/demo/rules/paths.md",
                revision: "r1",
                kind: "rule",
                name: "paths",
                description: "Applied to matching files",
                scope: "catalog",
                origin: "demo",
                group: "Rules",
                source: "/catalog/rules/paths.md",
                selectable: false,
                unavailable_reason: "Loaded automatically.",
                preflight_hint: "Visible for context only.",
              },
            ],
            warnings: [],
          };
        if (url.pathname.endsWith("/events")) return route.abort();
        return route.fulfill({ json: data });
      }
      const file = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
      return route.fulfill({
        body: await fs.readFile(
          path.join(
            __dirname,
            file.startsWith("assets/") ? "../harness_ui" : "../agent_service",
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

    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.15.0"));

    await page.goto("http://slash-palette.test");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    // Chat-first shell: the workspace panel starts closed; this check reads its resources list.
    if (await page.locator("#activity-panel").isHidden()) await page.click("#panel-toggle");
    const promptBox = page.locator("#prompt");
    assert.equal(await promptBox.getAttribute("role"), "combobox");
    assert.equal(await page.getByRole("combobox", { name: "Message" }).count(), 1);
    assert.equal(await promptBox.getAttribute("aria-haspopup"), "listbox");
    assert.equal(await promptBox.getAttribute("aria-controls"), "resource-menu");
    assert.equal(await promptBox.getAttribute("aria-expanded"), "false");
    assert.deepEqual(
      await page.evaluate(() => [
        activityTitle({ type: "hook_scope", data: { scope: "project" } }),
        activityTitle({ type: "resource_fallback", data: { scope: "advisory" } }),
        activityTitle({
          type: "invocation_started",
          data: {
            invocation: {
              kind: "agent",
              resource_id: "catalog/demo/agents/reviewer.toml",
              mode: "delegated",
            },
            role: "reviewer",
            backend: "codex",
            model: "gpt-6-astra",
          },
        }),
      ]),
      [
        "Using project hooks only",
        "Resource fallback is advisory",
        "Started reviewer · codex · gpt-6-astra",
      ],
    );
    await page.fill("#prompt", "/dmrv");
    const reviewer = page.locator(
      '#resource-menu [data-resource-id="catalog/demo/agents/reviewer.toml"]',
    );
    await reviewer.waitFor();
    assert.equal(await reviewer.getAttribute("aria-selected"), "true");
    assert.match(await page.locator("#resource-menu").innerText(), /AGENTS · CATALOG · demo/i);
    assert.match(await reviewer.innerText(), /Pinned · 0123456789ab/);
    assert.match(await page.locator("#resource-preview").innerText(), /Pinned commit 0123456789ab/);
    assert.equal(
      await page.locator("#resource-preview").getAttribute("title"),
      "Catalog demo · pinned commit 0123456789abcdef0123456789abcdef01234567",
    );
    const workspaceReviewer = page.locator(
      '#workspace-resources [data-resource-id="catalog/demo/agents/reviewer.toml"]',
    );
    await workspaceReviewer.waitFor();
    assert.match(await workspaceReviewer.innerText(), /catalog · demo[\s\S]*Pinned · 0123456789ab/i);
    await page.keyboard.press("ArrowDown");
    assert.equal(
      await page.evaluate(() => document.activeElement.dataset.resourceId),
      "catalog/demo/agents/reviewer.toml",
    );
    assert.equal(await reviewer.getAttribute("aria-selected"), "true");
    await page.keyboard.press("Escape");
    assert.equal(await page.evaluate(() => document.activeElement.id), "prompt");
    assert.equal(
      await page.locator("#resource-menu").evaluate((menu) =>
        menu.matches(":popover-open"),
      ),
      false,
    );
    await page.fill("#prompt", "/dmrv");
    await reviewer.waitFor();
    await page.mouse.click(1270, 10);
    assert.equal(
      await page.locator("#resource-menu").evaluate((menu) =>
        menu.matches(":popover-open"),
      ),
      false,
    );
    await page.fill("#prompt", "");
    await page.fill("#prompt", "/dmrv");
    await reviewer.waitFor();
    await page.keyboard.press("Tab");
    assert.equal(await page.inputValue("#prompt"), "/demo--reviewer ");
    assert.equal(await page.locator(".resource-chip").innerText(), "/demo--reviewer ×");
    await page.locator(".resource-chip").click();
    assert.equal(await page.inputValue("#prompt"), "");

    await page.fill("#prompt", "/check");
    await page.locator('#resource-menu [data-resource-id="project/p/.agents/skills/check/SKILL.md"]').click();
    await page.keyboard.type("/install");
    await page.locator('#resource-menu [data-resource-id="catalog/demo/commands/install.md"]').click();
    assert.equal(
      await page.locator(".resource-chain-preview").innerText(),
      "Runs in order: 1 /check → 2 /install",
    );
    assert.equal(
      await page.evaluate(() => {
        resourceSelections = [
          { id: "short", revision: "1", token: "/write" },
          { id: "long", revision: "1", token: "/writer" },
        ];
        $("prompt").value = "/writer a /write b";
        renderResourceChips();
        return document.querySelector(".resource-chain-preview").textContent;
      }),
      "Runs in order: 1 /writer → 2 /write",
    );
    assert.equal(
      await page.evaluate(() =>
        resourceMatchScore(
          { name: "short", description: "x".repeat(140) + "late-suffix" },
          "late-suffix",
        ),
      ),
      1,
    );
    await page.evaluate(() => {
      resourceSelections = [
        { id: "project/p/.agents/skills/check/SKILL.md", revision: "s1", token: "/check" },
        { id: "catalog/demo/commands/install.md", revision: "c1", token: "/install" },
      ];
      $("prompt").value = "before  /check   after\t";
      renderResourceChips();
    });
    await page.locator('.resource-chip[aria-label="Remove /check"]').click();
    assert.equal(await page.inputValue("#prompt"), "before    after\t");

    await page.fill("#prompt", "/model");
    await page.locator('#resource-menu [data-resource-id="builtin/model"]').waitFor();
    assert.match(await page.locator("#resource-menu").innerText(), /BUILT-INS/);
    await page.keyboard.press("Tab");
    assert.equal(await page.inputValue("#prompt"), "");
    assert(await page.locator("#model-menu").evaluate((node) => node.matches(":popover-open")));
    await page.keyboard.press("Escape");
    assert.equal(await page.evaluate(() => document.activeElement.id), "model-trigger");

    await page.fill("#prompt", "/");
    await page.locator('#resource-menu [data-resource-id="catalog/demo/commands/install.md"]').waitFor();
    assert.match(await page.locator("#resource-menu").innerText(), /MAINTENANCE/);
    assert(await page.locator('#resource-menu [data-resource-id="catalog/demo/rules/paths.md"]').isDisabled());
    await reviewer.focus();
    assert.match(await page.locator("#resource-preview").innerText(), /<change>/);
    assert.match(await page.locator("#resource-preview").innerText(), /reviewer\.toml/);

    await page.setViewportSize({ width: 390, height: 844 });
    await page.fill("#prompt", "");
    await page.fill("#prompt", "/");
    await reviewer.waitFor({ state: "visible" });
    const bounds = await page.locator("#resource-menu").boundingBox();
    assert(bounds.x >= 0 && bounds.x + bounds.width <= 390);
    assert(bounds.y >= 0 && bounds.y + bounds.height <= 844);
    await reviewer.click();
    assert.equal(await page.inputValue("#prompt"), "/demo--reviewer ");
    console.log("PASS: unified slash palette, fuzzy match, chips, preview and mobile bounds");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
