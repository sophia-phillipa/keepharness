// A theme chosen before the KeepHarness rename (0.15.0) was saved under the old key;
// the first paint keeps it, and the current key wins once it exists.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const theme = fs.readFileSync(
  path.join(__dirname, "../harness_ui/assets/theme.js"),
  "utf8",
);
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await page.route("http://theme.test/**", (route) =>
      new URL(route.request().url()).pathname === "/assets/theme.js"
        ? route.fulfill({ contentType: "text/javascript", body: theme })
        : route.fulfill({
            contentType: "text/html",
            body: '<html data-surface="harness"><head><script src="/assets/theme.js"></script></head><body></body></html>',
          }),
    );
    const palette = () =>
      page.evaluate(() => document.documentElement.dataset.palette);
    await page.goto("http://theme.test/");
    await page.evaluate(() =>
      localStorage.setItem("tail-harness:theme:harness", "amethyst"),
    );
    await page.reload();
    assert.equal(await palette(), "amethyst");
    await page.evaluate(() =>
      localStorage.setItem("keepharness:theme:harness", "porcelain"),
    );
    await page.reload();
    assert.equal(await palette(), "porcelain");
    console.log("PASS a theme saved before the rename survives it");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
