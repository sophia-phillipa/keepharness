// H25 queue stresser (P6 engineer / P3 professional): three tabs hit the three
// server queue limits at once, then one tab stacks follow-ups on a running turn.
const assert = require("node:assert/strict");
const { mockHarness, runPersona, sse } = require("./_harness.cjs");

const CLAUDE = {
  id: "claude-sonnet-4-6",
  backend: "claude",
  efforts: ["low"],
  permissions: { upload: false },
  execution_modes: ["native"],
};
const catalog = { models: [CLAUDE], providers: { claude: true } };

function strictConsole(page) {
  page.removeAllListeners("console");
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error" && !/^Failed to load resource/.test(m.text()))
      errors.push(m.text());
  });
  return () => assert.deepEqual(errors, []);
}
const statusText = (page) => page.locator("#status").textContent();

// Server-side limits and their Retry-After, as agent_service raises them.
const LIMITS = [
  ["queue_full", "5", /The server queue is full\. .*Try again in 5 seconds\./],
  [
    "owner_queue_full",
    "5",
    /You reached the queue limit for requests\. .*Try again in 5 seconds\./,
  ],
  [
    "submission_rate_limit",
    "60",
    /You sent new requests too quickly\. .*Try again in 60 seconds\./,
  ],
];

runPersona("H25", [
  {
    title: "H25-S1 three tabs hit queue_full, owner_queue_full, rate limit",
    timeout: 15000,
    async run(first) {
      const browser = first.context().browser();
      const extra = [await browser.newContext(), await browser.newContext()];
      const pages = [first, await extra[0].newPage(), await extra[1].newPage()];
      try {
        const checks = [],
          posts = [0, 0, 0];
        for (const [i, page] of pages.entries()) {
          page.setDefaultTimeout(5000);
          checks.push(strictConsole(page));
          const [code, after] = LIMITS[i];
          await mockHarness(page, {
            "GET /v1/models": { json: catalog },
            "POST /v1/jobs": (route) => {
              posts[i]++;
              return route.fulfill({
                status: 429,
                headers: { "Retry-After": after },
                json: { code },
              });
            },
          });
          await page.goto("http://harness.test");
          await page.locator("#startup-gate").waitFor({ state: "hidden" });
          await page.fill("#prompt", "Tab " + (i + 1) + " asks for a report");
        }
        await Promise.all(pages.map((p) => p.click("#send")));
        for (const page of pages)
          await page.waitForFunction(() =>
            /^Couldn't run/.test(document.querySelector("#status").textContent),
          );
        // Give any automatic retry a chance to show up.
        await first.waitForTimeout(6000);
        const messages = [];
        for (const [i, page] of pages.entries()) {
          const text = await statusText(page);
          messages.push(text);
          console.log("H25-S1 " + LIMITS[i][0] + ":", text);
          assert.match(text, LIMITS[i][2]);
          assert.match(text, /Your draft was preserved\.$/);
          assert.doesNotMatch(text, /queue_full|rate_limit/);
          assert.equal(
            await page.inputValue("#prompt"),
            "Tab " + (i + 1) + " asks for a report",
          );
          assert(await page.locator("#send").isEnabled());
          checks[i]();
        }
        assert.equal(new Set(messages).size, 3, "distinct messages");
        assert.deepEqual(posts, [1, 1, 1], "no automatic retry storm");
      } finally {
        await Promise.all(extra.map((c) => c.close()));
      }
    },
  },
  {
    title: "H25-S2 two follow-ups queued behind a running answer",
    timeout: 10000,
    async run(page) {
      const noConsoleErrors = strictConsole(page);
      let release;
      const released = new Promise((r) => (release = r));
      let firstDone = false;
      const s = await mockHarness(page, {
        "GET /v1/models": { json: catalog },
        "POST /v1/jobs": (route) => {
          s.posts.push(route.request().postDataJSON());
          const id = "job-" + s.posts.length;
          s.turns.push({
            id,
            project: "sem-projeto",
            state: id === "job-1" ? "running" : "queued",
            request: s.posts.at(-1),
          });
          return route.fulfill({ json: { job_id: id } });
        },
        "/^GET \\/v1\\/jobs\\/job-\\d+\\/events$/": async (route) => {
          const id = new URL(route.request().url()).pathname.split("/")[3];
          if (id === "job-1") {
            await released;
            firstDone = true;
          }
          return route.fulfill({
            body: sse([
              { id: 1, type: "answer_delta", data: { text: "Answer " + id } },
            ]),
            contentType: "text/event-stream",
          });
        },
        "/^GET \\/v1\\/jobs\\/job-\\d+$/": (route) => {
          const id = new URL(route.request().url()).pathname.split("/")[3];
          const done = id !== "job-1" || firstDone;
          return route.fulfill({
            json: {
              ...s.turns.find((t) => t.id === id),
              state: done ? "completed" : "running",
              result: done ? { answer: "Answer " + id } : undefined,
            },
          });
        },
      });
      await page.goto("http://harness.test");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
      await page.fill("#prompt", "Draft the long report");
      await page.click("#send");
      await page.locator("#cancel").waitFor();

      for (const prompt of ["Add a summary", "And a title"]) {
        await page.fill("#prompt", prompt);
        await page.click("#send");
        await page.waitForFunction(
          () => document.querySelector("#prompt").value === "",
        );
        assert.equal(
          await statusText(page),
          "Message sent. Waiting for the current response to finish.",
        );
      }
      const chips = page.locator("#messages article.assistant .run-highlight");
      assert.equal(await chips.count(), 3);
      // "Queued" is worded as "Waiting to run" on the pending turns.
      assert.match(await chips.nth(1).textContent(), /Waiting to run/);
      assert.match(await chips.nth(2).textContent(), /Waiting to run/);
      assert.equal(s.posts[1].parent_job_id, "job-1");
      assert.equal(s.posts[2].parent_job_id, "job-2");

      release();
      await page.getByText("Answer job-3").waitFor();
      const answers = await page
        .locator("#messages article.assistant .chat-bubble")
        .allInnerTexts();
      assert.deepEqual(
        answers.map((t) => t.trim()),
        ["Answer job-1", "Answer job-2", "Answer job-3"],
      );
      assert.equal(s.posts.length, 3);
      noConsoleErrors();
    },
  },
]);
