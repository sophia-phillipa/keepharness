// H13 phone novice (P1/P5): fat-finger taps on a 375x667 phone, rotation with a
// draft, and picking a conversation from the slide-in sidebar.
"use strict";
const assert = require("node:assert/strict");
const { mockHarness, runPersona } = require("./_harness.cjs");

// Mx fixture ("claude-fx-5" stands for fx-claude: TailUI.selectableModel hides other Claude ids).
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
  "GET /v1/conversations/c2": {
    json: {
      title: "Birthday message for grandma",
      turns: [
        {
          id: "c2",
          project: "sem-projeto",
          state: "completed",
          request: {
            backend: "codex",
            model: "fx-codex",
            prompt: "Write a birthday note",
          },
          result: { answer: "Happy birthday, grandma!" },
        },
      ],
    },
  },
  "GET /v1/conversations": {
    json: {
      conversations: [
        {
          id: "c1",
          title: "Grocery list",
          project: "sem-projeto",
          state: "completed",
          execution: { backend: "codex", model: "fx-codex" },
        },
        {
          id: "c2",
          title: "Birthday message for grandma",
          project: "sem-projeto",
          state: "completed",
          execution: { backend: "codex", model: "fx-codex" },
        },
      ],
    },
  },
};
const PHONE = { width: 375, height: 667 };

async function open(page) {
  const s = await mockHarness(page, MX);
  await page.goto("http://harness.test");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  return s;
}
const noHorizontalScroll = (page) =>
  page.evaluate(
    () =>
      document.documentElement.scrollWidth <= innerWidth &&
      document.body.scrollWidth <= innerWidth,
  );

runPersona("H13", [
  {
    title:
      "H13-S1 44px targets, edge taps on send and rotation keeps the draft",
    viewport: PHONE,
    async run(page) {
      const s = await open(page);
      await page.fill("#prompt", "Is it going to rain tomorrow?");
      const size = async (id) => {
        const b = await page.locator(id).boundingBox();
        return [b.width, b.height].map(Math.round);
      };
      const send = await size("#send");
      assert(
        send[0] >= 44 && send[1] >= 44,
        "#send must be at least 44x44, got " + send,
      );
      // KNOWN BUG F-64: the phone rule `@media (max-width:620px) .icon {min-width:44px;
      // min-height:44px}` (ui.css) loses to `.composer-actions .icon {40px}` and a
      // top-bar width rule, so #attach is 40x40 and #menu 40x44. Correct: both >= 44x44.
      assert.deepEqual(
        [await size("#attach"), await size("#menu")],
        [
          [40, 40],
          [40, 44],
        ],
        "F-64 fixed? flip this assertion",
      );
      // A thumb lands 3px inside each corner of #send; each must hit the button.
      const b = await page.locator("#send").boundingBox();
      for (const [x, y] of [
        [b.x + 3, b.y + 3],
        [b.x + b.width - 3, b.y + 3],
        [b.x + 3, b.y + b.height - 3],
        [b.x + b.width - 3, b.y + b.height - 3],
      ]) {
        const hit = await page.evaluate(
          ([x, y]) => !!document.elementFromPoint(x, y)?.closest("#send"),
          [x, y],
        );
        assert(hit, "tap at " + [x, y].map(Math.round) + " must reach #send");
      }
      // Rotate to landscape and back with an unsent draft.
      await page.setViewportSize({ width: 667, height: 375 });
      await page.waitForTimeout(200);
      assert.equal(
        await page.inputValue("#prompt"),
        "Is it going to rain tomorrow?",
      );
      assert(await noHorizontalScroll(page), "landscape: no horizontal scroll");
      // KNOWN BUG F-65: at 667x375 the header, the flex-shrink:0 #execution-mode-choice
      // card and the composer do not fit; main has no overflow handling and body is
      // overflow:hidden, so the composer (draft + #send) sits below the fold and cannot
      // be scrolled into view by touch. Correct: #send and #prompt stay on screen.
      const sendBox = await page.locator("#send").boundingBox();
      assert(
        sendBox.y + sendBox.height > 375,
        "F-65 fixed? #send is now on screen - flip this assertion",
      );
      await page.setViewportSize(PHONE);
      await page.waitForTimeout(200);
      assert.equal(
        await page.inputValue("#prompt"),
        "Is it going to rain tomorrow?",
      );
      assert.equal(s.posts.length, 0, "rotation must not send");
      await page
        .locator("#send")
        .tap({ position: { x: 4, y: 4 } })
        .catch(() => page.locator("#send").click({ position: { x: 4, y: 4 } }));
      await page.waitForFunction(
        () => !document.querySelector("#prompt").value,
      );
      assert.equal(s.posts.length, 1);
      assert.equal(s.posts[0].prompt, "Is it going to rain tomorrow?");
    },
  },
  {
    title: "H13-S2 open the menu, pick a conversation, sidebar closes",
    viewport: PHONE,
    async run(page) {
      await open(page);
      assert.equal(
        await page.locator("#menu").getAttribute("aria-expanded"),
        "false",
        "sidebar starts closed on a phone",
      );
      assert(await noHorizontalScroll(page));
      await page.click("#menu");
      await page.waitForFunction(
        () =>
          document.querySelector("#menu").getAttribute("aria-expanded") ===
          "true",
      );
      const item = page
        .locator("#history")
        .getByText("Birthday message for grandma");
      await item.waitFor();
      const box = await item.boundingBox();
      assert(
        box.x >= 0 && box.x + box.width <= PHONE.width + 1,
        "conversation title must be on screen",
      );
      await item.click();
      await page.waitForFunction(() =>
        document
          .querySelector("#conversation-title")
          .textContent.includes("Birthday"),
      );
      await page
        .locator("#messages")
        .getByText("Happy birthday, grandma!")
        .waitFor();
      await page.waitForTimeout(300); // slide-out transition
      // KNOWN BUG F-63: load() (and #new, project picks) close the phone sidebar with
      // classList.remove("open") but never reset #menu aria-expanded, which only
      // toggleSidebar() updates. Correct: "false" once the sidebar is gone.
      assert.equal(
        await page.locator("#menu").getAttribute("aria-expanded"),
        "true",
        "F-63 fixed? flip this assertion",
      );
      const sidebar = await page.locator("#sidebar").boundingBox();
      assert(
        !sidebar ||
          sidebar.x + sidebar.width <= 1 ||
          !(await page.locator("#sidebar").isVisible()),
        "sidebar must be off screen after picking",
      );
      assert(
        await noHorizontalScroll(page),
        "no horizontal scroll after picking",
      );
      await page.locator("#prompt").click();
      assert.equal(
        await page.evaluate(() => document.activeElement.id),
        "prompt",
        "composer usable after closing the sidebar",
      );
    },
  },
]);
