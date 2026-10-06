// Durable UI preferences, frontend half (WP6): a restart on another port keeps the preferences, old
// localStorage keys migrate into the harness store, a read-only store enqueues nothing and says why,
// a 413 is split per key, and the store file on disk really holds what the UI wrote.
//
// Own servers: this spec starts its harnesses itself, on ports 18096-18099, because it must stop
// and restart one on another port against the same state folder, which the shared test-ui.sh
// server cannot do. Each browser context is a new origin with an empty localStorage.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const PORTS = { a: 18096, b: 18097, fresh: 18098, locked: 18099 };
const READ_ONLY_NOTICE =
  "Interface preferences can't be saved right now, so changes last until you reload. Check the harness state folder.";
const results = [];
const servers = [];
let tmp = "";

async function assertFree(port) {
  await new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once("error", () => reject(new Error("port " + port + " is taken; free it before running this spec")));
    probe.listen(port, "127.0.0.1", () => probe.close(resolve));
  });
}

// Starts a harness whose state lives in <dir>/chat; returns its base URL and a stop function.
async function startServer(port, dir, { prepare } = {}) {
  await assertFree(port);
  const state = path.join(dir, "chat");
  fs.mkdirSync(state, { recursive: true });
  if (prepare) prepare(state);
  const config = path.join(dir, "config.json");
  fs.writeFileSync(
    config,
    JSON.stringify({
      state_dir: state, bind: "127.0.0.1", port, local_access: true,
      clients: { local: { sha256: "0".repeat(64), projects: ["sem-projeto"] } },
      projects: { "sem-projeto": {} }, services: {},
      origins: ["http://127.0.0.1:" + port], config_revision: "ui-state-restart-" + port,
    }),
  );
  const log = fs.openSync(path.join(dir, "server-" + port + ".log"), "a");
  const child = spawn(process.env.PYTHON || "python3", ["-m", "agent_service.app"], {
    cwd: ROOT, env: { ...process.env, KEEPHARNESS_AGENT_CONFIG: config }, stdio: ["ignore", log, log],
  });
  const exited = new Promise((resolve) => child.once("exit", resolve));
  const server = {
    base: "http://127.0.0.1:" + port, state, child,
    async stop() { if (child.exitCode === null) { child.kill("SIGTERM"); await exited; } },
  };
  servers.push(server);
  for (let i = 0; i < 80; i++) {
    if (child.exitCode !== null) throw new Error("harness on " + port + " exited; see " + dir);
    try { if ((await fetch(server.base + "/v1/version")).ok) return server; } catch {}
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("harness on " + port + " did not start");
}

const api = async (server, method, body) => {
  const response = await fetch(server.base + "/v1/ui-state", {
    method, headers: { "Content-Type": "application/json" }, body: body && JSON.stringify(body),
  });
  return { status: response.status, body: await response.json() };
};
const storedValues = async (server) => (await api(server, "GET")).body.values;
async function reset(server) {
  const keys = Object.keys(await storedValues(server));
  if (keys.length) assert.equal((await api(server, "PATCH", { values: Object.fromEntries(keys.map((key) => [key, null])) })).status, 200);
}
// The store file as the UI left it on disk: the proof that server mode really ran.
function storeFile(server) {
  const root = path.join(server.state, "ui-state");
  const [owner] = fs.existsSync(root) ? fs.readdirSync(root) : [];
  return owner ? path.join(root, owner, "preferences.json") : null;
}
const onDisk = (server) => {
  const file = storeFile(server);
  assert(file && fs.existsSync(file), "no preferences.json under " + server.state);
  assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  const document = JSON.parse(fs.readFileSync(file, "utf8"));
  assert.equal(document.version, 1);
  return document.values;
};

async function openPage(browser, server, { route, items } = {}) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  const page = await context.newPage();
  page.patches = [];
  page.warnings = [];
  page.on("pageerror", (error) => console.error("PAGEERROR", error.message));
  page.on("console", (message) => { if (message.type() === "warning") page.warnings.push(message.text()); });
  page.on("request", (request) => {
    if (request.method() === "PATCH" && new URL(request.url()).pathname === "/v1/ui-state") page.patches.push(request);
  });
  // Every text the status line ever showed: a later "Ready to chat" may replace a notice.
  await page.addInitScript(() => {
    window.statusHistory = [];
    new MutationObserver(() => {
      const text = document.getElementById("status")?.textContent;
      if (text && text !== window.statusHistory.at(-1)) window.statusHistory.push(text);
    }).observe(document, { childList: true, characterData: true, subtree: true });
  });
  if (route) await page.route("**/v1/ui-state", route);
  if (items) {
    // Old localStorage keys, written on the server's origin before the app loads (a JSON page runs no app script).
    await page.goto(server.base + "/v1/version");
    await page.evaluate((entries) => { for (const [key, value] of Object.entries(entries)) localStorage.setItem(key, value); }, items);
  }
  await page.goto(server.base + "/");
  await page.waitForFunction(() => window.HarnessPrefs && document.querySelector("#menu"));
  return page;
}
async function waitUntil(check, timeout = 4000) {
  const end = Date.now() + timeout;
  for (;;) {
    try { if (check()) return; } catch {}
    if (Date.now() > end) throw new Error("condition not met within " + timeout + " ms: " + check);
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}
const flush = (page) => page.evaluate(() => window.HarnessPrefs.flush());
const lsItem = (page, key) => page.evaluate((name) => localStorage.getItem(name), key);
const setReadingSize = (page, size) =>
  page.evaluate((value) => { const select = document.getElementById("reading-size"); select.value = value; select.dispatchEvent(new Event("change")); }, size);
const shown = (page, text) => page.waitForFunction((wanted) => window.statusHistory.some((line) => line.includes(wanted)), text);
// Ctrl+, opens Settings at the remembered section once the interface is ready.
async function openSettingsAtLastSection(page) {
  for (let attempt = 0; attempt < 40; attempt++) {
    await page.keyboard.press("Control+,");
    if (await page.locator("#settings-dialog").evaluate((dialog) => dialog.open)) return;
    await page.waitForTimeout(100);
  }
  throw new Error("Settings did not open");
}
const pressedSection = (page) => page.locator("[data-settings][aria-pressed='true']").evaluateAll((buttons) => buttons.map((b) => b.dataset.adminSection || b.dataset.settings));

async function step(name, work) {
  await work();
  results.push(name);
  console.log("PASS " + name);
}

(async () => {
  tmp = fs.mkdtempSync(path.join(process.env.TMPDIR || os.tmpdir(), "kh-ui-state-"));
  const browser = await chromium.launch();
  try {
    const shared = await startServer(PORTS.a, path.join(tmp, "shared"));
    const fresh = await startServer(PORTS.fresh, path.join(tmp, "fresh"));
    const locked = await startServer(PORTS.locked, path.join(tmp, "locked"), {
      // A store folder the owner cannot write: the harness reports read_only on the first read.
      prepare: (state) => fs.mkdirSync(path.join(state, "ui-state"), { mode: 0o500 }),
    });

    await step("preferences set through the UI survive a restart on another port", async () => {
      let page = await openPage(browser, shared);
      assert.equal(await page.evaluate(() => window.HarnessPrefs.server), true, "the page must run in server mode");
      await page.click("#menu");
      await setReadingSize(page, "19");
      await flush(page);
      const expected = { sidebar_collapsed: true, reading_size: "19" };
      assert.deepEqual(Object.fromEntries(Object.keys(expected).map((key) => [key, onDisk(shared)[key]])), expected);
      assert.deepEqual(await storedValues(shared), onDisk(shared));
      // Server mode keeps no copy in localStorage.
      assert.equal(await lsItem(page, "sidebar-collapsed"), null);
      assert.equal(await lsItem(page, "reading-size"), null);
      await page.context().close();
      await shared.stop();

      const moved = await startServer(PORTS.b, path.join(tmp, "shared"));
      page = await openPage(browser, moved);
      assert.equal(await page.evaluate(() => document.body.classList.contains("sidebar-collapsed")), true);
      assert.equal(await page.locator("#reading-size").inputValue(), "19");
      assert.equal(await lsItem(page, "sidebar-collapsed"), null);
      await page.context().close();
    });

    await step("a fresh state folder gives the defaults", async () => {
      assert.deepEqual(await storedValues(fresh), {});
      const page = await openPage(browser, fresh);
      assert.equal(await page.evaluate(() => document.body.classList.contains("sidebar-collapsed")), false);
      assert.equal(await page.locator("#reading-size").inputValue(), "15");
      await page.context().close();
    });

    await step("old localStorage keys migrate: accepted ones move and are erased, invalid and theme stay", async () => {
      await reset(fresh);
      const page = await openPage(browser, fresh, { items: {
        "sidebar-collapsed": "1",
        "reading-size": "19",
        "panel-order": "conversations-right",
        "project-expanded": JSON.stringify({ "bad id!": true }),
        "keepharness:theme:harness": "graphite",
      } });
      await flush(page);
      const values = onDisk(fresh);
      assert.equal(values.sidebar_collapsed, true);
      assert.equal(values.reading_size, "19");
      assert.equal(values.panel_order, "conversations-right");
      assert.equal(values.theme, "graphite");
      assert(!("project_expanded" in values), "an invalid value must not reach the store");
      assert.equal(await lsItem(page, "sidebar-collapsed"), null);
      assert.equal(await lsItem(page, "reading-size"), null);
      assert.equal(await lsItem(page, "panel-order"), null);
      assert.equal(await lsItem(page, "project-expanded"), JSON.stringify({ "bad id!": true }), "a rejected key stays in localStorage");
      assert.equal(await lsItem(page, "keepharness:theme:harness"), "graphite", "theme keeps its first-paint cache");
      await page.context().close();
    });

    await step("workspace-section-* keys convert to workspace_sections and round-trip", async () => {
      await reset(fresh);
      const page = await openPage(browser, fresh, { items: {
        "workspace-section-resources": JSON.stringify({ open: false, height: null }),
        "workspace-section-files": JSON.stringify({ open: true, height: 220 }),
      } });
      await flush(page);
      assert.deepEqual(onDisk(fresh).workspace_sections, { resources: { open: false, height: null }, files: { open: true, height: 220 } });
      assert.equal(await lsItem(page, "workspace-section-resources"), null);
      assert.equal(await lsItem(page, "workspace-section-files"), null);
      const expanded = (name) => page.locator("#workspace-" + name + "-head").getAttribute("aria-expanded");
      assert.equal(await expanded("resources"), "false", "no stored section is open: Resources stays collapsed");
      assert.equal(await expanded("activity"), "true", "the Activities accordion falls back to Activity");
      assert.equal(await page.locator("#workspace-files").evaluate((n) => n.style.height), "", "D-033: Files fills its view; a stored height is not applied");
      // D-033: a section opened through the accordion lands in the store (exactly one open) and is restored after a reload.
      await page.locator("#workspace-resources-head").evaluate((head) => head.click());
      await page.waitForFunction(() => window.HarnessPrefs.get("workspace_sections", {}).resources?.open === true);
      await flush(page);
      assert.equal(onDisk(fresh).workspace_sections.resources.open, true);
      assert.equal(onDisk(fresh).workspace_sections.activity.open, false);
      await page.reload();
      await page.waitForFunction(() => window.HarnessPrefs && document.querySelector("#menu"));
      assert.equal(await expanded("resources"), "true");
      assert.equal(await expanded("activity"), "false");
      assert.equal(await page.locator('[data-workspace-section="files"]').evaluate((d) => d.open), true);
      await page.context().close();
    });

    await step("last_section: the remembered section opens, an unknown one falls back to Appearance", async () => {
      await reset(fresh);
      assert.equal((await api(fresh, "PATCH", { values: { last_section: "models" } })).status, 200);
      let page = await openPage(browser, fresh);
      await openSettingsAtLastSection(page);
      assert.deepEqual(await pressedSection(page), ["models"]);
      await page.context().close();

      assert.equal((await api(fresh, "PATCH", { values: { last_section: "gone-section" } })).status, 200);
      page = await openPage(browser, fresh);
      await openSettingsAtLastSection(page);
      assert.deepEqual(await pressedSection(page), ["appearance"]);
      // Picking a section stores it.
      await page.locator("[data-settings='customize']").click();
      await flush(page);
      assert.equal(onDisk(fresh).last_section, "customize");
      await page.context().close();
    });

    await step("a 413 is split per key and the keys that fit still land", async () => {
      await reset(fresh);
      // The client caps keep every real batch under the 64 KB body limit, so the 413 is simulated at
      // the route: any batch of several keys, and the key project_expanded alone, is refused.
      const refused = (route) => {
        if (route.request().method() !== "PATCH") return route.continue();
        const keys = Object.keys(route.request().postDataJSON().values);
        return keys.length > 1 || keys.includes("project_expanded")
          ? route.fulfill({ status: 413, contentType: "application/json", body: JSON.stringify({ code: "payload_limit" }) })
          : route.continue();
      };
      const page = await openPage(browser, fresh, { route: refused });
      await page.evaluate(async () => {
        const prefs = window.HarnessPrefs;
        prefs.set("sidebar_collapsed", true);
        prefs.set("reading_size", "17");
        prefs.set("panel_order", "conversations-right");
        prefs.set("project_expanded", { alpha: true });
        await prefs.flush();
      });
      const sent = page.patches.map((request) => Object.keys(request.postDataJSON().values));
      assert(sent.some((keys) => keys.length >= 4), "the whole batch is tried first: " + JSON.stringify(sent));
      assert(sent.some((keys) => keys.length === 1 && keys[0] === "project_expanded"), "the refused key is tried alone");
      const values = onDisk(fresh);
      assert.equal(values.sidebar_collapsed, true);
      assert.equal(values.reading_size, "17");
      assert.equal(values.panel_order, "conversations-right");
      assert(!("project_expanded" in values));
      assert(page.warnings.some((text) => text.includes("ui-state: dropped project_expanded")), JSON.stringify(page.warnings));
      await page.context().close();
      // The simulated body is the one the real server sends for an over-limit request.
      const real = await api(fresh, "PATCH", { values: { chat_selection: { model: "x".repeat(70000) } } });
      assert.equal(real.status, 413);
      assert.equal(real.body.code, "payload_limit");
    });

    await step("a store that is read-only at load shows the notice and enqueues nothing", async () => {
      assert.equal((await api(locked, "GET")).body.read_only, true);
      const page = await openPage(browser, locked);
      assert.equal(await page.evaluate(() => window.HarnessPrefs.server), true);
      await shown(page, READ_ONLY_NOTICE);
      // initialize() ends with "Ready to chat." or "Set up a model": the notice must survive it and stay visible.
      await page.waitForFunction(() => typeof interfaceReady !== "undefined" && interfaceReady);
      await page.waitForTimeout(300);
      const bar = await page.locator("#status").evaluate((node) => ({ text: node.textContent, className: node.className, hidden: node.hidden }));
      assert.equal(bar.text, READ_ONLY_NOTICE, "the notice is what #status shows after init");
      assert.notEqual(bar.className, "visually-hidden", "the notice must not be visually hidden");
      assert.equal(bar.hidden, false);
      // The first-run tour (tour_seen cannot be stored here) covers the page once ready: click through the DOM.
      await page.evaluate(() => document.getElementById("menu").click());
      await setReadingSize(page, "17");
      await page.waitForTimeout(1200); // well past the 400 ms debounce
      await flush(page);
      assert.equal(page.patches.length, 0, "no write may leave the page");
      assert.equal(storeFile(locked), null, "nothing may be written to the store");
      await page.context().close();
    });

    await step("a store that turns read-only mid-session answers 409, shows the notice and stops writing", async () => {
      await reset(fresh);
      const page = await openPage(browser, fresh);
      await page.click("#menu");
      await flush(page);
      const owner = path.dirname(storeFile(fresh));
      fs.chmodSync(owner, 0o500);
      try {
        await setReadingSize(page, "17");
        await flush(page);
        assert.equal(page.patches.length >= 1, true);
        const last = page.patches.at(-1);
        assert.equal((await last.response()).status(), 409);
        await shown(page, READ_ONLY_NOTICE);
        const before = page.patches.length;
        await setReadingSize(page, "19");
        await page.waitForTimeout(1000);
        await flush(page);
        assert.equal(page.patches.length, before, "no retry loop after a 409");
      } finally {
        fs.chmodSync(owner, 0o700);
      }
      await page.context().close();
    });

    await step("a change made just before the page goes away is kept (pitfall 8)", async () => {
      await reset(fresh);
      const page = await openPage(browser, fresh);
      await setReadingSize(page, "19");
      await page.goto("about:blank"); // well inside the 400 ms debounce
      await waitUntil(() => onDisk(fresh).reading_size === "19");
      await page.context().close();
    });

    await step("leaving the page sends each change once, not once per exit event", async () => {
      await reset(fresh);
      const page = await openPage(browser, fresh);
      await page.evaluate(() => {
        window.HarnessPrefs.set("reading_size", "17");
        Object.defineProperty(document, "visibilityState", { get: () => "hidden", configurable: true });
        Object.defineProperty(document, "hidden", { get: () => true, configurable: true });
        document.dispatchEvent(new Event("visibilitychange"));
        dispatchEvent(new Event("pagehide"));
        document.dispatchEvent(new Event("visibilitychange"));
        dispatchEvent(new Event("pagehide"));
      });
      await waitUntil(() => onDisk(fresh).reading_size === "17");
      await page.waitForTimeout(500);
      const sends = page.patches.filter((request) => "reading_size" in request.postDataJSON().values);
      assert.equal(sends.length, 1, "one keepalive PATCH per change: " + sends.length);
      await page.context().close();
    });

    await step("a keepalive batch over the in-flight quota is sent per key (values here exceed the server's caps; only the request sizes matter)", async () => {
      await reset(fresh);
      const page = await openPage(browser, fresh);
      await page.evaluate(() => {
        const prefs = window.HarnessPrefs;
        const state = (n) => Object.fromEntries(Array.from({ length: 100 }, (_, i) => [n + String(i).padStart(3, "0") + "x".repeat(100), { token: "t".repeat(60), state: "completed", unread: false }]));
        prefs.set("conversation_activity", state("a"));
        prefs.set("project_list_preferences", Object.fromEntries(Array.from({ length: 200 }, (_, i) => ["p" + i + "x".repeat(100), { favorite: true, hidden: false, hide_icon: true }])));
        prefs.set("project_expanded", Object.fromEntries(Array.from({ length: 200 }, (_, i) => ["p" + i + "x".repeat(100), true])));
        prefs.set("conversation_scroll", Array.from({ length: 100 }, (_, i) => ["s" + i + "x".repeat(50), 120000 + i]));
        void prefs.flush({ keepalive: true });
      });
      await page.waitForTimeout(2500);
      const sizes = page.patches.map((request) => request.postData().length);
      assert(sizes.length > 1, "the oversized batch is split: " + JSON.stringify(sizes));
      assert(Math.max(...sizes) < 60 * 1024, "no keepalive body over 60 KB: " + JSON.stringify(sizes));
      await page.context().close();
    });

    await step("a 5xx on a PATCH is retried and the final stored value is the latest (pitfall 5)", async () => {
      await reset(fresh);
      let failed = 0;
      const once = (route) => {
        if (route.request().method() === "PATCH" && failed++ === 0)
          return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ code: "unavailable" }) });
        return route.continue();
      };
      const page = await openPage(browser, fresh, { route: once });
      await page.evaluate(() => window.HarnessPrefs.set("reading_size", "17"));
      await waitUntil(() => failed >= 1);
      await page.evaluate(() => window.HarnessPrefs.set("reading_size", "19"));
      await waitUntil(() => onDisk(fresh).reading_size === "19", 8000);
      await page.waitForTimeout(500);
      assert.equal(onDisk(fresh).reading_size, "19", "the older value must not land after the newer one");
      await page.context().close();
    });

    await step("a conversation_activity map at its cap fits the 16 KB value cap (pitfall 2)", async () => {
      await reset(fresh);
      const page = await openPage(browser, fresh);
      const hex = (n, i) => (n + i.toString(16)).padStart(32, "0").slice(-32); // uuid4().hex, the harness's real ids
      const map = Object.fromEntries(Array.from({ length: 100 }, (_, i) => [hex("c", i), { token: hex("j", i), state: "completed", unread: true }]));
      const bytes = JSON.stringify(map).length;
      await page.evaluate((value) => window.HarnessPrefs.set("conversation_activity", value), map);
      await flush(page);
      assert.equal(Object.keys(onDisk(fresh).conversation_activity).length, 100, "bytes=" + bytes);
      assert(page.warnings.every((text) => !text.includes("dropped")), JSON.stringify(page.warnings));
      console.log("conversation_activity at 100 realistic entries: " + bytes + " bytes of 16384");
      await page.context().close();
    });

    await step("conversation_activity keeps the 100 most recent conversations of a longer list (W1)", async () => {
      await reset(fresh);
      const page = await openPage(browser, fresh);
      await page.waitForFunction(() => typeof interfaceReady !== "undefined" && interfaceReady);
      await page.evaluate(async () => {
        // The history answers newest first; 120 conversations, c119 the newest.
        conversations = Array.from({ length: 120 }, (_, i) => 119 - i).map((i) => ({ id: "c" + String(i).padStart(3, "0"), state: "completed", last_job_id: "j" + i, updated: 1000 + i }));
        conversationActivity = {};
        conversations.forEach(observeConversation);
        saveConversationActivity();
        await window.HarnessPrefs.flush(); // before a history poll can replace the list
      });
      const stored = Object.keys(onDisk(fresh).conversation_activity).sort();
      assert.equal(stored.length, 100);
      assert.equal(stored[0], "c020");
      assert.equal(stored.at(-1), "c119");
      await page.context().close();
    });

    await step("hide_icon, panel_widths, chat_selection, conversation_scroll and run_console_height round-trip a restart (pitfall 1)", async () => {
      const dir = path.join(tmp, "roundtrip");
      const wanted = {
        project_list_preferences: { "sem-projeto": { favorite: false, hidden: false, hide_icon: true } },
        panel_widths: { sidebar: 311, activity_panel: 402 },
        chat_selection: { model: "gpt-5.6-sol", effort: "medium" },
        conversation_scroll: [["abc123", 640], ["def456", -1]],
        run_console_height: 275,
      };
      let server = await startServer(PORTS.a, dir);
      let page = await openPage(browser, server);
      await page.evaluate((values) => { for (const [key, value] of Object.entries(values)) window.HarnessPrefs.set(key, value); }, wanted);
      await flush(page);
      assert.deepEqual(Object.fromEntries(Object.keys(wanted).map((key) => [key, onDisk(server)[key]])), wanted);
      await page.context().close();
      await server.stop();

      server = await startServer(PORTS.a, dir);
      page = await openPage(browser, server);
      const read = await page.evaluate((keys) => Object.fromEntries(keys.map((key) => [key, window.HarnessPrefs.get(key)])), Object.keys(wanted));
      assert.deepEqual(read, wanted);
      for (const old of ["sidebar-width", "activity-panel-width", "chat-selection", "conversation-scroll", "run-console-height", "project-list-preferences"])
        assert.equal(await lsItem(page, old), null, old);
      await page.context().close();
    });

    console.log("ui-state-restart: " + results.length + " steps passed");
  } finally {
    await browser.close();
    await Promise.all(servers.map((server) => server.stop()));
  }
})()
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  })
  .finally(() => {
    if (!tmp) return;
    // chmod first: the read-only fixture folder is 0500.
    spawnSync("chmod", ["-R", "u+w", tmp]);
    fs.rmSync(tmp, { recursive: true, force: true });
  });
