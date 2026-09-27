// H16 paste maximalist (P2/P3): pastes a 200 KB server log straight into the
// composer, then asks for a 5,000-line answer and navigates it.
"use strict";
const assert = require("node:assert/strict");
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

function logText(bytes) {
  const lines = [];
  for (let i = 0; lines.join("\n").length < bytes; i++)
    lines.push(
      `2026-09-26T10:${String(i % 60).padStart(2, "0")}:00Z WARN worker[${i}] retrying upstream request id=${i.toString(16)} after 503`,
    );
  return lines.join("\n").slice(0, bytes);
}

async function open(page, over) {
  const s = await mockHarness(page, { "GET /v1/models": MODELS, ...over });
  await page.goto("http://harness.test");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  await page.locator("#model-label", { hasText: /Sonnet|claude/i }).waitFor();
  return s;
}

// A real clipboard paste (Ctrl+V fires one input event). The system clipboard
// is filled from a scratch textarea because http://harness.test is not a secure
// context (no navigator.clipboard). execCommand("insertText") is not a stand-in:
// it fires one input event per line and made a 200 KB paste take minutes.
async function paste(page, text) {
  await page.evaluate((value) => {
    const source = document.createElement("textarea");
    source.id = "h16-clipboard";
    source.value = value;
    document.body.append(source);
    source.select();
  }, text);
  await page.keyboard.press("Control+C");
  await page.evaluate(() => document.getElementById("h16-clipboard").remove());
  await page.locator("#prompt").focus();
  const start = Date.now();
  await page.keyboard.press("Control+V");
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(r)));
  return Date.now() - start;
}

runPersona("h16", [
  {
    title: "H16-S1 200 KB paste refused by payload_limit keeps the draft",
    timeout: 10000,
    async run(page) {
      const consoleClean = allowHttpErrors(page, [413]);
      let posts = 0;
      await open(page, {
        "POST /v1/jobs": (route) => {
          posts++;
          return route.fulfill({
            status: 413,
            json: { code: "payload_limit" },
          });
        },
      });
      const log = logText(200 * 1024);
      const ms = await paste(page, log);
      assert(ms < 500, "paste handled in " + Math.round(ms) + " ms (< 500)");
      assert.equal(
        (await page.locator("#prompt").inputValue()).length,
        log.length,
      );
      // Typing one more character into the 200 KB draft must stay responsive.
      await page.keyboard.press("Control+End");
      const typed = Date.now();
      await page.keyboard.type("!");
      await page.evaluate(() => new Promise((r) => requestAnimationFrame(r)));
      const keyMs = Date.now() - typed;
      assert(keyMs < 500, "keystroke handled in " + keyMs + " ms (< 500)");
      console.log("H16-S1 paste " + ms + " ms, keystroke " + keyMs + " ms");
      await page.keyboard.press("Backspace");
      await page.locator("#send:not([disabled])").click();
      await page.locator("#status", { hasText: "Couldn't run" }).waitFor();
      assert.equal(posts, 1);
      const text = await page.locator("#status").innerText();
      // F-70: a readable sentence with a next step, never the raw code.
      assert.match(text, /^Couldn't run: This message is too large to send\./);
      assert.match(text, /attach it as a file/);
      assert.doesNotMatch(text, /payload_limit/);
      assert.equal(
        await page.locator("#prompt").inputValue(),
        log,
        "draft kept",
      );
      assert.equal(
        await page.locator(".message.user, .bubble.user").count(),
        0,
      );
      assert(
        await page.locator("#send").isEnabled(),
        "can retry after editing",
      );
      consoleClean();
    },
  },
  {
    title: "H16-S2 5,000-line answer scrolls inside its code block",
    timeout: 20000,
    async run(page) {
      const code = Array.from(
        { length: 5000 },
        (_, i) => `line ${i + 1}: ${"x".repeat(20)}`,
      ).join("\n");
      const answer =
        "Here is the full listing:\n\n```text\n" + code + "\n```\n\nEND-H16";
      const s = await open(page, {
        "GET /v1/jobs/job-1": (route) =>
          route.fulfill({
            json: {
              id: "job-1",
              project: "sem-projeto",
              state: "completed",
              request: s.posts[0],
              result: { answer },
            },
          }),
      });
      const prompt = logText(140 * 1024);
      await paste(page, prompt);
      await page.locator("#send:not([disabled])").click();
      const block = page.locator("#messages .message pre").last();
      await page.locator("#messages", { hasText: "END-H16" }).waitFor();
      assert.equal(s.posts.length, 1);
      assert.equal(
        s.posts[0].prompt,
        prompt.trim(),
        "140 KB prompt sent intact",
      );
      const box = await block.evaluate((e) => ({
        client: e.clientHeight,
        scroll: e.scrollHeight,
        overflow: getComputedStyle(e).overflowY,
      }));
      assert(box.client <= 330, "code block is capped (" + box.client + "px)");
      assert(box.scroll > box.client * 10, "code block scrolls internally");
      assert.equal(box.overflow, "auto");
      const noPageScroll = await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      );
      assert(noPageScroll, "no horizontal page scroll");
      await page.locator("#messages").evaluate((e) => (e.scrollTop = 0));
      await page.locator("#latest-message").waitFor({ state: "visible" });
      await page.locator("#latest-message").click();
      await page.locator("#latest-message").waitFor({ state: "hidden" });
      const atBottom = await page
        .locator("#messages")
        .evaluate((e) => e.scrollHeight - e.scrollTop - e.clientHeight < 150);
      assert(atBottom, "jumped to the latest message");
      await visible(page, "#prompt");
    },
  },
]);
