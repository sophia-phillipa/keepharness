const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises"),
  path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
        viewport: { width: 1280, height: 900 },
      }),
      errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const titlePayload = "<img src=x onerror=window.__xss=1>";
    const answerPayload = [
      "Report",
      "<script>window.__xss=2</script>",
      "<img src=x onerror=window.__xss=3>",
      "[a](javascript:window.__xss=4)",
      "[b](data:text/html,<script>window.__xss=5</script>)",
    ].join("\n\n");
    const attachmentPayload = '"><svg onload=window.__xss=6>';
    const turn = {
      id: "job-one",
      project: "sem-projeto",
      state: "completed",
      request: {
        prompt: "Question one",
        model: "fixture",
        backend: "local",
        effort: "low",
      },
      attachments: [
        { file_id: "f1", name: attachmentPayload, preview_url: null },
      ],
      result: { answer: answerPayload, model: "fixture", total_seconds: 1 },
    };
    const origin = process.env.HARNESS_URL || "http://panel.test";
    await page.route(origin + "/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      let data = {};
      if (p.startsWith("/v1/")) {
        if (p === "/v1/projects")
          data = { projects: ["sem-projeto"], details: {} };
        if (p === "/v1/models")
          data = {
            models: [
              {
                id: "fixture",
                name: "Test model",
                backend: "local",
                efforts: ["low"],
              },
            ],
            providers: { local: true },
            uploads_enabled: false,
          };
        if (p === "/v1/version") data = { version: "test", build: "xss" };
        if (p === "/v1/conversations")
          data = {
            conversations: [
              {
                id: "conversation",
                title: titlePayload,
                project: "sem-projeto",
                state: "completed",
              },
            ],
          };
        if (p === "/v1/conversations/conversation") data = { turns: [turn] };
        return route.fulfill({ json: data });
      }
      if (process.env.HARNESS_URL) return route.continue();
      const file = p === "/" ? "index.html" : p.slice(1);
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
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.12.1"));
    await page.goto(origin);
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    // The conversation list renders the malicious title before any conversation is opened.
    await page.locator(".conversation-row>button").first().waitFor();
    assert.match(
      await page.locator(".conversation-row>button").first().textContent(),
      /onerror=window\.__xss=1/,
      "malicious title should render as plain text in the conversation list",
    );
    await page.click(".conversation-row>button");
    await page.waitForFunction(
      () => !!document.querySelector("#messages .attachment.message-file"),
    );
    assert.match(
      await page.locator("#messages").innerText(),
      /Report/,
      "the mocked answer must actually reach the render path",
    );
    assert.match(
      await page
        .locator("#messages .attachment-name,#messages .message-file span")
        .last()
        .textContent()
        .then((t) => t || ""),
      /svg onload=window\.__xss=6/,
      "the attachment payload must actually reach the render path",
    );
    const xss = await page.evaluate(() => window.__xss);
    assert.equal(xss, undefined, "no payload executed");
    const inlineScripts = await page.evaluate(() =>
      Array.from(document.querySelectorAll("script:not([src])")).map(
        (s) => s.textContent,
      ),
    );
    assert.deepEqual(
      inlineScripts.filter((t) => t.includes("__xss")),
      [],
      "no injected inline script content",
    );
    assert.equal(
      await page.locator("svg[onload]").count(),
      0,
      "no svg[onload] in the DOM",
    );
    assert.equal(
      await page.locator("img[onerror]").count(),
      0,
      "no img[onerror] in the DOM",
    );
    assert.equal(
      await page.locator('a[href^="javascript:"]').count(),
      0,
      "no javascript: links",
    );
    assert.equal(
      await page.locator('a[href^="data:"]').count(),
      0,
      "no data: links",
    );
    assert.deepEqual(errors, [], "no page errors");
    console.log(
      "PASS: conversation title, assistant answer and attachment name payloads reach the DOM as text only; no script/onerror/onload/javascript:/data: execution or injection.",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
