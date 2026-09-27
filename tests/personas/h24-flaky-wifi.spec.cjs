// H24 flaky Wi-Fi (P5 mobile, unstable network): the phone drops offline in the
// middle of a streamed answer and again while sending. Playwright's
// context.setOffline() does not affect routed requests, so the route below also
// aborts every harness request while `net.offline` is set.
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
const PHONE = { width: 390, height: 844 };

function strictConsole(page) {
  page.removeAllListeners("console");
  const errors = [];
  page.on("console", (m) => {
    if (m.type() === "error" && !/^Failed to load resource/.test(m.text()))
      errors.push(m.text());
  });
  return () => assert.deepEqual(errors, []);
}

// Registered after mockHarness, so it runs first and falls back when online.
async function flakyNetwork(page) {
  const net = { offline: false, blocked: [] };
  await page.route("http://harness.test/**", (route) => {
    if (!net.offline) return route.fallback();
    net.blocked.push(route.request().method() + " " + route.request().url());
    return route.abort("internetdisconnected");
  });
  net.set = async (offline) => {
    net.offline = offline;
    await page.context().setOffline(offline);
  };
  return net;
}
const delta = (id, text) => ({ id, type: "answer_delta", data: { text } });
const statusText = (page) => page.locator("#status").textContent();
// Keeps every #status text so transient messages can be asserted afterwards.
const recordStatus = (page) =>
  page.addInitScript(() => {
    window.statusLog = [];
    document.addEventListener("DOMContentLoaded", () => {
      const node = document.getElementById("status");
      new MutationObserver(() =>
        window.statusLog.push(node.textContent),
      ).observe(node, { childList: true, characterData: true, subtree: true });
    });
  });

runPersona("H24", [
  {
    title: "H24-S1 offline for 5 s in the middle of a streamed answer",
    viewport: PHONE,
    timeout: 15000,
    async run(page) {
      const noConsoleErrors = strictConsole(page);
      const words = [
        "Alpha ",
        "Bravo ",
        "Charlie ",
        "Delta ",
        "Echo ",
        "Foxtrot",
      ];
      let finished = false;
      const cursors = [];
      const s = await mockHarness(page, {
        "GET /v1/models": { json: catalog },
        "GET /v1/jobs/job-1/events": async (route) => {
          const cursor = route.request().headers()["last-event-id"];
          cursors.push(cursor);
          if (cursors.length === 1) {
            // First half, then the Wi-Fi drops before the stream resumes.
            await route.fulfill({
              body: sse(words.slice(0, 3).map((w, i) => delta(i + 1, w))),
              contentType: "text/event-stream",
            });
            await net.set(true);
            return;
          }
          // The server replays from its own buffer, overlapping ids 2..3.
          finished = true;
          return route.fulfill({
            body: sse(words.slice(1).map((w, i) => delta(i + 2, w))),
            contentType: "text/event-stream",
          });
        },
        "GET /v1/jobs/job-1": (route) =>
          route.fulfill({
            json: {
              id: "job-1",
              project: "sem-projeto",
              state: finished ? "completed" : "running",
              request: s.posts[0],
              result: finished ? {} : undefined,
            },
          }),
      });
      const net = await flakyNetwork(page);
      await recordStatus(page);
      await page.goto("http://harness.test");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
      await page.fill("#prompt", "List the NATO alphabet");
      await page.click("#send");
      await page.waitForFunction(() =>
        window.statusLog.some((t) => /^Connection lost\./.test(t)),
      );
      // The partial answer stays on screen while offline.
      await page.getByText("Alpha Bravo Charlie").waitFor();
      // Worst case, deterministic: the periodic readiness check (every 5-15 s)
      // runs during the 5 s outage.
      await page.evaluate(() => {
        readinessRetryAt = 0;
        return probeReadiness();
      });
      await page.waitForTimeout(5000);
      const offlineView = {
        gate: await page.locator("#startup-gate").isVisible(),
        status: await statusText(page),
      };
      console.log("H24-S1 offline:", JSON.stringify(offlineView));
      // The whole UI (including "Resume tracking") sits behind the gate, and
      // the stream-specific "Connection lost… Resume tracking" advice is gone.
      assert.equal(offlineView.gate, true);
      assert.doesNotMatch(offlineView.status, /Resume tracking/);
      await net.set(false);
      // F-82: `online` re-initialises the page and re-attaches to the run by
      // itself, from the last event it saw, without a tap on "Resume tracking".
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
      await page.getByText("Foxtrot").waitFor();
      await page.waitForFunction(
        () =>
          !/Connection lost/.test(
            document.querySelector("#status").textContent,
          ),
      );
      assert.deepEqual(cursors, ["0", "3"]);
      assert.equal(s.posts.length, 1);
      const answers = page.locator("#messages article.assistant");
      assert.equal(await answers.count(), 1);
      assert.equal(
        (await answers.first().locator(".chat-bubble").innerText()).trim(),
        words.join("").trim(),
      );
      assert(await page.locator("#resume-execution").isHidden());
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        "no horizontal scroll at 390 px",
      );
      noConsoleErrors();
    },
  },
  {
    title: "H24-S2 offline at send, lost reply, reload, resend",
    viewport: PHONE,
    timeout: 15000,
    async run(page) {
      const noConsoleErrors = strictConsole(page);
      const attempts = [],
        accepted = new Map();
      let loseReply = false;
      const s = await mockHarness(page, {
        "GET /v1/models": { json: catalog },
        "POST /v1/jobs": (route) => {
          const key = route.request().headers()["idempotency-key"];
          attempts.push(key);
          if (!accepted.has(key)) {
            s.posts.push(route.request().postDataJSON());
            const id = "job-" + s.posts.length;
            accepted.set(key, id);
            s.turns.push({
              id,
              project: "sem-projeto",
              state: "completed",
              request: s.posts.at(-1),
              result: { answer: "Notes summarised." },
            });
          }
          // The server accepted the job, but the reply never reaches the phone.
          if (loseReply) return route.abort("connectionreset");
          return route.fulfill({
            json: { job_id: accepted.get(key), reused: attempts.length > 1 },
          });
        },
      });
      const net = await flakyNetwork(page);
      // Every blocked POST is still an attempt from the user's point of view.
      page.on("request", (r) => {
        if (
          net.offline &&
          r.method() === "POST" &&
          r.url().endsWith("/v1/jobs")
        )
          attempts.push(r.headers()["idempotency-key"]);
      });
      await page.goto("http://harness.test");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
      await page.fill("#prompt", "Summarise my notes");

      await net.set(true);
      await page.click("#send");
      await page.waitForFunction(() =>
        /Couldn't connect to the server/.test(
          document.querySelector("#status").textContent,
        ),
      );
      assert.equal(await page.inputValue("#prompt"), "Summarise my notes");
      assert.equal(s.posts.length, 0);

      await net.set(false);
      loseReply = true;
      await page.click("#send");
      await page.waitForFunction(() =>
        /Couldn't connect to the server/.test(
          document.querySelector("#status").textContent,
        ),
      );
      assert.equal(await page.inputValue("#prompt"), "Summarise my notes");
      assert.equal(s.posts.length, 1);

      // The user reloads; the draft and the pending submission key survive.
      loseReply = false;
      await page.reload();
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
      assert.equal(await page.inputValue("#prompt"), "Summarise my notes");
      await page.click("#send");
      await page.getByText("Notes summarised.").waitFor();

      assert.equal(attempts.length, 3);
      assert.equal(new Set(attempts).size, 1, "same Idempotency-Key");
      assert.equal(s.posts.length, 1, "no duplicate job");
      assert.equal(
        await page.locator("#messages article.user").count(),
        1,
        "one user bubble",
      );
      assert.equal(await page.inputValue("#prompt"), "");
      noConsoleErrors();
    },
  },
]);
