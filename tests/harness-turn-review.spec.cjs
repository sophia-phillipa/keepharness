// Regression: a completed turn shows the files it changed inline under its own answer
// (#57 WP3, D-053). Read-only; opening the review never switches Chat/Code, never opens the
// right panel and never touches the draft or submits a turn (D-037).
const assert = require("node:assert/strict");
const { mockHarness, runPersona } = require("./personas/_harness.cjs");

const PROVIDERS = [
  { name: "Codex", backend: "codex", id: "gpt-fixture-codex" },
  { name: "Claude", backend: "claude", id: "claude-sonnet-5" },
  { name: "DeepSeek", backend: "deepseek", id: "deepseek-fixture-chat" },
];

const LONG_PATH = "deep/" + "segment-".repeat(22) + "end.ts";

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
      edits: [
        {
          op: "created",
          diff_state: "diff",
          diff: "+hello\n",
          tool: "fileChange",
          source: "codex",
        },
      ],
    },
    {
      path: "assets/logo.png",
      op: "created",
      edits: [
        {
          op: "created",
          diff_state: "binary",
          diff: null,
          tool: "fileChange",
          source: "codex",
        },
      ],
    },
    {
      path: "data/big.json",
      op: "modified",
      edits: [
        {
          op: "modified",
          diff_state: "oversized",
          diff: null,
          tool: "fileChange",
          source: "codex",
        },
      ],
    },
    {
      path: "lib/gone.py",
      op: "deleted",
      edits: [
        {
          op: "deleted",
          diff_state: "unavailable",
          diff: null,
          tool: "fileChange",
          source: "codex",
        },
      ],
    },
    {
      path: "src/renamed.py",
      op: "modified",
      edits: [
        {
          op: "modified",
          diff_state: "partial",
          diff: "+fragment\n",
          moved_from: "src/old.py",
          tool: "fileChange",
          source: "codex",
        },
      ],
    },
  ],
};

const NONE = {
  job_state: "completed",
  state: "none",
  truncated: false,
  shell_unattributed: false,
  files: [],
};

function allowedErrors(page) {
  const errors = [];
  page.on("console", (m) => {
    if (m.type() === "error" && !/^Failed to load resource/.test(m.text()))
      errors.push(m.text());
  });
  return errors;
}

// Sends one prompt in the current conversation and waits for its answer to finish.
async function send(page, text, count) {
  await page.fill("#prompt", text);
  await page.click("#send");
  await page
    .locator("#messages .message.assistant")
    .nth(count - 1)
    .locator(".run-meta")
    .waitFor();
}

async function mockTurns(page, provider, changes) {
  const model = {
    id: provider.id,
    backend: provider.backend,
    efforts: ["low"],
    permissions: { upload: false },
    execution_modes: ["native"],
  };
  return mockHarness(page, {
    "GET /v1/models": {
      json: {
        models: [model],
        providers: { codex: true, claude: true, deepseek: true },
        uploads_enabled: false,
      },
    },
    "GET /v1/jobs/job-1/file-changes": { json: changes[0] },
    "GET /v1/jobs/job-2/file-changes": { json: changes[1] || NONE },
  });
}

async function openHarness(page) {
  await page.goto("http://harness.test");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
}

runPersona("harness-turn-review", [
  ...PROVIDERS.flatMap((provider) => [
    {
      title: `${provider.name}: summary, per-file list, diff and state labels under its own answer`,
      async run(page) {
        const errors = allowedErrors(page);
        const s = await mockTurns(page, provider, [EDITING]);
        await openHarness(page);
        await send(page, "FAKE-EDITS change the files", 1);

        const toggle = page.locator('[data-testid="turn-review-toggle"]');
        await toggle.waitFor();
        assert.equal(await toggle.textContent(), "6 files changed");
        assert.equal(await toggle.getAttribute("aria-expanded"), "false");
        assert.equal(
          await page.locator('[data-testid="turn-review-list"]').isVisible(),
          false,
        );

        await toggle.click();
        assert.equal(await toggle.getAttribute("aria-expanded"), "true");
        const rows = page.locator('[data-testid="turn-review-file"]');
        assert.equal(await rows.count(), 6);
        const renamed = page.locator('[data-testid="turn-review-file"]').nth(5);
        assert.match(await renamed.textContent(), /renamed from src\/old\.py/);
        assert.equal(await renamed.getAttribute("title"), "src/renamed.py");

        await rows.nth(0).click();
        const diff = page.locator('[data-testid="turn-review-diff"]').nth(0);
        assert.match(await diff.locator("pre").textContent(), /\+new line/);
        assert.equal(await rows.nth(0).getAttribute("aria-expanded"), "true");

        const texts = [];
        for (const i of [2, 3, 4, 5]) {
          await rows.nth(i).click();
          texts.push(
            await page
              .locator('[data-testid="turn-review-diff"]')
              .nth(i)
              .textContent(),
          );
        }
        assert.match(texts[0], /Binary file: no text diff/);
        assert.match(texts[1], /Diff too large to show/);
        assert.match(texts[2], /Diff not available/);
        assert.match(texts[3], /Partial diff: fragments only/);

        assert.equal(s.posts.length, 1, "opening the review submits no turn");
        assert.deepEqual(errors, []);
      },
    },
    {
      title: `${provider.name}: no-edit turn after an editing turn shows no list`,
      async run(page) {
        await mockTurns(page, provider, [EDITING, NONE]);
        await openHarness(page);
        await send(page, "FAKE-EDITS change the files", 1);
        await page.locator('[data-testid="turn-review-toggle"]').waitFor();
        await send(page, "Only answer, no edits", 2);
        await page.waitForTimeout(300);
        const second = page.locator("#messages .message.assistant").nth(1);
        assert.equal(
          await second.locator('[data-testid="turn-review"]').isVisible(),
          false,
        );
        assert.equal(
          await second.locator('[data-testid="turn-review-toggle"]').count(),
          0,
        );
        assert.equal(
          await page.locator('[data-testid="turn-review-toggle"]').count(),
          1,
        );
      },
    },
    {
      title: `${provider.name}: reloaded conversation asks file-changes only for turns with edits`,
      async run(page) {
        const s = await mockTurns(page, provider, [EDITING, NONE]);
        await openHarness(page);
        await send(page, "FAKE-EDITS change the files", 1);
        await send(page, "Only answer, no edits", 2);
        s.turns[0].has_turn_edits = true;
        s.turns[1].has_turn_edits = false;
        const requested = [];
        page.on("request", (request) => {
          const { pathname } = new URL(request.url());
          if (pathname.endsWith("/file-changes")) requested.push(pathname);
        });
        await page.reload();
        await page.locator('[data-testid="turn-review-toggle"]').waitFor();
        await page.waitForTimeout(300);
        assert.deepEqual(requested, ["/v1/jobs/job-1/file-changes"]);
      },
    },
  ]),
  {
    title:
      "keyboard opens and closes the summary and a file with visible focus",
    async run(page) {
      await mockTurns(page, PROVIDERS[0], [EDITING]);
      await openHarness(page);
      await send(page, "FAKE-EDITS change the files", 1);
      const toggle = page.locator('[data-testid="turn-review-toggle"]');
      await toggle.waitFor();
      await toggle.focus();
      await page.keyboard.press("Enter");
      assert.equal(await toggle.getAttribute("aria-expanded"), "true");
      const file = page.locator('[data-testid="turn-review-file"]').nth(0);
      await file.focus();
      await page.keyboard.press("Space");
      assert.equal(await file.getAttribute("aria-expanded"), "true");
      assert.notEqual(
        await file.evaluate((el) => getComputedStyle(el).outlineStyle),
        "none",
      );
      await page.keyboard.press("Enter");
      assert.equal(await file.getAttribute("aria-expanded"), "false");
      await toggle.focus();
      await page.keyboard.press("Enter");
      assert.equal(await toggle.getAttribute("aria-expanded"), "false");
    },
  },
  {
    title:
      "shell and truncation notices and the stopped label; long path keeps its full title",
    async run(page) {
      const changes = {
        ...EDITING,
        job_state: "cancelled",
        shell_unattributed: true,
        truncated: true,
        files: [
          {
            path: LONG_PATH,
            op: "created",
            edits: [
              {
                op: "created",
                diff_state: "diff",
                diff: "+x\n",
                tool: "fileChange",
                source: "codex",
              },
            ],
          },
        ],
      };
      await mockTurns(page, PROVIDERS[0], [changes]);
      await openHarness(page);
      await send(page, "FAKE-EDITS change the files", 1);
      await page.locator('[data-testid="turn-review-toggle"]').waitFor();
      assert.equal(
        await page.locator('[data-testid="turn-review-shell"]').textContent(),
        "Shell commands may have changed other files.",
      );
      assert.match(
        await page.locator('[data-testid="turn-review-stopped"]').textContent(),
        /Stopped before finishing/,
      );
      assert.match(
        await page
          .locator('[data-testid="turn-review-truncated"]')
          .textContent(),
        /incomplete/,
      );
      await page.locator('[data-testid="turn-review-toggle"]').click();
      assert.equal(
        await page
          .locator('[data-testid="turn-review-file"]')
          .getAttribute("title"),
        LONG_PATH,
      );
    },
  },
  {
    title:
      "opening the review keeps Chat/Code, the panel and the draft unchanged",
    async run(page) {
      const s = await mockTurns(page, PROVIDERS[0], [EDITING]);
      await openHarness(page);
      await send(page, "FAKE-EDITS change the files", 1);
      await page.fill("#prompt", "a draft kept as it is");
      const before = await page
        .locator("#panel-toggle")
        .getAttribute("aria-expanded");
      await page.locator('[data-testid="turn-review-toggle"]').click();
      await page.locator('[data-testid="turn-review-file"]').nth(0).click();
      assert.equal(
        await page.locator("#prompt").inputValue(),
        "a draft kept as it is",
      );
      assert.equal(
        await page.locator("#panel-toggle").getAttribute("aria-expanded"),
        before,
      );
      assert.equal(s.posts.length, 1);
    },
  },
]);
