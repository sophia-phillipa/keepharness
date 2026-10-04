// Where the operator works: the isolated fixture servers or a given instance, seen
// through a Playwright Chromium page or the packaged KeepHarness desktop window.
"use strict";
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const readline = require("node:readline");

const REPO = path.resolve(__dirname, "../../..");
const playwright = () => require(process.env.PLAYWRIGHT_MODULE || "playwright");

// ------------------------------------------------------------------ fixture servers

async function startFixture(options) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "keepharness-operator-"));
  const [adminPort, harnessPort] = options.fixturePorts;
  const child = spawn(
    options.python,
    [path.join(__dirname, "../fixture/serve_fixture.py"), "--root", root, "--admin-port", adminPort, "--harness-port", harnessPort],
    { cwd: REPO, stdio: ["ignore", "pipe", "pipe"] },
  );
  let stderr = "";
  child.stderr.on("data", (chunk) => (stderr = (stderr + chunk).slice(-4000)));
  const lines = readline.createInterface({ input: child.stdout });
  const ready = await Promise.race([
    new Promise((resolve) => lines.once("line", resolve)),
    new Promise((_, reject) => child.once("exit", (code) => reject(new Error(`fixture exited (${code}): ${stderr.slice(-800)}`)))),
    new Promise((_, reject) => setTimeout(() => reject(new Error("fixture did not start in 60 s")), 60000)),
  ]);
  const info = JSON.parse(ready);
  return {
    ...info,
    async stop() {
      if (child.exitCode === null && child.signalCode === null) {
        const exited = new Promise((resolve) => child.once("exit", resolve));
        child.kill("SIGTERM");
        await Promise.race([exited, new Promise((resolve) => setTimeout(resolve, 20000))]);
        if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
      }
      fs.rmSync(root, { recursive: true, force: true });
    },
  };
}

// ------------------------------------------------------------------ sessions

async function openBrowser(options, base) {
  const { chromium } = playwright();
  // Loopback-to-loopback framing of the admin (Settings > System) must not be blocked.
  const browser = await chromium.launch({ headless: !options.visible, args: ["--disable-features=LocalNetworkAccessChecks"] });
  const context = await browser.newContext({ viewport: options.viewport, colorScheme: "light", acceptDownloads: false });
  const page = await context.newPage();
  const session = {
    target: "browser",
    browser,
    context,
    page,
    base,
    async resize(width, height) {
      await session.page.setViewportSize({ width, height });
      return true;
    },
    async newPage() {
      return context.newPage();
    },
    async close() {
      await browser.close().catch(() => {});
    },
  };
  return session;
}

async function launchDesktop(options, base, admin) {
  const { _electron: electron } = playwright();
  if (!options.app || !fs.existsSync(options.app)) throw new Error("desktop binary not found: " + (options.app || "(none)") + " (pass --app)");
  // Chromium's single-instance socket lives under TMPDIR and must fit a 108-byte socket
  // path; with a long TMPDIR the app quits at once, so use a short private folder then.
  const parent = os.tmpdir().length > 40 ? "/tmp" : os.tmpdir();
  const root = fs.mkdtempSync(path.join(parent, "kh-operator-"));
  const userData = path.join(root, "profile");
  const ports = { KEEPHARNESS_ADMIN_PORT: new URL(admin).port, KEEPHARNESS_PORT: new URL(base).port };
  // A separate profile keeps this window clear of a desktop app the user already runs.
  const args = ["--user-data-dir=" + userData];
  if (process.env.DISPLAY) args.unshift("--ozone-platform=x11");
  const env = { ...process.env, ...ports, TMPDIR: root };
  const app = await electron.launch({ executablePath: options.app, args, env, timeout: 60000 }).catch((error) => {
    fs.rmSync(root, { recursive: true, force: true });
    throw error;
  });
  const splash = await app.firstWindow();
  const splashUrl = splash.url();
  let page = null;
  for (let i = 0; i < 240 && !page; i++) {
    page = app.windows().find((w) => w.url().startsWith(base) || w.url().startsWith(admin)) || null;
    if (!page) await new Promise((resolve) => setTimeout(resolve, 250));
  }
  if (!page) throw new Error("the desktop window never loaded " + base);
  await page.waitForLoadState("domcontentloaded");
  const session = {
    target: "desktop",
    app,
    page,
    base,
    admin,
    splashUrl,
    async windows() {
      return app.evaluate(({ BrowserWindow }) =>
        BrowserWindow.getAllWindows().map((w) => ({
          title: w.getTitle(),
          bounds: w.getBounds(),
          visible: w.isVisible(),
          handle: w.getNativeWindowHandle().readUInt32LE(0),
          url: w.webContents.getURL(),
        })),
      );
    },
    async resize(width, height) {
      return app.evaluate(({ BrowserWindow }, size) => {
        const win = BrowserWindow.getAllWindows().find((w) => w.isVisible() && !w.webContents.getURL().startsWith("file:"));
        const [minWidth, minHeight] = win.getMinimumSize();
        if (size.width < minWidth || size.height < minHeight) return false;
        win.setContentSize(size.width, size.height);
        return true;
      }, { width, height });
    },
    async newPage() {
      throw new Error("the desktop target has a single window");
    },
    async close() {
      await app.close().catch(() => {});
      fs.rmSync(root, { recursive: true, force: true });
    },
  };
  return session;
}

async function openSession(options, base, admin) {
  return options.target === "desktop" ? launchDesktop(options, base, admin) : openBrowser(options, base);
}

module.exports = { startFixture, openSession, launchDesktop, REPO };
