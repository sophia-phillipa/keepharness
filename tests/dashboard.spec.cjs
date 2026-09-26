const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 1366, height: 1000 },
    });
    let expired = false;
    const now = Date.now() / 1000;
    await page.route("**/api/dashboard*", (route) => {
      const detail = new URL(route.request().url()).searchParams.has("job");
      return route.fulfill({
        json: detail
          ? {
              prompt: "<script>unsafe()</script>",
              answer: "Observed response",
              events: [{ time: now, type: "thinking", data: {} }],
            }
          : {
              available: true,
              checked_at: now,
              requests_per_second: 0.2,
              active: 1,
              queued: 0,
              input_tokens: 100,
              output_tokens: 20,
              measured_jobs: 1,
              latest_output_tokens_per_second: 2,
              hardware: {
                cpu_percent: 25,
                memory_used: 10,
                memory_total: 20,
                gpus: [],
              },
              recent: expired
                ? []
                : [
                    {
                      id: "read-only",
                      created: now,
                      state: "running",
                      backend: "codex",
                      model: "example",
                      project: "demo",
                      output_tokens: 20,
                    },
                  ],
            },
      });
    });
    await page.goto(process.env.ADMIN_URL || "http://127.0.0.1:18194/");
    await page.locator("[data-panel=runs]").click();
    await page.locator(".execution-row>summary").click();
    await page.getByText("Observed response", { exact: true }).waitFor();
    assert.equal(await page.locator(".execution-detail script").count(), 0);
    assert.equal(
      await page
        .locator(".execution-detail button,.execution-detail textarea")
        .count(),
      0,
    );
    await page.waitForTimeout(3200);
    assert.equal(await page.locator(".execution-row").getAttribute("open"), "");
    expired = true;
    await page
      .getByText("No run active or finished in the last 30 minutes.", {
        exact: true,
      })
      .waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    console.log(
      "PASS: read-only inspection, safe text, refresh, expiry and mobile",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
