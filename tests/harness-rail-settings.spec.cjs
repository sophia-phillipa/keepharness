// D43 / D44 / D45 (L52): the rail names itself on keyboard focus, Admin/Theme/About live in Settings,
// Settings is grouped as Appearance · Plugins · Agents · Models · Usage · Connect a client · About,
// Space and Scheduled stay reachable on a phone, and Chat | Code is one conversation in two views.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");

const RAIL = [
  "menu",
  "search-conversations",
  "attention-bell",
  "panel-toggle",
  "rail-space",
  "rail-scheduled",
  "rail-runs",
  "rail-agents",
  "settings",
];

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 1280, height: 860 },
    });
    page.on("dialog", () => {
      throw Error("Unexpected browser dialog");
    });
    if (!process.env.HARNESS_URL)
      await page.route("http://panel.test/**", (route) => {
        const pathname = new URL(route.request().url()).pathname;
        return route.fulfill({
          path: path.join(
            __dirname,
            "..",
            pathname.startsWith("/assets/") ? "harness_ui" : "agent_service",
            pathname === "/" ? "index.html" : pathname,
          ),
        });
      });
    await page.route("**/v1/**", (route) => {
      const pathname = new URL(route.request().url()).pathname;
      const data =
        pathname === "/v1/models"
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
          : pathname === "/v1/projects"
            ? { projects: ["sem-projeto"], details: {} }
            : pathname === "/v1/conversations"
              ? { conversations: [] }
              : pathname === "/v1/catalog"
                ? { agents: [], skills: [] }
                : pathname === "/v1/version"
                  ? { version: "fixture", build: "fixture" }
                  : {};
      return route.fulfill({ json: data });
    });
    await page.addInitScript(() =>
      localStorage.setItem("keepharness-tour-seen", "0.16.0"),
    );
    await page.goto(process.env.HARNESS_URL || "http://panel.test/");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });

    // Rail: Admin, Theme and About are gone; every remaining button shows its label on keyboard focus.
    for (const id of ["admin-shortcut-top", "theme-toggle", "about"])
      assert.equal(
        await page.locator("#app-topbar #" + id).count(),
        0,
        id + " left the rail",
      );
    await page.locator("#app-topbar #" + RAIL[0]).focus();
    for (const id of RAIL) {
      const button = page.locator("#" + id);
      await button.focus();
      await page.keyboard.press("Shift+Tab");
      await page.keyboard.press("Tab");
      const label = await button.evaluate((el) => {
        const after = getComputedStyle(el, "::after");
        return {
          content: after.content,
          display: after.display,
          expected: el.title,
        };
      });
      assert.equal(label.display, "block", id + " shows its label on focus");
      assert.equal(label.content, `"${label.expected}"`, id + " label text");
    }
    assert.deepEqual(
      await page.locator("#rail-space use").getAttribute("href"),
      "/assets/icons.svg#file-text",
      "Space has a page icon, not the copy glyph",
    );

    // Settings: the structure of D44, with Theme, About and the tour inside it.
    await page.keyboard.press("Control+,");
    const nav = (
      await page
        .locator("#settings-dialog .settings-nav button:visible")
        .allInnerTexts()
    ).map((text) => text.trim());
    assert.deepEqual(nav, [
      "Appearance",
      "Models",
      "Plugins",
      "Agents",
      "Archived chats",
      "Usage",
      "Connect a client",
      "About",
      "Take the tour",
    ]);
    assert.equal(
      await page.locator("#settings-appearance #theme-toggle").count(),
      1,
    );
    assert.equal(
      await page.locator("#settings-quota use").getAttribute("href"),
      "/assets/icons.svg#gauge",
    );
    await page.click('[data-settings="plugins"]');
    await page.locator("#catalog-skills").waitFor({ state: "attached" });
    assert.equal(
      await page.locator("#settings-plugins h3").innerText(),
      "Plugins",
    );
    assert.equal(
      await page.locator("#settings-plugins p").first().innerText(),
      "Manage plugins, skills, and MCPs",
    );
    assert.equal(
      await page.locator("#settings-plugins #catalog-skills").count(),
      1,
      "Skills live under Plugins",
    );
    await page.click('[data-settings="agents"]');
    await page.locator("#agent-create").waitFor({ state: "visible" });
    assert.equal(
      await page.locator("#settings-agents #harness-agents-list").count(),
      1,
      "Your agents live under Agents",
    );
    assert.equal(await page.locator("#settings-plugins").isHidden(), true);
    await page.click('[data-settings="models"]');
    await page.locator("#settings-models").waitFor({ state: "visible" });
    assert.equal(
      await page.locator("#maestro-plan-policy").count(),
      0,
      "Settings has no plan-review policy",
    );
    await page.click('[data-settings="appearance"]');
    assert(
      await page
        .locator("#panel-order-options i")
        .evaluateAll((nodes) =>
          nodes.every(
            (node) => parseFloat(getComputedStyle(node).fontSize) >= 11,
          ),
        ),
      "visible panel-position text is at least 11px",
    );
    const before = await page.evaluate(
      () => document.documentElement.dataset.theme,
    );
    await page.click("#theme-toggle");
    assert.notEqual(
      await page.evaluate(() => document.documentElement.dataset.theme),
      before,
    );
    for (const [palette, action] of [
      ["paper", "dark"],
      ["graphite", "light"],
    ]) {
      await page.click(`[data-theme-choice="${palette}"]`);
      assert.equal(
        await page.locator("#theme-toggle").innerText(),
        `Switch to ${action} theme`,
      );
    }
    await page.click("#about");
    await page.locator("#about-dialog").waitFor({ state: "visible" });
    assert.match(
      await page.locator("#about-dialog").innerText(),
      /formerly Tail Harness/,
    );
    await page.click("#about-close");
    assert.equal(
      await page.evaluate(() => document.activeElement.id),
      "about",
      "closing About returns to its Settings entry",
    );
    await page.click("#settings-close");

    // The side-panel toggle shows files and run activity beside the chat; the conversation does not change.
    const greeting = await page.locator("#welcome h1").innerText();
    await page.click("#panel-toggle");
    await page.locator("#activity-panel").waitFor({ state: "visible" });
    assert.equal(await page.locator("#welcome h1").innerText(), greeting);
    await page.click("#panel-toggle");
    await page.locator("#activity-panel").waitFor({ state: "hidden" });

    // Phone: the rail hides Space and Scheduled, so the drawer lists them (UX-R1-2).
    for (const width of [620, 390]) {
      await page.setViewportSize({ width, height: 844 });
      assert.equal(
        await page.locator("#rail-space").isVisible(),
        false,
        "no rail button at " + width,
      );
      if (!(await page.locator("#sidebar").isVisible()))
        await page.click("#menu");
      for (const [button, dialog] of [
        ["sidebar-space", "space-dialog"],
        ["sidebar-scheduled", "scheduled-dialog"],
      ]) {
        await page.click("#" + button);
        await page.locator("#" + dialog).waitFor({ state: "visible" });
        await page.keyboard.press("Escape");
        await page.locator("#" + dialog).waitFor({ state: "hidden" });
        if (!(await page.locator("#sidebar").isVisible()))
          await page.click("#menu");
      }
    }
    await page.setViewportSize({ width: 1280, height: 860 });
    assert.equal(
      await page.locator("#sidebar-space").isVisible(),
      false,
      "the drawer entries are phone-only",
    );
    console.log(
      "PASS: rail labels on focus, Settings structure, Theme/About/Admin in Settings, side-panel toggle, Space and Scheduled on a phone",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
