// Feature only: PLAYWRIGHT_MODULE=/path/to/playwright node tests/response-format.spec.cjs
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({
        viewport: { width: 1280, height: 960 },
      }),
      errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const markdown =
      '# Result\n\n**Bold** and *café*.\n\n| Name | Value |\n| --- | --- |\n| Item | 42 |\n\n- First\n- Second\n\n> Quote\n\n```js\nconst x = "<tag>";\n```';
    const turns = [
      {
        id: "first",
        project: "sem-projeto",
        state: "completed",
        request: { backend: "qwen", prompt: "**literal**" },
        result: { answer: markdown },
      },
      {
        id: "second",
        project: "sem-projeto",
        state: "completed",
        request: { backend: "qwen", prompt: "JSON" },
        result: { answer: '{"name":"Test person","list":[1,2]}' },
      },
    ];
    const origin = process.env.HARNESS_URL || "http://panel.test";
    await page.route(origin + "/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      if (p.startsWith("/v1/")) {
        let data = {};
        if (p === "/v1/projects") data = { projects: ["sem-projeto"] };
        else if (p === "/v1/models")
          data = {
            models: [{ id: "qwen-local", backend: "qwen", efforts: ["low"] }],
          };
        else if (p === "/v1/conversations") data = { conversations: [] };
        else if (p === "/v1/conversations/saved") data = { turns };
        else if (p === "/v1/jobs/second") data = turns[1];
        else if (p === "/v1/version")
          data = { version: "test", build: "format-test" };
        else if (p === "/v1/catalog")
          data = { agents: [], skills: [], warnings: [] };
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
            : "text/html",
      });
    });
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.3"));
    await page.goto(origin);
    await page.waitForFunction(() => models.length === 1);
    // Real event handler, including a Markdown token split across chunks.
    await page.evaluate(() => {
      active = assistant();
      last = 0;
      event({ id: 1, type: "answer_delta", data: { text: "**Bol" } });
      event({ id: 2, type: "answer_delta", data: { text: "d** and *café*" } });
    });
    assert.equal(
      await page.locator(".assistant .text strong").textContent(),
      "Bold",
    );
    assert.equal(
      await page.locator(".assistant .text em").textContent(),
      "café",
    );
    await page.evaluate(() => load("saved"));
    assert.equal(
      await page.locator(".assistant .text table tbody tr").count(),
      1,
    );
    assert.equal(
      await page.locator(".assistant .text h1").textContent(),
      "Result",
    );
    assert.equal(
      await page
        .locator(".assistant .text")
        .first()
        .evaluate((el) => getComputedStyle(el).whiteSpace),
      "normal",
    );
    assert.equal(await page.locator(".assistant .text ul li").count(), 2);
    assert.equal(await page.locator(".assistant .text blockquote").count(), 1);
    assert.equal(
      await page.locator(".user .text").first().textContent(),
      "**literal**",
    );
    assert.equal(await page.locator(".user strong").count(), 0);
    const pretty = JSON.stringify(JSON.parse(turns[1].result.answer), null, 2);
    assert.equal(
      (
        await page.locator(".assistant .text pre code").last().textContent()
      ).trim(),
      pretty,
    );
    assert.equal(await page.locator(".copy-answer").count(), 0);
    // Reuse actual final-result path for fenced JSON, invalid JSON and hostile Markdown.
    async function finalAnswer(answer) {
      turns[1].result.answer = answer;
      await page.evaluate(() => result());
    }
    await finalAnswer('```json\n{"nested":{"ok":true}}\n```');
    assert.equal(
      (
        await page.locator(".assistant .text pre code").last().textContent()
      ).trim(),
      JSON.stringify({ nested: { ok: true } }, null, 2),
    );
    await finalAnswer('Data:\n\n```json\n{"a":[1,2]}\n```');
    assert.equal(
      (
        await page.locator(".assistant .text pre code").last().textContent()
      ).trim(),
      JSON.stringify({ a: [1, 2] }, null, 2),
    );
    await finalAnswer({ a: [1, 2] });
    assert.equal(
      (
        await page.locator(".assistant .text pre code").last().textContent()
      ).trim(),
      JSON.stringify({ a: [1, 2] }, null, 2),
    );
    await finalAnswer('```json\n{"incomplete":\n```');
    assert(
      (await page.locator(".assistant .text").last().textContent()).includes(
        '{"incomplete":',
      ),
    );
    await finalAnswer(
      '<script>window.injected=1</script>\n\n<img src=x onerror="window.injected=1">\n\n[attack](javascript:alert(1))\n\n![image](https://example.com/pixel)\n\n[safe](https://example.com)',
    );
    const body = page.locator(".assistant .text").last();
    assert.equal(await body.locator("script,img,iframe").count(), 0);
    assert.equal(await body.locator('a[href^="javascript:"]').count(), 0);
    assert.equal(
      await body.locator('a[href="https://example.com"]').count(),
      1,
    );
    const sourceLink = body.locator('a[href="https://example.com"]');
    assert.equal(await sourceLink.getAttribute("target"), "_blank");
    assert.equal(await sourceLink.getAttribute("rel"), "noopener noreferrer");
    await page
      .context()
      .route("https://example.com/**", (route) =>
        route.fulfill({ body: "Source page", contentType: "text/html" }),
      );
    const originalUrl = page.url(),
      opened = page.waitForEvent("popup");
    await sourceLink.click();
    const popup = await opened;
    await popup.waitForLoadState();
    assert.equal(page.url(), originalUrl);
    assert.equal(await popup.evaluate(() => window.opener), null);
    await popup.close();

    assert.equal(await page.evaluate(() => window.injected), undefined);
    await finalAnswer(markdown + "\n\n```text\n" + "a".repeat(220) + "\n```");
    if (await page.locator("#activity-panel").isVisible())
      await page.locator("#panel-toggle").click();
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 960 });
      for (const theme of ["light", "dark"]) {
        await page.evaluate((t) => {
          TailTheme.apply(t === "dark" ? "amethyst" : "violet-bordeaux", false);
        }, theme);
        assert(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
        );
      }
    }
    await page.setViewportSize({ width: 1280, height: 960 });
    await page.evaluate(() => {
      TailTheme.apply("violet-bordeaux", false);
      $("messages").style.scrollBehavior = "auto";
      $("messages").scrollTop = 0;
    });
    await page.screenshot({
      path:
        process.env.FORMAT_SCREENSHOT ||
        "/tmp/tail-harness-response-format.png",
      fullPage: true,
    });
    assert.deepEqual(errors, []);
    console.log(
      "PASS: streaming, historical/final Markdown, tables, JSON, no copy button, literal user text, XSS and responsive layout.",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
