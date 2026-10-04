const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(5000);
    await page.route("http://panel.test/**", (route) => {
      const pathname = new URL(route.request().url()).pathname;
      return route.fulfill({
        path: path.join(
          __dirname,
          "..",
          pathname.startsWith("/assets/") ? "harness_ui" : "agent_service",
          pathname === "/" ? "index.html" : pathname,
        ),
      });
    });
    await page.route("**/v1/**", (route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === "/v1/files")
        return route.fulfill({
          status: 201,
          json: { file_id: "long", excerpt: true },
        });
      const data =
        pathname === "/v1/projects"
          ? { projects: ["p"] }
          : pathname === "/v1/models"
            ? {
                models: [
                  {
                    id: "deepseek-flash",
                    name: "DeepSeek",
                    backend: "deepseek",
                    efforts: ["configured"],
                    permissions: { upload: true },
                  },
                ],
                uploads_enabled: true,
              }
            : pathname === "/v1/conversations"
              ? { conversations: [] }
              : {};
      return route.fulfill({ json: data });
    });
    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
    await page.goto("http://panel.test/");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.evaluate(() =>
      upload([new File(["x"], "report.pdf", { type: "application/pdf" })]),
    );
    const composer = page.locator("#attachments .attachment-excerpt");
    await composer.waitFor();
    assert.equal(await composer.innerText(), "excerpt sent");
    await page.evaluate(() => {
      const message = bubble("user", "Summarize");
      messageAttachments(message, [
        { id: "long", name: "report.pdf", excerpt: true },
        { id: "short", name: "notes.txt" },
      ]);
    });
    const sent = page.locator(".message-file");
    assert.equal(await sent.count(), 2);
    assert.equal(await page.locator(".message-file .attachment-excerpt").count(), 1);
    assert.match(
      await sent.first().locator(".attachment-excerpt").getAttribute("title"),
      /report\.pdf.*sent inline/,
    );
    console.log("attachment excerpt chip ok");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
