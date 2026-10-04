// H11 keyboard-only user (P4): no mouse; skip link, Ctrl+K search, model picker
// arrows and a keyboard approval must keep a visible, never-lost focus.
"use strict";
const assert = require("node:assert/strict");
const { mockHarness, runPersona } = require("./_harness.cjs");

// Default Mx fixture (roster): fx-claude and fx-codex (the Claude id is
// "claude-fx-5" because HarnessUI.selectableModel hides non "claude-<family>-<n>" ids), native+scoped; sem-projeto + demo.
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

async function open(page, over = {}) {
  const s = await mockHarness(page, { ...MX, ...over(() => s) });
  await page.goto("http://harness.test");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  return s;
}

const focused = (page) =>
  page.evaluate(() => {
    const e = document.activeElement;
    return e.id || e.getAttribute("role") || e.tagName;
  });

// A keyboard user must see where focus is: :focus-visible plus a real outline or ring.
async function focusVisible(page, label) {
  const ok = await page.evaluate(() => {
    const e = document.activeElement,
      st = getComputedStyle(e);
    return (
      e !== document.body &&
      e.matches(":focus-visible") &&
      ((st.outlineStyle !== "none" && parseFloat(st.outlineWidth) > 0) ||
        st.boxShadow !== "none" ||
        // menu options signal focus with a background fill instead of an outline
        st.backgroundColor !==
          getComputedStyle(e.closest("[popover]") || e.parentElement)
            .backgroundColor)
    );
  });
  assert(
    ok,
    label + ": focus must be visible (got " + (await focused(page)) + ")",
  );
}

async function tabTo(page, id, max = 40) {
  for (let i = 0; i < max; i++) {
    await page.keyboard.press("Tab");
    if ((await focused(page)) === id) return;
  }
  throw new Error("Tab never reached #" + id);
}

runPersona("H11", [
  {
    title: "H11-S1 skip link, send, Ctrl+K and model picker by keyboard",
    async run(page) {
      const s = await open(page, () => ({}));
      await page.keyboard.press("Tab");
      assert.equal(
        await page.evaluate(() => document.activeElement.className),
        "skip-link",
      );
      await focusVisible(page, "skip link");
      await page.keyboard.press("Enter");
      assert.equal(
        await focused(page),
        "prompt",
        "Skip to message must land in #prompt",
      );
      await page.keyboard.type("hello from the keyboard");
      await page.keyboard.press("Enter");
      await page.waitForFunction(() =>
        document.querySelector(
          "#messages .user, #messages [data-role=user], #messages .chat-bubble",
        ),
      );
      assert.equal(s.posts.length, 1);
      assert.equal(s.posts[0].prompt, "hello from the keyboard");

      // Ctrl+K from the composer opens search; Escape returns to where the user was.
      await page.locator("#prompt").focus();
      await page.keyboard.press("Control+k");
      await page.locator("#conversation-search-dialog[open]").waitFor();
      assert.equal(await focused(page), "conversation-search");
      await page.keyboard.press("Escape");
      await page
        .locator("#conversation-search-dialog")
        .waitFor({ state: "hidden" });
      assert.equal(await focused(page), "prompt");

      // Opened from the top-bar button, Escape returns to #search-conversations.
      await page.locator("#search-conversations").focus();
      await page.keyboard.press("Enter");
      await page.locator("#conversation-search-dialog[open]").waitFor();
      // No trap: Tab cycles inside the modal and Escape still leaves it.
      for (let i = 0; i < 6; i++) await page.keyboard.press("Tab");
      assert(
        await page.evaluate(
          () => !!document.activeElement.closest("#conversation-search-dialog"),
        ),
      );
      await page.keyboard.press("Escape");
      await page
        .locator("#conversation-search-dialog")
        .waitFor({ state: "hidden" });
      assert.equal(await focused(page), "search-conversations");
      await focusVisible(page, "search button after Escape");

      // Model picker: ArrowDown opens it on the selected option, arrows move, Enter picks.
      await page.locator("#model-trigger").focus();
      await focusVisible(page, "model trigger");
      await page.keyboard.press("ArrowDown");
      await page.locator("#model-menu:popover-open").waitFor();
      // the popover "toggle" event is async, so poll the attribute
      await page.waitForFunction(
        () =>
          document
            .querySelector("#model-trigger")
            .getAttribute("aria-expanded") === "true",
      );
      const start = await page.evaluate(
        () =>
          document.activeElement.dataset.value ||
          document.activeElement.textContent,
      );
      await focusVisible(page, "model option");
      let target = null;
      for (let i = 0; i < 6 && !target; i++) {
        await page.keyboard.press("ArrowDown");
        const v = await page.evaluate(
          () => document.activeElement.dataset.value || "",
        );
        if (v && v !== start) target = v;
        else if (!v) await page.keyboard.press("ArrowRight"); // expand a collapsed provider group
      }
      assert(target, "arrows must reach another model");
      await page.keyboard.press("Enter");
      await page.locator("#model-menu").waitFor({ state: "hidden" });
      assert.equal(await page.inputValue("#model"), target);
      assert.equal(
        await focused(page),
        "model-trigger",
        "focus returns to the trigger after picking",
      );

      // Escape closes the reopened picker without losing focus to <body>.
      await page.keyboard.press("ArrowDown");
      await page.locator("#model-menu:popover-open").waitFor();
      await page.keyboard.press("Escape");
      await page.locator("#model-menu").waitFor({ state: "hidden" });
      assert.notEqual(
        await focused(page),
        "BODY",
        "Escape from the model menu must not drop focus",
      );

      // Tab from the picker leaves it (no trap) and reaches #send / #cancel.
      await page.keyboard.press("ArrowDown");
      await page.locator("#model-menu:popover-open").waitFor();
      await page.keyboard.press("Tab");
      await page.locator("#model-menu").waitFor({ state: "hidden" });
    },
  },
  {
    title:
      "H11-S2 approval card is answered with the keyboard and focus survives",
    async run(page) {
      const s = await open(page, (get) => ({
        "GET /v1/jobs/job-1": (route) => {
          const st = get();
          return route.fulfill({
            json: {
              id: "job-1",
              project: "sem-projeto",
              state: st.approvals.length ? "completed" : "running",
              request: st.posts[0],
              result: st.approvals.length
                ? { answer: "Listed the folder." }
                : {},
            },
          });
        },
      }));
      s.events.push({
        id: 1,
        type: "approval_required",
        data: {
          approval_id: "a1",
          request: { command: "ls" },
          can_remember: true,
        },
      });
      await page.locator("#prompt").focus();
      await page.keyboard.type("list the folder");
      await page.keyboard.press("Enter");
      await page.locator("#approval-a1").waitFor();
      // Only Tab/Shift+Tab: reach "Allow once" from the composer.
      let reached = false;
      for (let i = 0; i < 60 && !reached; i++) {
        await page.keyboard.press("Shift+Tab");
        reached = await page.evaluate(
          () => document.activeElement.textContent === "Allow once",
        );
      }
      assert(reached, "Allow once must be reachable with Shift+Tab");
      await focusVisible(page, "Allow once");
      await page.keyboard.press("Enter");
      await page.waitForFunction(() => !document.getElementById("approval-a1"));
      assert.deepEqual(s.approvals, [
        { id: "a1", body: { approved: true, answers: {}, scope: "once" } },
      ]);
      await page.locator("#messages").getByText("Listed the folder.").waitFor();
      // F-60: the answered card is removed, so focus moves to #prompt first
      // instead of falling back to <body>.
      assert.equal(await focused(page), "prompt");
    },
  },
]);
