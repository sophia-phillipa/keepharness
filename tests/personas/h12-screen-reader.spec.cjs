// H12 screen-reader user (P4): every control is named in the real accessibility
// tree, progress reaches the live region, disclosure state is exposed, and a
// failed deletion is announced without closing the dialog.
"use strict";
const assert = require("node:assert/strict");
const { mockHarness, runPersona, sse } = require("./_harness.cjs");

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
  "GET /v1/conversations": {
    json: {
      conversations: [
        {
          id: "c1",
          title: "Budget notes",
          project: "sem-projeto",
          state: "completed",
          execution: { backend: "codex", model: "fx-codex" },
        },
      ],
    },
  },
};

async function open(page, over = {}) {
  const s = await mockHarness(page, { ...MX, ...over });
  await page.goto("http://harness.test");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  return s;
}

// runPersona fails on any console.error, including Chromium's own network log for
// an intentional HTTP error; keep every other console error fatal. (Local
// stand-in until _harness.cjs filters "Failed to load resource" itself.)
function allowHttpErrorLogs(page) {
  page.removeAllListeners("console");
  const errors = [];
  page.on(
    "console",
    (m) =>
      m.type() === "error" &&
      !/^Failed to load resource: the server responded with a status of \d+/.test(
        m.text(),
      ) &&
      errors.push(m.text()),
  );
  return () => assert.deepEqual(errors, []);
}

const INTERACTIVE = new Set([
  "button",
  "link",
  "textbox",
  "searchbox",
  "combobox",
  "checkbox",
  "switch",
  "tab",
  "menuitem",
  "option",
  "treeitem",
  "slider",
  "listbox",
  "radio",
]);

// Chromium's computed accessibility tree (what a screen reader gets), not DOM heuristics.
async function unnamedControls(page) {
  const cdp = await page.context().newCDPSession(page);
  const { nodes } = await cdp.send("Accessibility.getFullAXTree");
  await cdp.detach();
  return nodes
    .filter(
      (n) =>
        !n.ignored &&
        INTERACTIVE.has(n.role?.value) &&
        !String(n.name?.value || "").trim(),
    )
    .map((n) => n.role.value + "#" + n.backendDOMNodeId);
}

runPersona("H12", [
  {
    title:
      "H12-S1 named controls, live progress and aria-expanded during a turn",
    timeout: 8000,
    async run(page) {
      let release;
      const gate = new Promise((r) => (release = r));
      await open(page, {
        "GET /v1/jobs/job-1/events": async (route) => {
          await gate; // hold the stream so the page sits mid-turn
          await route.fulfill({
            contentType: "text/event-stream",
            body: sse([
              { id: 1, type: "answer_delta", data: { text: "Partial " } },
              { id: 2, type: "answer_delta", data: { text: "answer." } },
            ]),
          });
        },
      });
      assert.deepEqual(
        await unnamedControls(page),
        [],
        "idle page: every control needs a name",
      );
      assert.equal(
        await page.locator("#status").getAttribute("role"),
        "status",
      );

      await page.fill("#prompt", "Summarize my notes");
      await page.keyboard.press("Enter");
      await page.locator("#cancel").waitFor();
      // KNOWN BUG F-62: status() in ui.js blanks every progress label ("Sending
      // request", "Working", "Completed"…), and the only other live region
      // (#activity-state) sits in the hidden Activity view, so a screen reader hears
      // nothing between Enter and the answer. Correct: #status announces progress.
      const midTurn = await page.locator("#status").innerText();
      assert.equal(
        midTurn.trim(),
        "",
        "F-62 fixed? #status now says " +
          JSON.stringify(midTurn) +
          " - flip this assertion",
      );
      assert(
        await page.locator("#activity-view").isHidden(),
        "the Activity live region is not rendered by default",
      );
      assert.deepEqual(
        await unnamedControls(page),
        [],
        "mid-turn: every control needs a name",
      );
      release();
      await page.locator("#messages").getByText("Fixture response.").waitFor();
      // F-62 (same cause): completion is not announced either.
      assert.equal((await page.locator("#status").innerText()).trim(), "");

      // Disclosure state must follow what is actually shown.
      for (const [button, region] of [
        ["#menu", "#sidebar"],
        ["#panel-toggle", "#activity-panel"],
      ]) {
        for (let i = 0; i < 2; i++) {
          await page.locator(button).focus();
          await page.keyboard.press("Enter");
          await page.waitForTimeout(250); // sidebar transition
          const state = await page.evaluate(
            ([b, r]) => {
              const el = document.querySelector(r),
                box = el.getBoundingClientRect();
              return {
                expanded: document
                  .querySelector(b)
                  .getAttribute("aria-expanded"),
                shown:
                  !el.hidden &&
                  getComputedStyle(el).display !== "none" &&
                  box.width > 40 &&
                  box.right > 0 &&
                  box.left < innerWidth,
              };
            },
            [button, region],
          );
          assert.equal(
            state.expanded,
            String(state.shown),
            button + " aria-expanded must match " + region,
          );
        }
      }
    },
  },
  {
    title: "H12-S2 failed delete is announced and the dialog stays open",
    async run(page) {
      const noOtherErrors = allowHttpErrorLogs(page);
      let deletes = 0;
      await open(page, {
        "DELETE /v1/conversations/c1": (route) => {
          deletes++;
          return route.fulfill({ status: 500, json: { detail: "boom" } });
        },
      });
      await page
        .locator("#history .conversation-actions summary")
        .first()
        .click();
      await page
        .locator("#history .conversation-actions[open] button")
        .filter({ hasText: "Delete conversation" })
        .click();
      const dialog = page.locator("#delete-conversation-dialog");
      await dialog.waitFor();
      assert.equal(
        await page
          .locator("#delete-conversation-cancel")
          .evaluate((e) => e === document.activeElement),
        true,
        "safe default focus",
      );
      // Keyboard activation, as a screen-reader user would do it.
      await page.locator("#delete-conversation-confirm").focus();
      await page.keyboard.press("Enter");
      const error = page.locator("#delete-conversation-error");
      await error.filter({ hasText: "Couldn't delete" }).waitFor();
      assert.equal(await error.getAttribute("role"), "alert");
      assert(await dialog.isVisible(), "dialog must stay open after a failure");
      assert.equal(deletes, 1);
      assert.doesNotMatch(await error.innerText(), /boom|undefined|\[object/);
      // KNOWN BUG F-61: the confirm button is disabled while the DELETE runs, so
      // Chromium's focus fixup moves focus to <body> and it is never restored.
      // Correct: focus stays on (or returns to) a control inside the dialog.
      const where = await page.evaluate(() =>
        document.activeElement.closest("dialog")
          ? "dialog"
          : document.activeElement.tagName,
      );
      assert.equal(
        where,
        "BODY",
        "F-61 fixed? focus is now in " + where + " - flip this assertion",
      );
      await page.keyboard.press("Escape");
      await dialog.waitFor({ state: "hidden" });
      assert.equal(deletes, 1);
      noOtherErrors();
    },
  },
]);
