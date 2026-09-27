// H17 attachment hoarder (P2/P3): selects 30 files at once, then prunes the
// list and re-adds files, one of them larger than the 100 MiB per-file limit.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { mockHarness, runPersona } = require("./_harness.cjs");

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
const KINDS = [
  ["txt", "text/plain"],
  ["csv", "text/csv"],
  ["md", "text/markdown"],
  ["png", "image/png"],
  ["pdf", "application/pdf"],
  ["json", "application/json"],
];

function mixed(count, prefix) {
  return Array.from({ length: count }, (_, i) => {
    const [ext, mimeType] = KINDS[i % KINDS.length];
    return {
      name: `${prefix}-${String(i + 1).padStart(2, "0")}.${ext}`,
      mimeType,
      buffer: Buffer.from(`${prefix} file ${i + 1}\n`),
    };
  });
}

async function open(page) {
  const uploads = [];
  await mockHarness(page, {
    "GET /v1/models": MODELS,
    "POST /v1/files": (route) => {
      const name = decodeURIComponent(
        route.request().headers()["x-filename"] || "",
      );
      uploads.push(name);
      return route.fulfill({ json: { file_id: "f-" + uploads.length } });
    },
  });
  await page.goto("http://harness.test");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  await page.locator("#attach:not([disabled])").waitFor();
  return uploads;
}

const chips = (page) =>
  page.locator("#attachments .attachment-name").allInnerTexts();
const idle = (page) => page.locator("#attach:not([disabled])").waitFor();

runPersona("h17", [
  {
    title: "H17-S1 30 files at once keep the first 20 and explain the limit",
    timeout: 15000,
    async run(page) {
      const uploads = await open(page);
      const picked = mixed(30, "batch");
      await page.locator("#file").setInputFiles(picked);
      await page.locator("#status", { hasText: "Limit of 20" }).waitFor();
      await idle(page);
      const first20 = picked.slice(0, 20).map((f) => f.name);
      assert.deepEqual(await chips(page), first20);
      assert.deepEqual(uploads, first20, "one POST per kept file, in order");
      assert.equal(
        await page.locator("#attachment-count").innerText(),
        "20 / 20 files attached",
      );
      // KNOWN BUG F-71: the file-picker path only says the limit was reached
      // after uploading 20 files one by one; it does not say that 10 of the 30
      // selected files were dropped, nor which ones (the Files panel path says
      // "The selection was limited to the available slots.").
      assert.equal(
        await page.locator("#status").innerText(),
        "Limit of 20 attachments reached. Remove one before adding another.",
      );
      assert.equal(
        await page.locator(".message", { hasText: "batch-21" }).count(),
        0,
      );
    },
  },
  {
    title: "H17-S2 remove 3, re-add with one oversize file",
    timeout: 20000,
    async run(page) {
      const uploads = await open(page);
      const picked = mixed(20, "keep");
      await page.locator("#file").setInputFiles(picked);
      await page.locator("#status", { hasText: "File received." }).waitFor();
      await idle(page);
      assert.equal((await chips(page)).length, 20);
      for (const name of ["keep-02.csv", "keep-05.pdf", "keep-09.md"])
        await page
          .getByRole("button", { name: "Remove attachment " + name })
          .click();
      const kept = picked
        .map((f) => f.name)
        .filter(
          (n) => !["keep-02.csv", "keep-05.pdf", "keep-09.md"].includes(n),
        );
      assert.deepEqual(await chips(page), kept);
      const dir = await fs.mkdtemp(path.join(os.tmpdir(), "h17-"));
      const big = path.join(dir, "scan-archive.pdf");
      try {
        await (await fs.open(big, "w")).close();
        await fs.truncate(big, 100 * 1024 * 1024 + 1);
        const again = mixed(3, "again");
        const files = [];
        for (const f of again) {
          const file = path.join(dir, f.name);
          await fs.writeFile(file, f.buffer);
          files.push(file);
        }
        uploads.length = 0;
        await page
          .locator("#file")
          .setInputFiles([files[0], big, files[1], files[2]]);
        await page
          .locator("#attachment-count", { hasText: "20 / 20" })
          .waitFor();
        await idle(page);
      } finally {
        await fs.rm(dir, { recursive: true, force: true });
      }
      const added = ["again-01.txt", "again-02.csv", "again-03.md"];
      assert.deepEqual(
        uploads,
        added,
        "one POST per file, none for the oversize one",
      );
      assert.deepEqual(await chips(page), [...kept, ...added], "order stable");
      // KNOWN BUG F-72: the client-side oversize notice ("scan-archive.pdf: The
      // per-file limit is 100 MiB.") only lives in #status and is overwritten by
      // the next file's "Uploading…"/"File received." message, and no persistent
      // "File skipped" notice is added (server-side skips get one), so the user
      // never learns which file was left out.
      assert.equal(await page.locator("#status").innerText(), "File received.");
      assert.equal(
        await page.locator(".message", { hasText: "scan-archive.pdf" }).count(),
        0,
      );
    },
  },
]);
