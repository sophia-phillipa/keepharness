// H22 double-clicker (P2): double clicks on send, create project and delete
// confirm must produce exactly one request, one key and one dialog close.
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
  "GET /v1/conversations": {
    json: {
      conversations: [
        {
          id: "c1",
          title: "Old chat",
          project: "sem-projeto",
          state: "completed",
          execution: { backend: "codex", model: "fx-codex" },
        },
      ],
    },
  },
};
const delay = (ms) => new Promise((r) => setTimeout(r, ms));

async function open(page, over = {}) {
  const s = await mockHarness(page, { ...MX, ...over });
  await page.goto("http://harness.test");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  return s;
}

// Counts native dialog closes, so "closes once" is observable.
const countCloses = (page, id) =>
  page.evaluate((id) => {
    window.__closes = 0;
    document
      .getElementById(id)
      .addEventListener("close", () => window.__closes++);
  }, id);

runPersona("H22", [
  {
    title: "H22-S1 double-click send gives one POST and one Idempotency-Key",
    async run(page) {
      const s = await open(page);
      // Registered after mockHarness, so it runs first: hold POST /v1/jobs 500 ms,
      // then fall back to the helper, which records the body and key.
      await page.route("http://harness.test/v1/jobs", async (route) => {
        if (route.request().method() === "POST") await delay(500);
        return route.fallback();
      });
      await page.fill("#prompt", "Book the meeting room");
      await page.dblclick("#send");
      // Twist: an impatient Enter while the first request is still in flight.
      await page
        .locator("#prompt")
        .press("Enter")
        .catch(() => {});
      await page.waitForFunction(
        () => document.querySelectorAll("#messages .message.user").length >= 1,
      );
      await page.locator("#messages").getByText("Fixture response.").waitFor();
      await delay(300);
      assert.equal(s.posts.length, 1, "exactly one POST /v1/jobs");
      assert.equal(new Set(s.keys).size, 1);
      assert.match(
        s.keys[0],
        /^[0-9a-f-]{16,}$/i,
        "an Idempotency-Key is sent",
      );
      assert.equal(
        await page.locator("#messages .message.user").count(),
        1,
        "one user bubble",
      );
      assert.equal(await page.inputValue("#prompt"), "");
    },
  },
  {
    title: "H22-S2 double-click create project and delete confirm act once",
    async run(page) {
      let created = null;
      const projectPosts = [],
        deletes = [];
      const s = await open(page, {
        "GET /v1/projects": (route) =>
          route.fulfill({
            json: created
              ? {
                  projects: ["sem-projeto", "demo", "new"],
                  details: {
                    demo: { label: "Demo" },
                    new: { label: created.name },
                  },
                }
              : {
                  projects: ["sem-projeto", "demo"],
                  details: { demo: { label: "Demo" } },
                },
          }),
        "POST /v1/projects": async (route) => {
          projectPosts.push(route.request().postDataJSON());
          await delay(500);
          created = projectPosts[0];
          return route.fulfill({ json: { project_id: "new" } });
        },
        "GET /v1/project-directories": {
          json: {
            roots: [{ id: "home", label: "Local folders" }],
            root_id: "home",
            path: "",
            absolute_path: "/home/test-user",
            entries: [
              {
                name: "Work A",
                path: "Work A",
                absolute_path: "/home/test-user/Work A",
                type: "directory",
              },
            ],
            limited: false,
          },
        },
        "DELETE /v1/conversations/c1": async (route) => {
          deletes.push(route.request().url());
          await delay(500);
          return route.fulfill({ json: {} });
        },
      });
      void s;
      if (!(await page.locator("#project-tree").evaluate((el) => el.open)))
        await page.locator("#project-tree > summary").click();
      await page.click("#add-project");
      await page.locator("#project-dialog").waitFor();
      await page.fill("#project-name", "Quarterly reports");
      await page
        .locator("#project-directory-list .project-file-row")
        .filter({ hasText: "Work A" })
        .click();
      await page.click("#project-directory-add-current");
      await countCloses(page, "project-dialog");
      await page.dblclick("#project-create");
      await page.locator("#project-dialog").waitFor({ state: "hidden" });
      await delay(300);
      assert.equal(projectPosts.length, 1, "exactly one POST /v1/projects");
      assert.deepEqual(projectPosts[0], {
        name: "Quarterly reports",
        paths: ["/home/test-user/Work A"],
      });
      assert.equal(
        await page.evaluate(() => window.__closes),
        1,
        "project dialog closes once",
      );

      await page
        .locator("#sidebar .conversation-actions summary")
        .first()
        .click();
      await page
        .locator("#sidebar .conversation-actions[open] button")
        .filter({ hasText: "Delete permanently" })
        .click();
      await page.locator("#delete-conversation-dialog").waitFor();
      await countCloses(page, "delete-conversation-dialog");
      await page.dblclick("#delete-conversation-confirm");
      await page
        .locator("#delete-conversation-dialog")
        .waitFor({ state: "hidden" });
      await delay(300);
      assert.equal(deletes.length, 1, "exactly one DELETE");
      assert.equal(
        await page.evaluate(() => window.__closes),
        1,
        "delete dialog closes once",
      );
    },
  },
  {
    title: "H22-S3 (extra twist) double-click cancel sends one cancel",
    async run(page) {
      let release;
      const gate = new Promise((r) => (release = r));
      const s = await open(page, {
        "GET /v1/jobs/job-1/events": async (route) => {
          await gate; // keep the run in flight until the user cancels
          await route.fulfill({ contentType: "text/event-stream", body: "" });
        },
        "GET /v1/jobs/job-1": (route) =>
          route.fulfill({
            json: {
              id: "job-1",
              project: "sem-projeto",
              state: s.cancels ? "cancelled" : "running",
              request: s.posts[0],
              result: {},
            },
          }),
      });
      await page.fill("#prompt", "Write a long essay");
      await page.locator("#prompt").press("Enter");
      await page.locator("#cancel").waitFor();
      await page.dblclick("#cancel");
      await delay(300);
      release();
      await page.locator("#send").waitFor();
      assert.equal(s.cancels, 1, "exactly one POST /cancel");
      assert.equal(s.posts.length, 1);
    },
  },
]);
