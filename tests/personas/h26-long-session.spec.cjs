// H26 long-session writer (P3): keeps one conversation running for 300 turns,
// then juggles 500 conversations in the search dialog. Both must stay fast.
"use strict";
const assert = require("node:assert/strict");
const { mockHarness, runPersona } = require("./_harness.cjs");

const MODEL_ID = "claude-sonnet-4-6";
const MODELS = {
  json: {
    uploads_enabled: false,
    providers: { claude: true },
    models: [
      {
        id: MODEL_ID,
        backend: "claude",
        efforts: ["low"],
        permissions: { upload: true },
        execution_modes: ["native"],
      },
    ],
  },
};

function longConversationTurns(count) {
  return Array.from({ length: count }, (_, i) => ({
    id: "job-" + (i + 1),
    project: "sem-projeto",
    state: "completed",
    request: {
      prompt: "Turn " + (i + 1) + ": what changed since last time?",
      model: MODEL_ID,
      backend: "claude",
      access_mode: "ask",
    },
    result: {
      answer:
        i + 1 === count
          ? "Answer " + (i + 1) + " TURN-" + count + "-DONE"
          : "Answer " + (i + 1),
      total_seconds: 1.2,
      model: MODEL_ID,
    },
  }));
}

function manyConversations(count, needleCount, needle) {
  return Array.from({ length: count }, (_, i) => ({
    id: "conv-" + (i + 1),
    // Every `needle`-th title carries the search token so the expected match
    // count is deterministic regardless of iteration order.
    title:
      i < needleCount
        ? needle + " review " + (i + 1)
        : "Standup notes " + (i + 1),
    project: "sem-projeto",
    state: "completed",
    execution: { model: MODEL_ID },
  }));
}

async function open(page, over) {
  const s = await mockHarness(page, { "GET /v1/models": MODELS, ...over });
  await page.goto("http://harness.test");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  await page.locator("#model-label", { hasText: /Sonnet|claude/i }).waitFor();
  return s;
}

runPersona("h26", [
  {
    title: "H26-S1 a 300-turn conversation opens under 2 s and jumps to latest",
    timeout: 15000,
    async run(page) {
      const title = "Release retro marathon";
      await open(page, {
        "GET /v1/conversations": {
          json: {
            conversations: [
              {
                id: "c1",
                title,
                project: "sem-projeto",
                state: "completed",
                execution: { model: MODEL_ID },
              },
            ],
          },
        },
        "GET /v1/conversations/c1": {
          json: { title, turns: longConversationTurns(300) },
        },
      });
      const row = page
        .locator("#history .conversation-row > button")
        .filter({ hasText: title });
      await row.waitFor();
      const start = Date.now();
      await row.click();
      await page.locator("#messages", { hasText: "TURN-300-DONE" }).waitFor();
      const ms = Date.now() - start;
      assert(ms < 2000, "conversation opened in " + ms + " ms (< 2000)");
      console.log("H26-S1 open " + ms + " ms");
      assert.equal(await page.locator("#messages .message.user").count(), 300);
      assert.equal(
        await page.locator("#messages .message.assistant").count(),
        300,
      );
      // Nothing scrolled the pane down while appending 600 bubbles, so the
      // jump-to-latest control must already be offered.
      await page.locator("#latest-message").waitFor({ state: "visible" });
      await page.locator("#latest-message").click();
      await page.locator("#latest-message").waitFor({ state: "hidden" });
      const atBottom = await page
        .locator("#messages")
        .evaluate((e) => e.scrollHeight - e.scrollTop - e.clientHeight < 150);
      assert(atBottom, "jumped to the latest message");
    },
  },
  {
    title:
      "H26-S2 searching 500 conversations stays under 200 ms per keystroke",
    timeout: 15000,
    async run(page) {
      const needle = "Quarterly budget",
        needleCount = 23;
      await open(page, {
        "GET /v1/conversations": {
          json: {
            conversations: manyConversations(500, needleCount, needle),
          },
        },
      });
      await page.locator("#search-conversations").click();
      await page.locator("#conversation-search-dialog[open]").waitFor();
      const input = page.locator("#conversation-search");
      await input.focus();
      const query = "quarterly";
      for (const ch of query) {
        const typed = Date.now();
        await page.keyboard.type(ch);
        await page.evaluate(() => new Promise((r) => requestAnimationFrame(r)));
        const ms = Date.now() - typed;
        assert(
          ms < 200,
          "keystroke '" + ch + "' handled in " + ms + " ms (< 200)",
        );
      }
      await page.locator("#search-results", { hasText: /found/ }).waitFor();
      assert.equal(await input.inputValue(), query);
      assert.equal(
        await page.locator("#search-results").innerText(),
        needleCount + " conversation(s) found",
      );
      assert.equal(
        await page.locator(".conversation-search-result").count(),
        needleCount,
      );
    },
  },
]);
