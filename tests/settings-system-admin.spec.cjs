// Settings › System shows the local admin on the same screen (Sophia, 2026-10-03):
// the Settings submenu opens it, the admin hides its own navigation and
// follows the harness theme, and its CSP lets only the harness origin frame it.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");

(async () => {
  const admin = (process.env.ADMIN_URL || "").replace(/\/$/, "");
  if (!admin) throw new Error("ADMIN_URL is required (run through scripts/test-ui.sh)");
  const policy = (await fetch(admin + "/")).headers.get("content-security-policy");
  const harness = policy.match(/frame-ancestors (http:\/\/127\.0\.0\.1:\d+)/)?.[1];
  assert(harness, "admin names the harness origin it may be framed by: " + policy);

  // The harness page is served by a Playwright route, which Chrome treats as a public
  // address; real use serves it from 127.0.0.1, so loopback-to-loopback framing is allowed.
  const browser = await chromium.launch({ args: ["--disable-features=LocalNetworkAccessChecks"] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, colorScheme: "dark" });
    // The framed admin answers only the owner's browser (scripts/test-ui.sh exports its cookie).
    const [cookieName, ...cookieValue] = (process.env.ADMIN_LOCAL_COOKIE || "").split("=");
    if (cookieName && cookieValue.length)
      await page.context().addCookies([{ name: cookieName, value: cookieValue.join("="), domain: "127.0.0.1", path: "/", httpOnly: true, sameSite: "Strict" }]);
    // Serve this checkout's harness at the origin the admin trusts.
    const serveHarness = async (route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname.startsWith("/v1/")) {
        const data =
          pathname === "/v1/models"
            ? {
                models: [{ id: "fixture", name: "Fixture", backend: "codex", efforts: ["low"] }],
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
    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
    await page.goto(harness + "/");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });

    // D43: the rail has no Admin button; Settings › System opens the same admin.
    await page.click("#settings");
    await page.getByRole("menuitem", { name: "Providers" }).click();
    const dialog = page.locator("#settings-dialog");
    await dialog.waitFor({ state: "visible" });
    const providers = dialog.getByRole("button", { name: "Providers" });
    assert.equal(await providers.getAttribute("aria-pressed"), "true");
    assert(await dialog.getByRole("group", { name: "System" }).isVisible());
    assert(await page.locator("#catalog-refresh").isHidden(), "catalog refresh belongs to agents and skills");

    const frame = page.locator("#admin-frame");
    const src = new URL(await frame.getAttribute("src"));
    assert.equal(src.origin, admin);
    assert.equal(src.searchParams.get("embedded"), "1");
    assert.equal(src.searchParams.get("theme"), "graphite");
    assert.equal(src.hash, "#providers");
    assert.equal(await frame.getAttribute("title"), "Administration: Providers");

    const content = page.frameLocator("#admin-frame");
    // A fresh admin may open its provider wizard as a modal, so read the heading by id.
    const heading = content.locator("#overview h1");
    await heading.filter({ hasText: "AI Providers" }).waitFor({ state: "attached" });
    assert(await content.locator(".sidebar").isHidden(), "the harness owns the navigation");
    assert(await content.locator("#open-harness").isHidden());
    assert.equal(await content.locator("html").getAttribute("data-palette"), "graphite");
    const frameBox = await frame.boundingBox();
    assert(frameBox.height > 700 && frameBox.width > 900, JSON.stringify(frameBox));

    await dialog.getByRole("button", { name: "Run history" }).click();
    await heading.filter({ hasText: /^Runs$/ }).waitFor({ state: "attached" });
    assert.equal(await frame.getAttribute("title"), "Administration: Run history");

    await dialog.getByRole("button", { name: "Appearance" }).click();
    assert(await page.locator("#settings-system").isHidden());
    assert(await page.locator("#catalog-refresh").isVisible());
    // Opened as localhost, the page cannot frame a 127.0.0.1 admin (different site): no System group.
    const other = await browser.newPage();
    const localhost = harness.replace("127.0.0.1", "localhost");
    await other.route(localhost + "/**", serveHarness);
    await other.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
    await other.goto(localhost + "/");
    await other.locator("#startup-gate").waitFor({ state: "hidden" });
    assert.equal(await other.locator("#settings-system-nav").isHidden(), true);
    await other.click("#settings");
    assert.equal(await other.getByRole("menuitem", { name: "Providers" }).count(), 0);
    console.log("PASS settings system shows the admin on the same screen");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
