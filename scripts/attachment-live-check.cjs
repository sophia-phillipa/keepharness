// Explicit opt-in local-file audit; does not run inference or alter source files.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict"),
  fs = require("node:fs/promises");
(async () => {
  const { ATTACHMENT_TEST_URL, ATTACHMENT_MANIFEST, ATTACHMENT_OUTPUT } =
    process.env;
  assert(
    ATTACHMENT_TEST_URL && ATTACHMENT_MANIFEST && ATTACHMENT_OUTPUT,
    "Provide isolated test URL, approved file manifest and output path",
  );
  const manifest = JSON.parse(await fs.readFile(ATTACHMENT_MANIFEST, "utf8")),
    browser = await chromium.launch(),
    results = [];
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(30000);

    await page.goto(ATTACHMENT_TEST_URL);
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.evaluate(() => {
      const original = window.fetch;
      window.attachmentResponses = [];
      window.fetch = async (...args) => {
        const response = await original(...args);
        if (
          ["/v1/files", "/v1/project-files/attach"].includes(
            new URL(response.url).pathname,
          )
        )
          window.attachmentResponses.push({
            status: response.status,
            data: await response.clone().json(),
          });
        return response;
      };
    });
    for (let round = 1; round <= 10; round++) {
      await page.setViewportSize({
        width: round % 2 ? 1280 : 390,
        height: 900,
      });
      if (
        round % 2 === 0 &&
        (await page.locator("#activity-panel").isVisible())
      )
        await page.locator("#panel-toggle").click();
      for (
        let index = (round - 1) * 2;
        index < Math.min(round * 2, manifest.length);
        index++
      ) {
        const item = manifest[index],
          pending = page.waitForResponse(
            (r) => new URL(r.url()).pathname === "/v1/files",
          );
        await page.locator("#file").setInputFiles(item.path);
        const response = await pending;
        await page.waitForFunction(
          () => !uploads && window.attachmentResponses.length > 0,
        );
        const { data } = await page.evaluate(() =>
          window.attachmentResponses.shift(),
        );
        const record = {
          round,
          sample: index + 1,
          bytes: item.bytes,
          status: response.status(),
          expected: item.expected,
          code: data.code || null,
        };
        results.push(record);
        assert.equal(response.status(), item.expected, JSON.stringify(record));
        if (response.status() === 201) {
          assert.equal(data.sha256, item.sha256);
          assert.equal(data.bytes, item.bytes);
          assert.equal(
            await page.locator("#attachments .attachment").count(),
            1,
          );
          await page.locator("#attachments button").click();
        } else
          assert.equal(
            await page.locator("#attachments .attachment").count(),
            0,
          );
      }
      if (round === 10) {
        const pending = page.waitForResponse(
          (r) => new URL(r.url()).pathname === "/v1/project-files/attach",
        );
        await page.evaluate(() =>
          attachSelectedProjectFiles({ root_id: "home", paths: ["Desktop"] }),
        );
        const response = await pending;
        const { data } = await page.evaluate(() =>
          window.attachmentResponses.shift(),
        );
        assert.equal(response.status(), 200);
        const desktopFiles = manifest.filter(
          (i) =>
            i.path.includes("/Desktop/") &&
            !i.path.split("/").at(-1).startsWith("."),
        );
        assert.equal(
          data.attachments.length,
          desktopFiles.length,
          JSON.stringify(data.skipped),
        );
        assert.equal(
          await page.locator("#attachments .attachment").count(),
          desktopFiles.length,
        );
        results.push({
          round,
          folder: "Desktop",
          accepted: data.attachments.length,
          skipped: data.skipped.length,
          status: 200,
        });
      }
      console.log("PASS real-file round " + round);
    }
  } finally {
    await browser.close();
    await fs.writeFile(
      ATTACHMENT_OUTPUT,
      JSON.stringify(results, null, 2) + "\n",
    );
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
