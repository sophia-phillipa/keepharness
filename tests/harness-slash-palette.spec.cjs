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
            file.startsWith("assets/") ? "../tail_ui" : "../agent_service",
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

    await page.goto("http://slash-palette.test");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    assert.deepEqual(
      await page.evaluate(() => [
        activityTitle({ type: "hook_scope", data: { scope: "project" } }),
        activityTitle({ type: "resource_fallback", data: { scope: "advisory" } }),
        activityTitle({
          type: "invocation_started",
          data: { role: "reviewer", backend: "codex", model: "gpt-6-astra" },
        }),
      ]),
      [
        "Using project hooks only",
        "Resource fallback is advisory",
        "Started reviewer · codex · gpt-6-astra",
      ],
    );
    await page.fill("#prompt", "/dmrv");
    const reviewer = page.locator('[data-resource-id="catalog/demo/agents/reviewer.toml"]');
    await reviewer.waitFor();
    assert.match(await page.locator("#resource-menu").innerText(), /AGENTS · CATALOG · demo/i);
    await page.keyboard.press("Tab");
    assert.equal(await page.inputValue("#prompt"), "/demo--reviewer ");
    assert.equal(await page.locator(".resource-chip").innerText(), "/demo--reviewer ×");
    await page.locator(".resource-chip").click();
    assert.equal(await page.inputValue("#prompt"), "");

    await page.fill("#prompt", "/");
    await page.locator('[data-resource-id="catalog/demo/commands/install.md"]').waitFor();
    assert.match(await page.locator("#resource-menu").innerText(), /MAINTENANCE/);
    assert(await page.locator('[data-resource-id="catalog/demo/rules/paths.md"]').isDisabled());
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
