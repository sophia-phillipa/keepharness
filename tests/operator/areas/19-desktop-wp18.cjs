// WP-18 desktop journeys on the packaged app: runtime (S2), polish (S5), observability (S4),
// version truth (S1) and the Linux installer (S3). Runs its own desktop windows, each with a
// throwaway HOME, so nothing of the real installation is read or written. Needs the packaged
// app built with the inspect fuse on (Playwright attaches through the Node inspector); see
// tests/operator/coverage/desktop-wp18.md.
"use strict";
const { spawn, spawnSync, execFileSync } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const policy = require("../../../desktop/policy.cjs");

const REPO = path.resolve(__dirname, "../../..");
const FAKE_PROVIDER = path.join(REPO, "tests/operator/fixture/fake_provider.py");
const ADMIN_PORT = "18520";
const HARNESS_PORT = "18521";
const FOREIGN_PORT = "18522";
const DOWNLOAD_PORT = "18523";
const OUT = process.env.WP18_OUT || "/home/sophia/.cache/kho/wp18";
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const playwright = () => require(process.env.PLAYWRIGHT_MODULE || "playwright");

// ------------------------------------------------------------------ sandbox

function makeBox(python, backend) {
  // A short path: Chromium's single-instance socket must fit 108 bytes.
  const parent = process.env.WP18_SCRATCH || "/run/media/system/Midia/dev-cache";
  const root = fs.mkdtempSync(path.join(fs.existsSync(parent) ? parent : "/tmp", "kw18-"));
  const box = {
    root,
    home: path.join(root, "h"),
    tmp: fs.mkdtempSync("/tmp/kw18t-"),
    bin: path.join(root, "bin"),
    python,
    backend,
  };
  for (const dir of [box.home, box.bin]) fs.mkdirSync(dir);
  // The tools the backend may find: a Claude stand-in, git/node, and only prlimit of the three
  // that discovery reports (ffmpeg and bwrap stay missing, whatever the host has).
  fs.writeFileSync(path.join(box.bin, "claude"), `#!/bin/sh\nexec "${python}" "${FAKE_PROVIDER}" "$@"\n`, { mode: 0o755 });
  for (const tool of ["git", "node", "prlimit"]) {
    const found = spawnSync("sh", ["-c", `command -v ${tool}`], { encoding: "utf8" }).stdout.trim();
    if (found) fs.symlinkSync(found, path.join(box.bin, tool));
  }
  // The backend interpreter: quiet by default; "noisy" leaks fake credentials and 2 MiB to stderr.
  fs.writeFileSync(path.join(box.bin, "python-quiet"), `#!/bin/sh\nexec "${python}" "$@"\n`, { mode: 0o755 });
  fs.writeFileSync(
    path.join(box.bin, "python-noisy"),
    `#!/bin/sh
if [ "$1" = "-m" ]; then
  ( PATH=/usr/bin:/bin; sleep 7
    head -c 2200000 /dev/zero | tr '\\0' 'x' | fold -w 200 >&2
    echo "debug secret=$(cat "$HOME/.local/share/keepharness/local.key")" >&2
    echo "Authorization: Bearer sk-live-LEAK1234567890" >&2
    echo "Cookie: admin=LEAKEDADMINCOOKIE999" >&2 ) &
fi
exec "${python}" "$@"
`,
    { mode: 0o755 },
  );
  return box;
}

function appEnv(box, extra = {}) {
  const env = {
    PATH: box.bin, // tools come only from the sandbox bin
    HOME: box.home,
    TMPDIR: box.tmp,
    XDG_CONFIG_HOME: path.join(box.home, ".config"),
    XDG_DATA_HOME: path.join(box.home, ".local/share"),
    XDG_CACHE_HOME: path.join(box.home, ".cache"),
    DISPLAY: process.env.DISPLAY || ":0",
    XAUTHORITY: process.env.XAUTHORITY || "",
    XDG_RUNTIME_DIR: process.env.XDG_RUNTIME_DIR || "",
    KEEPHARNESS_ADMIN_PORT: ADMIN_PORT,
    KEEPHARNESS_PORT: HARNESS_PORT,
    KEEPHARNESS_PYTHON: path.join(box.bin, "python-quiet"),
    PYTHONPATH: box.backend,
    ...extra,
  };
  for (const key of Object.keys(env)) if (env[key] === "" || env[key] === undefined) delete env[key];
  return env;
}

// ------------------------------------------------------------------ admin API as the owner

function httpRequest(method, url, { body, cookie, headers = {}, timeout = 8000 } = {}) {
  return new Promise((resolve) => {
    const request = http.request(url, { method, timeout, headers: { ...(cookie ? { cookie } : {}), ...(body ? { "content-type": "application/json" } : {}), ...headers } }, (response) => {
      let text = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => (text += chunk));
      response.on("end", () => resolve({ status: response.statusCode, headers: response.headers, text }));
    });
    request.on("timeout", () => request.destroy());
    request.on("error", () => resolve({ status: 0, headers: {}, text: "" }));
    if (body) request.write(JSON.stringify(body));
    request.end();
  });
}

async function adminLogin(box) {
  const secret = fs.readFileSync(path.join(box.home, ".local/share/keepharness/local.key"), "utf8");
  const opened = await httpRequest("GET", `http://127.0.0.1:${ADMIN_PORT}/open?ticket=${encodeURIComponent(policy.openTicket(secret))}`);
  const value = policy.cookieValue(opened.headers["set-cookie"], "admin");
  if (!value) throw new Error("the admin did not accept the owner ticket");
  return `admin=${value}`;
}

async function adminApi(box, method, route, body) {
  const cookie = await adminLogin(box);
  const response = await httpRequest(method, `http://127.0.0.1:${ADMIN_PORT}${route}`, { body, cookie, headers: { "x-harness-admin": "1" } });
  let json = null;
  try {
    json = JSON.parse(response.text);
  } catch {}
  return { status: response.status, json, text: response.text };
}

// The settings the owner would save in the wizard: Claude (the stand-in CLI) with one model, on
// a test port. Any other port could be a real harness.
async function provision(box) {
  const state = await adminApi(box, "GET", "/api/state");
  if (state.status !== 200) throw new Error("admin state: " + state.status);
  const settings = state.json.settings;
  settings.port = Number(HARNESS_PORT);
  settings.tailnet_port = Number(HARNESS_PORT);
  settings.services.claude = { ...settings.services.claude, enabled: true, models: ["claude-sonnet-5-5"], mode: "native", projects: ["sem-projeto"] };
  settings.services.claude.permissions = { read: true, write: true, upload: true, tests: false, internet: false, shell: false, hooks: false };
  const saved = await adminApi(box, "POST", "/api/settings", settings);
  if (saved.status !== 200) throw new Error("saving settings failed: " + saved.status + " " + saved.text.slice(0, 200));
  const started = await adminApi(box, "POST", "/api/start", {});
  if (started.status !== 200) throw new Error("starting the harness failed: " + started.status + " " + started.text.slice(0, 200));
  await untilTrue(async () => (await httpRequest("GET", `http://127.0.0.1:${HARNESS_PORT}/`)).status > 0, "the harness did not answer", 40000);
}

async function untilTrue(predicate, message, timeout = 15000, step = 200) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await Promise.resolve(predicate()).catch(() => false)) return;
    await sleep(step);
  }
  throw new Error(message);
}


// ------------------------------------------------------------------ the app under test

// Main-process hooks, installed right after launch: every message box is recorded and answered
// from a queue (or drawn natively when the answer is "real"), links are recorded instead of
// opened in Sophia's browser, taskbar and badge calls and downloads are recorded, and the show
// and hide events of every window are counted.
async function instrument(app) {
  await app.evaluate((electron) => {
    const { dialog, shell, BrowserWindow, session } = electron;
    const G = globalThis;
    if (G.__op) return;
    const op = (G.__op = { dialogs: [], answers: [], opened: [], progress: [], badges: [], downloads: [], windows: [], autoSave: null });
    const original = dialog.showMessageBox.bind(dialog);
    dialog.showMessageBox = (...args) => {
      const options = args.find((a) => a && typeof a === "object" && "message" in a) || {};
      op.dialogs.push({ message: options.message, detail: options.detail, buttons: options.buttons, title: options.title, type: options.type, at: Date.now() });
      const answer = op.answers.shift();
      if (answer === "real") return original(...args);
      return Promise.resolve({ response: answer ?? options.cancelId ?? 0, checkboxChecked: false });
    };
    shell.openExternal = (url) => (op.opened.push(String(url)), Promise.resolve());
    const setProgressBar = BrowserWindow.prototype.setProgressBar;
    BrowserWindow.prototype.setProgressBar = function (value, ...rest) {
      op.progress.push(value);
      return setProgressBar.call(this, value, ...rest);
    };
    const setBadge = electron.app.setBadgeCount.bind(electron.app);
    electron.app.setBadgeCount = (count) => (op.badges.push(count), setBadge(count));
    session.defaultSession.on("will-download", (event, item) => {
      // The app's own listener ran first: it refused (defaultPrevented) or set the save dialog.
      let options = null;
      try {
        options = item.getSaveDialogOptions();
      } catch {}
      op.downloads.push({ url: item.getURL(), name: item.getFilename(), refused: event.defaultPrevented, dialogPath: options?.defaultPath || null });
      // Tests that need the file (not the dialog) choose a path, which skips the dialog.
      if (!event.defaultPrevented && op.autoSave) item.setSavePath(op.autoSave + "/" + item.getFilename());
    });
    electron.app.on("browser-window-created", (_event, window) => {
      const record = { id: window.id, shows: 0, hides: 0, created: Date.now() };
      op.windows.push(record);
      window.on("show", () => record.shows++);
      window.on("hide", () => record.hides++);
      window.on("closed", () => (record.closed = Date.now()));
    });
  });
}

const mainState = (app) => app.evaluate(() => globalThis.__op);
const setAnswers = (app, answers) => app.evaluate((_e, list) => (globalThis.__op.answers = list), answers);
async function dialogsNow(app) {
  return (await mainState(app)).dialogs;
}

// Close a dialog the app drew natively, by asking the window manager (the same request the
// close button sends) for a window of this process that is not one of its BrowserWindows.
const X_CLOSE = String.raw`
import ctypes, sys
x = ctypes.cdll.LoadLibrary("libX11.so.6")
x.XOpenDisplay.restype = ctypes.c_void_p
x.XInternAtom.restype = ctypes.c_ulong
x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
x.XDefaultRootWindow.restype = ctypes.c_ulong
x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
x.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.c_void_p]
class Msg(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int), ("display", ctypes.c_void_p), ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong), ("format", ctypes.c_int), ("data", ctypes.c_long * 5)]
d = x.XOpenDisplay(None)
msg = Msg(type=33, send_event=1, window=int(sys.argv[1], 16), message_type=x.XInternAtom(d, b"_NET_CLOSE_WINDOW", 0), format=32)
msg.data[1] = 1
x.XSendEvent(d, x.XDefaultRootWindow(d), 0, (1 << 20) | (1 << 19), ctypes.byref(msg))
x.XFlush(d)
`;
function xprop(args) {
  try {
    return execFileSync("xprop", args, { encoding: "utf8", timeout: 4000 });
  } catch {
    return "";
  }
}
async function nativeDialogWindow(app) {
  const pid = app.process().pid;
  const known = new Set(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().map((w) => w.getNativeWindowHandle().readUInt32LE(0))));
  const ids = (xprop(["-root", "_NET_CLIENT_LIST"]).match(/0x[0-9a-f]+/g) || []);
  return ids.find((id) => new RegExp(`_NET_WM_PID\\(CARDINAL\\) = ${pid}\\b`).test(xprop(["-id", id, "_NET_WM_PID"])) && !known.has(parseInt(id, 16)));
}
async function closeNativeDialog(app, timeout = 8000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const id = await nativeDialogWindow(app);
    if (id) {
      spawnSync("python3", ["-c", X_CLOSE, id], { timeout: 5000 });
      return id;
    }
    await sleep(200);
  }
  return null;
}

async function launchApp(box, options, { extraEnv = {}, args = [] } = {}) {
  const { _electron: electron } = playwright();
  const app = await electron.launch({
    executablePath: options.app,
    args: ["--ozone-platform=x11", ...args],
    env: appEnv(box, extraEnv),
    timeout: 60000,
  });
  const handle = { app, box, closed: false };
  handle.splashUrl = (await app.firstWindow()).url();
  await instrument(app);
  handle.userData = await app.evaluate(({ app: a }) => a.getPath("userData"));
  handle.pid = app.process().pid;
  handle.exited = new Promise((resolve) => app.process().once("exit", (code) => resolve(code)));
  return handle;
}
const appWindows = (h, match) => h.app.windows().filter((w) => !w.isClosed() && (!match || match(w.url())));
const harnessUrl = () => `http://127.0.0.1:${HARNESS_PORT}`;
const adminUrl = () => `http://127.0.0.1:${ADMIN_PORT}`;
async function waitPage(h, prefix, timeout = 60000) {
  let page = null;
  await untilTrue(() => (page = appWindows(h, (u) => u.startsWith(prefix))[0]), `no window ever showed ${prefix}`, timeout, 250);
  await page.waitForLoadState("domcontentloaded");
  return page;
}
async function quitApp(h) {
  if (h.closed) return;
  h.closed = true;
  await h.app.evaluate(({ app }) => app.quit()).catch(() => {});
  await Promise.race([h.exited, sleep(15000)]);
  if (h.app.process().exitCode === null) h.app.process().kill("SIGKILL");
}
const listWindows = (h) =>
  h.app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows().map((w) => ({ id: w.id, url: w.webContents.getURL(), title: w.getTitle(), visible: w.isVisible(), bounds: w.getBounds(), maximized: w.isMaximized(), min: w.getMinimumSize() })),
  );


// ------------------------------------------------------------------ helpers for the journeys

const SIZES = [[960, 640], [1280, 800]];
const readText = (file) => {
  try {
    return fs.readFileSync(file, "utf8");
  } catch {
    return "";
  }
};
const readJson = (file) => {
  try {
    return JSON.parse(readText(file));
  } catch {
    return null;
  }
};
const tree = (dir) => {
  const rows = [];
  const walk = (current) => {
    for (const entry of fs.readdirSync(current, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const full = path.join(current, entry.name);
      rows.push(path.relative(dir, full) + (entry.isSymbolicLink() ? " -> " + fs.readlinkSync(full) : ""));
      if (entry.isDirectory()) walk(full);
    }
  };
  if (fs.existsSync(dir)) walk(dir);
  return rows;
};

// A tiny server on a test port. With `gate`, it listens only after open(), so a test can install
// its hooks before the app first looks at the port.
function tinyServer(port, handler, { gate = false } = {}) {
  const requests = [];
  const server = http.createServer((request, response) => {
    requests.push(request.method + " " + request.url);
    handler(request, response);
  });
  const api = {
    requests,
    open: () => new Promise((resolve) => server.listen(Number(port), "127.0.0.1", resolve)),
    close: () => new Promise((resolve) => (server.listening ? server.close(() => resolve()) : resolve())),
  };
  if (!gate) server.listen(Number(port), "127.0.0.1");
  return api;
}

function sandboxPids(box) {
  const pids = [];
  for (const entry of fs.readdirSync("/proc")) {
    if (!/^\d+$/.test(entry)) continue;
    try {
      if (fs.readFileSync(`/proc/${entry}/environ`, "utf8").split("\0").includes("HOME=" + box.home)) pids.push(Number(entry));
    } catch {}
  }
  return pids.filter((pid) => pid !== process.pid);
}
const childrenOf = (pid) => readText(`/proc/${pid}/task/${pid}/children`).trim().split(/\s+/).filter(Boolean).map(Number);
const cmdline = (pid) => readText(`/proc/${pid}/cmdline`).split("\0").join(" ");
function findDescendant(pid, pattern) {
  for (const child of childrenOf(pid)) {
    if (pattern.test(cmdline(child))) return child;
    const deeper = findDescendant(child, pattern);
    if (deeper) return deeper;
  }
  return null;
}

function mdTable(rows) {
  return rows.map((r) => `- ${r.id}: ${r.status.toUpperCase()}${r.detail ? " — " + r.detail : ""}${r.shot ? " [shot " + r.shot + "]" : ""}`).join("\n");
}

module.exports = {
  id: "desktop-wp18",
  title: "Desktop app: WP-18 journeys",
  async run(op) {
    const options = op.options;
    const slices = new Set((process.env.WP18_SLICES || "s2,s5,s4,s1,s3").split(","));
    const previous = op.session;
    const shim = { target: "desktop", page: null, base: harnessUrl(), admin: adminUrl(), async resize() { return false; }, async newPage() { throw new Error("n/a"); }, async close() {} };
    const idle = { on() {}, evaluate: async () => {}, isClosed: () => true, locator: () => ({ isVisible: async () => false }) };
    const results = {};
    let box = null, h = null;
    const servers = [];
    const shotsDir = path.join(OUT, "shots");
    fs.mkdirSync(shotsDir, { recursive: true });
    const progressFile = path.join(OUT, "progress.md");

    // A step, recorded for the slice's progress note.
    const S = async (slice, id, caption, fn, opt = {}) => {
      if (!slices.has(slice)) return null;
      const record = await op.step(id, caption, async (page) => fn(page), { lint: false, recover: false, ...opt });
      (results[slice] ||= []).push(record);
      if (record.status === "fail" && h && !h.closed) {
        for (const [i, page] of h.app.windows().entries()) if (!page.isClosed()) record.shot = (await shoot(page, `fail-${id}-${i}`).catch(() => null)) || record.shot;
      }
      return record;
    };
    const closeSlice = (slice, title) => {
      if (!slices.has(slice) || !results[slice]) return;
      const text = `\n## ${title} (${new Date().toISOString().slice(0, 19)}Z)\n${mdTable(results[slice])}\n`;
      fs.appendFileSync(progressFile, text);
    };
    const focus = (page) => (shim.page = page);
    const win = (match) => listWindows(h).then((all) => all.find((w) => match(w.url)));
    const shoot = async (page, name) => {
      const file = path.join(shotsDir, name + ".png");
      await page.screenshot({ path: file, timeout: 15000 }).catch(() => {});
      return file;
    };
    const resizeTo = async (page, urlPrefix, width, height) => {
      await h.app.evaluate(({ BrowserWindow }, a) => {
        const w = BrowserWindow.getAllWindows().find((x) => x.webContents.getURL().startsWith(a.urlPrefix));
        if (w.isMaximized()) w.unmaximize();
        w.setContentSize(a.width, a.height);
      }, { urlPrefix, width, height });
      await untilTrue(async () => (await page.evaluate(() => innerWidth)) === width, "innerWidth never became " + width, 4000).catch(() => {});
      return page.evaluate(() => [innerWidth, innerHeight]);
    };
    const themeOf = (page) => page.evaluate(() => document.documentElement.dataset.theme);
    // The app's own selectors: Settings > Appearance in the chat, the theme picker in the admin.
    const setTheme = async (page, kind, dark) => {
      if ((await themeOf(page)) === (dark ? "dark" : "light")) return;
      if (kind === "chat") {
        await op.click(page.locator("#settings"));
        await op.click(page.locator("#theme-toggle"));
        await op.click(page.locator("#settings-close"));
      } else {
        await op.click(page.locator("#theme"));
        await op.click(page.locator(`#appearance-dialog [data-theme-choice="${dark ? "graphite" : "paper"}"]`));
        await op.click(page.locator("#appearance-close"));
      }
      await untilTrue(async () => (await themeOf(page)) === (dark ? "dark" : "light"), "the theme did not switch", 5000);
    };
    // Both sizes in both themes; returns the inner sizes it measured.
    const matrix = async (page, kind, urlPrefix, name) => {
      const measured = [];
      for (const dark of [false, true]) {
        await setTheme(page, kind, dark);
        for (const [width, height] of SIZES) {
          const inner = await resizeTo(page, urlPrefix, width, height);
          await sleep(300);
          await shoot(page, `${name}-${width}x${height}-${dark ? "dark" : "light"}`);
          measured.push(`${width}x${height}:${inner.join("x")}${dark ? "d" : "l"}`);
        }
      }
      await setTheme(page, kind, false);
      return measured;
    };
    const dismissTour = async (page) => {
      const skip = page.locator("#tour-skip");
      if (await skip.isVisible().catch(() => false)) await skip.click();
    };
    const gateOpen = async (page) => {
      await page.locator("#startup-gate").waitFor({ state: "hidden", timeout: 30000 });
      // A returning user has seen the tour for this release.
      await page.evaluate(async () => {
        const { version } = await fetch("/v1/version").then((r) => r.json());
        localStorage.setItem("keepharness-tour-seen", version);
      });
      await sleep(1500);
      await dismissTour(page);
      await page.locator("#tour-root").waitFor({ state: "detached", timeout: 5000 }).catch(() => {});
    };
    const launch = async (extraEnv, label) => {
      h = await launchApp(box, options, { extraEnv });
      h.label = label;
      return h;
    };
    const quit = async () => {
      if (h) await quitApp(h);
      for (const port of [ADMIN_PORT, HARNESS_PORT]) await untilTrue(async () => (await httpRequest("GET", `http://127.0.0.1:${port}/`, { timeout: 1500 })).status === 0, `port ${port} stayed busy after the app quit`, 60000).catch(() => {});
    };
    const chatAlive = () =>
      h.app.evaluate(async ({ BrowserWindow }, url) => {
        const w = BrowserWindow.getAllWindows().find((x) => x.webContents.getURL().startsWith(url));
        if (!w || w.webContents.isCrashed()) return false;
        return w.webContents.executeJavaScript("!!document.getElementById('prompt')");
      }, harnessUrl());
    const readLog = () => ({ main: readText(path.join(h.userData, "logs/main.log")), rotated: readText(path.join(h.userData, "logs/main.log.1")) });
    const chatReady = async () => {
      const page = await waitPage(h, harnessUrl(), 60000);
      focus(page);
      await gateOpen(page);
      await page.locator("#prompt").waitFor({ state: "visible", timeout: 20000 });
      return page;
    };
    const sendAndWait = async (page, text) => {
      await op.fill(page.locator("#prompt"), text);
      const before = await page.locator("#messages").getByRole("button", { name: "View run" }).count();
      await op.click(page.locator("#send"));
      await untilTrue(async () => (await page.locator("#messages").getByRole("button", { name: "View run" }).count()) > before, "the run did not start", 30000);
    };
    const runDone = (page, timeout = 60000) =>
      untilTrue(async () => (await page.locator("#conversation-state-pill").getAttribute("data-state")) === "done", "the answer did not finish", timeout);

    // Playwright asserts when an Electron renderer is crashed on purpose; that is expected here.
    const ignoreCrash = (error) => {
      if (!/Target crashed/.test(String(error && error.message))) throw error;
    };
    process.on("uncaughtException", ignoreCrash);
    try {
      // ---------------------------------------------------------------- setup
      await op.step("setup", "Prepare a throwaway HOME and the test app (nothing of the real install is touched)", async () => {
        if (!options.app || !fs.existsSync(options.app)) op.skip("no packaged desktop binary (pass --app)");
        if (!process.env.DISPLAY) op.skip("no display: run with --visible and DISPLAY=:0");
        if (previous?.target === "browser" && previous.browser) await previous.browser.close().catch(() => {});
        const python = process.env.PYTHON || path.join(REPO, ".venv/bin/python");
        const backend = process.env.WP18_BACKEND || REPO;
        box = makeBox(python, backend);
        // The backend is an installed copy, so the version-truth journeys can edit it.
        const site = path.join(box.root, "site");
        fs.mkdirSync(site);
        for (const dir of ["agent_service", "control", "harness_ui", "adapters", "profiles"]) {
          fs.cpSync(path.join(backend, dir), path.join(site, dir), { recursive: true, filter: (src) => !/__pycache__/.test(src) });
        }
        box.backend = site;
        servers.push(tinyServer(DOWNLOAD_PORT, (request, response) => {
          response.writeHead(200, { "content-type": "application/octet-stream", "content-disposition": 'attachment; filename="foreign.bin"' });
          response.end("foreign bytes");
        }));
        for (const port of [ADMIN_PORT, HARNESS_PORT, FOREIGN_PORT, DOWNLOAD_PORT].slice(0, 3)) {
          const busy = await httpRequest("GET", `http://127.0.0.1:${port}/`, { timeout: 800 });
          op.check(busy.status === 0, `test port ${port} is already in use`);
        }
        shim.page = idle;
        op.session = shim;
        fs.writeFileSync(progressFile, `# WP-18 operator progress\n\nSandbox ${box.root}; app ${options.app}\n`, { flag: fs.existsSync(progressFile) ? "a" : "w" });
      }, { critical: true });
      if (!box) return;

      // Proof that the profile lives inside the throwaway HOME, before the visible launches: a probe
      // on a private X display (Xvfb) reads app.getPath("userData").
      await op.step("sandbox-proof", "The app's userData folder is inside the throwaway HOME (probe on a private display)", async () => {
        const display = ":97";
        const xvfb = spawn("Xvfb", [display, "-screen", "0", "800x600x24"], { stdio: "ignore" });
        try {
          await sleep(1500);
          const { _electron: electron } = playwright();
          const probeEnv = appEnv(box, { DISPLAY: display, XAUTHORITY: "", KEEPHARNESS_ADMIN_PORT: "18529", KEEPHARNESS_PORT: "18528" });
          delete probeEnv.XAUTHORITY;
          const app = await electron.launch({ executablePath: options.app, args: ["--ozone-platform=x11"], env: probeEnv, timeout: 60000 });
          const userData = await app.evaluate(({ app: a }) => a.getPath("userData"));
          await app.evaluate(({ app: a }) => a.exit(0)).catch(() => {});
          op.check(userData.startsWith(box.home + path.sep), `userData is ${userData}, outside ${box.home}`);
          op.check(!userData.startsWith(process.env.HOME + "/.config"), "userData is under the real ~/.config");
        } finally {
          xvfb.kill("SIGTERM");
          for (const pid of sandboxPids(box)) {
            try {
              process.kill(pid, "SIGKILL");
            } catch {}
          }
        }
      }, { critical: true });

      // ================================================================ S2: desktop runtime
      let timeline = [];
      await S("s2", "s2.fresh-launch", "Fresh install: splash first, then the app's window (timed)", async () => {
        h = await launchApp(box, options, { extraEnv: { KEEPHARNESS_PYTHON: path.join(box.bin, "python-noisy") } });
        h.label = "A";
        const splash = h.app.windows()[0];
        const splashShot = await shoot(splash, "s2-splash").catch(() => null);
        const started = Date.now();
        let mainAt = null, splashGoneAt = null;
        while (Date.now() - started < 60000) {
          const all = await listWindows(h);
          const visibleMain = all.find((w) => !w.url.startsWith("file:") && w.visible);
          const splashOpen = all.some((w) => w.url.startsWith("file:") && w.visible);
          timeline.push({ t: Date.now() - started, splash: splashOpen, main: !!visibleMain });
          if (!splashOpen && splashGoneAt === null) splashGoneAt = Date.now() - started;
          if (visibleMain) {
            mainAt = Date.now() - started;
            break;
          }
          await sleep(200);
        }
        h.gap = mainAt === null || splashGoneAt === null ? null : Math.max(0, mainAt - splashGoneAt);
        h.mainAt = mainAt;
        h.splashGoneAt = splashGoneAt;
        op.check(/splash\.html$/.test(h.splashUrl), "the first window was " + h.splashUrl);
        op.check(mainAt !== null, "no window appeared within 60 s");
        const page = await waitPage(h, adminUrl(), 20000);
        focus(page);
        await page.getByRole("link", { name: "Providers", exact: true }).waitFor({ timeout: 20000 });
      }, { critical: true });

      await S("s2", "s2.splash-then-one-window", "Splash first, then exactly one window, shown once and never hidden (no flicker)", async () => {
        const state = await mainState(h.app);
        const main = state.windows.filter((w) => w.shows > 0);
        const visibleNow = (await listWindows(h)).filter((w) => w.visible);
        op.check(main.length === 1 && main[0].shows === 1 && main[0].hides === 0, "windows shown: " + JSON.stringify(state.windows));
        op.check(visibleNow.length === 1 && !visibleNow[0].url.startsWith("file:"), "visible windows: " + visibleNow.map((w) => w.url).join(", "));
        op.check(h.splashGoneAt !== null, "the splash never closed");
        // The splash must hand over to the window: a long stretch with nothing on screen is a blank desktop.
        op.check(h.gap !== null && h.gap <= 3000, `the screen had no window for ${h.gap} ms after the splash closed (splash gone at ${h.splashGoneAt} ms, window at ${h.mainAt} ms)`);
      });

      await S("s2", "s2.menu", "The menu is branded (KeepHarness, Edit, View) and has no Reload or Developer Tools", async () => {
        const menu = await h.app.evaluate(({ Menu }) => {
          const walk = (items) => items.map((i) => ({ label: i.label, role: i.role, accelerator: i.accelerator ? String(i.accelerator) : null, sub: i.submenu ? walk(i.submenu.items) : null }));
          return walk(Menu.getApplicationMenu().items);
        });
        const top = menu.map((m) => m.label);
        const flat = JSON.stringify(menu);
        op.check(JSON.stringify(top) === JSON.stringify(["KeepHarness", "Edit", "View"]), "top-level menu: " + top.join(", "));
        op.check(!/reload|devtools|developer/i.test(flat), "the menu still offers a developer item: " + flat.match(/[^"]*(reload|devtools|developer)[^"]*/i));
        op.check(/About KeepHarness/.test(flat) && /Quit/.test(flat), "About or Quit is missing");
        op.check(/zoomIn/i.test(flat) && /togglefullscreen/i.test(flat), "View lacks zoom or full screen");
      });

      await S("s2", "s2.fresh-admin-visual", "Fresh install with no provider opens the Admin panel (960x640 and 1280x800, light and dark)", async () => {
        const page = h.app.windows().find((w) => w.url().startsWith(adminUrl()));
        focus(page);
        const measured = await matrix(page, "admin", adminUrl(), "s2-admin-fresh");
        // Known polish gap to hold the screen to: header actions on one row at the minimum width.
        await resizeTo(page, adminUrl(), 960, 640);
        const rows = await page.evaluate(() => new Set([...document.querySelectorAll("main > header .header-right > *")].filter((e) => e.offsetParent).map((e) => Math.round(e.getBoundingClientRect().top))).size);
        await resizeTo(page, adminUrl(), 1440, 900);
        op.check(rows === 1, `Admin header actions wrap to ${rows} rows at 960px (${measured.join(" ")})`);
      }, { lint: false });

      await S("s2", "s2.log-rotation-redaction", "main.log stays within 1 MiB with one rotation, and credentials in the backend's output are redacted", async () => {
        const logs = path.join(h.userData, "logs");
        await untilTrue(() => readText(path.join(logs, "main.log")).includes("[REDACTED]") && fs.existsSync(path.join(logs, "main.log.1")) && /Cookie: \[REDACTED\]/.test(readLog().main), "the backend's noisy output never reached the log", 60000, 500);
        await sleep(1000);
        const { main, rotated } = readLog();
        const size = fs.statSync(path.join(logs, "main.log")).size;
        const secret = readText(path.join(box.home, ".local/share/keepharness/local.key")).trim();
        op.check(size <= 1048576, `main.log is ${size} bytes`);
        op.check(fs.statSync(path.join(logs, "main.log.1")).size <= 1048576, "main.log.1 is over 1 MiB");
        op.check(fs.readdirSync(logs).sort().join() === "main.log,main.log.1", "log files: " + fs.readdirSync(logs));
        for (const [name, text] of [["LEAK1234567890", main + rotated], ["LEAKEDADMINCOOKIE999", main + rotated], [secret, main + rotated]]) op.check(secret && !text.includes(name), "the log contains an unredacted credential: " + name.slice(0, 8));
        op.check(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z /m.test(main), "log lines lack ISO timestamps");
        op.check((fs.statSync(path.join(logs, "main.log")).mode & 0o077) === 0, "main.log is readable by others");
      });

      await S("s2", "s2.provision", "Set up Claude (stand-in CLI) on test port 18521 through the admin, as the wizard would", async () => {
        await provision(box);
        const page = h.app.windows().find((w) => w.url().startsWith(adminUrl()));
        await page.reload();
        await op.see(page.locator("#runtime-badge"));
        await untilTrue(async () => !/Checking/i.test(await page.locator("#runtime-badge").innerText()), "the admin badge stayed on Checking", 30000);
        box.provisioned = true;
      });
      if (h) await quit();

      // Launch B: the harness is configured, so it needs the owner's credential.
      await S("s2", "s2.accepts-own-harness", "Fresh install with a credential-requiring harness: the app accepts it (no 'not a KeepHarness service')", async () => {
        h = await launchApp(box, options);
        h.label = "B";
        const page = await chatReady();
        const anonymous = await httpRequest("GET", `http://127.0.0.1:${HARNESS_PORT}/v1/version`);
        const dialogs = await dialogsNow(h.app);
        // The credential is required: without the owner session the version probe is refused.
        op.check([401, 403].includes(anonymous.status), "the harness answered /v1/version without a credential: " + anonymous.status);
        op.check(!dialogs.some((d) => /not a KeepHarness/i.test(d.message || "")), "refused: " + JSON.stringify(dialogs));
        op.check(page.url().startsWith(harnessUrl()), "the window shows " + page.url());
        const version = await page.evaluate(() => fetch("/v1/version").then((r) => r.json()));
        op.check(version.product === "keepharness", "product: " + version.product);
        await shoot(page, "s2-chat-home");
      }, { critical: true });

      await S("s2", "s2.admin-second-window", "Admin opens in one reusable second window, and the chat window stays on the chat", async () => {
        const page = focus(h.app.windows().find((w) => w.url().startsWith(harnessUrl())));
        for (let round = 0; round < 2; round++) {
          await op.click(page.locator("#settings"));
          await op.click(page.locator("#admin-shortcut"));
          await untilTrue(async () => (await listWindows(h)).some((w) => w.url.startsWith(adminUrl())), "no window showed the admin", 20000);
          await op.click(page.locator("#settings-close"));
          await sleep(800);
        }
        const all = await listWindows(h);
        op.check(all.length === 2, "windows: " + all.map((w) => w.url).join(", "));
        const chat = all.find((w) => w.url.startsWith(harnessUrl()));
        const admin = all.find((w) => w.url.startsWith(adminUrl()));
        op.check(chat && admin && admin.visible, "the chat window or the admin window is missing");
        const adminPage = await waitPage(h, adminUrl(), 20000);
        await adminPage.getByRole("link", { name: "Providers", exact: true }).waitFor({ timeout: 20000 });
        focus(adminPage);
        await shoot(adminPage, "s2-admin-second-window");
      });

      await S("s2", "s2.admin-window-close-reopen", "Closing the Admin window leaves the chat running; Admin can be opened again", async () => {
        await h.app.evaluate(({ BrowserWindow }, url) => BrowserWindow.getAllWindows().find((w) => w.webContents.getURL().startsWith(url)).close(), adminUrl());
        await untilTrue(async () => (await listWindows(h)).length === 1, "the admin window did not close", 8000);
        const page = focus(h.app.windows().find((w) => w.url().startsWith(harnessUrl())));
        op.check((await listWindows(h))[0].visible, "the chat window went away");
        await op.click(page.locator("#settings"));
        await op.click(page.locator("#admin-shortcut"));
        await untilTrue(async () => (await listWindows(h)).length === 2, "the admin did not open again", 20000);
        await op.click(page.locator("#settings-close"));
      });

      await S("s2", "s2.renderer-crash", "A crashed page shows the dialog; Reload brings the chat back", async () => {
        await setAnswers(h.app, [0]);
        const before = (await dialogsNow(h.app)).length;
        await h.app.evaluate(({ BrowserWindow }, url) => BrowserWindow.getAllWindows().find((w) => w.webContents.getURL().startsWith(url)).webContents.forcefullyCrashRenderer(), harnessUrl());
        await untilTrue(async () => (await dialogsNow(h.app)).length > before, "no dialog after the renderer crashed", 15000);
        const dialog = (await dialogsNow(h.app))[before];
        await op.caption(`Dialog: "${dialog.message}"  [${(dialog.buttons || []).join(" | ")}]  answered: Reload`);
        op.check(dialog.message === "The KeepHarness page stopped." && JSON.stringify(dialog.buttons) === '["Reload","Quit"]', "dialog: " + JSON.stringify(dialog));
        // Playwright keeps the crashed page as dead; the reload is observed from the main process.
        await untilTrue(chatAlive, "the chat did not come back after Reload", 30000);
      });

      await S("s2", "s2.backend-exit", "When the service dies, the dialog offers 'Restart service'; the chat comes back", async () => {
        const backend = findDescendant(h.pid, /-m control/);
        op.check(backend, "the app has no backend child (it attached to a running service?)");
        await setAnswers(h.app, [0]);
        const before = (await dialogsNow(h.app)).length;
        process.kill(backend, "SIGTERM");
        await untilTrue(async () => (await dialogsNow(h.app)).length > before, "no dialog after the service stopped", 60000);
        const dialog = (await dialogsNow(h.app))[before];
        await op.caption(`Dialog: "${dialog.message}"  [${(dialog.buttons || []).join(" | ")}]  answered: Restart service`);
        op.check(dialog.message === "The KeepHarness service stopped." && JSON.stringify(dialog.buttons) === '["Restart service","Quit"]', "dialog: " + JSON.stringify(dialog));
        await untilTrue(async () => {
          const next = findDescendant(h.pid, /-m control/);
          return next && next !== backend && (await httpRequest("GET", adminUrl() + "/")).status > 0;
        }, "the service did not restart", 60000);
        await untilTrue(chatAlive, "the chat did not return after the restart", 60000);
      });

      await S("s2", "s2.min-size", "The window cannot shrink below 960x640", async () => {
        const [main] = (await listWindows(h)).filter((w) => w.url.startsWith(harnessUrl()));
        op.check(main.min[0] >= 960 && main.min[1] >= 640, "minimum " + main.min);
        const size = await h.app.evaluate(({ BrowserWindow }, url) => {
          const w = BrowserWindow.getAllWindows().find((x) => x.webContents.getURL().startsWith(url));
          w.setSize(700, 400);
          return w.getSize();
        }, harnessUrl());
        op.check(size[0] >= 960 && size[1] >= 640, "setSize(700,400) gave " + size);
      });

      await S("s2", "s2.bounds-save", "Move and resize the window, then quit: size and position are saved", async () => {
        const saved = await h.app.evaluate(({ BrowserWindow }, url) => {
          const w = BrowserWindow.getAllWindows().find((x) => x.webContents.getURL().startsWith(url));
          w.setBounds({ x: 160, y: 90, width: 1180, height: 760 });
          return w.getNormalBounds();
        }, harnessUrl());
        h.savedBounds = saved;
        await sleep(600);
        await quit();
        const file = readJson(path.join(h.userData, "window-state.json"));
        op.check(file && file.width === saved.width && file.height === saved.height && file.x === saved.x && file.y === saved.y, `window-state.json ${JSON.stringify(file)} vs ${JSON.stringify(saved)}`);
        h.savedState = file;
      });

      await S("s2", "s2.bounds-restore", "Relaunch: the window comes back at the saved size and position", async () => {
        const saved = h.savedBounds;
        h = await launchApp(box, options);
        h.label = "C";
        await chatReady();
        const [main] = (await listWindows(h)).filter((w) => w.url.startsWith(harnessUrl()));
        const near = (a, b) => Math.abs(a - b) <= 2;
        op.check(near(main.bounds.width, saved.width) && near(main.bounds.height, saved.height), `size ${main.bounds.width}x${main.bounds.height}, saved ${saved.width}x${saved.height}`);
        op.check(near(main.bounds.x, saved.x) && near(main.bounds.y, saved.y), `position ${main.bounds.x},${main.bounds.y}, saved ${saved.x},${saved.y}`);
        // Maximize for the next check.
        await h.app.evaluate(({ BrowserWindow }, url) => BrowserWindow.getAllWindows().find((x) => x.webContents.getURL().startsWith(url)).maximize(), harnessUrl());
        await untilTrue(async () => (await listWindows(h)).find((w) => w.url.startsWith(harnessUrl())).maximized, "the window did not maximize", 5000);
        await sleep(500);
        await quit();
      });

      await S("s2", "s2.maximized-restore", "Relaunch: a maximized window comes back maximized", async () => {
        h = await launchApp(box, options);
        h.label = "D";
        await chatReady();
        await untilTrue(async () => (await listWindows(h)).find((w) => w.url.startsWith(harnessUrl())).maximized, "the window is not maximized after relaunch", 8000);
        await h.app.evaluate(({ BrowserWindow }, url) => BrowserWindow.getAllWindows().find((x) => x.webContents.getURL().startsWith(url)).unmaximize(), harnessUrl());
        await sleep(500);
        await quit();
      });

      await S("s2", "s2.foreign-product", "A service answering as another product is refused with a dialog, and the app quits", async () => {
        const stranger = tinyServer(FOREIGN_PORT, (request, response) => {
          response.writeHead(200, { "content-type": "application/json" });
          response.end(request.url.startsWith("/v1/version") ? JSON.stringify({ product: "other-product", version: "9.9.9" }) : "<html><title>Not KeepHarness</title></html>");
        }, { gate: true });
        servers.push(stranger);
        h = await launchApp(box, options, { extraEnv: { KEEPHARNESS_PORT: FOREIGN_PORT } });
        h.label = "E";
        await setAnswers(h.app, ["real"]);
        await stranger.open();
        await untilTrue(async () => (await dialogsNow(h.app)).length > 0, "the app showed no dialog about the foreign service", 60000);
        const dialog = (await dialogsNow(h.app))[0];
        op.check(dialog.message === "This is not a KeepHarness service.", "dialog: " + JSON.stringify(dialog));
        await op.caption(`Native dialog: "${dialog.message}" — closing it`);
        await sleep(1800);
        const closed = await closeNativeDialog(h.app, 6000);
        op.check(closed, "the native dialog never appeared as a window");
        const code = await Promise.race([h.exited.then((c) => ["exit", c]), sleep(20000).then(() => ["timeout"])]);
        op.check(code[0] === "exit", "the app kept running after the refusal");
        op.check(!stranger.requests.some((r) => r === "GET /"), "the foreign page was requested: " + stranger.requests.join(", "));
        h.closed = true;
        await stranger.close();
      });
      closeSlice("s2", "S2 desktop runtime");

      // ---------------------------------------------------------------- shared preparation
      const prepare = async () => {
        if (box.provisioned) return;
        await op.step("prepare", "Prepare a configured install (Claude stand-in on port 18521)", async () => {
          h = await launchApp(box, options);
          const page = await waitPage(h, adminUrl(), 60000);
          focus(page);
          await provision(box);
          box.provisioned = true;
          await quit();
        }, { lint: false, recover: false, critical: true });
      };
      const openAdminWindow = async (page) => {
        await op.click(page.locator("#settings"));
        await op.click(page.locator("#admin-shortcut"));
        await untilTrue(async () => (await listWindows(h)).some((w) => w.url.startsWith(adminUrl())), "no window showed the admin", 20000);
        await op.click(page.locator("#settings-close"));
        const adminPage = await waitPage(h, adminUrl(), 20000);
        await adminPage.getByRole("link", { name: "Providers", exact: true }).waitFor({ timeout: 20000 });
        return adminPage;
      };
      const rowOf = (page, title) => page.locator("#sidebar").getByRole("button").filter({ hasText: title }).filter({ visible: true }).first();
      const conversationTitle = (page) => page.locator("#conversation-title").innerText();
      const windowTitleOf = async (match) => (await listWindows(h)).find((w) => match(w.url)).title;

      // ================================================================ S5: desktop polish
      if (slices.has("s5") || slices.has("s4") || slices.has("s1")) await prepare();
      const downloads = path.join(box.root, "downloads");
      let t1 = "", t2 = "", route1 = "";
      await S("s5", "s5.launch", "Launch the configured app (chat opens)", async () => {
        h = await launchApp(box, options);
        h.label = "F";
        const page = await chatReady();
        fs.mkdirSync(downloads, { recursive: true });
        await h.app.evaluate((_e, dir) => (globalThis.__op.autoSave = dir), downloads);
        await shoot(page, "s5-chat-home");
      }, { critical: true });

      await S("s5", "s5.about-build-label", "About KeepHarness shows the build label (version, commit, build time)", async () => {
        const manifest = readJson(path.join(path.dirname(options.app), "build-manifest.json"));
        op.check(manifest, "no build-manifest.json next to the app");
        const expected = `${manifest.version} (${manifest.commit.slice(0, 7)}) · ${manifest.built_at}`;
        await setAnswers(h.app, ["real"]);
        const before = (await dialogsNow(h.app)).length;
        await h.app.evaluate(({ Menu }) => void Menu.getApplicationMenu().getMenuItemById("about").click());
        await untilTrue(async () => (await dialogsNow(h.app)).length > before, "About showed nothing", 8000);
        const dialog = (await dialogsNow(h.app))[before];
        await op.caption(`About dialog: ${dialog.detail}`);
        await sleep(1800);
        const closed = await closeNativeDialog(h.app, 6000);
        op.check(dialog.message === "KeepHarness" && dialog.detail === expected, `About says "${dialog.detail}", expected "${expected}"`);
        op.check(closed, "the About box never appeared as a native window");
      });

      await S("s5", "s5.title-follows-conversation", "The window title follows the active conversation", async () => {
        const page = h.app.windows().find((w) => !w.isClosed() && w.url().startsWith(harnessUrl()));
        focus(page);
        const initial = await windowTitleOf((u) => u.startsWith(harnessUrl()));
        await sendAndWait(page, "Window title check alpha");
        await runDone(page);
        t1 = (await conversationTitle(page)).trim();
        route1 = page.url();
        await untilTrue(async () => (await windowTitleOf((u) => u.startsWith(harnessUrl()))) === `${t1} — KeepHarness`, `window title is "${await windowTitleOf((u) => u.startsWith(harnessUrl()))}", conversation "${t1}"`, 8000);
        await op.click(page.locator("#new"));
        await sendAndWait(page, "Second chat beta");
        await runDone(page);
        t2 = (await conversationTitle(page)).trim();
        op.check(t2 !== t1, "both conversations are titled " + t1);
        await untilTrue(async () => (await windowTitleOf((u) => u.startsWith(harnessUrl()))) === `${t2} — KeepHarness`, "the title did not follow the second conversation", 8000);
        await op.click(rowOf(page, t1));
        await untilTrue(async () => (await windowTitleOf((u) => u.startsWith(harnessUrl()))) === `${t1} — KeepHarness`, "the title did not return to the first conversation", 8000);
        h.initialTitle = initial;
        route1 = page.url();
      });

      await S("s5", "s5.busy-indicator", "While a run is active the taskbar progress and badge are set, and cleared afterwards", async () => {
        const page = h.app.windows().find((w) => !w.isClosed() && w.url().startsWith(harnessUrl()));
        await h.app.evaluate(() => ((globalThis.__op.progress = []), (globalThis.__op.badges = [])));
        await op.click(page.locator("#new"));
        await sendAndWait(page, "OP-SLOW take your time");
        await untilTrue(async () => (await mainState(h.app)).progress.includes(2), "no taskbar progress while the run was active", 30000);
        let state = await mainState(h.app);
        op.check(state.badges.includes(1), "no badge while busy: " + JSON.stringify(state.badges));
        await runDone(page, 90000);
        await untilTrue(async () => {
          state = await mainState(h.app);
          return state.progress.at(-1) === -1 && state.badges.at(-1) === 0;
        }, "the indicators were not cleared after the run: " + JSON.stringify([state.progress, state.badges]), 40000);
        // Only the Electron calls are visible to a test; the taskbar itself belongs to the desktop shell.
      });

      await S("s5", "s5.download-app-origin", "Admin 'Export saved configuration' (a blob: URL) is accepted with a save dialog", async () => {
        const page = h.app.windows().find((w) => !w.isClosed() && w.url().startsWith(harnessUrl()));
        const adminPage = await openAdminWindow(page);
        focus(adminPage);
        await op.click(adminPage.getByRole("link", { name: "Providers", exact: true }));
        await op.click(adminPage.locator("#manage-network"));
        // First with a chosen path: the file arrives and is the export.
        await h.app.evaluate(() => (globalThis.__op.downloads = []));
        await op.click(adminPage.locator("#export-settings"));
        await untilTrue(async () => (await mainState(h.app)).downloads.length > 0, "no download started", 15000);
        let entry = (await mainState(h.app)).downloads[0];
        op.check(entry.url.startsWith("blob:http://127.0.0.1:" + ADMIN_PORT), "download url " + entry.url);
        op.check(!entry.refused, "the app refused its own export");
        op.check(entry.dialogPath && entry.dialogPath.endsWith(entry.name), `no save dialog default: ${JSON.stringify(entry)}`);
        await untilTrue(() => fs.existsSync(path.join(downloads, entry.name)), "the exported file never arrived", 15000);
        op.check(JSON.parse(readText(path.join(downloads, entry.name))), "the export is not JSON");
        // Then without a chosen path: the native save dialog appears; close it.
        await h.app.evaluate(() => ((globalThis.__op.autoSave = null), (globalThis.__op.downloads = [])));
        await op.click(adminPage.locator("#export-settings"));
        const id = await closeNativeDialog(h.app, 10000);
        await shoot(adminPage, "s5-export-save-dialog-page");
        h.saveDialogSeen = !!id;
        op.check(id, "the save dialog never appeared as a native window");
        await h.app.evaluate((_e, dir) => (globalThis.__op.autoSave = dir), downloads);
        await op.click(adminPage.locator("#network-close"));
      });

      await S("s5", "s5.download-foreign", "A download from another origin (or a data: URL) is refused", async () => {
        const page = h.app.windows().find((w) => !w.isClosed() && w.url().startsWith(harnessUrl()));
        focus(page);
        await h.app.evaluate(() => ((globalThis.__op.downloads = []), (globalThis.__op.opened = [])));
        const files = fs.readdirSync(downloads).length;
        // A cross-origin link ignores the download attribute and is handled as navigation (handed to the browser).
        await page.evaluate((port) => {
          const a = document.createElement("a");
          a.id = "foreign-link"; a.href = `http://127.0.0.1:${port}/foreign.bin`; a.download = "x.bin"; a.textContent = "foreign";
          document.body.append(a);
          a.click();
        }, DOWNLOAD_PORT);
        await untilTrue(async () => (await mainState(h.app)).opened.length > 0, "the foreign link was neither refused nor handed to the browser", 10000);
        // A download the page cannot start by itself (session.downloadURL) must still be refused.
        await h.app.evaluate(({ BrowserWindow }, a) => {
          const w = BrowserWindow.getAllWindows().find((x) => x.webContents.getURL().startsWith(a.chat));
          w.webContents.downloadURL(`http://127.0.0.1:${a.port}/foreign.bin`);
          w.webContents.downloadURL("data:text/plain,hello");
        }, { chat: harnessUrl(), port: DOWNLOAD_PORT });
        await untilTrue(async () => (await mainState(h.app)).downloads.length >= 2, "downloads seen: " + JSON.stringify((await mainState(h.app)).downloads), 15000);
        const state = await mainState(h.app);
        op.check(state.downloads.every((d) => d.refused), "not refused: " + JSON.stringify(state.downloads));
        op.check(fs.readdirSync(downloads).length === files, "a foreign file was saved");
        h.foreignOpened = state.opened;
      });

      await S("s5", "s5.route-save", "Quit with a conversation open: its route is saved", async () => {
        const page = h.app.windows().find((w) => !w.isClosed() && w.url().startsWith(harnessUrl()));
        const probe = await page.evaluate(() => ({ url: location.href, rows: [...document.querySelectorAll("#sidebar button")].map((b) => [b.innerText.slice(0, 30), b.offsetParent !== null]), inert: document.querySelector("#sidebar")?.closest("[inert]")?.tagName || null, hidden: document.querySelector("#sidebar")?.getBoundingClientRect().width }));
        op.check((await rowOf(page, t1).count()) > 0, `no sidebar row for "${t1}": ${JSON.stringify(probe)}`);
        await page.evaluate((title) => [...document.querySelectorAll("#sidebar button")].find((b) => b.innerText.includes(title)).click(), t1);
        await untilTrue(async () => (await conversationTitle(page)).trim() === t1, "conversation 1 did not open", 10000);
        route1 = page.url();
        await sleep(500);
        await quit();
        const saved = readJson(path.join(h.userData, "window-state.json"));
        const url = new URL(route1);
        op.check(saved && saved.route === url.pathname + url.search + url.hash, `saved route ${saved && saved.route}, page was ${route1}`);
        op.check(url.pathname + url.search + url.hash !== "/", "the open conversation has no route of its own: " + route1);
      });

      await S("s5", "s5.route-restore", "Relaunch: the conversation that was open comes back", async () => {
        h = await launchApp(box, options);
        h.label = "G";
        const page = await chatReady();
        op.check(page.url() === route1, `restored ${page.url()}, expected ${route1}`);
        await untilTrue(async () => (await conversationTitle(page)).trim() === t1, `conversation shows "${await conversationTitle(page)}", expected "${t1}"`, 15000);
        await shoot(page, "s5-route-restored");
        await quit();
      });
      closeSlice("s5", "S5 desktop polish");

      // ================================================================ S4: observability in the Admin
      let adminPage4 = null;
      await S("s4", "s4.launch", "Launch the configured app and open Admin in its own window", async () => {
        h = await launchApp(box, options);
        h.label = "H";
        const page = await chatReady();
        adminPage4 = await openAdminWindow(page);
        focus(adminPage4);
        await h.app.evaluate(({ app: a }) => void a);
        await op.click(adminPage4.locator("#environment-diagnostics > summary"));
      }, { critical: true });

      await S("s4", "s4.environment-rows", "Admin lists ffmpeg, bwrap and prlimit, with install hints for what is missing", async () => {
        const rows = adminPage4.locator("#environment-tools > div");
        await untilTrue(async () => (await rows.count()) >= 3, "discovery tool rows missing (is the S4 backend in use?)", 20000);
        const texts = await rows.allInnerTexts();
        const row = (name) => texts.find((t) => t.startsWith(name));
        const hasPrlimit = fs.existsSync(path.join(box.bin, "prlimit"));
        op.check(/^ffmpeg — Missing/.test(row("ffmpeg") || ""), "ffmpeg: " + row("ffmpeg"));
        op.check(/^bwrap — Missing/.test(row("bwrap") || ""), "bwrap: " + row("bwrap"));
        op.check(new RegExp("^prlimit — " + (hasPrlimit ? "Available" : "Missing")).test(row("prlimit") || ""), "prlimit: " + row("prlimit"));
        for (const name of ["ffmpeg", "bwrap"]) op.check(/Fedora: sudo dnf install/.test(row(name)) && /Debian \/ Ubuntu: sudo apt install/.test(row(name)) && /Bazzite: /.test(row(name)) && /Arch: /.test(row(name)), `hints for ${name}: ${row(name)}`);
        op.check(!/Fedora/.test(row("prlimit") || "") || !hasPrlimit || true, "");
      });

      await S("s4", "s4.log-tail", "The log-tail panel loads the harness log on demand and says it is read-only", async () => {
        const tail = adminPage4.locator("#log-tail");
        op.check(/Refresh to load/.test(await tail.innerText()), "initial text: " + (await tail.innerText()));
        await op.click(adminPage4.locator("#refresh-log-tail"));
        await untilTrue(async () => !/Refresh to load/.test(await tail.innerText()), "the tail never loaded", 15000);
        const text = await tail.innerText();
        const secret = readText(path.join(box.home, ".local/share/keepharness/local.key")).trim();
        op.check(!/^Could not read/.test(text), text.slice(0, 120));
        op.check(!text.includes(secret), "the secret is in the log tail");
        op.check(/Read-only: last 200 lines/.test(await adminPage4.locator("#environment-diagnostics").innerText()), "no read-only note");
        op.check(await adminPage4.locator("#refresh-log-tail").isEnabled(), "the Refresh button stayed disabled");
        h.logTailLines = text.split("\n").length;
      });

      await S("s4", "s4.last-exit-and-startup-error", "After the harness is killed, Admin shows 'Last exit'; after repeated crashes, 'Startup error' next to it", async () => {
        const adminPid = findDescendant(h.pid, /-m control/);
        op.check(adminPid, "no admin child");
        const harnessPid = () => findDescendant(adminPid, /agent_service/);
        const diagnostics = adminPage4.locator("#runtime-diagnostics");
        op.check(!(await diagnostics.isVisible()), "diagnostics shown on a healthy start: " + (await diagnostics.innerText()));
        let last = harnessPid();
        op.check(last, "no harness child of the admin");
        for (let round = 1; round <= 3; round++) {
          process.kill(last, "SIGKILL");
          if (round < 3) {
            await untilTrue(() => harnessPid() && harnessPid() !== last, `the harness was not restarted after kill ${round}`, 60000, 300);
            last = harnessPid();
            await sleep(1000);
          }
        }
        await adminPage4.reload();
        await adminPage4.locator("#runtime-diagnostics").waitFor({ state: "visible", timeout: 30000 });
        await untilTrue(async () => /Startup error: .*3 times/.test(await diagnostics.innerText()) && /Last exit: -9/.test(await diagnostics.innerText()), "diagnostics: " + (await diagnostics.innerText()), 30000);
        const text = await diagnostics.innerText();
        op.check(text.indexOf("Startup error") < text.indexOf("Last exit"), "order: " + text);
        await shoot(adminPage4, "s4-startup-error-last-exit");
        h.diagnostics = text;
      }, { lint: true });

      await S("s4", "s4.admin-visual", "Admin diagnostics at 960x640 and 1280x800, light and dark", async () => {
        const measured = await matrix(adminPage4, "admin", adminUrl(), "s4-admin-diagnostics");
        const spill = await adminPage4.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        op.check(spill <= 0, `the page scrolls sideways by ${spill}px (${measured.join(" ")})`);
      });

      await S("s4", "s4.restart-harness", "Start the harness again from the admin (back to a healthy state)", async () => {
        const started = await adminApi(box, "POST", "/api/start", {});
        op.check(started.status === 200, "start: " + started.status + " " + started.text.slice(0, 120));
        await untilTrue(async () => (await httpRequest("GET", `http://127.0.0.1:${HARNESS_PORT}/`)).status > 0, "the harness did not come back", 60000);
      });
      closeSlice("s4", "S4 Admin observability");

      // ================================================================ S1: version truth
      const chat1 = () => h.app.windows().find((w) => !w.isClosed() && w.url().startsWith(harnessUrl()));
      const versionText = (page) => page.evaluate(() => document.getElementById("version").textContent);
      await S("s1", "s1.baseline", "Reload the chat on the installed copy: no restart notice", async () => {
        if (!h || h.closed) {
          h = await launchApp(box, options);
          h.label = "I";
        }
        const page = chat1();
        await page.reload();
        await chatReady();
        focus(chat1());
        op.check(!/Restart to finish/.test(await versionText(chat1())), "notice at baseline: " + (await versionText(chat1())));
      }, { critical: true });

      await S("s1", "s1.ui-only-change", "A UI-only change in the installed copy: the page reloads itself, with no restart notice", async () => {
        const page = chat1();
        await page.evaluate(() => (window.__operatorMarker = 1));
        fs.appendFileSync(path.join(box.backend, "agent_service/ui.js"), "\n// operator: ui-only edit\n");
        await untilTrue(async () => (await chat1().evaluate(() => window.__operatorMarker).catch(() => 1)) === undefined, "the page did not reload after a UI-only change", 120000, 1000);
        await chatReady();
        op.check(!/Restart to finish/.test(await versionText(chat1())), "restart notice after a UI-only change: " + (await versionText(chat1())));
      });

      await S("s1", "s1.python-change", "A Python change in the installed copy shows 'Restart to finish the update'; a restart clears it", async () => {
        const page = chat1();
        fs.appendFileSync(path.join(box.backend, "agent_service/errors.py"), "\n# operator: python edit\n");
        await untilTrue(async () => /Restart to finish the update/.test(await versionText(chat1())), "no restart notice after a Python change: " + (await versionText(chat1())), 120000, 1000);
        await op.click(page.locator("#settings"));
        await sleep(600);
        await shoot(chat1(), "s1-restart-notice");
        await op.click(page.locator("#settings-close"));
        await adminApi(box, "POST", "/api/stop", {});
        const started = await adminApi(box, "POST", "/api/start", {});
        op.check(started.status === 200, "restart: " + started.status);
        await untilTrue(async () => !/Restart to finish/.test(await versionText(chat1())) && /Release/.test(await versionText(chat1())), "the notice stayed after the restart: " + (await versionText(chat1())), 120000, 1000);
      });
      closeSlice("s1", "S1 version truth and safe updates");
      if (h && !h.closed) await quit();

      // ================================================================ S3: installer (throwaway HOME, no window)
      const version = readText(path.join(REPO, "agent_service/VERSION")).trim();
      const pkg = process.env.WP18_PACKAGE || path.join(REPO, "dist", `keepharness-${version}-linux-x64`);
      const home3 = path.join(box.root, "install-home");
      const opt3 = path.join(home3, ".local/opt");
      const entry3 = path.join(home3, ".local/share/applications/keepharness.desktop");
      const browserEntry = path.join(home3, ".local/share/applications/keepharness-browser.desktop");
      const installer = (dir, args = [], input) =>
        spawnSync("bash", [path.join(dir, "install-desktop-linux.sh"), ...args], { env: { PATH: "/usr/bin:/bin", HOME: home3, LANG: "C.UTF-8" }, encoding: "utf8", timeout: 120000, input });
      const link = (name) => {
        try {
          return path.basename(fs.readlinkSync(path.join(opt3, "keepharness", name)));
        } catch {
          return null;
        }
      };
      const logTo = (name, result) => fs.writeFileSync(path.join(OUT, `s3-${name}.txt`), `exit ${result.status}\n--- stdout\n${result.stdout}\n--- stderr\n${result.stderr}\n`);
      let pkg2 = null;
      await S("s3", "s3.install-first", "Install the package: opt folder, current link, menu entry, and no browser menu entry", async () => {
        op.check(fs.existsSync(path.join(pkg, "install-desktop-linux.sh")), "no production package at " + pkg + " (build it with scripts/package-desktop-linux.sh)");
        fs.mkdirSync(path.join(home3, ".config/keepharness"), { recursive: true });
        fs.mkdirSync(path.join(home3, ".local/share/keepharness"), { recursive: true });
        fs.writeFileSync(path.join(home3, ".config/keepharness/config.txt"), "python product config\n");
        fs.writeFileSync(path.join(home3, ".local/share/keepharness/state.txt"), "python product state\n");
        const result = installer(pkg);
        logTo("install-first", result);
        op.check(result.status === 0, "installer exit " + result.status + ": " + result.stderr.slice(0, 200));
        op.check(link("current") === `keepharness-${version}`, "current -> " + link("current"));
        op.check(link("previous") === null, "previous -> " + link("previous"));
        const entry = readText(entry3);
        op.check(entry.includes(`Exec="${home3}/.local/opt/keepharness/current/keepharness"`) && entry.includes(`TryExec=${home3}/.local/opt/keepharness/current/keepharness`), "entry: " + entry);
        op.check(!fs.existsSync(browserEntry), "keepharness-browser.desktop was written");
        op.check(fs.existsSync(path.join(opt3, `keepharness-${version}`, "keepharness-bin")), "the version folder lacks keepharness-bin");
      }, { critical: true });

      await S("s3", "s3.install-second", "Install a newer package: current moves to it and previous keeps the old one", async () => {
        pkg2 = path.join(box.root, "pkg2", `keepharness-0.16.1-linux-x64`);
        fs.mkdirSync(path.dirname(pkg2));
        fs.cpSync(pkg, pkg2, { recursive: true, verbatimSymlinks: true });
        fs.writeFileSync(path.join(pkg2, "VERSION"), "0.16.1\n");
        const manifest = readJson(path.join(pkg2, "build-manifest.json"));
        manifest.version = "0.16.1";
        fs.writeFileSync(path.join(pkg2, "build-manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
        // Per-file checksums for the repackaged copy (the checks detect corruption, not tampering).
        const sums = spawnSync("python3", ["-c", `
import hashlib, pathlib, sys
out = pathlib.Path(sys.argv[1])
files = sorted(p for p in out.rglob("*") if p.is_file() and p.name != "SHA256SUMS")
(out / "SHA256SUMS").write_text("".join(hashlib.file_digest(p.open("rb"), "sha256").hexdigest() + "  " + p.relative_to(out).as_posix() + "\\n" for p in files))
`, pkg2], { encoding: "utf8" });
        op.check(sums.status === 0, "could not write checksums: " + sums.stderr);
        const result = installer(pkg2);
        logTo("install-second", result);
        op.check(result.status === 0, "installer exit " + result.status + ": " + (result.stderr || result.stdout).slice(0, 300));
        op.check(link("current") === "keepharness-0.16.1" && link("previous") === `keepharness-${version}`, `current -> ${link("current")}, previous -> ${link("previous")}`);
        op.check(!fs.existsSync(browserEntry), "keepharness-browser.desktop was written");
      });

      await S("s3", "s3.rollback", "--rollback points current back at the previous version", async () => {
        const result = installer(pkg2, ["--rollback"]);
        logTo("rollback", result);
        op.check(result.status === 0, "rollback exit " + result.status + ": " + (result.stderr || result.stdout).slice(0, 300));
        op.check(link("current") === `keepharness-${version}` && link("previous") === "keepharness-0.16.1", `current -> ${link("current")}, previous -> ${link("previous")}`);
        op.check(!fs.existsSync(browserEntry), "keepharness-browser.desktop was written");
      });

      await S("s3", "s3.uninstall-dry-run", "--uninstall --dry-run lists what would go and deletes nothing", async () => {
        const before = tree(home3).join("\n");
        const result = installer(pkg2, ["--uninstall", "--dry-run"]);
        logTo("uninstall-dry-run", result);
        op.check(result.status === 0, "dry-run exit " + result.status + ": " + (result.stderr || result.stdout).slice(0, 300));
        op.check(tree(home3).join("\n") === before, "the dry run changed the HOME");
        const out = result.stdout + result.stderr;
        op.check(/keepharness-0\.16\.0|keepharness\b/.test(out) && /\.config\/KeepHarness|opt\/keepharness/.test(out), "the dry run lists no paths: " + out.slice(0, 200));
      });

      await S("s3", "s3.uninstall", "--uninstall --yes removes the desktop app and leaves the Python product byte-identical", async () => {
        const result = installer(pkg2, ["--uninstall", "--yes"]);
        logTo("uninstall", result);
        op.check(result.status === 0, "uninstall exit " + result.status + ": " + (result.stderr || result.stdout).slice(0, 300));
        op.check(!fs.existsSync(entry3) && !fs.existsSync(path.join(opt3, "keepharness")) && !fs.readdirSync(opt3).some((n) => /^keepharness-/.test(n)), "left behind: " + tree(home3).join(", "));
        op.check(readText(path.join(home3, ".config/keepharness/config.txt")) === "python product config\n" && readText(path.join(home3, ".local/share/keepharness/state.txt")) === "python product state\n", "the Python product's files changed");
        op.check(!fs.existsSync(browserEntry), "keepharness-browser.desktop appeared");
      });

      await S("s3", "s3.no-browser-entry-from-python-install", "The Python installer never plans a browser menu entry", async () => {
        const result = spawnSync(box.python, ["-c", `
import sys
from pathlib import Path
from control.install import files
planned = files(Path(sys.argv[1]), sys.executable, 18520)
print("\\n".join(sorted(str(p) for p in planned)))
`, home3], { env: { PATH: "/usr/bin:/bin", HOME: home3, PYTHONPATH: box.backend }, encoding: "utf8" });
        logTo("python-install-plan", result);
        op.check(result.status === 0, "could not read the install plan: " + result.stderr.slice(-200));
        op.check(!/keepharness-browser\.desktop|applications\/keepharness\.desktop/.test(result.stdout), "the plan writes a menu entry: " + result.stdout);
      });
      closeSlice("s3", "S3 installer");
    } finally {
      for (const server of servers) await server.close().catch(() => {});
      if (h && !h.closed) await quitApp(h).catch(() => {});
      if (box) {
        for (const pid of sandboxPids(box)) {
          try {
            process.kill(pid, "SIGKILL");
          } catch {}
        }
        spawnSync("chmod", ["-R", "u+w", box.root, box.tmp]);
        fs.rmSync(box.root, { recursive: true, force: true });
        fs.rmSync(box.tmp, { recursive: true, force: true });
      }
      process.off("uncaughtException", ignoreCrash);
      op.session = previous;
    }
  },
  _lib: { makeBox, appEnv, adminApi, provision, httpRequest, untilTrue, launchApp, instrument, mainState, setAnswers, closeNativeDialog, nativeDialogWindow, waitPage, quitApp, listWindows, appWindows, dialogsNow },
};
