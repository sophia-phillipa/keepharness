// Turn file review (#57 WP3, #79): the summary under a completed turn, in fixture mode. The fake
// provider writes no files, so each editing turn answers the file-changes route the review reads
// (the same interception harness-turn-review.spec.cjs uses). Every step that renders a review
// sends a turn, so it is a write and skips in real mode.
"use strict";
const { home, newChat, send, waitAnswer } = require("../lib/app.cjs");

// Exactly 200 characters: the long-path case of dossier/turn-file-review.md (#79).
const PATH_200 = "deep/" + "segment-".repeat(24) + ".ts";
const NONE = {
  job_state: "completed",
  state: "captured",
  truncated: false,
  shell_unattributed: false,
  files: [],
};
const EDITING = {
  job_state: "completed",
  state: "captured",
  truncated: false,
  shell_unattributed: false,
  files: [
    {
      path: "src/app.py",
      op: "modified",
      edits: [
        {
          op: "modified",
          diff_state: "diff",
          diff: "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-old line\n+new line\n",
          tool: "fileChange",
          source: "codex",
        },
      ],
    },
    {
      path: "docs/new.md",
      op: "created",
      edits: [{ op: "created", diff_state: "diff", diff: "+hello\n", tool: "fileChange", source: "codex" }],
    },
  ],
};
const LONG = {
  ...EDITING,
  files: [
    {
      path: PATH_200,
      op: "created",
      edits: [{ op: "created", diff_state: "diff", diff: "+x\n", tool: "fileChange", source: "codex" }],
    },
  ],
};

// Per area run: the responses for the turns, in the order their jobs first ask for file-changes.
let plan = { responses: [], jobs: [], served: 0 };

// Runs in the page: WCAG contrast of an element's text against the background it is painted on.
// Translucent layers (e.g. the hover tint, color-mix with transparent) are composited over the
// layer below them, up to the first opaque one; the text colour is taken as opaque.
function measureContrast(el) {
  const parse = (css) => {
    const n = css.match(/[\d.]+/g).map(Number);
    if (css.startsWith("color(")) return [n[0] * 255, n[1] * 255, n[2] * 255, n[3] ?? 1];
    return [n[0], n[1], n[2], n[3] ?? 1];
  };
  const lum = ([r, g, b]) => {
    const [x, y, z] = [r, g, b].map((c) => {
      const v = c / 255;
      return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * x + 0.7152 * y + 0.0722 * z;
  };
  const layers = [];
  for (let node = el; node; node = node.parentElement) {
    const c = parse(getComputedStyle(node).backgroundColor);
    if (c[3] > 0) layers.push(c);
    if (c[3] >= 1) break;
  }
  let bg = [255, 255, 255];
  for (const [r, g, b, a] of layers.reverse()) {
    bg = [0, 1, 2].map((i) => [r, g, b][i] * a + bg[i] * (1 - a));
  }
  const fg = parse(getComputedStyle(el).color);
  const [hi, lo] = [lum(fg), lum(bg)].sort((p, q) => q - p);
  return (hi + 0.05) / (lo + 0.05);
}

module.exports = {
  id: "turn-review",
  title: "Turn file review (fixture edits, keyboard, long path, palettes)",
  async run(op) {
    const page = op.page;
    const assistant = (n) => page.locator("#messages .message.assistant").nth(n);
    const toggle = () => page.locator('[data-testid="turn-review-toggle"]');
    const palette = () => page.evaluate(() => document.documentElement.dataset.palette);

    // A new conversation whose turns answer file-changes with `responses`, in order, after
    // sending one prompt per entry in `prompts`. Returns once every answer has finished.
    async function turns(responses, prompts = ["Edit the files"]) {
      plan = { responses, jobs: [], served: 0 };
      await newChat(op);
      for (const text of prompts) {
        const before = await send(op, text);
        await waitAnswer(op, before);
      }
      await op.until(async () => plan.served >= responses.length, "file-changes was not requested");
      // The review renders after the response; let the page paint it before reading it.
      await page.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))));
    }

    const openAppearance = async () => {
      await op.click(page.locator("#settings"));
      await op.click(page.locator("#settings-menu").getByRole("menuitem", { name: "Appearance", exact: true }));
      await op.see(page.locator("#settings-dialog"));
    };
    // The Appearance toggle switches between Paper (light) and Graphite (dark).
    const toggleTheme = async (wanted) => {
      await openAppearance();
      await op.click(page.locator("#theme-toggle"));
      await op.click(page.locator("#settings-close"));
      await op.until(async () => (await palette()) === wanted, `the toggle did not switch to ${wanted}`);
    };

    // Fixture only: the file-changes route answers from the plan; the page never reaches the
    // real (empty) answer for a turn this area made.
    if (op.fixtureMode) {
      await page.route("**/v1/jobs/*/file-changes", (route) => {
        const job = decodeURIComponent(new URL(route.request().url()).pathname.split("/")[3]);
        let index = plan.jobs.indexOf(job);
        if (index < 0) {
          plan.jobs.push(job);
          index = plan.jobs.length - 1;
        }
        plan.served += 1;
        return route.fulfill({ json: plan.responses[index] || NONE });
      });
    }

    try {
      await op.step(
        "home",
        "Start on the start screen",
        async () => {
          await home(op);
        },
        { critical: true },
      );

      await op.step(
        "light-review",
        "Light palette: the review is rendered and its text has at least 4.5:1 contrast",
        async () => {
          if ((await palette()) !== "paper") await page.evaluate(() => window.HarnessTheme?.apply("paper"));
          op.check((await palette()) === "paper", "not in Paper");
          await turns([EDITING]);
          await op.click(toggle());
          // Measure the rest state: the pointer leaves the toggle so its hover tint is not read.
          await page.mouse.move(0, 0);
          const ratios = await Promise.all([
            toggle().evaluate(measureContrast),
            page.locator('[data-testid="turn-review-file"] .turn-review-path').first().evaluate(measureContrast),
          ]);
          op.check(
            Math.min(...ratios) >= 4.5,
            `light review text is ${ratios.map((r) => r.toFixed(2)).join(" / ")}:1`,
          );
        },
        { writes: true },
      );

      await op.step(
        "dark-review",
        "Dark palette: a new review is rendered and its text has at least 4.5:1 contrast",
        async () => {
          await toggleTheme("graphite");
          op.check(
            (await page.evaluate(() => document.documentElement.dataset.theme)) === "dark",
            "data-theme is not dark",
          );
          await turns([EDITING]);
          await op.click(toggle());
          // Measure the rest state: the pointer leaves the toggle so its hover tint is not read.
          await page.mouse.move(0, 0);
          const ratios = await Promise.all([
            toggle().evaluate(measureContrast),
            page.locator('[data-testid="turn-review-file"] .turn-review-path').first().evaluate(measureContrast),
          ]);
          op.check(
            Math.min(...ratios) >= 4.5,
            `dark review text is ${ratios.map((r) => r.toFixed(2)).join(" / ")}:1`,
          );
        },
        { writes: true },
      );

      await op.step(
        "keyboard",
        "Keyboard: Enter and Space open and close the summary and a file, with a visible focus",
        async () => {
          await turns([EDITING]);
          await toggle().focus();
          await op.press("Enter");
          op.check((await toggle().getAttribute("aria-expanded")) === "true", "Enter did not open the summary");
          const file = page.locator('[data-testid="turn-review-file"]').nth(0);
          await file.focus();
          await op.press("Space");
          op.check((await file.getAttribute("aria-expanded")) === "true", "Space did not open the file");
          const outline = await file.evaluate((el) => ({
            style: getComputedStyle(el).outlineStyle,
            width: parseFloat(getComputedStyle(el).outlineWidth),
          }));
          op.check(outline.style !== "none" && outline.width > 0, "the focused file has no visible outline");
          await op.press("Enter");
          op.check((await file.getAttribute("aria-expanded")) === "false", "Enter did not close the file");
          await toggle().focus();
          await op.press("Enter");
          op.check((await toggle().getAttribute("aria-expanded")) === "false", "Enter did not close the summary");
        },
        { writes: true },
      );

      await op.step(
        "long-path",
        "A 200-character path does not widen or overflow the assistant bubble",
        async () => {
          await turns([LONG]);
          await op.click(toggle());
          const measured = await page.evaluate(() => {
            const answer = document.querySelector("#messages .message.assistant");
            const list = answer.querySelector('[data-testid="turn-review-list"]');
            return {
              answerWidth: answer.getBoundingClientRect().width,
              answerScroll: answer.scrollWidth,
              answerClient: answer.clientWidth,
              listWidth: list.getBoundingClientRect().width,
              chatWidth: document.querySelector("#messages").clientWidth,
            };
          });
          op.check(
            measured.answerScroll <= measured.answerClient,
            `answer overflows: scrollWidth ${measured.answerScroll} > clientWidth ${measured.answerClient}`,
          );
          op.check(
            measured.answerWidth <= measured.chatWidth + 0.5,
            `answer wider than chat: ${measured.answerWidth} > ${measured.chatWidth}`,
          );
          op.check(
            measured.listWidth <= measured.answerWidth + 0.5,
            `list wider than answer: ${measured.listWidth} > ${measured.answerWidth}`,
          );
        },
        { writes: true },
      );

      await op.step(
        "no-edit-turn",
        "A turn after an editing turn that changed no files shows no review list",
        async () => {
          await turns([EDITING, NONE], ["Edit the files", "Answer without edits"]);
          op.check(
            (await assistant(0).locator('[data-testid="turn-review-list"]').count()) === 1,
            "the editing turn shows no review list",
          );
          // The review container exists on every turn but stays hidden when there are no edits.
          op.check(
            (await assistant(1).locator('[data-testid="turn-review-list"]').count()) === 0,
            "the no-edit turn shows a review list",
          );
          op.check(
            !(await assistant(1).locator('[data-testid="turn-review"]').isVisible()),
            "the no-edit turn shows its review",
          );
        },
        { writes: true },
      );
    } finally {
      if (op.fixtureMode) await page.unroute("**/v1/jobs/*/file-changes");
    }
  },
};
