// #62: the keyboard-focused Access menu item shows a visible, high-contrast focus indicator in every palette.
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
    const failures = [];
    for (const width of [1280, 390]) {
      const page = await browser.newPage({
        viewport: { width, height: 844 },
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
      const palettes = await page.evaluate(() =>
        HarnessTheme.themes.map((t) => t.id),
      );
      assert(palettes.includes("paper") && palettes.length === 8, palettes);
      for (const palette of palettes) {
        await page.evaluate((id) => HarnessTheme.apply(id), palette);
        await page.selectOption("#model", "gpt-6-astra");
        await page.locator("#access-trigger").focus();
        await page.keyboard.press("Enter");
        await page.locator("#access-menu").waitFor({ state: "visible" });
        await page.keyboard.press("ArrowDown");
        // Under load the options may still be rebuilding: wait until one of them owns the focus.
        await page.waitForFunction(() =>
          document.activeElement?.matches('#access-menu [role="option"]'),
        );
        const result = await page.evaluate(() => {
          const item = document.activeElement;
          const parse = (css) => {
            const c = document.createElement("canvas").getContext("2d");
            c.fillStyle = css;
            c.fillRect(0, 0, 1, 1);
            return [...c.getImageData(0, 0, 1, 1).data].slice(0, 3);
          };
          const relativeLuminance = ([r, g, b]) =>
            0.2126 * linearChannel(r) +
            0.7152 * linearChannel(g) +
            0.0722 * linearChannel(b);
          const linearChannel = (v) => {
            v /= 255;
            return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
          };
          const ratio = (a, b) => {
            const [hi, lo] = [
              relativeLuminance(parse(a)),
              relativeLuminance(parse(b)),
            ].sort((x, y) => y - x);
            return (hi + 0.05) / (lo + 0.05);
          };
          const style = getComputedStyle(item);
          const menuBg = getComputedStyle(
            item.closest(".composer-menu"),
          ).backgroundColor;
          return {
            isOption: item.matches('#access-menu [role="option"][data-access]'),
            visible: item.matches(":focus-visible"),
            style: style.outlineStyle,
            width: parseFloat(style.outlineWidth),
            color: style.outlineColor,
            contrastWithItem: ratio(style.outlineColor, style.backgroundColor),
            contrastWithMenu: ratio(style.outlineColor, menuBg),
          };
        });
        const ok =
          result.isOption &&
          result.visible &&
          result.style !== "none" &&
          result.width >= 2 &&
          result.contrastWithItem >= 3 &&
          result.contrastWithMenu >= 3;
        if (!ok)
          failures.push(`${palette}@${width}: ${JSON.stringify(result)}`);
        await page.keyboard.press("Escape");
      }
      await page.close();
    }
    assert.deepEqual(failures, []);
    console.log(
      "PASS Access menu focus indicator: 8 palettes (Paper and dark) x 1280/390px, outline >= 2px, >= 3:1",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
