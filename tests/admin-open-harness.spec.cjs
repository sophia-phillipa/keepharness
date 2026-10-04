// The admin's "Open harness" link never ships a default address: it follows the status the
// admin reports, and it is disabled, with a reason, while the harness is stopped.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");

(async () => {
  const admin = (process.env.ADMIN_URL || "").replace(/\/$/, "");
  if (!admin) throw new Error("ADMIN_URL is required (run through scripts/test-ui.sh)");
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    // The admin answers only the owner's browser (scripts/test-ui.sh exports its cookie).
    const [cookieName, ...cookieValue] = (process.env.ADMIN_LOCAL_COOKIE || "").split("=");
    if (cookieName && cookieValue.length)
      await context.addCookies([{ name: cookieName, value: cookieValue.join("="), domain: "127.0.0.1", path: "/", httpOnly: true, sameSite: "Strict" }]);
    let status = { running: true, local_url: "http://127.0.0.1:18999/", shared: false };
    const page = await context.newPage();
    await page.route("**/api/state", async (route) => {
      const response = await route.fetch();
      const state = await response.json();
      await route.fulfill({ response, json: { ...state, status: { ...state.status, ...status } } });
    });
    const link = page.locator("#open-harness");
    const open = async () => {
      await page.goto(admin + "/");
      await page.locator("#runtime-badge").filter({ hasText: /Harness (active|stopped)/ }).waitFor();
    };

    // The shipped page has no address of its own, and does not claim a harness before the status arrives.
    const shipped = await (await fetch(admin + "/")).text();
    assert(!/id="open-harness"[^>]*href="http/.test(shipped), "the page ships a default harness address");

    await open();
    assert.equal(await link.getAttribute("href"), "http://127.0.0.1:18999/");
    assert.equal(await link.getAttribute("aria-disabled"), "false");
    assert.equal(await link.getAttribute("title"), "Open the harness chat");

    status = { running: false, local_url: "http://127.0.0.1:18999/", shared: false };
    await open();
    assert.equal(await link.getAttribute("href"), "http://127.0.0.1:18999/", "the real address stays known while stopped");
    assert.equal(await link.getAttribute("aria-disabled"), "true");
    assert.match(await link.getAttribute("title"), /harness is stopped/i);
    const popups = [];
    context.on("page", (created) => popups.push(created));
    await link.click({ force: true }); // Playwright treats aria-disabled as not clickable
    await page.waitForTimeout(300);
    assert.equal(popups.length, 0, "a stopped harness opened a window");

    status = { running: true, shared: true, local_url: "http://127.0.0.1:18999/", remote_url: "http://studio:18998/" };
    await open();
    assert.equal(await link.getAttribute("href"), "http://studio:18998/", "a shared harness uses its network address");
    console.log("admin open-harness link OK");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
