// H14 tablet photographer (P3/P2): attaches an iPhone HEIC photo the server cannot
// read, then a 120 MiB camera image that exceeds the per-file limit.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { mockHarness, runPersona, visible } = require("./_harness.cjs");

const MODELS = {
  json: {
    uploads_enabled: true,
    providers: { claude: true },
    models: [
      {
        id: "claude-sonnet-4-6",
        backend: "claude",
        efforts: ["low"],
        permissions: { upload: true },
        execution_modes: ["native"],
      },
    ],
  },
};

// runPersona treats every console error as a failure, including Chromium's
// "Failed to load resource" line for a 4xx the scenario provokes on purpose.
// Swap its console listener for one that tolerates only the given statuses.
function allowHttpErrors(page, statuses) {
  const errors = [],
    expected = new RegExp("status of (" + statuses.join("|") + ") ");
  page.removeAllListeners("console");
  page.on("console", (m) => {
    if (m.type() === "error" && !expected.test(m.text())) errors.push(m.text());
  });
  return () => assert.deepEqual(errors, []);
}

async function open(page, over) {
  const uploads = [];
  const s = await mockHarness(page, {
    "GET /v1/models": MODELS,
    "POST /v1/files": async (route) => {
      const name = decodeURIComponent(
        route.request().headers()["x-filename"] || "",
      );
      uploads.push(name);
      return over(route, name);
    },
  });
  await page.goto("http://harness.test");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  await page.locator("#attach:not([disabled])").waitFor();
  return { s, uploads };
}

runPersona("h14", [
  {
    title: "H14-S1 HEIC photo on a tablet gets a named plain-language notice",
    viewport: { width: 768, height: 1024 },
    async run(page) {
      const consoleClean = allowHttpErrors(page, [422]);
      const { uploads } = await open(page, (route) =>
        route.fulfill({
          status: 422,
          json: { code: "unsupported_binary_format" },
        }),
      );
      await page.locator("#file").setInputFiles({
        name: "photo.heic",
        mimeType: "image/heic",
        buffer: Buffer.from("\0\0\0\x18ftypheic\0\0\0\0heicmif1"),
      });
      await page.locator("#status", { hasText: "Couldn't upload" }).waitFor();
      assert.deepEqual(uploads, ["photo.heic"]);
      const text = await page.locator("#status").innerText();
      assert.match(text, /doesn't have a reader available yet/);
      assert.doesNotMatch(text, /unsupported_binary_format/);
      const notice = page.locator(".message", { hasText: "File skipped" });
      await notice.waitFor();
      assert.match(await notice.innerText(), /“photo\.heic” was skipped/);
      assert.equal(await page.locator("#attachments .attachment").count(), 0);
      await visible(page, "#prompt");
      consoleClean();
    },
  },
  {
    title: "H14-S2 120 MiB image is refused before any upload",
    viewport: { width: 768, height: 1024 },
    async run(page) {
      const { uploads } = await open(page, (route) =>
        route.fulfill({ json: { file_id: "never" } }),
      );
      // Sparse file: setInputFiles refuses in-memory buffers above 50 MB.
      const dir = await fs.mkdtemp(path.join(os.tmpdir(), "h14-"));
      const big = path.join(dir, "IMG_0001.jpg");
      try {
        await (await fs.open(big, "w")).close();
        await fs.truncate(big, 120 * 1024 * 1024);
        await page.locator("#file").setInputFiles(big);
        await page.locator("#status", { hasText: "100 MiB" }).waitFor();
      } finally {
        await fs.rm(dir, { recursive: true, force: true });
      }
      assert.deepEqual(uploads, []);
      const text = await page.locator("#status").innerText();
      // Roster expected "Images can be up to 100 MiB."; the generic per-file
      // wording is equally plain and names the file, so it is accepted.
      assert.equal(text, "IMG_0001.jpg: The per-file limit is 100 MiB.");
      assert.equal(await page.locator("#attachments .attachment").count(), 0);
      assert(await page.locator("#attach").isEnabled(), "can try again");
    },
  },
]);
