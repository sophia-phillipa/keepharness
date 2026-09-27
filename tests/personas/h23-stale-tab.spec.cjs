// H23 stale tab (P2 rushed / P3 domain professional): acts on a conversation and a
// project that another tab or the admin already changed on the server.
const assert = require("node:assert/strict");
const { mockHarness, runPersona } = require("./_harness.cjs");

const CODEX = {
  id: "gpt-5.6-luna",
  backend: "codex",
  efforts: ["low"],
  permissions: { upload: false },
  execution_modes: ["native", "scoped"],
};
const catalog = { models: [CODEX], providers: { codex: true } };

// Mocked 4xx responses are the scenario itself; Chromium logs each one as
// "Failed to load resource", so only other console errors fail the persona.
function strictConsole(page) {
  page.removeAllListeners("console");
  const errors = [];
  page.on("console", (m) => {
    if (m.type() === "error" && !/^Failed to load resource/.test(m.text()))
      errors.push(m.text());
  });
  return () => assert.deepEqual(errors, []);
}
const statusText = (page) => page.locator("#status").textContent();

runPersona("H23", [
  {
    title: "H23-S1 send on a conversation deleted or advanced in another tab",
    timeout: 8000,
    async run(page) {
      const noConsoleErrors = strictConsole(page);
      const dialogs = [];
      page.on("dialog", (d) => {
        dialogs.push(d.message());
        void d.accept();
      });
      let deleted = false;
      const turn = {
        id: "c1",
        project: "sem-projeto",
        state: "completed",
        request: {
          prompt: "Draft the budget",
          backend: "codex",
          model: CODEX.id,
        },
        result: { answer: "Here is the budget." },
      };
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
                execution: { backend: "codex", model: CODEX.id },
              },
            ],
          },
        },
        "GET /v1/conversations/c1": (route) =>
          deleted
            ? route.fulfill({
                status: 404,
                json: { code: "conversation_not_found" },
              })
            : route.fulfill({
                json: { title: "Budget review", turns: [turn] },
              }),
        "POST /v1/jobs": (route) => {
          s.posts.push(route.request().postDataJSON());
          return route.fulfill({
            status: 409,
            json: { code: "conversation_has_newer_turn" },
          });
        },
      });
      await page.goto("http://harness.test");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
      await page.getByRole("button", { name: "Budget review" }).click();
      await page.getByText("Here is the budget.").waitFor();

      // Another tab already sent a newer turn: this tab's parent is stale.
      await page.fill("#prompt", "Add the Q3 numbers");
      await page.click("#send");
      await page.waitForFunction(() =>
        /^Couldn't run/.test(document.querySelector("#status").textContent),
      );
      assert.equal(s.posts.length, 1);
      assert.equal(s.posts[0].parent_job_id, "c1");
      assert.equal(await page.inputValue("#prompt"), "Add the Q3 numbers");
      const conflict = await statusText(page);
      console.log("H23-S1 409 status:", conflict);
      // KNOWN BUG F-80: the 409 surfaces the raw protocol code with a generic
      // "check the data" hint; nothing tells the user another tab moved the
      // conversation on or how to refresh it (userErrors has no entry).
      assert.match(conflict, /\(conversation_has_newer_turn\)/);
      assert.doesNotMatch(
        conflict,
        /another tab|reload|refresh|newer message/i,
      );

      // The other tab then deletes the conversation; reopening it here fails.
      deleted = true;
      await page.getByRole("button", { name: "Budget review" }).click();
      await page.waitForFunction(() =>
        /Couldn't open the conversation/.test(
          document.querySelector("#status").textContent,
        ),
      );
      assert.equal(await page.inputValue("#prompt"), "Add the Q3 numbers");
      const missing = await statusText(page);
      console.log("H23-S1 404 status:", missing);
      assert.match(missing, /Your draft was preserved/);
      // KNOWN BUG F-80: raw "conversation_not_found" code, and the stale row
      // stays in the list so the user can keep clicking a deleted item.
      assert.match(missing, /\(conversation_not_found\)/);
      assert.equal(
        await page.getByRole("button", { name: "Budget review" }).count(),
        1,
      );

      // The only way out is "New conversation", which asks to discard the draft
      // that the error message just promised was preserved.
      await page.click("#new");
      // KNOWN BUG F-80: recovery requires discarding the preserved draft.
      assert.deepEqual(dialogs, [
        "Discard the draft and attachments to start a new conversation?",
      ]);
      assert.equal(await page.inputValue("#prompt"), "");
      noConsoleErrors();
    },
  },
  {
    title: "H23-S2 pick a project the admin just removed",
    timeout: 12000,
    async run(page) {
      const noConsoleErrors = strictConsole(page);
      let removed = false;
      const s = await mockHarness(page, {
        "GET /v1/projects": (route) =>
          route.fulfill({
            json: removed
              ? { projects: ["sem-projeto"], details: {} }
              : {
                  projects: ["sem-projeto", "demo"],
                  details: { demo: { label: "Demo" } },
                },
          }),
        "GET /v1/models": (route) => {
          const project = new URL(route.request().url()).searchParams.get(
            "project_id",
          );
          if (removed && project === "demo")
            return route.fulfill({
              status: 403,
              json: { code: "project_denied" },
            });
          return route.fulfill({ json: catalog });
        },
      });
      await page.goto("http://harness.test");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });

      // The admin removes "demo" while this tab still lists it.
      removed = true;
      await page.getByRole("button", { name: "Demo", exact: true }).click();
      await page
        .getByRole("button", { name: "New Conversation in Demo" })
        .click();
      await page.waitForFunction(() =>
        /project's permissions/.test(
          document.querySelector("#status").textContent,
        ),
      );
      // "access" wording although the project no longer exists; send stays
      // disabled while the permission check is pending.
      assert.match(await statusText(page), /don't have access to this project/);
      await page.fill("#prompt", "Plan the demo launch");
      assert(await page.locator("#send").isDisabled());

      // Next background readiness probe (5 s timer, triggered directly here).
      await page.evaluate(() => {
        readinessRetryAt = 0;
        return probeReadiness();
      });
      // KNOWN BUG F-81: a removed project is reported as a lost server
      // connection and blocks the whole UI behind the startup gate.
      assert(await page.locator("#startup-gate").isVisible());
      assert.match(
        await page.locator("#startup-gate").textContent(),
        /Waiting for the server/,
      );
      assert.match(
        await statusText(page),
        /Connection to the server lost: Project unavailable/,
      );

      // Automatic re-initialisation (5 s interval) recovers silently.
      await page
        .locator("#startup-gate")
        .waitFor({ state: "hidden", timeout: 8000 });
      assert.equal(await page.inputValue("#prompt"), "Plan the demo launch");
      assert.equal(await page.inputValue("#project"), "sem-projeto");
      // KNOWN BUG F-81: the "Project unavailable" explanation is overwritten by
      // "Ready to chat." and the draft is now silently scoped to "No project".
      assert.equal(await statusText(page), "Ready to chat.");
      assert.equal(await page.getByText("Project unavailable").count(), 0);
      assert.equal(
        await page.getByRole("button", { name: "Demo", exact: true }).count(),
        0,
      );
      await page.click("#send");
      await page.getByText("Fixture response.").waitFor();
      assert.equal(s.posts.length, 1);
      assert.equal(s.posts[0].project_id, "sem-projeto");
      noConsoleErrors();
    },
  },
]);
