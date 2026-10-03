// H39 performance skeptic (P6 engineer): opens the harness on a slow phone
// (CDP CPU throttling 4x) over DevTools "Slow 3G", then streams a 2,000-delta answer.
// CDP Network.emulateNetworkConditions does not throttle Playwright-fulfilled
// routes, so the route below models Slow 3G per request instead (2,000 ms RTT,
// 50,000 B/s, bandwidth not shared between requests: an optimistic lower bound).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { mockHarness, runPersona, sse } = require("./_harness.cjs");

const CLAUDE = {
  id: "claude-sonnet-4-6",
  backend: "claude",
  efforts: ["low"],
  permissions: { upload: false },
  execution_modes: ["native"],
};
const catalog = { models: [CLAUDE], providers: { claude: true } };
const SLOW_3G = { latency: 2000, bytesPerSecond: 50000 };
const ROOT = path.join(__dirname, "..", "..");

function staticSize(pathname) {
  const file = pathname === "/" ? "index.html" : pathname.slice(1);
  const dir = file.startsWith("assets/") ? "harness_ui" : "agent_service";
  try {
    return fs.statSync(path.join(ROOT, dir, file)).size;
  } catch {
    return 0;
  }
}

async function slowDevice(page, network) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
  const log = [];
  if (network) {
    await cdp.send("Network.enable");
    await cdp.send("Network.emulateNetworkConditions", {
      offline: false,
      latency: SLOW_3G.latency,
      downloadThroughput: SLOW_3G.bytesPerSecond,
      uploadThroughput: SLOW_3G.bytesPerSecond,
    });
    // Registered after mockHarness, so it delays and then falls back to it.
    await page.route("http://harness.test/**", async (route) => {
      const { pathname } = new URL(route.request().url());
      const bytes = pathname.startsWith("/v1/") ? 0 : staticSize(pathname);
      log.push({ pathname, bytes, at: Date.now() });
      await new Promise((r) =>
        setTimeout(
          r,
          SLOW_3G.latency + (bytes / SLOW_3G.bytesPerSecond) * 1000,
        ),
      );
      return route.fallback();
    });
  }
  await page.addInitScript(() => {
    window.longTasks = [];
    new PerformanceObserver((list) => {
      for (const e of list.getEntries())
        window.longTasks.push({ start: e.startTime, duration: e.duration });
    }).observe({ type: "longtask", buffered: true });
  });
  return log;
}

runPersona("H39", [
  {
    title: "H39-S1 first load on a 4x slower CPU over Slow 3G",
    timeout: 120000,
    async run(page) {
      await mockHarness(page, { "GET /v1/models": { json: catalog } });
      const log = await slowDevice(page, true);
      const started = Date.now();
      await page.goto("http://harness.test", { waitUntil: "commit" });
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
      const ready = Date.now() - started;
      const tasks = await page.evaluate(() => window.longTasks);
      const over200 = tasks.filter((t) => t.duration > 200);
      const bytes = log.reduce((sum, r) => sum + r.bytes, 0);
      console.log(
        "H39-S1:",
        JSON.stringify({
          readyMs: ready,
          requests: log.length,
          staticBytes: bytes,
          longTasksOver200: over200.map((t) => Math.round(t.duration)),
          biggest: log
            .filter((r) => r.bytes > 50000)
            .map((r) => r.pathname + " " + r.bytes),
        }),
      );
      assert(over200.length <= 3, "at most 3 long tasks over 200 ms");
      // The gate itself hides well inside the 5 s budget (script execution does
      // not wait on the stylesheet/asset downloads), so the roster's "hidden
      // < 5 s" bar is met even under 4x CPU + Slow 3G.
      assert(ready < 4000, "startup gate hides well inside the 5 s budget");
      // KNOWN BUG F-86: every visit still re-downloads ~1.17 MB of
      // uncompressed static files from scratch — no gzip/br anywhere
      // (agent_service/app.py has no compression middleware), ui.js/ui.css/
      // index.html are served "Cache-Control: no-store"
      // (agent_service/routes/system.py) and /assets/* is "no-cache" with no
      // conditional-request support (harness_ui/__init__.py:asset_response), so
      // there is no 304 path. tabler.min.css alone is 694 KB. On a real Slow-3G
      // link that payload keeps loading in the background well past the point
      // the gate opens.
      assert(
        bytes > 1000000,
        "startup still re-downloads over 1 MB of uncompressed, uncached static assets",
      );
    },
  },
  {
    title: "H39-S2 stream of 2,000 answer deltas on a 4x slower CPU",
    timeout: 120000,
    async run(page) {
      const deltas = Array.from({ length: 2000 }, (_, i) => ({
        id: i + 1,
        type: "answer_delta",
        data: {
          text: "Token " + i + (i % 50 === 49 ? ".\n\n" : " "),
        },
      }));
      const full = deltas.map((d) => d.data.text).join("");
      const s = await mockHarness(page, {
        "GET /v1/models": { json: catalog },
        "GET /v1/jobs/job-1/events": {
          body: sse(deltas),
          headers: { "content-type": "text/event-stream" },
        },
        "GET /v1/jobs/job-1": (route) =>
          route.fulfill({
            json: {
              id: "job-1",
              project: "sem-projeto",
              state: "completed",
              request: s.posts[0],
              result: {},
            },
          }),
      });
      await slowDevice(page, false);
      await page.goto("http://harness.test");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
      const before = await page.evaluate(() => window.longTasks.length);
      await page.fill("#prompt", "Stream a very long answer");
      const started = Date.now();
      await page.click("#send");
      await page.getByText("Token 1999").waitFor({ timeout: 110000 });
      const streamed = Date.now() - started;
      const tasks = (await page.evaluate(() => window.longTasks)).slice(before);
      const worst = Math.max(0, ...tasks.map((t) => t.duration));
      console.log(
        "H39-S2:",
        JSON.stringify({
          streamedMs: streamed,
          longTasks: tasks.length,
          worstMs: Math.round(worst),
          answerChars: full.length,
        }),
      );
      const answers = page.locator("#messages article.assistant");
      assert.equal(await answers.count(), 1, "one assistant message");
      assert.equal(s.posts.length, 1);
      assert.match(
        await answers.first().locator(".chat-bubble").innerText(),
        /Token 0 Token 1 .*Token 1999/s,
      );
      // F-87: the markdown is re-rendered at most once per animation frame, so
      // a burst of deltas never blocks the page beyond the 500 ms budget.
      assert(worst < 500, "longest task " + Math.round(worst) + " ms");
    },
  },
]);
