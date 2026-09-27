// H18 malicious uploader (P6): hostile filenames, then parser bait (script-laden
// SVG, zip bombs, polyglot PDF). Nothing may execute; every refusal is named.
"use strict";
const assert = require("node:assert/strict");
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
const RLO = "‮";

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

// Mirrors agent_service/routes/files.py:upload_file's filename check, then lets
// `verdict(name)` pick the extraction outcome (null = accepted).
async function open(page, verdict) {
  const seen = [],
    dialogs = [];
  page.on("dialog", (d) => {
    dialogs.push(d.message());
    void d.dismiss();
  });
  await page.addInitScript(() => {
    window.__pwned = [];
    for (const name of ["alert", "confirm", "prompt"])
      window[name] = (m) => window.__pwned.push(name + ":" + m);
  });
  await mockHarness(page, {
    "GET /v1/models": MODELS,
    "POST /v1/files": (route) => {
      const name = decodeURIComponent(
        route.request().headers()["x-filename"] || "",
      );
      seen.push(name);
      const bad =
        !name.trim() ||
        name.length > 160 ||
        [...name].some(
          (c) =>
            "/\\".includes(c) ||
            c.charCodeAt(0) < 32 ||
            (c.charCodeAt(0) >= 127 && c.charCodeAt(0) < 160),
        );
      const code = bad ? "invalid_filename" : verdict(name);
      return code
        ? route.fulfill({ status: 422, json: { code } })
        : route.fulfill({ json: { file_id: "f-" + seen.length } });
    },
  });
  await page.goto("http://harness.test");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  await page.locator("#attach:not([disabled])").waitFor();
  return { seen, dialogs };
}

async function attachOne(page, file) {
  await page.locator("#file").setInputFiles(file);
  await page
    .locator("#status", { hasText: /^(File received\.|Couldn't upload)/ })
    .waitFor();
  await page.locator("#attach:not([disabled])").waitFor();
  return page.locator("#status").innerText();
}

async function noExecution(page, dialogs) {
  assert.deepEqual(dialogs, [], "no native dialog opened");
  assert.deepEqual(await page.evaluate(() => window.__pwned), []);
  assert.equal(
    await page
      .locator("#attachments svg[onload], #messages svg[onload]")
      .count(),
    0,
  );
  assert.equal(
    await page.locator("#attachments script, #messages script").count(),
    0,
  );
}

runPersona("h18", [
  {
    title: "H18-S1 hostile filenames are refused or shown inert",
    async run(page) {
      const consoleClean = allowHttpErrors(page, [422]);
      const { seen, dialogs } = await open(page, () => null);
      const text = Buffer.from("root:x:0:0\n");
      // A path-carrying name: Chromium strips directories from File.name, so
      // the server's invalid_filename refusal is exercised with a backslash.
      const traversal = await attachOne(page, {
        name: "..\\..\\etc\\passwd.txt",
        mimeType: "text/plain",
        buffer: text,
      });
      assert.equal(
        traversal,
        "Couldn't upload: The filename contains a path, control characters, or exceeds 160 characters.",
      );
      const html = '"><svg onload=alert(1)>.txt';
      assert.equal(
        await attachOne(page, {
          name: html,
          mimeType: "text/plain",
          buffer: text,
        }),
        "File received.",
      );
      const spoof = "invoice" + RLO + "txt.exe";
      assert.equal(
        await attachOne(page, {
          name: spoof,
          mimeType: "text/plain",
          buffer: text,
        }),
        "File received.",
      );
      assert.equal(seen[0], "..\\..\\etc\\passwd.txt");
      const names = await page
        .locator("#attachments .attachment-name")
        .allTextContents();
      assert.deepEqual(names, [html, spoof], "chips hold the literal names");
      // KNOWN BUG F-74: U+202E (RIGHT-TO-LEFT OVERRIDE) is accepted by the server
      // (routes/files.py:238-243 only rejects C0/C1 controls) and rendered as-is
      // by renderFiles (ui.js:3438), so "invoice‮txt.exe" displays as
      // "invoiceexe.txt": the real extension is visually hidden.
      const chip = page.locator("#attachments .attachment-name").nth(1);
      assert(
        (await chip.textContent()).includes(RLO),
        "bidi override reaches the chip unmodified",
      );
      assert.equal(
        await chip.evaluate((e) => getComputedStyle(e).unicodeBidi),
        "normal",
      );
      await page.locator("#prompt").fill("Summarize these files");
      await page.locator("#send:not([disabled])").click();
      await page.locator("#messages .message.user").waitFor();
      await page.locator("#send").waitFor();
      await noExecution(page, dialogs);
      consoleClean();
    },
  },
  {
    title: "H18-S2 parser bait gets a notice per file and nothing executes",
    timeout: 8000,
    async run(page) {
      const consoleClean = allowHttpErrors(page, [422]);
      // Outcomes the real server gives for these inputs (agent_service/tools.py):
      // SVG is UTF-8 text so it is accepted as text (no preview_url); a .zip is
      // not UTF-8; an Office zip bomb trips the expansion limit; a polyglot that
      // does not start with %PDF- is refused as invalid_pdf.
      const verdicts = {
        "diagram.svg": null,
        "bomb.zip": "unsupported_binary_format",
        "bomb.docx": "document_expansion_limit",
        "report.pdf": "invalid_pdf",
      };
      const { seen, dialogs } = await open(page, (n) => verdicts[n]);
      const results = {};
      results.svg = await attachOne(page, {
        name: "diagram.svg",
        mimeType: "image/svg+xml",
        buffer: Buffer.from(
          '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(2)</script></svg>',
        ),
      });
      results.zip = await attachOne(page, {
        name: "bomb.zip",
        mimeType: "application/zip",
        buffer: Buffer.from("PK\x03\x04\x14\0\0\0\x08\0\xff\xfe", "latin1"),
      });
      results.docx = await attachOne(page, {
        name: "bomb.docx",
        mimeType:
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        buffer: Buffer.from("PK\x03\x04", "latin1"),
      });
      results.pdf = await attachOne(page, {
        name: "report.pdf",
        mimeType: "application/pdf",
        buffer: Buffer.from(
          "<html><script>alert(3)</script></html>\n%PDF-1.7\n%%EOF",
        ),
      });
      assert.deepEqual(seen, Object.keys(verdicts), "one POST per file");
      assert.equal(results.svg, "File received.");
      assert.equal(
        await page.locator("#attachments img").count(),
        0,
        "no preview for the SVG",
      );
      assert.match(results.zip, /doesn't have a reader available yet/);
      assert.equal(
        results.docx,
        "Couldn't upload: The document exceeds the safe decompression limit.",
      );
      // KNOWN BUG F-73: invalid_pdf (and other extraction codes: pdf_extraction_failed,
      // unsafe_document_xml in the status line, audio_decode_failed, audio_no_speech,
      // document_text_unavailable) are missing from attachmentError/attachmentNotice
      // in agent_service/ui.js, so the raw code is shown and no "File skipped"
      // notice names the file.
      assert.equal(results.pdf, "Couldn't upload: invalid_pdf");
      const skipped = await page
        .locator(".message", { hasText: "File skipped" })
        .allInnerTexts();
      assert.equal(
        skipped.length,
        2,
        "notices for bomb.zip and bomb.docx only",
      );
      assert(skipped.some((t) => t.includes("“bomb.zip”")));
      assert(skipped.some((t) => t.includes("“bomb.docx”")));
      assert(!skipped.some((t) => t.includes("report.pdf")));
      assert.deepEqual(
        await page.locator("#attachments .attachment-name").allTextContents(),
        ["diagram.svg"],
      );
      await noExecution(page, dialogs);
      consoleClean();
    },
  },
]);
