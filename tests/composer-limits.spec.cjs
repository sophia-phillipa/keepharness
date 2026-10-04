// QA-R4-3/4/5: the draft limit and the attachment limits are visible before they bite, and typing stays cheap.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    const errors = [];
    let uploads = 0;
    page.on("pageerror", (e) => errors.push(e.message));
    await page.route("http://limits.test/**", async (route) => {
      const url = new URL(route.request().url()),
        pathname = url.pathname;
      if (!pathname.startsWith("/v1/"))
        return route.fulfill({
          path: path.join(__dirname, "..", pathname.startsWith("/assets/") ? "harness_ui" : "agent_service", pathname === "/" ? "index.html" : pathname),
        });
      let data = {};
      if (pathname === "/v1/projects") data = { projects: ["sem-projeto"] };
      else if (pathname === "/v1/models")
        data = { models: [{ id: "fixture", name: "Fixture", backend: "local", efforts: ["low"], permissions: { upload: true } }], providers: { local: true }, uploads_enabled: true };
      else if (pathname === "/v1/conversations") data = { conversations: [] };
      else if (pathname === "/v1/version") data = { version: "fixture", build: "limits" };
      else if (pathname === "/v1/files") data = { file_id: "file-" + ++uploads, name: "n" };
      return route.fulfill({ json: data });
    });
    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.15.0"));
    await page.goto("http://limits.test/");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.waitForFunction(() => document.getElementById("model-label")?.textContent !== "Loading models…");

    const draft = (value) =>
      page.evaluate((text) => {
        const prompt = document.getElementById("prompt");
        prompt.value = text;
        prompt.dispatchEvent(new Event("input", { bubbles: true }));
      }, value);
    const note = page.locator("#draft-limit");

    // QA-R4-3: the limit is in bytes, shown near it, and a draft over it cannot be sent.
    await draft("a".repeat(100000));
    assert.equal(await note.isHidden(), true, "far from the limit nothing is shown");
    assert.equal(await page.locator("#send").isDisabled(), false);
    await draft("a".repeat(130000));
    assert.equal(await note.innerText(), "130,000 / 150,000 bytes");
    assert.equal(await page.locator("#send").isDisabled(), false);
    await draft("数".repeat(52000)); // 156,000 bytes: fewer characters than the ASCII case, over the limit
    assert.match(await note.innerText(), /6,000 bytes over the 150,000-byte limit\. Shorten it or attach it as a file\./);
    assert.equal(await page.locator("#send").isDisabled(), true);
    await draft("short");
    assert.equal(await note.isHidden(), true);
    assert.equal(await page.locator("#send").isDisabled(), false);

    // QA-R4-5: a long draft is not rescanned and split on every key.
    await draft("word ".repeat(20000));
    const cost = await page.evaluate(() => {
      const prompt = document.getElementById("prompt"),
        started = performance.now();
      for (let i = 0; i < 10; i++) prompt.dispatchEvent(new Event("input", { bubbles: true }));
      return { average: (performance.now() - started) / 10, mirror: document.getElementById("prompt-highlights").childNodes.length };
    });
    assert.equal(cost.mirror, 0, "no highlight nodes without a selected resource");
    assert(cost.average < 40, "an input event on a 100 KB draft took " + cost.average.toFixed(1) + " ms");
    await page.waitForFunction(() => /^100,000 characters$/.test(document.getElementById("character-count").textContent));
    await draft("");

    // QA-R4-4: chips show their size, the count states the per-file limit, and "+" says why it stops at 20.
    const file = (name, bytes) => ({ name, mimeType: "text/plain", buffer: Buffer.alloc(bytes, "x") });
    await page.setInputFiles("#file", file("notes.txt", 2048));
    await page.locator("#attachments .attachment").first().waitFor();
    assert.equal(await page.locator("#attachments .attachment-size").first().innerText(), "2 KB");
    assert.equal(await page.locator("#attachment-count").innerText(), "1 / 20 files attached");
    assert.equal(await page.locator("#attachment-count").getAttribute("title"), "Up to 20 files per message, 100 MiB each");
    assert.equal(await page.locator("#attach").getAttribute("aria-describedby"), "attachment-help");
    await page.setInputFiles("#file", Array.from({ length: 19 }, (_, i) => file("more-" + i + ".txt", 10)));
    await page.waitForFunction(() => document.querySelectorAll("#attachments .attachment").length === 20);
    assert.equal(await page.locator("#attach").isDisabled(), true);
    assert.match(await page.locator("#attach").getAttribute("title"), /20 of 20 files attached\. Remove one/);
    assert.deepEqual(errors, []);
    console.log("PASS: byte-based draft limit shown near it, over-limit blocks Send, cheap typing, chip sizes and the 20-file limit");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
