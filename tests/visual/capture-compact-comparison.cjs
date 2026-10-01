const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { outputDirectory, mountVisual, selectState } = require("./harness-visual-helpers.cjs");

(async () => {
  const reference = process.env.MOCK_REFERENCE_HTML;
  if (!reference || !fs.existsSync(reference)) throw new Error("Set MOCK_REFERENCE_HTML to the readable mock-4 HTML file");
  const output = outputDirectory();
  const browser = await chromium.launch();
  try {
    const mock = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await mock.goto(pathToFileURL(path.resolve(reference)).href);
    await mock.waitForTimeout(100);
    await mock.screenshot({ path: path.join(output.directory, "compact-desktop-mock-closed.png") });
    await mock.keyboard.press("Control+j");
    await mock.waitForTimeout(100);
    await mock.screenshot({ path: path.join(output.directory, "compact-desktop-mock-open.png") });

    const actual = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await mountVisual(actual);
    await actual.evaluate(() => window.TailTheme.apply("porcelain"));
    await selectState(actual, "console-closed");
    await actual.screenshot({ path: path.join(output.directory, "compact-desktop-app-closed.png") });
    await selectState(actual, "console-open");
    await actual.screenshot({ path: path.join(output.directory, "compact-desktop-app-open.png") });
    console.log(`PASS: compact comparison screenshots written to ${output.directory}`);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
