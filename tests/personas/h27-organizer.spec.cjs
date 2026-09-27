// H27 organizer (P3/P7): renames a conversation at the length limit (plain
// text, then emoji/combining marks), then checks long titles in #history and
// the conversation-title header, plus accent-insensitive search.
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

function oneTurnConversation(title) {
  return {
    title,
    turns: [
      {
        id: "job-1",
        project: "sem-projeto",
        state: "completed",
        request: {
          prompt: "Summarize the sprint",
          model: MODEL_ID,
          backend: "claude",
          access_mode: "ask",
        },
        result: { answer: "Sprint summary ready.", total_seconds: 0.8 },
      },
    ],
  };
}

async function open(page, conversations, over) {
  const patches = [];
  const s = await mockHarness(page, {
    "GET /v1/models": MODELS,
    "GET /v1/conversations": { json: { conversations } },
    "PATCH /v1/conversations/c1": (route) => {
      patches.push(route.request().postDataJSON());
      return route.fulfill({ json: {} });
    },
    ...over,
  });
  await page.goto("http://harness.test");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  await page.locator("#model-label", { hasText: /Sonnet|claude/i }).waitFor();
  return { s, patches };
}

async function openRenameDialog(page, title) {
  const row = page.locator(".conversation-row", { hasText: title });
  await row.locator("summary").click();
  await page.getByRole("button", { name: "Rename conversation" }).click();
  await page.locator("#rename-conversation-dialog[open]").waitFor();
}

async function setRenameValue(page, value) {
  const input = page.locator("#rename-conversation-name");
  await input.fill("");
  // fill() would insert the value in one shot; the app validates on the
  // "input" event, which fill() does dispatch, so this is a faithful paste.
  await input.fill(value);
}

runPersona("h27", [
  {
    title:
      "H27-S1 renaming past 100 characters is refused; emoji/combining at the limit is accepted",
    timeout: 10000,
    async run(page) {
      const title = "Sprint 42 retro";
      const { patches } = await open(
        page,
        [
          {
            id: "c1",
            title,
            project: "sem-projeto",
            state: "completed",
            execution: { model: MODEL_ID },
          },
        ],
        { "GET /v1/conversations/c1": { json: oneTurnConversation(title) } },
      );
      await page
        .locator("#history .conversation-row > button")
        .filter({ hasText: title })
        .click();
      await page.locator(".message.assistant").waitFor();
      await openRenameDialog(page, title);

      const tooLong = "R".repeat(500);
      await setRenameValue(page, tooLong);
      await page
        .locator("#rename-conversation-error", { hasText: "100 characters" })
        .waitFor();
      assert.equal(
        await page.locator("#rename-conversation-error").innerText(),
        "Use at most 100 characters.",
      );
      assert.equal(
        await page.locator("#rename-conversation-count").innerText(),
        "500 / 100 characters",
      );
      assert(
        await page.locator("#rename-conversation-save").isDisabled(),
        "Save is disabled over the limit",
      );

      // Exactly 100 Unicode code points: 20 emoji (surrogate pairs) plus 40
      // base+combining-accent pairs (e + U+0301), 20 visually distinct
      // grapheme clusters. The app counts code points, not clusters.
      const atLimit = "\u{1F642}".repeat(20) + "é".repeat(40);
      assert.equal(
        Array.from(atLimit).length,
        100,
        "fixture is 100 code points",
      );
      await setRenameValue(page, atLimit);
      await page
        .locator("#rename-conversation-count", { hasText: "100 / 100" })
        .waitFor();
      assert.equal(
        await page.locator("#rename-conversation-error").innerText(),
        "",
      );
      assert(
        await page.locator("#rename-conversation-save").isEnabled(),
        "Save is enabled exactly at the limit",
      );
      await page.locator("#rename-conversation-save").click();
      await page.locator("#rename-conversation-dialog[open]").waitFor({
        state: "hidden",
      });
      assert.equal(patches.length, 1, "exactly one PATCH sent");
      assert.deepEqual(
        patches[0],
        { title: atLimit },
        "PATCH body exact, untrimmed",
      );
    },
  },
  {
    title:
      "H27-S2 long titles keep an ellipsis and full tooltip; search ignores accents",
    timeout: 10000,
    async run(page) {
      const plain =
        "Café roadmap".repeat(1) +
        " budget planning for the whole engineering organization next year";
      const accented =
        "Preparação do relatório final da equipe de operações internacionais";
      const emojiHeavy = "🙂".repeat(45) + " status update";
      const conversations = [
        { id: "c1", title: plain, project: "sem-projeto", state: "completed" },
        {
          id: "c2",
          title: accented,
          project: "sem-projeto",
          state: "completed",
        },
        {
          id: "c3",
          title: emojiHeavy,
          project: "sem-projeto",
          state: "completed",
        },
      ];
      const { s } = await open(page, conversations, {
        "GET /v1/conversations/c3": { json: oneTurnConversation(emojiHeavy) },
      });
      void s;

      // Sidebar rows: CSS clamps to 2 lines, but the full title is always the
      // tooltip and the accessible name, regardless of length.
      const row = page.locator(".conversation-row", {
        hasText: "budget planning",
      });
      const openButton = row.locator("button").first();
      assert.equal(await openButton.getAttribute("title"), plain);
      const box = await openButton.evaluate((e) => ({
        scroll: e.scrollHeight,
        client: e.clientHeight,
      }));
      assert(box.scroll <= box.client + 1, "line-clamp caps the row's own box");

      // Accent-insensitive search: "cafe" and "café" both find the accented
      // title, and a plain query also finds a title with different accents.
      await page.locator("#search-conversations").click();
      await page.locator("#conversation-search-dialog[open]").waitFor();
      for (const query of ["cafe", "café", "CAFÉ"]) {
        await page.locator("#conversation-search").fill(query);
        await page.locator("#search-results", { hasText: /found/ }).waitFor();
        assert.equal(
          await page.locator("#search-results").innerText(),
          "1 conversation(s) found",
          "query " + JSON.stringify(query),
        );
      }
      await page.locator("#conversation-search").fill("preparacao");
      await page.locator("#search-results", { hasText: /found/ }).waitFor();
      assert.equal(
        await page.locator("#search-results").innerText(),
        "1 conversation(s) found",
        "accent-insensitive match on a Portuguese title",
      );
      await page.locator("#conversation-search-close").click();

      // The main header truncates at 80 UTF-16 units with a plain slice(0,79):
      // for a title made of 2-unit emoji, that cut lands mid-surrogate-pair.
      await page
        .locator("#history .conversation-row > button")
        .filter({ hasText: "status update" })
        .click();
      await page.locator(".message.assistant").waitFor();
      const header = await page
        .locator("#conversation-title")
        .evaluate((e) => ({
          text: e.textContent,
          title: e.getAttribute("title"),
        }));
      assert.equal(
        header.title,
        emojiHeavy,
        "tooltip keeps the untruncated title",
      );
      // KNOWN BUG F-75: agent_service/ui.js setConversationTitle() slices the
      // header at 79 UTF-16 code units (full.slice(0, 79)) without checking
      // surrogate-pair boundaries. For this 2-unit-per-emoji title the cut
      // lands between a high and low surrogate, so the visible header ends in
      // a lone (unpaired) high surrogate right before the added ellipsis.
      const lastCode = header.text.codePointAt(header.text.length - 2);
      assert(
        lastCode >= 0xd800 && lastCode <= 0xdbff,
        "header text ends with a lone high surrogate before the ellipsis",
      );
      assert.equal(header.text.slice(-1), "…");
      assert(header.text.length < emojiHeavy.length, "header is truncated");
    },
  },
]);
