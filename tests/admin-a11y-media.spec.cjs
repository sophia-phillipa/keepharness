const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

// P5-22 (admin side): admin.css has no reduced-motion media query at all, so the
// bar to clear is that it never animates in the first place — every transition and
// animation duration must already be at or under 0.01s, with or without the
// media query. Reuses the admin-layout-accessibility.spec.cjs route-mock recipe.
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
    });
    await page.route("http://admin.test/**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith("/api/")) return route.fallback();
      const file = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
      return route.fulfill({
        body: await fs.readFile(
          path.join(
            __dirname,
            file.startsWith("assets/") ? "../harness_ui" : "../control",
            file,
          ),
        ),
        contentType: file.endsWith(".js")
          ? "text/javascript"
          : file.endsWith(".css")
            ? "text/css"
            : file.endsWith(".svg")
              ? "image/svg+xml"
              : "text/html",
      });
    });
    const state = {
      settings: {
        services: {},
        projects: [],
        logins: [],
        port: 8095,
        tailnet_port: 8095,
        uploads_enabled: false,
      },
      inventory: {
        platform: "Linux",
        services: [],
        projects: [],
        network: { online: false },
      },
      authentication: {},
      models: {},
      integrations: {},
      operations: [],
      credentials: {},
      status: { running: false },
    };
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      const result = url.pathname.endsWith("/state")
        ? state
        : url.pathname.endsWith("/scan")
          ? state.inventory
          : {};
      await route.fulfill({ json: result });
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto("http://admin.test/");

    await page.emulateMedia({ reducedMotion: "reduce" });

    const durations = await page.evaluate(() => {
      const offenders = [];
      for (const el of document.querySelectorAll("*")) {
        const style = getComputedStyle(el);
        for (const value of [
          style.transitionDuration,
          style.animationDuration,
        ]) {
          for (const part of value.split(",")) {
            const seconds = parseFloat(part);
            if (Number.isFinite(seconds) && seconds > 0.01) {
              offenders.push({
                tag: el.tagName,
                id: el.id,
                className: el.className,
                transitionDuration: style.transitionDuration,
                animationDuration: style.animationDuration,
              });
            }
          }
        }
      }
      return offenders;
    });

    assert.deepEqual(errors, []);
    assert.equal(durations.length, 0, JSON.stringify(durations.slice(0, 5)));
    console.log(
      "PASS: admin panel has no transition or animation longer than 0.01s under reduced motion (admin.css defines none at all).",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
