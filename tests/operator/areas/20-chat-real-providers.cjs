// Real-provider chat campaign on the packaged desktop app. Self-run (see the command below): it
// attaches the app to the persistent campaign instance (tests/operator/chat-campaign/instance.cjs),
// drives real Codex turns visibly, and records per-turn metrics. Under run-operator.cjs without
// CHAT_SLICE (or in fixture mode) the area is skipped.
//
//   node tests/operator/chat-campaign/instance.cjs start
//   env -u WAYLAND_DISPLAY DISPLAY=:0 CHAT_SLICE=pilot CHAT_BUDGET=3 \
//     NODE_PATH=<node_modules with playwright> PLAYWRIGHT_MODULE=<same>/playwright \
//     node tests/operator/areas/20-chat-real-providers.cjs
//
// Env: CHAT_SLICE (pilot|luna|deepseek), CHAT_SCENARIOS (comma list, overrides the slice),
// CHAT_BUDGET (real prompts allowed), CHAT_DEEPSEEK_WAIT_MS, CHAT_APP (an inspect-enabled copy of the
// packaged binary: Playwright cannot attach to the production package, whose inspect fuse is off).
// Known limitation: the desktop attaches to the running admin, so closing the app does not stop
// the backend (the instance keeps running until `instance.cjs stop`).
"use strict";
const { spawn, execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const instance = require("../chat-campaign/instance.cjs");
const { chooseModel, chooseAccess, newChat, submit, waitAnswer, dismissTour } = require("../lib/app.cjs");

const { paths, FACTS, PROJECT_NAME, adminApi, portOpen } = instance;
const REPO = path.resolve(__dirname, "../../..");
const ADMIN_PORT = "18641";
const HARNESS_PORT = "18640";
const APP = process.env.CHAT_APP || path.join(instance.paths.root, "app/keepharness-bin"); // inspect-enabled copy of the packaged build (see plan.md)
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const playwright = () => require(process.env.PLAYWRIGHT_MODULE || "playwright");
const LIMIT = /quota|credit|rate.?limit|usage.?limit|limit reached|too many requests|insufficient|billing|exhausted/i;
const harnessUrl = `http://127.0.0.1:${HARNESS_PORT}`;

let ctx = null; // set by the self-run main(): the live app handle, sampler and metrics

// ------------------------------------------------------------------ scenarios

const SLICES = {
  pilot: ["new-chat-short", "follow-up", "project-facts", "reopen"],
  luna: ["luna-short"],
  deepseek: ["await-deepseek-key"],
};

const ask = async (op, name, text, pattern) => turn(op, name, text, pattern);

const SCENARIOS = {
  "new-chat-short": async (op) => {
    await op.step("new-chat-short", "Now: a new chat on Sol (Medium) asking for the highest mountain", async () => {
      await newChat(op);
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      await ask(op, "new-chat-short", "What is the highest mountain on Earth? Answer in one short sentence.", /Everest/);
    }, { lint: false });
  },
  "follow-up": async (op) => {
    await op.step("follow-up", "Now: a follow-up in the same conversation", async () => {
      await ask(op, "follow-up", "How tall is it, in meters?", /8[,.\s]?8\d\d/);
    }, { lint: false });
  },
  "project-facts": async (op) => {
    await op.step("project-facts", `Now: a chat in the project ${PROJECT_NAME}, Read only, asking what FACTS.md says`, async () => {
      await newChat(op);
      await chooseProject(op, "Chat facts");
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      await chooseAccess(op, "Read only");
      const all = new RegExp(`(?=[\\s\\S]*${FACTS.harbor})(?=[\\s\\S]*${FACTS.lamp})(?=[\\s\\S]*${FACTS.code})`);
      await ask(op, "project-facts", "Read FACTS.md in this project and quote exactly the harbor name, the lantern count with its wording, and the door code.", all);
    }, { lint: false });
  },
  reopen: async (op) => {
    await op.step("reopen", "Now: closing the app and opening it again", async () => {
      const before = await snapshot(op.page);
      before.bounds = await bounds(ctx.handle);
      await op.caption("Now: closing the app");
      await quitApp(ctx.handle);
      await sleep(1500);
      const started = Date.now();
      await openApp(op.session);
      const ms = Date.now() - started;
      await dismissTourSoon(op);
      const after = await snapshot(op.page);
      after.bounds = await bounds(ctx.handle);
      const restored = !!before.title && before.title === after.title && after.articles > 0;
      ctx.reopen = { ms, before, after, restored };
      record({ scenario: "reopen", reopen_ms: ms, bounds_before: before.bounds, bounds_after: after.bounds, restored, title_before: before.title, title_after: after.title });
      op.check(after.title !== undefined, "the reopened app showed no conversation header");
    }, { lint: false });
  },
  "luna-short": async (op) => {
    await op.step("luna-short", "Now: a new chat on Luna asking for the highest mountain", async () => {
      await newChat(op);
      await chooseModel(op, "gpt-5.6-luna");
      await chooseEffort(op, "Medium");
      await ask(op, "luna-short", "What is the highest mountain on Earth? Answer in one short sentence.", /Everest/);
    }, { lint: false });
  },
  "await-deepseek-key": async (op) => {
    await op.step("await-deepseek-key", "Now: waiting for Sophia to enter the DeepSeek key in Settings > Providers", async () => {
      const limit = Date.now() + Number(process.env.CHAT_DEEPSEEK_WAIT_MS || 900000);
      let lastCaption = 0;
      for (;;) {
        const state = await adminApi("GET", "/api/state");
        const j = state.json || {};
        if (j.credentials?.deepseek === true || j.authentication?.deepseek === true || j.authentication?.deepseek?.configured === true) return;
        if (Date.now() > limit) throw new Error("no DeepSeek key was entered before the timeout");
        if (Date.now() - lastCaption > 20000) {
          lastCaption = Date.now();
          await op.caption("Now: waiting for Sophia to enter the DeepSeek key in Settings › Providers");
        }
        await sleep(2000);
      }
    }, { lint: false });
  },
};

// ------------------------------------------------------------------ UI helpers

async function chooseEffort(op, label) {
  await op.click(op.page.locator("#effort-trigger"));
  const option = op.page.locator("#effort-menu").getByRole("option", { name: new RegExp("^\\W*" + label) });
  if (!(await option.count())) throw new Error(`this model offers no ${label} effort`);
  await op.click(option.first());
  await op.seeText(op.page.locator("#effort-label"), new RegExp(label));
}

// After "New chat": the project menu lists "No project" first, then the project labels.
async function chooseProject(op, label) {
  await op.click(op.page.locator("#project-button"));
  await op.click(op.page.locator("#project-menu").getByRole("option", { name: new RegExp(label) }));
  await op.seeText(op.page.locator("#project-button-label"), new RegExp(label));
}

async function dismissTourSoon(op) {
  await sleep(1500);
  await dismissTour(op);
}

async function snapshot(page) {
  return page.evaluate(() => ({
    url: location.href,
    title: document.getElementById("conversation-title")?.innerText || "",
    articles: document.querySelectorAll("#messages article").length,
  }));
}

// ------------------------------------------------------------------ one real turn

async function turn(op, name, text, pattern) {
  const page = op.page;
  await page.evaluate(() => (window.__kh.lat = []));
  await op.fill(page.locator("#prompt"), text);
  const from = Date.now();
  const before = await submit(op);
  let answer = null;
  let error = null;
  try {
    answer = await waitAnswer(op, before, pattern, 180000);
  } catch (e) {
    error = e;
  }
  const to = Date.now();
  await sleep(1200); // one more resource sample after the answer
  const m = await page.evaluate(() => {
    const t = window.__kh.turn || {};
    const lat = window.__kh.lat.slice().sort((a, b) => a - b);
    const q = (p) => (lat.length ? lat[Math.min(lat.length - 1, Math.floor(p * lat.length))] : null);
    const last = [...document.querySelectorAll("#messages article")].pop();
    return {
      ttft_ms: t.first == null ? null : Math.round(t.first),
      total_ms: t.done == null ? null : Math.round(t.done),
      failed: t.failed || null,
      paint: { n: lat.length, median: q(0.5), p95: q(0.95), max: lat.length ? lat[lat.length - 1] : null },
      pill: document.getElementById("conversation-state-pill")?.innerText || "",
      reply: (last?.innerText || "").slice(0, 400),
    };
  });
  ctx.turns += 1;
  const shot = path.join(ctx.out, "shots", `turn-${ctx.turns}.png`);
  await page.screenshot({ path: shot, timeout: 15000 }).catch(() => {});
  const win = ctx.samples.filter((s) => s.t >= from && s.t <= to + 1500);
  const max = (key) => (win.length ? Math.max(...win.map((s) => s[key])) : null);
  const avg = (key) => (win.length ? +(win.reduce((a, s) => a + s[key], 0) / win.length).toFixed(1) : null);
  const line = {
    scenario: name, turn: ctx.turns, ttft_ms: m.ttft_ms, total_ms: m.total_ms ?? to - from,
    paint_ms: m.paint, electron_rss_mb: max("electron_rss"), harness_rss_mb: max("harness_rss"),
    electron_cpu_pct: avg("electron_cpu"), harness_cpu_pct: avg("harness_cpu"), samples: win.length,
    pill: m.pill, reply: m.reply, shot,
  };
  record(line);
  if (error || m.failed || /Failed/.test(m.pill)) {
    if (LIMIT.test(`${m.pill} ${m.reply} ${m.failed || ""} ${error?.message || ""}`)) {
      op.area.halted = true;
      record({ scenario: name, halted: "quota/credit/rate-limit text seen", pill: m.pill, reply: m.reply });
    }
  }
  if (error) throw error;
  return answer;
}

function record(line) {
  ctx.lines.push(line);
  fs.appendFileSync(path.join(ctx.out, "metrics.jsonl"), JSON.stringify({ at: new Date().toISOString(), ...line }) + "\n");
}

// ------------------------------------------------------------------ the area

const area = {
  id: "chat-real",
  title: "Chat with real providers (desktop, Codex)",
  async run(op) {
    if (!ctx || op.fixtureMode || !process.env.CHAT_SLICE) op.skip("self-run area: set CHAT_SLICE and run this file directly");
    await op.step("open", "Now: the app opened on the campaign profile", async () => {
      op.check(ctx.userData.startsWith(ctx.home + path.sep), `userData ${ctx.userData} is outside ${ctx.home}`);
      await dismissTourSoon(op);
      record({ scenario: "open", open_ms: ctx.openMs, userData: ctx.userData });
    }, { critical: true, lint: false });
    const wanted = (process.env.CHAT_SCENARIOS ? process.env.CHAT_SCENARIOS.split(",") : SLICES[process.env.CHAT_SLICE]) || [];
    for (const id of wanted) {
      if (!SCENARIOS[id]) throw new Error("unknown scenario " + id);
      await SCENARIOS[id](op);
    }
  },
};
module.exports = area;

// ------------------------------------------------------------------ app launch

function appEnv(extra = {}) {
  const home = ctx.home;
  return {
    ...process.env,
    HOME: home,
    TMPDIR: ctx.tmp,
    XDG_CONFIG_HOME: path.join(home, ".config"),
    XDG_DATA_HOME: path.join(home, ".local/share"),
    XDG_CACHE_HOME: path.join(home, ".cache"),
    DISPLAY: process.env.DISPLAY || ":0",
    KEEPHARNESS_ADMIN_PORT: ADMIN_PORT,
    KEEPHARNESS_PORT: HARNESS_PORT,
    ...extra,
  };
}

const METRICS_JS = `(() => {
  if (window.__kh) return;
  const kh = (window.__kh = { lat: [], turn: null });
  document.addEventListener("keydown", (e) => {
    if (!(e.target && e.target.id === "prompt")) return;
    const t = performance.now();
    requestAnimationFrame(() => requestAnimationFrame(() => kh.lat.push(performance.now() - t)));
  }, true);
  document.addEventListener("click", (e) => {
    if (!(e.target && e.target.closest && e.target.closest("#send"))) return;
    kh.turn = { t0: performance.now(), n: document.querySelectorAll("#messages article.assistant").length, first: null, done: null, busy: false, failed: null };
  }, true);
  const check = () => {
    const t = kh.turn;
    if (!t) return;
    const now = performance.now() - t.t0;
    const articles = [...document.querySelectorAll("#messages article.assistant")];
    const last = articles[articles.length - 1];
    if (t.first == null && articles.length > t.n && last && last.textContent.trim()) t.first = now;
    const pill = document.getElementById("conversation-state-pill");
    const state = pill && pill.dataset.state;
    if (state && state !== "done") t.busy = true;
    if (t.busy && state === "done" && t.done == null) t.done = now;
    if (pill && /Failed/.test(pill.innerText) && !t.failed) t.failed = pill.innerText;
  };
  new MutationObserver(check).observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ["data-state"] });
})();`;

async function openApp(session) {
  const { _electron: electron } = playwright();
  const started = Date.now();
  const app = await electron.launch({ executablePath: APP, args: ["--ozone-platform=x11"], env: appEnv(), timeout: 60000 });
  const handle = { app, closed: false };
  await app.firstWindow();
  // Proof the profile is the campaign HOME, before any action in the window.
  ctx.userData = await app.evaluate(({ app: a }) => a.getPath("userData"));
  if (!ctx.userData.startsWith(ctx.home + path.sep)) {
    await quitApp(handle);
    throw new Error(`userData ${ctx.userData} is outside ${ctx.home}; refusing to continue`);
  }
  handle.exited = new Promise((resolve) => app.process().once("exit", (code) => resolve(code)));
  ctx.handle = handle;
  ctx.electronPid = app.process().pid;
  let page = null;
  const deadline = Date.now() + 60000;
  while (!page && Date.now() < deadline) {
    page = app.windows().find((w) => !w.isClosed() && w.url().startsWith(harnessUrl)) || null;
    if (!page) await sleep(250);
  }
  if (!page) throw new Error("no window ever showed " + harnessUrl);
  await page.waitForLoadState("domcontentloaded");
  await page.addInitScript(METRICS_JS);
  await page.evaluate(METRICS_JS);
  await page.locator("#startup-gate").waitFor({ state: "hidden", timeout: 40000 });
  ctx.openMs = Date.now() - started;
  session.page = page;
  return handle;
}

async function quitApp(h) {
  if (h.closed) return;
  h.closed = true;
  await h.app.evaluate(({ app }) => app.quit()).catch(() => {});
  await Promise.race([h.exited, sleep(15000)]);
  if (h.app.process().exitCode === null) h.app.process().kill("SIGKILL");
}

const bounds = (h) =>
  h.app.evaluate(({ BrowserWindow }, base) => BrowserWindow.getAllWindows().find((w) => w.webContents.getURL().startsWith(base))?.getBounds() || null, harnessUrl).catch(() => null);

// ------------------------------------------------------------------ /proc sampler

const PAGE_MB = 4096 / 1048576;
function readProcs() {
  const map = new Map();
  for (const d of fs.readdirSync("/proc")) {
    if (!/^\d+$/.test(d)) continue;
    try {
      const f = fs.readFileSync(`/proc/${d}/stat`, "utf8");
      const rest = f.slice(f.lastIndexOf(")") + 2).split(" ");
      const rss = Number(fs.readFileSync(`/proc/${d}/statm`, "utf8").split(" ")[1]) * PAGE_MB;
      map.set(Number(d), { ppid: Number(rest[1]), ticks: Number(rest[11]) + Number(rest[12]), rss });
    } catch {}
  }
  return map;
}
function tree(map, root) {
  const out = new Set(map.has(root) ? [root] : []);
  for (let grew = true; grew; ) {
    grew = false;
    for (const [pid, p] of map) if (!out.has(pid) && out.has(p.ppid)) (out.add(pid), (grew = true));
  }
  return out;
}
function harnessPid() {
  try {
    const out = execFileSync("ss", ["-ltnp", `sport = :${HARNESS_PORT}`], { encoding: "utf8" });
    return Number((out.match(/pid=(\d+)/) || [])[1]) || 0;
  } catch {
    return 0;
  }
}
function startSampler() {
  let prev = null;
  let prevAt = 0;
  let hpid = 0;
  let n = 0;
  const timer = setInterval(() => {
    try {
      const map = readProcs();
      const now = Date.now();
      if (!hpid || !map.has(hpid) || n++ % 10 === 0) hpid = harnessPid();
      const groups = { electron: tree(map, ctx.electronPid || 0), harness: tree(map, hpid) };
      const sum = (set, key, from = map) => [...set].reduce((a, pid) => a + (from.get(pid)?.[key] || 0), 0);
      const cpu = (set) => (prev ? [...set].reduce((a, pid) => a + Math.max(0, (map.get(pid)?.ticks || 0) - (prev.get(pid)?.ticks ?? map.get(pid)?.ticks ?? 0)), 0) / ((now - prevAt) / 1000) : 0);
      ctx.samples.push({
        t: now, electron_rss: +sum(groups.electron, "rss").toFixed(1), harness_rss: +sum(groups.harness, "rss").toFixed(1),
        electron_cpu: +cpu(groups.electron).toFixed(1), harness_cpu: +cpu(groups.harness).toFixed(1),
      });
      prev = map;
      prevAt = now;
    } catch {}
  }, 1000);
  return () => clearInterval(timer);
}

// ------------------------------------------------------------------ self-run

function summaryMd(op, slice) {
  const steps = op.area?.steps || [];
  const passed = steps.filter((s) => s.status === "pass").length;
  const rows = ctx.lines.filter((l) => l.turn).map((l) => `| ${l.turn} | ${l.scenario} | ${l.ttft_ms ?? "-"} | ${l.total_ms ?? "-"} | ${l.paint_ms.median?.toFixed?.(1) ?? "-"} / ${l.paint_ms.p95?.toFixed?.(1) ?? "-"} | ${l.electron_rss_mb ?? "-"} | ${l.harness_rss_mb ?? "-"} | ${l.electron_cpu_pct ?? "-"} / ${l.harness_cpu_pct ?? "-"} |`);
  const re = ctx.reopen;
  return [
    `# Chat campaign: ${slice}`, "", `Checks passed: ${passed}/${steps.length}`,
    ...steps.map((s) => `- ${s.status.toUpperCase()} ${s.id}${s.detail ? ": " + s.detail : ""}`), "",
    "| turn | scenario | TTFT ms | total ms | input-to-paint median / p95 ms | Electron RSS MB | harness RSS MB | CPU % (Electron / harness) |",
    "|---|---|---|---|---|---|---|---|", ...rows, "",
    `App open: ${ctx.openMs} ms.`,
    re ? `Reopen: ${re.ms} ms; bounds before ${JSON.stringify(re.before.bounds)}, after ${JSON.stringify(re.after.bounds)}; last conversation restored: ${re.restored} ("${re.before.title}" -> "${re.after.title}").` : "",
    "Known limitation: the desktop attaches to the running admin, so closing the app does not stop the backend.", "",
  ].join("\n");
}

async function main() {
  const slice = process.env.CHAT_SLICE;
  if (!slice || !(SLICES[slice] || process.env.CHAT_SCENARIOS)) throw new Error("set CHAT_SLICE to one of " + Object.keys(SLICES).join(", "));
  if (!(await portOpen(Number(ADMIN_PORT))) || !(await portOpen(Number(HARNESS_PORT)))) throw new Error("the campaign instance is not running (node tests/operator/chat-campaign/instance.cjs start)");
  if (!fs.existsSync(APP)) throw new Error("packaged app not found: " + APP);
  const { Operator } = require("../lib/operator.cjs");
  const { Report } = require("../lib/report.cjs");
  const out = path.join(paths.runs, slice);
  fs.mkdirSync(path.join(out, "shots"), { recursive: true });
  ctx = { home: paths.home, tmp: fs.mkdtempSync(path.join(os.tmpdir(), "claude-kh-")), out, samples: [], lines: [], turns: 0, electronPid: 0 };
  const stopSampler = startSampler();
  const session = { page: null, target: "desktop", base: harnessUrl, close: async () => ctx.handle && quitApp(ctx.handle) };
  let op = null;
  let report = null;
  try {
    await openApp(session);
    const options = { mode: "real", visible: true, pace: 600, budget: Number(process.env.CHAT_BUDGET ?? 0), target: "desktop", url: harnessUrl, adminUrl: `http://127.0.0.1:${ADMIN_PORT}` };
    report = new Report(out, { mode: "real", target: "desktop", visible: true, url: harnessUrl, admin: options.adminUrl, started: new Date().toISOString(), budget: options.budget });
    op = new Operator({ session, options, known: { steps: {}, lint: [] }, report, fixture: null });
    await op.runArea(area);
  } finally {
    stopSampler();
    const summary = report?.write();
    if (op) fs.writeFileSync(path.join(out, "summary.md"), summaryMd(op, slice));
    await session.close().catch(() => {});
    fs.chmodSync(ctx.tmp, 0o700);
    fs.rmSync(ctx.tmp, { recursive: true, force: true });
    if (summary) {
      console.log(`SUMMARY ${JSON.stringify(summary)}\nout ${out}`);
      process.exitCode = summary.fail || report.areas.some((a) => a.error) ? 1 : 0;
    }
  }
}

if (require.main === module) main().catch((error) => ((process.exitCode = 2), console.error("chat campaign failed:", error.message)));
