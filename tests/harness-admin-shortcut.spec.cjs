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
                models: [
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
    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.15.0"));
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
      await page.locator("#admin-shortcut").getAttribute("href"),
      "http://127.0.0.1:8094/",
    );
    await page.click("#new");
    assert.equal(
      await page.locator("#admin-shortcut").getAttribute("href"),
      "http://127.0.0.1:8094/",
      "administration remains available after a new conversation",
    );
    await page.click("#settings");
    assert.equal(
      await page.locator("#admin-shortcut").isVisible(),
      true,
      "settings retains the administrative shortcut",
    );
    assert.equal(
      await page.locator("#admin-shortcut").getAttribute("href"),
      "http://127.0.0.1:8094/",
    );
    console.log(
      "PASS: administrative links survive a new conversation on network hostname",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
