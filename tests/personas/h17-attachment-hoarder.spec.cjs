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
      // F-71: the file-picker path says how many of the selection were dropped
      // and a persistent notice names them.
      assert.equal(
        await page.locator("#status").innerText(),
        "Limit of 20 attachments reached. 10 of the 30 selected files were not attached.",
      );
      const notice = page.locator(".message", { hasText: "File skipped" });
      assert.equal(await notice.count(), 1);
      const skipped = await notice.innerText();
      for (const f of picked.slice(20)) assert(skipped.includes(f.name));
      assert(!skipped.includes(picked[19].name));
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
      // F-72: the client-side oversize skip leaves a persistent notice that
      // names the file, like server-side skips do.
      const notice = page.locator(".message", { hasText: "scan-archive.pdf" });
      assert.equal(await notice.count(), 1);
      assert.match(await notice.innerText(), /File skipped/);
      assert.match(await notice.innerText(), /100 MiB/);
    },
  },
]);
