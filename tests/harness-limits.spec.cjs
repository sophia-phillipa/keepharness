const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage(),
      errors = [];
    let code = "submission_rate_limit",
      retry = "7",
      submissions = 0;
    page.on("pageerror", (e) => errors.push(e.message));
    await page.route("**/v1/models*", (r) =>
      r.fulfill({
        json: {
          models: [
            {
              id: "qwen-local",
              name: "Qwen local",
              backend: "local",
              efforts: ["low"],
            },
          ],
          providers: { local: {} },
        },
      }),
    );
    await page.route("**/v1/conversations*", (r) =>
      r.fulfill({ json: { conversations: [] } }),
    );
    const limited = (r) =>
      r.fulfill({
        status: 429,
        headers: retry === null ? {} : { "Retry-After": retry },
        json: { code },
      });
    await page.route("**/v1/jobs", (r) => {
      submissions++;
      return limited(r);
    });
    await page.route("**/v1/fixture-limit", limited);
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.14"));
    await page.goto(process.env.HARNESS_URL || "http://127.0.0.1:8095/");
    for (const [failure, expected] of [
      ["submission_rate_limit", "too quickly"],
      ["queue_full", "server queue is full"],
      ["owner_queue_full", "queue limit for requests"],
    ]) {
      code = failure;
      await page.fill("#prompt", "Preserve this draft: " + failure);
      await page.click("#send");
      await page.waitForFunction(
        (text) => document.querySelector("#status").textContent.includes(text),
        expected,
      );
      const text = await page.locator("#status").innerText();
      assert(text.includes("7 seconds"));
      assert(text.includes("draft was preserved"));
      assert.equal(
        await page.locator("#prompt").inputValue(),
        "Preserve this draft: " + failure,
      );
      // F-84: Send waits out Retry-After with a countdown, then comes back.
      assert(await page.locator("#send").isDisabled());
      assert.match(
        await page.locator("#send").getAttribute("aria-label"),
        /^Send message \(available in [67] seconds\)$/,
      );
      await page.waitForFunction(
        () => !document.querySelector("#send").disabled,
        null,
        { timeout: 9000 },
      );
    }
    // Errors remain visible; rejected submissions are never automatically retried.
    await page.waitForTimeout(5500);
    assert(await page.locator("#status").isVisible());
    assert.equal(submissions, 3);
    for (const [failure, expected] of [
      ["rate_limit", "Too many requests"],
      ["login_rate_limit", "Too many sign-in attempts"],
    ]) {
      code = failure;
      retry = new Date(Date.now() + 30000).toUTCString();
      const message = await page.evaluate(async () => {
        try {
          await api("/v1/fixture-limit");
        } catch (e) {
          return e.message;
        }
      });
      assert(message.includes(expected));
      assert.match(message, /Try again in \d+ seconds/);
    }
    code = "unknown_limit";
    retry = null;
    const fallback = await page.evaluate(async () => {
      try {
        await api("/v1/fixture-limit");
      } catch (e) {
        return e.message;
      }
    });
    assert(fallback.includes("temporary limit"));
    assert(fallback.includes("Wait a moment"));
    retry = "invalid";
    const invalid = await page.evaluate(async () => {
      try {
        await api("/v1/fixture-limit");
      } catch (e) {
        return e.message;
      }
    });
    assert(!invalid.includes("NaN"));
    assert(invalid.includes("Wait a moment"));
    assert.deepEqual(errors, []);
    console.log(
      "PASS: request/queue/login limits, Retry-After seconds/date/missing/invalid, preserved drafts, persistent errors, no automatic resubmission",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
