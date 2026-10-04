// Regression: interrupted runs keep what was received, drafts survive every
// conversation flow, and attachments and folder trees stay truthful (WP-C1:
// F-57, F-112, F-56, F-59, F-95/F-111, F-70 fallback).
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { mockHarness, runPersona, sse } = require("./personas/_harness.cjs");

const MODEL = {
  id: "claude-sonnet-5",
  backend: "claude",
  efforts: ["low"],
  permissions: { upload: true },
  execution_modes: ["native"],
};
const catalog = {
  models: [MODEL],
  providers: { claude: true },
  uploads_enabled: true,
};
const PARTIAL = "The first part of the answer";

// Mocked 4xx responses are part of the scenarios.
function allowHttpErrors(page) {
  page.removeAllListeners("console");
  const errors = [];
  page.on("console", (m) => {
    if (m.type() === "error" && !/^Failed to load resource/.test(m.text()))
      errors.push(m.text());
  });
  return () => assert.deepEqual(errors, []);
}

// One turn that streams PARTIAL (plus usage metrics) and settles as `settled`.
async function streamThenSettle(page, settled) {
  const s = await mockHarness(page, {
    "GET /v1/models": { json: catalog },
    "GET /v1/jobs/job-1/events": {
      body: sse([
        { id: 1, type: "answer_delta", data: { text: PARTIAL } },
        {
          id: 2,
          type: "usage_metrics",
          data: { output_tokens: 1, inference_seconds: 1.2 },
        },
      ]),
      headers: { "content-type": "text/event-stream" },
    },
    "GET /v1/jobs/job-1": (route) =>
      route.fulfill({
        json: {
          id: "job-1",
          project: "sem-projeto",
          request: s.posts[0],
          ...settled,
        },
      }),
  });
  await page.goto("http://harness.test");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  await page.fill("#prompt", "Explain the plan");
  await page.click("#send");
  return s;
}

runPersona("harness-runs-and-drafts", [
  {
    title: "F-57 a cancelled run does not show partial token counts",
    async run(page) {
      await streamThenSettle(page, {
        state: "cancelled",
        result: { partial_output: "persisted_events", metrics: null },
      });
      await page.locator("#status", { hasText: "Run cancelled" }).waitFor();
      const meter = await page.locator("#context-meter").innerText();
      assert.match(meter, /cancelled/i);
      assert.doesNotMatch(meter, /1 output|tk\/s/);
      assert.match(
        await page.locator("#messages article.assistant").last().innerText(),
        new RegExp(PARTIAL),
      );
    },
  },
  {
    title:
      "F-112 a provider crash keeps the partial answer and appends a notice",
    async run(page) {
      await streamThenSettle(page, {
        state: "failed",
        result: { error: "claude_stream_incomplete", metrics: null },
      });
      const answer = page.locator("#messages article.assistant").last();
      await answer.locator(".run-notice").waitFor();
      const bubble = await answer.locator(".chat-bubble").innerText();
      assert(bubble.indexOf(PARTIAL) === 0, "partial answer kept first");
      assert.match(bubble, /stopped before the answer was complete/);
      assert.doesNotMatch(bubble, /claude_stream_incomplete/);
      assert.match(
        await answer.locator(".run-notice").getAttribute("title"),
        /claude_stream_incomplete/,
      );
      assert.equal(await page.inputValue("#prompt"), "Explain the plan");
    },
  },
  {
    title:
      "F-114 reload renders a persisted partial_answer above the failure notice",
    async run(page) {
      const conversationTitle = "Long report";
      await mockHarness(page, {
        "GET /v1/models": { json: catalog },
        "GET /v1/conversations": {
          json: {
            conversations: [
              {
                id: "c1",
                title: conversationTitle,
                project: "sem-projeto",
                state: "failed",
              },
            ],
          },
        },
        "GET /v1/conversations/c1": {
          json: {
            title: conversationTitle,
            turns: [
              {
                id: "job-1",
                project: "sem-projeto",
                state: "failed",
                request: {
                  prompt: "Write the report",
                  model: MODEL.id,
                  backend: "claude",
                  access_mode: "ask",
                },
                result: {
                  error: "claude_stream_incomplete",
                  partial_answer: "**" + PARTIAL + "**",
                },
              },
            ],
          },
        },
      });
      await page.goto("http://harness.test");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
      await page
        .locator("#sidebar .conversation-row > button")
        .filter({ hasText: conversationTitle })
        .click();
      const answer = page.locator("#messages article.assistant").last();
      await answer.locator(".run-notice").waitFor();
      // Rendered through the same markdown pipeline as a normal answer.
      assert.equal(
        await answer
          .locator(".chat-bubble strong")
          .filter({ hasText: PARTIAL })
          .count(),
        1,
      );
      const bubbleText = await answer.locator(".chat-bubble").innerText();
      const noticeIndex = bubbleText.indexOf(
        "stopped before the answer was complete",
      );
      const partialIndex = bubbleText.indexOf(PARTIAL);
      assert(partialIndex >= 0, "partial answer text is shown");
      assert(
        partialIndex < noticeIndex,
        "partial answer renders above the failure notice",
      );
    },
  },
  {
    title: "F-70 unknown codes never reach the user raw",
    async run(page) {
      const clean = allowHttpErrors(page);
      await mockHarness(page, {
        "GET /v1/models": { json: catalog },
        "POST /v1/jobs": {
          status: 422,
          json: { code: "brand_new_code", message: "brand_new_code" },
        },
      });
      await page.goto("http://harness.test");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
      await page.fill("#prompt", "Hello");
      await page.click("#send");
      await page.locator("#status", { hasText: "Couldn't run" }).waitFor();
      const text = await page.locator("#status").innerText();
      assert.doesNotMatch(text, /brand_new_code/);
      const run = await page.evaluate(() => executionError("brand_new_code"));
      assert.match(run, /did not finish/);
      assert.doesNotMatch(run, /brand_new_code/);
      clean();
    },
  },
  {
    title: "F-56 attaching the same file twice keeps one and says so",
    async run(page) {
      const s = await mockHarness(page, {
        "GET /v1/models": { json: catalog },
      });
      await page.goto("http://harness.test");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
      // A file on disk, so both picks carry the same modification time.
      const dir = await fs.mkdtemp(path.join(os.tmpdir(), "f56-"));
      const file = path.join(dir, "c02.pdf");
      await fs.writeFile(file, "%PDF-1.7\n%%EOF");
      await page.locator("#attach:not([disabled])").waitFor();
      await page.locator("#file").setInputFiles(file);
      await page.locator("#status", { hasText: "File received." }).waitFor();
      await page.locator("#attach:not([disabled])").waitFor();
      await page.locator("#file").setInputFiles(file);
      await page.locator("#status", { hasText: "already attached" }).waitFor();
      assert.equal(await page.locator("#attachments .attachment").count(), 1);
      assert.equal(s.uploads, 1);
      await fs.rm(dir, { recursive: true, force: true });
    },
  },
  {
    title: "F-59 expanding a folder again lists folders created meanwhile",
    async run(page) {
      const listed = { "Work A": ["Old"] };
      let fetches = 0;
      await mockHarness(page, {
        "GET /v1/models": { json: catalog },
        "GET /v1/project-directories": (route) => {
          const folder = new URL(route.request().url()).searchParams.get(
            "path",
          );
          if (folder === "Work A") fetches++;
          const names = folder ? listed[folder] || [] : ["Work A"];
          return route.fulfill({
            json: {
              roots: [{ id: "home", label: "Local folders" }],
              root_id: "home",
              path: folder || "",
              absolute_path: "/home/test-user",
              entries: names.map((name) => ({
                name,
                path: folder ? folder + "/" + name : name,
                absolute_path: "/home/test-user/" + name,
                type: "directory",
              })),
              limited: false,
            },
          });
        },
      });
      await page.goto("http://harness.test");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
      if (!(await page.locator("#project-tree").evaluate((el) => el.open)))
        await page.locator("#project-tree > summary").click();
      await page.click("#add-project");
      const list = page.locator("#project-directory-list");
      await page
        .getByRole("button", { name: "Expand Work A", exact: true })
        .click();
      await list.getByText("Old", { exact: true }).waitFor();
      listed["Work A"].push("Created outside");
      await page
        .getByRole("button", { name: "Collapse Work A", exact: true })
        .click();
      await page
        .getByRole("button", { name: "Expand Work A", exact: true })
        .click();
      await list.getByText("Created outside", { exact: true }).waitFor();
      assert.equal(fetches, 2);
    },
  },
  {
    title: "F-95/F-111 deleting the open conversation keeps the draft",
    async run(page) {
      page.on("dialog", () => {
        throw Error("Unexpected browser dialog");
      });
      const s = await mockHarness(page, {
        "GET /v1/models": { json: catalog },
        "GET /v1/conversations": {
          json: {
            conversations: [
              {
                id: "c1",
                title: "Budget review",
                project: "sem-projeto",
                state: "completed",
                last_job_id: "c1",
              },
            ],
          },
        },
        "DELETE /v1/conversations/c1": { json: {} },
      });
      s.turns.push({
        id: "c1",
        project: "sem-projeto",
        state: "completed",
        request: {
          prompt: "Draft the budget",
          backend: "claude",
          model: MODEL.id,
        },
        result: { answer: "Here is the budget." },
      });
      await page.goto("http://harness.test");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
      await page.getByRole("button", { name: "Budget review" }).click();
      await page.getByText("Here is the budget.").waitFor();
      await page.fill("#prompt", "A question I have not sent yet");
      await page
        .locator("#sidebar .conversation-actions summary")
        .first()
        .click();
      await page
        .locator("#sidebar .conversation-actions[open] button")
        .filter({ hasText: "Delete permanently" })
        .click();
      await page.click("#delete-conversation-confirm");
      await page
        .locator("#delete-conversation-dialog")
        .waitFor({ state: "hidden" });
      assert.equal(
        await page.inputValue("#prompt"),
        "A question I have not sent yet",
      );
    },
  },
]);
