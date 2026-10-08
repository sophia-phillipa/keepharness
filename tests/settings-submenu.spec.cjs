// The Settings button opens a submenu of the visible Settings sections; each item jumps to
// that section (WP2). Ctrl+, opens the dialog at the last section without the submenu.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");

(async () => {
  const admin = (process.env.ADMIN_URL || "").replace(/\/$/, "");
  if (!admin)
    throw new Error("ADMIN_URL is required (run through scripts/test-ui.sh)");
  const policy = (await fetch(admin + "/")).headers.get(
    "content-security-policy",
  );
  const harness = policy.match(
    /frame-ancestors (http:\/\/127\.0\.0\.1:\d+)/,
  )?.[1];
  assert(
    harness,
    "admin names the harness origin it may be framed by: " + policy,
  );

  // The harness page is served by a Playwright route, which Chrome treats as a public
  // address; real use serves it from 127.0.0.1, so loopback-to-loopback framing is allowed.
  const browser = await chromium.launch({
    args: ["--disable-features=LocalNetworkAccessChecks"],
  });
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 900 },
      colorScheme: "dark",
    });
    // The framed admin answers only the owner's browser (scripts/test-ui.sh exports its cookie).
    const [cookieName, ...cookieValue] = (
      process.env.ADMIN_LOCAL_COOKIE || ""
    ).split("=");
    if (cookieName && cookieValue.length)
      await page.context().addCookies([
        {
          name: cookieName,
          value: cookieValue.join("="),
          domain: "127.0.0.1",
          path: "/",
          httpOnly: true,
          sameSite: "Strict",
        },
      ]);
    // Serve this checkout's harness at the origin the admin trusts.
    const serveHarness = async (route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname.startsWith("/v1/")) {
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
                admin_url: admin + "/",
              }
            : pathname === "/v1/projects"
              ? { projects: ["sem-projeto"], details: {} }
              : pathname === "/v1/conversations"
                ? { conversations: [] }
                : pathname === "/v1/version"
                  ? { version: "fixture", build: "fixture" }
                  : {};
        return route.fulfill({ json: data });
      }
      return route.fulfill({
        path: path.join(
          __dirname,
          "..",
          pathname.startsWith("/assets/") ? "harness_ui" : "agent_service",
          pathname === "/" ? "index.html" : pathname,
        ),
      });
    };
    await page.route(harness + "/**", serveHarness);
    await page.addInitScript(() =>
      localStorage.setItem("keepharness-tour-seen", "0.16.0"),
    );
    await page.goto(harness + "/");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });

    const menu = page.locator("#settings-menu");
    const dialog = page.locator("#settings-dialog");
    const names = (locator) =>
      locator.evaluateAll((nodes) => nodes.map((n) => n.textContent.trim()));
    assert.equal(
      await page.locator("#settings").getAttribute("aria-haspopup"),
      "menu",
    );
    assert.equal(
      await page.locator("#admin-shortcut").count(),
      0,
      "no separate Admin link",
    );

    // Click Settings: a menu with the groups, not the dialog.
    await page.click("#settings");
    await menu.waitFor({ state: "visible" });
    assert.equal(await dialog.evaluate((d) => d.open), false);
    assert.equal(await menu.getAttribute("role"), "menu");
    assert.deepEqual(await names(menu.locator('[role="group"] > p')), [
      "Personal",
      "Integrations",
      "Archived",
      "System",
    ]);
    assert.deepEqual(await names(menu.getByRole("menuitem")), [
      "Appearance",
      "Models",
      "Plugins",
      "Agents",
      "Archived chats",
      "Providers",
      "Operations",
      "Run history",
      "Catalogs and vault",
      "Connection / MCP",
    ]);

    // Keyboard: the first item has focus, arrows move it, Esc closes and returns focus to the button.
    assert.equal(
      await page.evaluate(() => document.activeElement.textContent.trim()),
      "Appearance",
    );
    await page.keyboard.press("ArrowDown");
    assert.equal(
      await page.evaluate(() => document.activeElement.textContent.trim()),
      "Models",
    );
    await page.keyboard.press("ArrowUp");
    await page.keyboard.press("ArrowUp");
    assert.equal(
      await page.evaluate(() => document.activeElement.textContent.trim()),
      "Connection / MCP",
      "arrows wrap",
    );
    await page.keyboard.press("Escape");
    await menu.waitFor({ state: "hidden" });
    assert.equal(
      await page.evaluate(() => document.activeElement.id),
      "settings",
    );

    // On every palette the open menu is a solid surface: nothing behind it shows through.
    for (const palette of [
      "graphite",
      "paper",
      "violet-bordeaux",
      "porcelain",
      "mineral-rose",
      "amethyst",
      "petroleum",
      "arizona",
    ]) {
      await page.evaluate((name) => {
        document.documentElement.dataset.palette = name;
      }, palette);
      await page.click("#settings");
      await menu.waitFor({ state: "visible" });
      const solid = await menu.evaluate((m) => {
        const style = getComputedStyle(m);
        const alpha = style.backgroundColor.match(
          /\/\s*([\d.]+)\s*\)|^rgba\(.*,\s*([\d.]+)\)$/,
        );
        const box = m.getBoundingClientRect();
        const topmost = [
          [10, 10],
          [box.width / 2, box.height / 2],
          [box.width - 10, box.height - 10],
        ].map(([dx, dy]) => {
          const hit = document.elementsFromPoint(
            box.left + dx,
            box.top + dy,
          )[0];
          return m.contains(hit);
        });
        return {
          alpha: alpha ? Number(alpha[1] ?? alpha[2]) : 1,
          opacity: Number(style.opacity),
          topmost,
        };
      });
      assert.deepEqual(
        solid,
        { alpha: 1, opacity: 1, topmost: [true, true, true] },
        "settings menu is opaque on " + palette,
      );
      await page.keyboard.press("Escape");
      await menu.waitFor({ state: "hidden" });
    }

    // Pick "Run history": dialog open at that section, admin framed at #runs.
    await page.click("#settings");
    await menu.getByRole("menuitem", { name: "Run history" }).click();
    await dialog.waitFor({ state: "visible" });
    assert.equal(
      await dialog
        .locator('[data-admin-section="runs"]')
        .getAttribute("aria-pressed"),
      "true",
    );
    assert.equal(
      new URL(await page.locator("#admin-frame").getAttribute("src")).hash,
      "#runs",
    );
    await page.click("#settings-close");

    // Plugins is the existing admin facade, embedded at #plugins rather than the native skill catalog.
    await page.click("#settings");
    await menu.getByRole("menuitem", { name: "Plugins" }).click();
    await dialog.waitFor({ state: "visible" });
    assert.equal(
      await dialog
        .locator('[data-admin-section="plugins"]')
        .getAttribute("aria-pressed"),
      "true",
    );
    assert.equal(
      new URL(await page.locator("#admin-frame").getAttribute("src")).hash,
      "#plugins",
    );
    const adminFrame = page.frameLocator("#admin-frame");
    await adminFrame.locator("#overview h1", { hasText: "Plugins" }).waitFor();
    assert.equal(
      await adminFrame.locator("#overview .panel-description").innerText(),
      "Manage plugins, skills, and MCPs",
    );
    assert.equal(
      await adminFrame
        .locator('[data-testid="plugins-panel"] button', { hasText: "Plugins" })
        .first()
        .isVisible(),
      true,
    );
    await page.click("#settings-close");

    // The rail reaches the same real facade and keeps Agents as a separate Settings page.
    await page.click("#rail-agents");
    await dialog
      .locator('[data-admin-section="plugins"][aria-pressed="true"]')
      .waitFor();
    assert.equal(
      new URL(await page.locator("#admin-frame").getAttribute("src")).hash,
      "#plugins",
    );
    await adminFrame
      .locator('[data-testid="plugins-panel"] button', { hasText: "Plugins" })
      .first()
      .waitFor();
    await page.click('[data-settings="agents"]');
    await page.locator("#settings-agents:not([hidden])").waitFor();
    await page.click("#settings-close");

    // The composer menu's Codex entry point also reaches the rendered admin facade.
    await page.click("#plugins-chip");
    const managePlugins = page
      .locator("#plugins-menu")
      .getByRole("button", { name: "Discover and manage plugins" });
    await managePlugins.waitFor();
    await managePlugins.click();
    await dialog
      .locator('[data-admin-section="plugins"][aria-pressed="true"]')
      .waitFor();
    assert.equal(
      new URL(await page.locator("#admin-frame").getAttribute("src")).hash,
      "#plugins",
    );
    await adminFrame
      .locator('[data-testid="plugins-panel"] button', { hasText: "Plugins" })
      .first()
      .waitFor();
    await page.click("#settings-close");

    // "Open admin panel" (#admin-link) is Settings > Providers, not a window.
    await page.evaluate(() => document.querySelector("#admin-link").click());
    await dialog.waitFor({ state: "visible" });
    assert.equal(
      await dialog
        .locator('[data-admin-section="providers"]')
        .getAttribute("aria-pressed"),
      "true",
    );
    await page.click("#settings-close");

    // Ctrl+, opens the dialog directly at the last section; no submenu.
    await page.keyboard.press("Control+,");
    await dialog.waitFor({ state: "visible" });
    assert.equal(await menu.evaluate((m) => m.matches(":popover-open")), false);
    assert.equal(
      await dialog
        .locator('[data-admin-section="providers"]')
        .getAttribute("aria-pressed"),
      "true",
    );
    await page.click("#settings-close");

    // Back after picking a section returns to the previous view (a conversation-less Home here).
    await page.click("#settings");
    await menu.getByRole("menuitem", { name: "Models" }).click();
    await dialog.waitFor({ state: "visible" });
    assert.equal(
      await dialog
        .locator('[data-settings="models"]')
        .getAttribute("aria-pressed"),
      "true",
    );
    await page.keyboard.press("Control+[");
    await dialog.waitFor({ state: "hidden" });

    // The desktop hash contract: #open=settings/<section> opens Settings there and is cleared.
    await page.evaluate(() => {
      location.hash = "open=settings/connection";
    });
    await dialog.waitFor({ state: "visible" });
    assert.equal(
      await dialog
        .locator('[data-admin-section="connection"]')
        .getAttribute("aria-pressed"),
      "true",
    );
    assert.equal(await page.evaluate(() => location.hash), "");

    // Opened as localhost, the page cannot frame a 127.0.0.1 admin: the System items are absent.
    const other = await browser.newPage();
    const localhost = harness.replace("127.0.0.1", "localhost");
    await other.route(localhost + "/**", serveHarness);
    await other.addInitScript(() =>
      localStorage.setItem("keepharness-tour-seen", "0.16.0"),
    );
    await other.goto(localhost + "/");
    await other.locator("#startup-gate").waitFor({ state: "hidden" });
    await other.click("#settings");
    assert.deepEqual(
      await names(other.locator("#settings-menu").getByRole("menuitem")),
      ["Appearance", "Models", "Plugins", "Agents", "Archived chats"],
    );
    assert.equal(await other.evaluate(() => openSettings("runs")), false);
    assert.equal(
      await other.locator("#settings-dialog").evaluate((d) => d.open),
      false,
    );
    await other.click("#rail-agents");
    await other.locator("#settings-agents:not([hidden])").waitFor();
    assert.equal(
      await other
        .locator('[data-settings="agents"]')
        .getAttribute("aria-pressed"),
      "true",
    );
    await other.click("#settings-close");
    await other.setViewportSize({ width: 390, height: 844 });
    await other.click("#settings");
    await other
      .locator("#settings-menu")
      .getByRole("menuitem", { name: "Plugins" })
      .click();
    await other.locator("#settings-plugins:not([hidden])").waitFor();
    console.log("PASS settings submenu opens each section");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
