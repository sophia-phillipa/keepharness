// H15 non-native input (P1/P7): accents, ZWJ emoji and Arabic must round-trip
// byte-identical; RTL/emoji titles must not bleed or get cut mid-character.
"use strict";
const assert = require("node:assert/strict");
const { mockHarness, runPersona } = require("./_harness.cjs");

// Mx fixture ("claude-fx-5" stands for fx-claude: HarnessUI.selectableModel hides other Claude ids).
const MX = {
  "GET /v1/models": {
    json: {
      models: [
        {
          id: "claude-fx-5",
          backend: "claude",
          efforts: ["low", "high"],
          permissions: { upload: true },
          execution_modes: ["native", "scoped"],
        },
        {
          id: "fx-codex",
          backend: "codex",
          efforts: ["low", "medium"],
          permissions: { upload: true },
          execution_modes: ["native", "scoped"],
        },
      ],
      providers: { claude: true, codex: true },
      uploads_enabled: true,
    },
  },
  "GET /v1/projects": {
    json: {
      projects: ["sem-projeto", "demo"],
      details: { demo: { label: "Demo" } },
    },
  },
};

const PROMPT = "Ação, coração e pão — olá! 👩‍👩‍👧‍👦 👨🏽‍💻 🏳️‍🌈 مرحبا بالعالم ١٢٣ end";
const ARABIC = "مرحبا بالعالم!";
const SPOOF = "Relatório ‮gpj.exe"; // unterminated RIGHT-TO-LEFT OVERRIDE
const FAMILY = "👩‍👩‍👧‍👦";
const LONG = "x".repeat(78) + FAMILY + " tail"; // the emoji straddles the 80-char cut
const row = (id, title) => ({
  id,
  title,
  project: "sem-projeto",
  state: "completed",
  execution: { backend: "codex", model: "fx-codex" },
});
const turn = (id, prompt) => ({
  id,
  project: "sem-projeto",
  state: "completed",
  request: { backend: "codex", model: "fx-codex", prompt },
  result: { answer: "ok" },
});

async function open(page, over) {
  const s = await mockHarness(page, { ...MX, ...over });
  await page.goto("http://harness.test");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  return s;
}
const hasLoneSurrogate = (t) =>
  /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(
    t,
  );

runPersona("H15", [
  {
    title: "H15-S1 accents, ZWJ emoji and Arabic round-trip unchanged",
    async run(page) {
      const s = await open(page, {
        // Echo the prompt back as the answer.
        "GET /v1/jobs/job-1": (route) =>
          route.fulfill({
            json: {
              id: "job-1",
              project: "sem-projeto",
              state: "completed",
              request: s.posts[0],
              result: { answer: s.posts[0].prompt },
            },
          }),
      });
      // IME twist: Enter that confirms a composition candidate must not send.
      await page.fill("#prompt", "にほんご");
      await page.locator("#prompt").evaluate((el) =>
        el.dispatchEvent(
          new KeyboardEvent("keydown", {
            key: "Enter",
            isComposing: true,
            bubbles: true,
            cancelable: true,
          }),
        ),
      );
      await page.waitForTimeout(200);
      assert.equal(
        s.posts.length,
        0,
        "Enter during IME composition must not send",
      );

      await page.fill("#prompt", PROMPT);
      // The spoken count is debounced (250 ms after typing pauses), so wait for it to settle.
      const counted = Array.from(PROMPT).length + " characters";
      await page.waitForFunction(
        (text) => document.getElementById("character-count").textContent === text,
        counted,
      );
      assert.equal(
        await page.locator("#character-count").textContent(),
        counted,
      );
      await page.locator("#prompt").press("Enter");
      await page.waitForFunction(
        () =>
          document.querySelectorAll("#messages .message.assistant .chat-bubble")
            .length === 1,
      );
      assert.equal(s.posts.length, 1);
      assert.equal(
        s.posts[0].prompt,
        PROMPT,
        "POST prompt must be byte-identical",
      );
      assert.equal(
        Buffer.from(s.posts[0].prompt).toString("hex"),
        Buffer.from(PROMPT).toString("hex"),
      );
      const user = await page
        .locator("#messages .message.user .chat-bubble")
        .first()
        .textContent();
      assert.equal(user, PROMPT, "user bubble must be byte-identical");
      await page
        .locator("#messages .message.assistant .chat-bubble")
        .getByText("end")
        .waitFor();
      const answer = (
        await page
          .locator("#messages .message.assistant .chat-bubble")
          .first()
          .innerText()
      ).trim();
      assert.equal(answer, PROMPT, "echoed answer must be byte-identical");
      const title = await page
        .locator("#conversation-title")
        .getAttribute("title");
      assert.equal(title, PROMPT, "title attr keeps the full prompt");
    },
  },
  {
    title: "H15-S2 RTL, override and emoji titles stay isolated and whole",
    async run(page) {
      await open(page, {
        "GET /v1/conversations": {
          json: {
            conversations: [
              row("c1", ARABIC),
              row("c2", SPOOF),
              row("c3", "Plain neighbour 42"),
              row("c4", LONG),
            ],
          },
        },
        "GET /v1/conversations/c4": {
          json: { title: LONG, turns: [turn("c4", LONG)] },
        },
        "GET /v1/conversations/c1": {
          json: { title: ARABIC, turns: [turn("c1", ARABIC)] },
        },
      });
      const titles = page.locator("#sidebar .conversation-title");
      await titles.nth(3).waitFor();
      assert.deepEqual(await titles.allTextContents(), [
        ARABIC,
        SPOOF,
        "Plain neighbour 42",
        LONG,
      ]);
      const buttons = page.locator("#sidebar .conversation-row > button");
      for (const [i, text] of [
        ARABIC,
        SPOOF,
        "Plain neighbour 42",
        LONG,
      ].entries())
        assert.equal(
          (await buttons.nth(i).getAttribute("title")).split("\n")[0],
          text,
          "row " + i + " title attr must hold the full text",
        );

      // No bleed: the override in row 2 must not reorder the next row, and each row's
      // "⋯" actions button stays to the right of its title.
      const geometry = await page.evaluate(() =>
        [...document.querySelectorAll("#sidebar .conversation-row")].map(
          (r) => {
            const t = r.querySelector(".conversation-title"),
              range = document.createRange();
            range.setStart(t.firstChild, 0);
            range.setEnd(t.firstChild, 1);
            const first = range.getBoundingClientRect().x;
            range.setStart(t.firstChild, t.firstChild.length - 1);
            range.setEnd(t.firstChild, t.firstChild.length);
            return {
              first,
              last: range.getBoundingClientRect().x,
              actions: r.querySelector("summary").getBoundingClientRect().x,
              titleRight: t.getBoundingClientRect().right,
            };
          },
        ),
      );
      assert(
        geometry[2].first < geometry[2].last,
        "the row after the RLO title must still read left-to-right",
      );
      for (const g of geometry)
        assert(
          g.actions >= g.titleRight - 1,
          "actions stay right of the title",
        );

      // Arabic title in the header: text is intact and the page does not throw.
      await page.locator("#history").getByText(ARABIC).click();
      await page.waitForFunction(
        (t) => document.querySelector("#conversation-title").title === t,
        ARABIC,
      );
      assert.equal(
        await page.locator("#conversation-title").textContent(),
        ARABIC,
      );

      // Long title whose 80-char cut falls inside a ZWJ emoji.
      await page.locator("#history").getByText("xxxxxxxx").click();
      await page.waitForFunction(
        (t) => document.querySelector("#conversation-title").title === t,
        LONG,
      );
      const shown = await page.locator("#conversation-title").textContent();
      // F-66 fixed: setConversationTitle() now cuts on code points
      // (Array.from), never splitting a surrogate pair.
      assert(
        !hasLoneSurrogate(shown),
        "header must not split a surrogate pair when truncating",
      );
      assert(shown.endsWith("…"));

      // F-67 fixed: title elements get dir="auto", so an Arabic title uses an
      // RTL base direction and its trailing "!" lands left of the text.
      await page.locator("#history").getByText(ARABIC).click();
      await page.waitForFunction(
        (t) => document.querySelector("#conversation-title").title === t,
        ARABIC,
      );
      const bangRightOfText = await page.evaluate(() =>
        ["#conversation-title", "#sidebar .conversation-title"].map((sel) => {
          const node = document.querySelector(sel).firstChild,
            r = document.createRange();
          r.setStart(node, 0);
          r.setEnd(node, 1);
          const first = r.getBoundingClientRect().x;
          r.setStart(node, node.length - 1);
          r.setEnd(node, node.length);
          return r.getBoundingClientRect().x > first;
        }),
      );
      assert.deepEqual(
        bangRightOfText,
        [false, false],
        "RTL titles must use an RTL base direction",
      );
    },
  },
]);
