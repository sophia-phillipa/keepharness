// The 10 s background refresh of the conversation list rebuilds the whole sidebar. It must not
// do that while a row or project actions menu is open (the menu would snap shut under the
// pointer), and it must go on refreshing once the menu is closed. Route fixtures only.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    page.setDefaultTimeout(5000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.clock.install();
    await page.route("http://poll.test/**", (route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname.startsWith("/v1/")) {
        let data = {};
        if (pathname === "/v1/projects") data = { projects: ["sem-projeto", "alpha"], details: { alpha: { label: "Alpha", root: "/home/user/first" } } };
        else if (pathname === "/v1/models") data = { models: [{ id: "fixture", name: "Fixture", backend: "local", efforts: ["low"], permissions: { upload: true } }], providers: { local: true }, uploads_enabled: true };
        else if (pathname === "/v1/conversations") {
          data = { conversations: [{ id: "c1", title: "Poll chat", project: "sem-projeto", state: "completed", backend: "local", model: "fixture" }] };
        } else if (pathname === "/v1/version") data = { version: "fixture", build: "poll" };
        return route.fulfill({ json: data });
      }
      return route.fulfill({
        path: path.join(__dirname, "..", pathname.startsWith("/assets/") ? "harness_ui" : "agent_service", pathname === "/" ? "index.html" : pathname),
      });
    });
    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
    await page.goto("http://poll.test");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    const sidebar = page.locator("#sidebar");
    const rowMenu = sidebar.getByRole("button", { name: "Rename conversation" });
    const projectMenu = sidebar.getByRole("button", { name: "Edit project" });
    await page.locator("#sidebar").getByRole("button", { name: "Poll chat" }).first().waitFor();

    // A marker on the first sidebar group survives only while the list is not rebuilt.
    const mark = () => page.locator("#projects > *").first().evaluate((el) => (el.dataset.mark = "kept"));
    const kept = () => page.locator("#projects > *").first().evaluate((el) => el.dataset.mark === "kept");

    // Row actions menu: a poll tick while it is open leaves the list, and the menu, alone.
    await sidebar.getByRole("button", { name: "Poll chat" }).first().hover();
    await sidebar.getByLabel("Actions for Poll chat").click();
    await rowMenu.waitFor({ state: "visible" });
    await mark();
    await page.clock.runFor(10500);
    assert.equal(await rowMenu.isVisible(), true, "the row actions menu closed on the background refresh");
    assert.equal(await kept(), true, "the sidebar was rebuilt while the row menu was open");
    await page.keyboard.press("Escape");
    await rowMenu.waitFor({ state: "hidden" });
    await page.clock.runFor(10500);
    assert.equal(await kept(), false, "the refresh must resume once the row menu is closed");

    // Project actions menu: same rule.
    await sidebar.getByRole("button", { name: "Actions for project Alpha" }).click();
    await projectMenu.waitFor({ state: "visible" });
    await mark();
    await page.clock.runFor(10500);
    assert.equal(await projectMenu.isVisible(), true, "the project actions menu closed on the background refresh");
    assert.equal(await kept(), true, "the sidebar was rebuilt while the project menu was open");
    await page.keyboard.press("Escape");
    await projectMenu.waitFor({ state: "hidden" });
    await page.clock.runFor(10500);
    assert.equal(await kept(), false, "the refresh must resume once the project menu is closed");
    assert.deepEqual(errors, []);
    console.log("PASS: the sidebar refresh leaves open actions menus alone");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
