const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 1280, height: 860 },
    });
    page.setDefaultTimeout(5000);
    const errors = [],
      writes = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("http://panel.test/**", (route) => {
      const pathname = new URL(route.request().url()).pathname;
      return route.fulfill({
        path: path.join(
          __dirname,
          "..",
          pathname.startsWith("/assets/") ? "tail_ui" : "agent_service",
          pathname === "/" ? "index.html" : pathname,
        ),
      });
    });
    await page.route("**/v1/**", (route) => {
      const url = new URL(route.request().url());
      let data = {};
      if (route.request().method() !== "GET") writes.push(url.pathname);
      if (url.pathname === "/v1/projects")
        data = {
          projects: ["sem-projeto", "alpha", "beta", "alpha-2"],
          details: {
            alpha: {
              label: "Alpha",
              icon: {
                path: "logo.svg",
                src:
                  "data:image/svg+xml;base64," +
                  Buffer.from(
                    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/></svg>',
                  ).toString("base64"),
              },
            },
            beta: { label: "Beta" },
            "alpha-2": { label: "Alpha", canonical_id: "alpha" },
          },
        };
      else if (url.pathname === "/v1/models")
        data = {
          models: [
            {
              id: "fixture",
              name: "Fixture",
              backend: "local",
              efforts: ["low"],
              permissions: { upload: true },
            },
          ],
          providers: { local: true },
          uploads_enabled: true,
        };
      else if (url.pathname === "/v1/conversations")
        data = {
          conversations: [
            {
              id: "existing",
              project: "alpha-2",
              title: "Preserved conversation",
              state: "completed",
            },
          ],
        };
      else if (url.pathname === "/v1/version")
        data = { version: "fixture", build: "project-preferences" };
      return route.fulfill({ json: data });
    });
    const ready = async () => {
      await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.10"));
      await page.goto("http://panel.test");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.locator("#project-tree > summary").click();
    };
    await ready();
    assert.equal(await page.locator(".project-group").count(), 2);
    await page.locator('[data-project-id="alpha"] > summary > button').click();
    await page
      .locator(".conversation-title").filter({ hasText: /^Preserved conversation$/ }).first()
      .waitFor();
    const logo = page.locator('[data-project-id="alpha"] .project-logo');
    assert.equal(await logo.count(), 1);
    assert(await logo.evaluate((img) => img.complete && img.naturalWidth > 0));
    assert(await logo.evaluate((img) => img.parentElement.firstChild === img));
    const toggle = async (label) => {
      await page
        .getByRole("button", { name: "Actions for project Alpha", exact: true })
        .click();
      const button = page.getByRole("button", { name: label, exact: true });
      assert.equal(await button.locator("use").count(), 1);
      await button.click();
    };
    await toggle("Hide project icon");
    assert.equal(await logo.count(), 0);
    await ready();
    assert.equal(await logo.count(), 0);
    await toggle("Show project icon");
    assert.equal(await logo.count(), 1);
    await page
      .getByRole("button", { name: "Actions for project Beta", exact: true })
      .click();
    assert(
      await page
        .getByRole("button", { name: "Show project icon", exact: true })
        .isDisabled(),
    );
    await page.keyboard.press("Escape");
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator("#menu").click();
    await page.screenshot({ path: "/tmp/project-icons-mobile.png" });
    assert.deepEqual(writes, []);
    assert.deepEqual(errors, []);
    console.log(
      "PASS: loaded image, placement, toggle persistence, missing icon and mobile",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
