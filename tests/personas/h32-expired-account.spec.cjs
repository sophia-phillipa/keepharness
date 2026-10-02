// H32 expired account (P2 rushed / P3 professional): Claude access expires in
// the middle of a streamed answer; after renewing, the user resends.
// account-renewal.spec.cjs covers conditions on empty streams; this persona
// covers the mid-stream case and the resend.
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
const PROMPT = "Plan the quarterly offsite";
const PARTIAL = "Here is the first half of the plan: venue, budget";

async function expiringClaude(page) {
  let expired = true;
  const s = await mockHarness(page, {
    "GET /v1/models": { json: catalog },
    "POST /v1/jobs": (route) => {
      s.posts.push(route.request().postDataJSON());
      const id = "job-" + s.posts.length;
      s.turns.push({
        id,
        project: "sem-projeto",
        state: expired ? "interrupted" : "completed",
        request: s.posts.at(-1),
        result: expired
          ? { condition: "claude_authentication_required" }
          : { answer: "Plan ready: venue, budget, agenda." },
      });
      return route.fulfill({ json: { job_id: id } });
    },
    "GET /v1/jobs/job-1/events": {
      body: sse([
        { id: 1, type: "answer_delta", data: { text: "Here is the first " } },
        { id: 2, type: "answer_delta", data: { text: "half of the plan: " } },
        { id: 3, type: "answer_delta", data: { text: "venue, budget" } },
      ]),
      headers: { "content-type": "text/event-stream" },
    },
  });
  s.renew = () => (expired = false);
  return s;
}
// Keeps every rendered answer so the partial text can be checked afterwards.
const recordAnswers = (page) =>
  page.addInitScript(() => {
    window.answerLog = [];
    document.addEventListener("DOMContentLoaded", () =>
      new MutationObserver(() => {
        const last = [
          ...document.querySelectorAll(
            "#messages article.assistant .chat-bubble",
          ),
        ].at(-1);
        if (last) window.answerLog.push(last.innerText);
      }).observe(document.getElementById("messages"), {
        childList: true,
        characterData: true,
        subtree: true,
      }),
    );
  });

async function expireMidStream(page, s) {
  await recordAnswers(page);
  await page.goto("http://harness.test");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  await page.fill("#prompt", PROMPT);
  await page.click("#send");
  await page.getByText("Your Claude access needs to be renewed.").waitFor();
  assert.equal(s.posts.length, 1);
}

runPersona("H32", [
  {
    title: "H32-S1 Claude access expires in the middle of the answer",
    async run(page) {
      const s = await expiringClaude(page);
      await expireMidStream(page, s);
      const answer = page.locator("#messages article.assistant").last();
      assert.match(
        await answer.locator(".run-highlight").textContent(),
        /Renew Claude access/,
      );
      assert.equal(
        await page.locator("#status").textContent(),
        "Renew Claude access",
      );
      assert.match(
        await page.locator(".composer-area #model-availability").innerText(),
        /admin panel, find Claude Code and click “Renew access”/,
      );
      // The prompt is kept in the user bubble…
      assert.equal(
        (
          await page.locator("#messages article.user").last().innerText()
        ).trim(),
        PROMPT,
      );
      // …the partial answer was on screen while it streamed…
      assert(
        await page.evaluate(
          (p) => window.answerLog.some((t) => t.includes(p)),
          PARTIAL,
        ),
      );
      // F-83: the partial answer stays in history; the renewal notice attaches
      // to the blocked composer, which preserves the prompt for recovery.
      const bubble = await answer.locator(".chat-bubble").innerText();
      assert(bubble.indexOf(PARTIAL) >= 0, "partial answer kept");
      assert.doesNotMatch(bubble, /Your Claude access/);
      assert(await page.locator(".composer-area #model-availability").isVisible());
      assert(await page.locator("#prompt").isDisabled());
      assert(await page.locator("#send").isDisabled());
      assert.equal(await page.inputValue("#prompt"), PROMPT);
    },
  },
  {
    title: "H32-S2 after renewing, the resend continues the same conversation",
    async run(page) {
      const s = await expiringClaude(page);
      await expireMidStream(page, s);
      const title = await page.locator("#conversation-title").textContent();
      s.renew();
      assert.equal(await page.inputValue("#prompt"), PROMPT);
      await page.locator("#models-retry").click();
      await page.waitForFunction(() => !document.querySelector("#prompt").disabled);
      assert.equal(await page.inputValue("#prompt"), PROMPT);
      await page.click("#send");
      await page.getByText("Plan ready: venue, budget, agenda.").waitFor();
      assert.equal(s.posts.length, 2);
      assert.equal(s.posts[1].parent_job_id, "job-1");
      assert.equal(s.posts[1].prompt, PROMPT);
      assert.equal(s.posts[1].backend, "claude");
      assert.equal(
        await page.locator("#conversation-title").textContent(),
        title,
      );
      assert.equal(await page.locator("#messages article.user").count(), 2);
      // The earlier turn keeps its neutral renewal summary.
      assert.match(
        await page
          .locator("#messages article.assistant .run-highlight")
          .first()
          .textContent(),
        /Renew Claude access/,
      );
    },
  },
]);
