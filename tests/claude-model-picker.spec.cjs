// Presentation policy: keep backend identifiers and saved settings intact.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
        viewport: { width: 390, height: 844 },
      }),
      errors = [],
      requests = [];
    page.on("request", (r) => requests.push(r.url()));
    page.on("pageerror", (e) => errors.push(e.message));
    const rejected = [
      "sonet",
      "sonnet",
      "opus",
      "haiku",
      "default",
      "claude-opus-4-6[1m]",
      "claude-opus-4-6[anything]",
      "claude-opus-4-6-20250921",
      "claude-haiku-4-5-20251001",
    ];
    const accepted = [
      "claude-opus-4-6",
      "claude-sonnet-4-6",
      "claude-opus-5",
      "claude-haiku-4-5",
      "claude-opus-4-10",
    ];
    const models = [...rejected, ...accepted].map((id) => ({
      id,
      backend: "claude",
      efforts: ["configured", "high"],
      permissions: {},
    }));
    models.push({
      id: "opus",
      backend: "local",
      efforts: ["configured"],
      permissions: {},
    });
    await page.route("http://picker.test/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      if (p.startsWith("/v1/")) {
        const data =
          p === "/v1/projects"
            ? { projects: ["sem-projeto", "project-two"] }
            : p === "/v1/models"
              ? { models, providers: { claude: true, local: true } }
              : p === "/v1/conversations"
                ? { conversations: [] }
                : {};
        return route.fulfill({ json: data });
      }
      const file = p === "/" ? "index.html" : p.slice(1);
      return route.fulfill({
        body: await fs.readFile(
          path.join(
            __dirname,
            file.startsWith("assets/") ? "../tail_ui" : "../agent_service",
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
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.1"));
    await page.goto("http://picker.test");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    const choices = () =>
      page
        .locator("#model option")
        .evaluateAll((els) => els.map((e) => e.value));
    assert.deepEqual(await choices(), [...accepted, "opus"]);
    await page.selectOption("#model", "claude-opus-4-6");
    await page.locator("#model-trigger").click();
    assert.equal(
      await page.locator('#model-menu [data-value="claude-opus-4-6"]').count(),
      1,
    );
    assert.equal(
      await page.locator('#model-menu [data-value="haiku"]').count(),
      0,
    );
    assert.deepEqual(
      await page
        .locator("[data-provider=claude] [role=option]")
        .evaluateAll((els) => els.map((e) => e.dataset.value)),
      [
        "claude-haiku-4-5",
        "claude-opus-5",
        "claude-opus-4-10",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
      ],
    );
    assert.equal(
      await page
        .locator("[data-provider=claude] [aria-selected=true] .access-check")
        .isVisible(),
      true,
    );
    assert.equal(
      await page.locator("[data-provider=claude] .model-logo-icon").count(),
      accepted.length,
    );
    await page.keyboard.press("ArrowLeft");
    assert.equal(
      await page.locator("[data-provider=claude]").getAttribute("open"),
      null,
    );
    assert.equal(
      await page
        .locator("[data-provider=claude] summary")
        .evaluate((el) => el === document.activeElement),
      true,
    );
    await page.keyboard.press("ArrowDown");
    assert.equal(
      await page
        .locator("[data-provider=local] summary")
        .evaluate((el) => el === document.activeElement),
      true,
    );
    await page.keyboard.press("Enter");
    assert.equal(
      await page.locator("[data-provider=local]").getAttribute("open"),
      "",
    );
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    assert.equal(await page.locator("#model").inputValue(), "opus");
    await page.selectOption("#model", "claude-opus-4-6");
    await page.locator("#model-trigger").click();
    assert.equal(
      await page.locator("[data-provider=claude]").getAttribute("open"),
      "",
    );
    const bounds = await page.locator("#model-menu").boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 390);
    await page.keyboard.press("Escape");
    models.push({
      id: "claude-sonnet-9",
      backend: "claude",
      efforts: ["configured", "max"],
      permissions: {},
    });
    await page.selectOption("#project", "project-two", { force: true });
    await page.waitForFunction(
      () => policyProject === "project-two" && !policyPending,
    );
    assert.deepEqual(await choices(), [...accepted, "opus", "claude-sonnet-9"]);
    assert.equal(await page.locator("#model").inputValue(), "claude-opus-4-6");
    await page.selectOption("#model", "claude-sonnet-9");
    assert.deepEqual(
      await page
        .locator("#effort option")
        .evaluateAll((els) => els.map((e) => e.value)),
      ["configured", "max"],
    );
    await page.locator("#model-trigger").click();
    await page.waitForFunction(() =>
      [...document.querySelectorAll("#model-menu summary use")].every(
        (el) => el.getBBox().width > 0,
      ),
    );
    if (process.env.SCREENSHOT_PATH)
      await page.screenshot({ path: process.env.SCREENSHOT_PATH });
    assert.ok(
      requests.every((url) => new URL(url).origin === "http://picker.test"),
      requests.join("\n"),
    );
    assert.deepEqual(errors, []);
    console.log(
      "PASS: Claude picker filters aliases, dated and bracketed IDs on load and project refresh; other providers and selection preserved",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
