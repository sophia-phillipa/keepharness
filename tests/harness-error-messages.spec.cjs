// Regression: run failures with a stable error code show a guided message (F-10).
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 390, height: 844 },
    });
    await page.route("**/v1/**", (route) => {
      const path = new URL(route.request().url()).pathname;
      const data =
        path === "/v1/projects"
          ? { projects: ["sem-projeto"], details: {} }
          : path === "/v1/models"
            ? {
                models: [
                  {
                    id: "fixture",
                    name: "Fixture",
                    backend: "local",
                    efforts: ["low"],
                  },
                ],
                providers: { local: true },
                uploads_enabled: false,
              }
            : path === "/v1/conversations"
              ? { conversations: [] }
              : path === "/v1/version"
                ? { version: "fixture", build: "fixture" }
                : {};
      return route.fulfill({ json: data });
    });
    const origin = process.env.HARNESS_URL || "http://panel.test";
    if (!process.env.HARNESS_URL)
      await page.route(origin + "/**", async (route) => {
        const pathname = new URL(route.request().url()).pathname;
        if (pathname.startsWith("/v1/")) return route.fallback();
        const file = pathname === "/" ? "index.html" : pathname.slice(1);
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
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(origin);
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    const [missing, other] = await page.evaluate(() =>
      ["cli_missing", "fixture_failure"].map(executionError),
    );
    assert.match(missing, /command-line tool is missing on the server/);
    assert(!missing.includes("cli_missing"));
    assert.equal(other, "The run did not finish: fixture_failure");
    // F-23: an isolated conversation refused up front names what the server lacks.
    const isolation = await page.evaluate(
      () => userErrors.isolation_unavailable,
    );
    assert.match(isolation, /bubblewrap/);
    assert.deepEqual(errors, []);
    console.log(
      "PASS: a missing provider CLI shows a guided message; other codes keep the generic text.",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
