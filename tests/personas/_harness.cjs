// Shared fixture pieces for tests/personas/*.spec.cjs (not a spec: the leading
// underscore keeps it out of the `*.spec.cjs` glob in scripts/test-ui.sh).
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

function contentType(file) {
  if (file.endsWith(".js")) return "text/javascript";
  if (file.endsWith(".css")) return "text/css";
  if (file.endsWith(".svg")) return "image/svg+xml";
  return "text/html";
}

async function serveStatic(route, pathname, appDir) {
  const file = pathname === "/" ? "index.html" : pathname.slice(1);
  const dir = file.startsWith("assets/") ? "tail_ui" : appDir;
  try {
    return await route.fulfill({
      body: await fs.readFile(path.join(__dirname, "..", "..", dir, file)),
      contentType: contentType(file),
    });
  } catch {
    return route.fulfill({ status: 404, body: "" });
  }
}

// `over` keys are "METHOD /path" (exact) or a stringified regex ("/pattern/flags")
// tested against that label; a value is "abort", "continue", a route function, or
// a `{status,json,headers,body}` fulfill spec.
function findOverride(over, method, pathname) {
  const label = method + " " + pathname;
  if (label in over) return over[label];
  for (const key of Object.keys(over)) {
    const m = key.match(/^\/(.*)\/([a-z]*)$/);
    if (m && new RegExp(m[1], m[2]).test(label)) return over[key];
  }
  return undefined;
}

async function applyOverride(route, v) {
  if (v === "abort") return route.abort("failed");
  if (v === "continue") return route.continue();
  if (typeof v === "function") return v(route);
  return route.fulfill({
    status: v.status || 200,
    headers: v.headers,
    json: v.json,
    body: v.body,
  });
}

function sse(events) {
  return events.map((e) => "data: " + JSON.stringify(e) + "\n\n").join("");
}

// Installs a route for `origin/**`: an `over` match wins first, then `apiPrefix`
// (plus "/events") goes to `api()`, which returns JSON or `{__sse: events}`;
// everything else is a static file from `appDir` (tail_ui for "assets/*").
function installRoute(page, origin, apiPrefix, appDir, api, over) {
  return page.route(origin + "/**", async (route) => {
    const req = route.request(),
      method = req.method(),
      pathname = new URL(req.url()).pathname,
      forced = findOverride(over, method, pathname);
    if (forced !== undefined) return applyOverride(route, forced);
    if (!pathname.startsWith(apiPrefix) && pathname !== "/events")
      return serveStatic(route, pathname, appDir);
    const data = await api(method, pathname, req);
    if (data && data.__sse)
      return route.fulfill({
        body: sse(data.__sse),
        contentType: "text/event-stream",
      });
    return route.fulfill({ json: data });
  });
}

const HARNESS_STATIC = {
  "/v1/projects": { projects: ["sem-projeto"], details: {} },
  "/v1/models": { models: [], providers: {}, uploads_enabled: false },
  "/v1/conversations": { conversations: [] },
  "/v1/usage": { available: false },
  "/v1/version": { version: "fixture", build: "harness" },
  "/v1/catalog": { agents: [], skills: [], warnings: [] },
  "/v1/project-directories": {
    roots: [],
    root_id: null,
    path: "",
    absolute_path: "",
    entries: [],
    limited: false,
  },
  "/v1/project-files/attach": { attachments: [], skipped: [] },
};

// Fake origin http://harness.test, mirroring agent_service's /v1 API.
async function mockHarness(page, over = {}) {
  const s = {
    turns: [],
    posts: [],
    keys: [],
    cancels: 0,
    uploads: 0,
    events: [],
    approvals: [],
  };
  await installRoute(
    page,
    "http://harness.test",
    "/v1/",
    "agent_service",
    async (method, pathname, req) => {
      if (pathname in HARNESS_STATIC) return HARNESS_STATIC[pathname];
      if (pathname === "/v1/files") {
        s.uploads++;
        return { file_id: "harness-file" };
      }
      if (pathname.startsWith("/v1/approvals/")) {
        s.approvals.push({
          id: pathname.split("/").at(-1),
          body: req.postDataJSON(),
        });
        return {};
      }
      if (pathname === "/v1/jobs" && method === "POST") {
        s.posts.push(req.postDataJSON());
        s.keys.push(req.headers()["idempotency-key"]);
        const id = "job-" + s.posts.length;
        s.turns.push({
          id,
          project: "sem-projeto",
          state: "completed",
          request: s.posts.at(-1),
          result: { answer: "Fixture response." },
        });
        return { job_id: id };
      }
      if (pathname.endsWith("/cancel")) {
        s.cancels++;
        return {};
      }
      if (pathname.endsWith("/events")) return { __sse: s.events };
      if (pathname.startsWith("/v1/jobs/")) {
        const id = pathname.split("/")[3];
        const found = s.turns.find((t) => t.id === id);
        return found || { id, state: "completed", result: {} };
      }
      if (pathname.startsWith("/v1/conversations/"))
        return { title: pathname.split("/").at(-1), turns: s.turns };
      return {};
    },
    over,
  );
  return s;
}

// Fake origin http://admin.test, mirroring control's /api and page routes.
async function mockAdmin(page, state, over = {}) {
  const calls = [];
  await installRoute(
    page,
    "http://admin.test",
    "/api/",
    "control",
    async (method, pathname, req) => {
      calls.push({
        method,
        path: pathname,
        body: req.postData() ? req.postDataJSON() : undefined,
      });
      return pathname === "/api/state" ? state : {};
    },
    over,
  );
  return calls;
}

async function runPersona(id, scenarios) {
  const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
  const out = process.env.EVAL_OUTPUT || "/tmp/tail-persona-eval";
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch();
  const results = [];
  try {
    for (const scenario of scenarios) {
      const context = await browser.newContext({
          viewport: scenario.viewport || { width: 1280, height: 900 },
        }),
        page = await context.newPage(),
        errors = [];
      page.setDefaultTimeout(scenario.timeout || 5000);
      page.on("pageerror", (e) => errors.push(e.message));
      page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
      const result = { scenario: scenario.title, status: "pass" };
      try {
        await scenario.run(page);
        assert.deepEqual(errors, []);
      } catch (e) {
        result.status = "fail";
        result.error = e.message;
        const shot = path.join(
          out,
          id + "-" + scenario.title.replace(/\W+/g, "-") + ".png",
        );
        await page.screenshot({ path: shot, fullPage: true }).catch(() => {});
      }
      results.push(result);
      await context.close();
    }
  } finally {
    await browser.close();
    await fs.writeFile(
      path.join(out, id + ".json"),
      JSON.stringify(results, null, 2) + "\n",
    );
  }
  if (results.some((r) => r.status === "fail")) process.exitCode = 1;
  return results;
}

async function visible(p, s) {
  assert(await p.locator(s).isVisible(), s + " must be visible");
}

async function fits(p, s) {
  const b = await p.locator(s).boundingBox(),
    v = p.viewportSize();
  assert(
    b &&
      b.x >= -1 &&
      b.y >= -1 &&
      b.x + b.width <= v.width + 1 &&
      b.y + b.height <= v.height + 1,
    s + " must fit on screen",
  );
}

async function named(p, s) {
  assert(
    await p.locator(s).evaluate(
      (e) =>
        !!(
          e.getAttribute("aria-label") ||
          (e.getAttribute("aria-labelledby") || "")
            .split(" ")
            .map((id) => document.getElementById(id)?.textContent || "")
            .join("")
            .trim()
        ),
    ),
    s + " needs an accessible name",
  );
}

module.exports = {
  mockHarness,
  mockAdmin,
  sse,
  runPersona,
  visible,
  fits,
  named,
};
