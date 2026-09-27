// H33 quota exhausted (P2 rushed / P7 UI specialist): hammers Send while the
// server rate-limits, then runs out of ChatGPT (Codex) quota mid-conversation.
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
// Shape of the Codex CLI usage-limit error as the adapter flattens it
// (adapters/codex/native.py: "codex_execution_failed: " + provider message).
const CODEX_QUOTA_ERROR =
  "codex_execution_failed: You've hit your usage limit. Upgrade to Pro " +
  "or try again in 2 hours 13 minutes.";

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

runPersona("H33", [
  {
    title: "H33-S1 clicks Send five times during a 30 s Retry-After window",
    timeout: 10000,
    async run(page) {
      const noConsoleErrors = strictConsole(page);
      let usage = 0;
      const s = await mockHarness(page, {
        "GET /v1/models": { json: catalog },
        "GET /v1/usage": (route) => {
          usage++;
          return route.fulfill({ json: { available: false } });
        },
        "POST /v1/jobs": (route) => {
          s.posts.push(route.request().postDataJSON());
          return route.fulfill({
            status: 429,
            headers: { "Retry-After": "30" },
            json: { code: "rate_limit" },
          });
        },
      });
      await page.goto("http://harness.test");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
      usage = 0;
      await page.fill("#prompt", "Refactor the billing module");
      const started = Date.now();
      // Five clicks: the first one gets the 429, the other four land on the
      // cooling-down button (force skips Playwright's wait for an enabled button).
      await page.click("#send");
      await page.waitForFunction(() =>
        /^Couldn't run/.test(document.querySelector("#status").textContent),
      );
      for (let i = 2; i <= 5; i++) await page.click("#send", { force: true });
      await page.focus("#prompt");
      await page.keyboard.press("Enter");
      await page.waitForTimeout(300);
      const elapsed = Date.now() - started;
      const text = await statusText(page);
      console.log("H33-S1 status:", text, "| elapsed ms:", elapsed);
      // The wait time is shown and the draft survives.
      assert.match(
        text,
        /Try again in 30 seconds\. Your draft was preserved\.$/,
      );
      assert.equal(
        await page.inputValue("#prompt"),
        "Refactor the billing module",
      );
      assert(elapsed < 30000);
      // F-84: Send stays disabled for the Retry-After window, with a countdown,
      // so the extra clicks send nothing.
      assert(await page.locator("#send").isDisabled());
      assert.match(
        await page.locator("#send").getAttribute("aria-label"),
        /^Send message \(available in (29|30) seconds\)$/,
      );
      assert.equal(s.posts.length, 1);
      assert.equal(usage, 1);
      noConsoleErrors();
    },
  },
  {
    title: "H33-S2 Codex run fails because the ChatGPT quota is exhausted",
    async run(page) {
      const noConsoleErrors = strictConsole(page);
      const s = await mockHarness(page, {
        "GET /v1/models": { json: catalog },
        "POST /v1/jobs": (route) => {
          s.posts.push(route.request().postDataJSON());
          s.turns.push({
            id: "job-1",
            project: "sem-projeto",
            state: "failed",
            request: s.posts.at(-1),
            result: { error: CODEX_QUOTA_ERROR, model: CODEX.id },
          });
          return route.fulfill({ json: { job_id: "job-1" } });
        },
      });
      await page.goto("http://harness.test");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
      await page.fill("#prompt", "Write the release notes");
      await page.click("#send");
      await page.getByText("You've hit your usage limit").waitFor();
      const answer = page.locator("#messages article.assistant").last();
      const bubble = await answer.locator(".chat-bubble").innerText(),
        chip = await answer.locator(".run-highlight").textContent(),
        status = await statusText(page);
      console.log("H33-S2:", JSON.stringify({ chip, status, bubble }));
      // KNOWN BUG F-85: executionCondition() (ui.js) and the queue worker's
      // condition map only know Claude codes, so a Codex quota exhaustion is a
      // generic failed run with the raw adapter code, not the guided
      // "Wait for quota renewal / select a different provider" condition.
      assert.match(bubble, /^The run did not finish: codex_execution_failed: /);
      assert.doesNotMatch(bubble, /Wait for .*quota|different provider/i);
      assert.doesNotMatch(chip, /Wait for quota renewal/);
      assert.equal(status, "Failed run");
      assert.equal(await page.getByText("Wait for quota renewal").count(), 0);
      noConsoleErrors();
    },
  },
]);
