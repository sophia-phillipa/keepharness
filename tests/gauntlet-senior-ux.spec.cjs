const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    let confirmed = 0,
      quotaDelay = 0;
    page.on("dialog", async (d) => {
      confirmed++;
      await d.dismiss();
    });
    await page.route("**/v1/**", async (r) => {
      const path = new URL(r.request().url()).pathname;
      let data = {};
      if (path === "/v1/projects")
        data = {
          projects: ["sem-projeto", "demo"],
          details: { demo: { label: "Work project" } },
        };
      if (path === "/v1/models")
        data = {
          models: [
            {
              id: "gpt-5.6-sol",
              backend: "codex",
              name: "Sol",
              efforts: ["low", "high"],
            },
            {
              id: "claude-sonnet-4-6",
              backend: "claude",
              name: "Sonnet",
              efforts: ["low", "high"],
            },
          ],
          providers: { codex: true, claude: true },
          uploads_enabled: false,
        };
      if (path === "/v1/usage" && quotaDelay)
        await new Promise((resolve) => setTimeout(resolve, quotaDelay));
      if (path === "/v1/usage")
        data =
          new URL(r.request().url()).searchParams.get("backend") === "claude"
            ? { available: false, backend: "claude" }
            : {
                available: true,
                backend: "codex",
                rateLimits: {
                  primary: {
                    usedPercent: 25,
                    windowDurationMins: 300,
                    resetsAt: 1999999999,
                  },
                },
              };
      if (path === "/v1/conversations") data = { conversations: [] };
      return r.fulfill({ json: data });
    });
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.15"));
    await page.goto(process.env.HARNESS_URL || "http://127.0.0.1:8095/");
    await page.selectOption("#model", "gpt-5.6-sol");
    await page.waitForFunction(() =>
      document.querySelector("#quota-short").textContent.includes("75%"),
    );
    await page.selectOption("#model", "claude-sonnet-4-6");
    await page.waitForFunction(
      () => !document.querySelector("#quota-short").textContent.includes("75%"),
      null,
      { timeout: 3000 },
    );
    assert.match(
      await page.locator("#quota-short").innerText(),
      /Claude|unavailable|not reported/,
    );
    quotaDelay = 250;
    await page.selectOption("#model", "gpt-5.6-sol");
    await page.selectOption("#model", "claude-sonnet-4-6");
    await page.waitForTimeout(350);
    assert.match(
      await page.locator("#quota-short").innerText(),
      /Claude|unavailable|not reported/,
      "Late OpenAI quota must not overwrite Claude status",
    );
    await page.fill("#prompt", "Important audit draft");
    // Projects are listed open by default in the Codex-style sidebar.
    if (!(await page.locator("#project-tree").evaluate((el) => el.open)))
      await page.locator("#project-tree > summary").click();
    await page
      .getByRole("button", { name: "Work project", exact: true })
      .click();
    assert.equal(
      await page.locator("#prompt").inputValue(),
      "Important audit draft",
    );
    await page.locator(".project-new").click();
    assert.equal(
      await page.locator("#prompt").inputValue(),
      "Important audit draft",
    );
    // F-95: a new chat keeps the unsent draft, so there is nothing to confirm.
    assert.equal(confirmed, 0, "no discard prompt");
    console.log(
      "PASS: Codex to Claude truthful quota, project selection preserves draft, new chat keeps the draft",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
