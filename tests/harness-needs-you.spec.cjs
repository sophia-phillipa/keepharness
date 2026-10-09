const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const { mount, run } = require("./run-console-fixture.cjs");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 400, height: 844 },
    });
    let pending = true,
      enrolled = false,
      second = false;
    const posts = [];
    await mount(page, async (url, request) => {
      if (url.pathname === "/v1/activity")
        return {
          json: {
            counts: { running: 1, queued: 0, needs_you: pending ? 1 : 0 },
            jobs: [run],
            providers: [],
            needs_you: pending
              ? [
                  {
                    ...run,
                    kind: "gate",
                    gate_id: "live-gate",
                    question: "Choose the next step",
                    timeout_at: Date.now() / 1000 + 100,
                    options: [
                      { id: "review", label: "Review" },
                      { id: "stop", label: "Stop" },
                    ],
                  },
                  ...(second
                    ? [
                        {
                          ...run,
                          kind: "approval",
                          approval_id: "other",
                          approval_kind: "command",
                          request: { command: "synthetic-command --check" },
                          expires_at: Date.now() / 1000 + 100,
                        },
                      ]
                    : []),
                ]
              : [],
          },
        };
      if (url.pathname === "/v1/approvals/live-gate") {
        posts.push(request.postDataJSON());
        if (!enrolled)
          return { status: 403, json: { code: "approval_session_required" } };
        pending = false;
        return { json: { resolved: true } };
      }
    });
    await page.locator("#attention-bell").click();
    await page.locator("#attention-open-inbox").click();
    await page
      .getByRole("dialog", { name: "Needs you", exact: true })
      .waitFor();
    assert.equal(
      await page.getByRole("button", { name: "Submit answer" }).isDisabled(),
      true,
    );
    await page.getByRole("radio", { name: "Review", exact: true }).check();
    second = true;
    await page.evaluate(() =>
      runConsole.observe({ type: "approval_required" }),
    );
    await page.getByText(/synthetic-command --check/).waitFor();
    assert.equal(
      await page
        .getByRole("radio", { name: "Review", exact: true })
        .isChecked(),
      true,
    );
    await page.getByRole("button", { name: "Submit answer" }).focus();
    await page.keyboard.press("Enter");
    await page.getByText(/enroll/i).waitFor();
    assert.equal(
      await page
        .getByRole("radio", { name: "Review", exact: true })
        .isChecked(),
      true,
    );
    enrolled = true;
    await page.getByRole("button", { name: "Submit answer" }).dblclick();
    await page.getByText("No live requests need your attention.").waitFor();
    assert.deepEqual(posts, [{ choice: "review" }, { choice: "review" }]);
    await page.keyboard.press("Escape");
    assert.equal(
      await page
        .locator("#attention-bell")
        .evaluate((el) => el === document.activeElement),
      true,
    );
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.locator("#attention-bell").click();
    await page.locator("#attention-open-inbox").click();
    await page.getByText("No live requests need your attention.").waitFor();
    assert.equal(await page.getByRole("radio").count(), 0);
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    console.log(
      "PASS live-only inbox, required selection, P0 human-session denial/recovery, duplicate prevention, reload, keyboard/focus and 400px",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
