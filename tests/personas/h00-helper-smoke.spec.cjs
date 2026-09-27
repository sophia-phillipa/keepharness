// Smoke test for the tests/personas/_harness.cjs shared fixture module itself.
const { mockHarness, visible, named } = require("./_harness.cjs");
(async () => {
  const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
  const browser = await chromium.launch(
    process.env.PLAYWRIGHT_CHANNEL
      ? { channel: process.env.PLAYWRIGHT_CHANNEL }
      : {},
  );
  try {
    const page = await browser.newPage({
      viewport: { width: 1280, height: 900 },
    });
    const errors = [];
    page.setDefaultTimeout(5000);
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    await mockHarness(page, {
      "GET /v1/models": {
        json: {
          models: [{ id: "harness-model", backend: "local", efforts: ["low"] }],
          providers: { local: true },
          uploads_enabled: false,
        },
      },
    });
    await page.goto("http://harness.test");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await visible(page, "#prompt");
    await named(page, "#prompt");
    if (errors.length)
      throw new Error("console/page errors: " + errors.join(", "));
    console.log("h00-helper-smoke pass");
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
