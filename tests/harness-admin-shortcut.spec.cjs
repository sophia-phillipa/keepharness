const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 1280, height: 860 },
    });
    if (!process.env.HARNESS_URL)
      await page.route("http://panel.test/**", (route) => {
        const pathname = new URL(route.request().url()).pathname;
        return route.fulfill({
          path: require("node:path").join(
            __dirname,
            "..",
            pathname.startsWith("/assets/") ? "harness_ui" : "agent_service",
            pathname === "/" ? "index.html" : pathname,
          ),
        });
      });
    await page.addInitScript(() => {
      localStorage.removeItem("sidebar-collapsed");
      localStorage.setItem("activity-open", "0");
    });
    let emptyModels = false;
    await page.route("http://127.0.0.1:8094/**", (route) =>
      route.fulfill({ contentType: "text/html", body: "<title>admin</title>" }),
    );
    await page.route("**/v1/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/v1/projects")
        await new Promise((resolve) => setTimeout(resolve, 250));
      const data =
        path === "/v1/projects"
          ? {
              projects: ["project-a", "sem-projeto"],
              details: { "project-a": { label: "Project Alpha" } },
            }
          : path === "/v1/models"
            ? {
                admin_url: "http://127.0.0.1:8094/",
                models: emptyModels
                  ? []
                  : [
                      {
                        id: "fixture",
                        name: "Fixture",
                        backend: "local",
                        efforts: ["low"],
                      },
                      {
                        id: "cloud-fixture",
                        name: "Cloud fixture",
                        backend: "codex",
                        efforts: ["low"],
                      },
                    ],
                providers: { local: true, codex: true },
                uploads_enabled: false,
              }
            : path === "/v1/conversations"
              ? { conversations: [] }
              : path === "/v1/version"
                ? { version: "fixture", build: "fixture" }
                : {};
      return route.fulfill({ json: data });
    });
    await page.addInitScript(() =>
      localStorage.setItem("keepharness-tour-seen", "0.16.0"),
    );
    await page.goto(process.env.HARNESS_URL || "http://panel.test/");
    await page.locator("#startup-gate").waitFor({ state: "visible" });
    assert.equal(
      await page.locator("#app-topbar").evaluate((el) => el.inert),
      true,
      "topbar stays inert while connection gate is active",
    );
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    assert.equal(
      await page.locator("#app-topbar").evaluate((el) => el.inert),
      false,
    );
    assert.equal(
      await page.locator("#app-topbar > :first-child").getAttribute("id"),
      "app-brand",
    );
    assert.equal(await page.locator("#app-brand").innerText(), "KeepHarness");
    assert.equal(await page.locator("#sidebar .brand").count(), 0);
    // D43: the rail no longer carries an Admin button; Settings holds the shortcut.
    assert.equal(await page.locator("#admin-shortcut-top").count(), 0);
    assert.equal(
      await page.locator("#admin-shortcut").count(),
      0,
      "the Admin panel link is gone",
    );
    await page.click("#new");
    // "Open admin panel" opens Settings > Providers on this page, not a window.
    assert.equal(
      await page.locator("#admin-link").getAttribute("href"),
      "http://127.0.0.1:8094/",
    );
    await page.keyboard.press("Control+,");
    assert.equal(await page.locator("#settings-dialog").isVisible(), true);
    assert.equal(
      await page.locator("#admin-shortcut").count(),
      0,
      "Settings holds sections, not an Admin link",
    );
    await page.keyboard.press("Escape");
    // This page is a network host (panel.test), so Settings > System is hidden and the admin cannot
    // be framed: "Open admin panel" must then fall back to its link instead of doing nothing.
    emptyModels = true;
    await page.click("#models-retry");
    await page.locator("#model-availability").waitFor({ state: "visible" });
    // Against a live harness at 127.0.0.1 System is available: the link opens Settings instead.
    const systemHidden = await page
      .locator("#settings-system-nav")
      .evaluate((el) => el.hidden);
    assert.equal(systemHidden, !process.env.HARNESS_URL);
    await page.click("#admin-link");
    if (systemHidden) await page.waitForURL("http://127.0.0.1:8094/");
    else {
      await page.locator("#settings-dialog").waitFor({ state: "visible" });
      assert.equal(page.url().startsWith("http://127.0.0.1:8094/"), false);
    }
    console.log(
      "PASS: no Admin shortcut; the admin URL stays available to Settings",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
