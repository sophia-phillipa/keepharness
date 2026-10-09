// D1: show the actual adapter settings beside every native Access preset.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
// Exercise the real catalog route rather than copying its metadata into a browser fixture.
const catalog = JSON.parse(
  execFileSync(
    process.env.PYTHON || "python3",
    [
      "-c",
      `
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from agent_service.routes.models import models
config = {"full_access": True, "codex": {"unrestricted": True}, "claude": {"unrestricted": True}}
entries = [{"id": ("gpt-6-astra" if p == "codex" else "claude-sonnet-5-5"), "name": p.title(), "backend": p, "model": "fixture", "efforts": ["configured"], "execution_modes": ["native"], "permissions": {"read": True, "write": True, "shell": True}} for p in ("codex", "claude")]
identity = ("local", {"projects": ["p"]})
service = SimpleNamespace(config=config, models_with_context=AsyncMock(return_value=entries), project=lambda *a: None, identity=lambda *a, **k: identity, uploads_enabled=lambda *a: False)
print(asyncio.run(models(SimpleNamespace(query_params={}), service, identity)).body.decode())
`,
    ],
    { cwd: path.join(__dirname, ".."), encoding: "utf8" },
  ),
);
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 390, height: 844 },
    });
    page.setDefaultTimeout(5000);
    await page.route("http://access.test/**", (route) => {
      const p = new URL(route.request().url()).pathname;
      if (!p.startsWith("/v1/"))
        return route.fulfill({
          path: path.join(
            __dirname,
            "..",
            p.startsWith("/assets/") ? "harness_ui" : "agent_service",
            p === "/" ? "index.html" : p,
          ),
        });
      return route.fulfill({
        json:
          p === "/v1/models"
            ? catalog
            : p === "/v1/projects"
              ? { projects: ["p"], details: { p: { label: "Project" } } }
              : p === "/v1/conversations"
                ? { conversations: [] }
                : p === "/v1/integrations"
                  ? {
                      effective_note:
                        "Availability and approvals follow the native CLI configuration and selected access mode.",
                      items: [
                        {
                          id: "mcp:fixture",
                          name: "fixture",
                          kind: "mcp",
                          status: "configured",
                          allowed: null,
                          effective: null,
                          reason:
                            "Availability and approvals follow the native CLI configuration and selected access mode.",
                        },
                      ],
                      elsewhere: [],
                    }
                  : {},
      });
    });
    await page.addInitScript(() =>
      localStorage.setItem("keepharness-tour-seen", "0.16.0"),
    );
    await page.goto("http://access.test/");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    const expected = {
      codex: [
        "read-only · on-request",
        "workspace-write · on-request",
        "danger-full-access · never",
        "read-only · never",
      ],
      claude: ["default", "acceptEdits", "bypassPermissions", "plan"],
    };
    for (const backend of ["codex", "claude", "codex"]) {
      await page.selectOption(
        "#model",
        backend === "codex" ? "gpt-6-astra" : "claude-sonnet-5-5",
      );
      await page.locator("#access-trigger").focus();
      await page.keyboard.press("Enter");
      const menu = page.locator("#access-menu");
      await menu.waitFor({ state: "visible" });
      assert.deepEqual(
        await menu.locator(".access-native-mode").allInnerTexts(),
        expected[backend],
      );
      assert.doesNotMatch(
        await menu.innerText(),
        /connectors and plugins are off|every connector call|asks before every command/,
      );
      if (process.env.ACCESS_SCREENSHOTS)
        await page.screenshot({
          path: path.join(
            process.env.ACCESS_SCREENSHOTS,
            "access-" + backend + "-390.png",
          ),
        });
      const box = await menu.boundingBox();
      assert(
        box.x >= 0 &&
          box.x + box.width <= 390 &&
          box.y >= 0 &&
          box.y + box.height <= 844,
        JSON.stringify(box),
      );
      await page.keyboard.press("End");
      await page.keyboard.press("Enter");
      assert.equal(
        await page.locator("#access-mode").inputValue(),
        "read_only",
      );
      assert.equal(
        await page
          .locator("#access-trigger")
          .evaluate((el) => el === document.activeElement),
        true,
      );
    }
    await page.selectOption("#model", "claude-sonnet-5-5");
    await page.click("#access-trigger");
    await page.locator('#access-menu [data-access="ask"]').click();
    assert.equal(await page.locator("#access-mode").inputValue(), "ask");
    await page.click("#plugins-chip");
    await page
      .getByRole("heading", { name: "Configured in the native CLI" })
      .waitFor();
    assert.equal(
      await page
        .getByRole("heading", {
          name: "Available in this conversation",
          exact: true,
        })
        .count(),
      0,
    );
    await page.locator('[data-integration-id="mcp:fixture"]').click();
    await page
      .getByText("Controlled by the native CLI", { exact: true })
      .waitFor();
    console.log(
      "PASS native Access: backend catalog, four modes, provider switches, keyboard, 390px",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
