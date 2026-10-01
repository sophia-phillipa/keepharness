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
          pathname.startsWith("/assets/") ? "tail_ui" : "agent_service",
          pathname === "/" ? "index.html" : pathname,
        ),
      });
    });
    await page.route("**/v1/**", (route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === "/v1/files")
        return route.fulfill({
          status: 422,
          json: { code: "model_images_unavailable" },
        });
      if (pathname === "/v1/project-files/attach")
        return route.fulfill({
          json: {
            attachments: [{ file_id: "txt", name: "notes.txt" }],
            skipped: [
              { path: "scan.png", reason: "local_vision_not_enabled" },
              { path: "design.psd", reason: "unsupported_binary_format" },
            ],
          },
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
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.7"));
    await page.goto("http://panel.test/");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.evaluate(() =>
      upload([new File(["image"], "photo.png", { type: "image/png" })]),
    );
    assert.match(
      await page.locator(".assistant .text").last().innerText(),
      /photo.png.*skipped.*selected model does not support/,
    );
    await page.evaluate(() =>
      attachSelectedProjectFiles({
        root_id: "home",
        paths: ["scan.png", "notes.txt", "design.psd"],
      }),
    );
    const notices = await page.locator(".assistant .text").allTextContents();
    assert(
      notices.some((t) => /scan.png.*local model has no image support/.test(t)),
    );
    assert(notices.some((t) => /design.psd.*no reader available/.test(t)));
    assert.equal(await page.evaluate(() => files.length), 1);
    const before = notices.length;
    await page.evaluate(() =>
      attachmentNotice("image.png", "image_capability_unavailable"),
    );
    assert.equal(
      await page.locator(".assistant").count(),
      before,
      "connection failures must not claim model incompatibility",
    );
    await page.evaluate(() =>
      attachmentNotice(
        "<img src=x onerror=alert(1)>.png",
        "images_require_native_service",
      ),
    );
    assert.equal(
      await page.locator(".assistant .text img").count(),
      0,
      "filenames must remain text",
    );
    console.log("attachment notices: passed");
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
