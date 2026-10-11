// D-055 P7, terminal pane. Written before the implementation (red on purpose).
//
// Testids and ids the implementer must render (this block is the contract for this spec):
//   #terminal-toggle         rail button (icon "terminal", title "Terminal"), aria-pressed.
//                            The spec finds it by id; also give it data-testid="terminal-toggle".
//   terminal-settings-toggle Settings > Appearance > Workspace switch "Terminal" (checked = open),
//                            in the same group as wide-layout-toggle and editor-settings-toggle
//   terminal-pane            #terminal-pane docked at the bottom of main, above the run console;
//                            hidden when closed
//   terminal-cwd             header label with the session cwd_label
//   terminal-output          scrolling plain-text output; the echoed typed line is part of it
//   terminal-input           single-line input; type="password" while the shell has echo off
//   terminal-send-ctrl-c     key buttons: send \x03, \x04, \t and \x1b[A
//   terminal-send-ctrl-d
//   terminal-send-tab
//   terminal-send-up
//   terminal-end-session     button: DELETE /v1/terminal/sessions/{id}
//   terminal-resize-handle   focusable separator (role="separator", tabindex 0)
//   terminal-notice          role="status" notice inside the pane
// Shortcut: Control+Backquote toggles the pane, also while the terminal input has focus.
//
// Contract choices made by this spec (the implementer must follow them):
//   - Resize is tested with the keyboard: focus terminal-resize-handle and press ArrowUp. A mouse drag
//     is not asserted, so the test does not depend on pointer timing. The saved terminal_height must
//     match the measured pane height (within 2 px) and survive a reload.
//   - Coexistence with the run console is checked geometrically (the pane's bottom edge sits above the
//     top edge of #run-console), so the spec does not depend on how the CSS order is implemented.
//   - The pty echoes the typed command, so a token like kh-term-ok appears once as the echoed command
//     and once as its output. The spec waits for two occurrences to prove the output arrived.
//
// Runs against the shared harness started by scripts/test-ui.sh (HARNESS_URL) with the real backend:
// the shell is a real sh started by the test server, in the sem-projeto scope. Preferences go through
// the real /v1/ui-state. No fakes.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const harness = (process.env.HARNESS_URL || "").replace(/\/$/, "");
if (!harness)
  throw new Error("HARNESS_URL is required (run through scripts/test-ui.sh)");

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const PROJECT = "sem-projeto";
const SESSION_COOKIE = "harness_session";

// The terminal routes need an enrolled browser. The owner's enrollment is issued by the same two calls
// tests/test_terminal_routes.py makes, against the test server's config, and the cookie value is returned.
function enrolledSession() {
  const code = [
    "import sys",
    "from agent_service.app import read_startup_config",
    "from agent_service import approval_sessions as a",
    "cfg = read_startup_config(sys.argv[1])",
    'print(a.consume_enrollment(cfg, a.issue_enrollment(cfg, "local")))',
  ].join("\n");
  const config = path.join(process.env.KEEPHARNESS_TEST_STATE, "chat.json");
  return execFileSync(process.env.PYTHON, ["-c", code, config], {
    cwd: path.join(__dirname, ".."),
    encoding: "utf8",
  }).trim();
}

// /v1/ui-state answers the owner's session with { values }; a missing key reads as its default.
async function savedValue(page, key) {
  const response = await page.request.get(harness + "/v1/ui-state");
  assert.equal(response.status(), 200, "GET /v1/ui-state");
  return (await response.json()).values[key];
}

async function waitSaved(page, key, expected) {
  for (let attempt = 0; attempt < 60; attempt++) {
    if ((await savedValue(page, key)) === expected) return;
    await delay(100);
  }
  assert.fail(key + " never reached " + JSON.stringify(expected) + " on the server");
}

// A pixel height is saved as measured by the layout, so allow the rounding of the border.
async function waitSavedNear(page, key, expected) {
  for (let attempt = 0; attempt < 60; attempt++) {
    const value = await savedValue(page, key);
    if (typeof value === "number" && Math.abs(value - expected) <= 2) return;
    await delay(100);
  }
  assert.fail(key + " never reached about " + expected + "px on the server");
}

// Polls an async condition for up to 8 s; used for server-side effects the page only reaches later.
async function until(check, message) {
  for (let attempt = 0; attempt < 80; attempt++) {
    if (await check()) return;
    await delay(100);
  }
  assert.fail(message);
}

// Records the session calls the pane makes, from the real responses and requests.
function recordTerminal(page, log) {
  page.on("response", async (response) => {
    const request = response.request();
    const { pathname } = new URL(response.url());
    if (pathname === "/v1/terminal/sessions" && request.method() === "POST") {
      const body = await response.json().catch(() => null);
      log.starts.push({ status: response.status(), body });
    } else if (request.method() === "DELETE" && /^\/v1\/terminal\/sessions\/[^/]+$/.test(pathname)) {
      log.ends.push({ status: response.status(), id: pathname.split("/").pop() });
    } else if (request.method() === "POST" && /\/v1\/terminal\/sessions\/[^/]+\/input$/.test(pathname)) {
      try {
        log.inputs.push(request.postDataJSON().data);
      } catch {
        log.inputs.push(null);
      }
    }
  });
}

// Waits until the output shows `token` at least `minimum` times. The pty echoes the typed command,
// so one occurrence can be the echo alone.
async function waitForOutput(page, token, minimum) {
  await page.waitForFunction(
    ([text, expected]) => {
      const output = document.querySelector('[data-testid="terminal-output"]');
      return output !== null && output.textContent.split(text).length - 1 >= expected;
    },
    [token, minimum],
    { timeout: 8000 },
  );
}

// The id of the focused element, so the spec can check where focus went.
function focusedId(page) {
  return page.evaluate(() => document.activeElement?.id ?? "");
}

// Types one line into the terminal input and sends it with Enter.
async function runLine(page, line) {
  const input = page.getByTestId("terminal-input");
  await input.fill(line);
  await input.press("Enter");
}

// Settings > Appearance > Workspace, the same way the editor spec opens it.
async function openSettingsWorkspace(page) {
  await page.evaluate(() => openSettings("appearance"));
  await page.getByTestId("workspace-group").waitFor({ state: "visible" });
}

(async () => {
  const browser = await chromium.launch();
  const pageErrors = [];
  const log = { starts: [], ends: [], inputs: [] };
  let page = null;
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await context.addCookies([{ name: SESSION_COOKIE, value: enrolledSession(), url: harness }]);
    page = await context.newPage();
    page.on("pageerror", (error) => pageErrors.push(error.message));
    recordTerminal(page, log);
    await page.addInitScript(() =>
      localStorage.setItem("keepharness-tour-seen", "0.16.0"),
    );
    await page.goto(harness + "/");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });

    const railToggle = page.locator("#terminal-toggle");
    const pane = page.getByTestId("terminal-pane");

    // 1. Closed by default; rail button, Settings switch and Control+Backquote agree and persist.
    assert.equal(await railToggle.getAttribute("title"), "Terminal");
    assert.equal(await railToggle.getAttribute("aria-pressed"), "false", "starts unpressed");
    assert.equal(await pane.isVisible(), false, "chat-first: the terminal starts closed");
    // A missing key reads as closed; loading the page does not write it.
    assert.equal(await savedValue(page, "terminal_open"), undefined, "nothing saved before a choice");

    await openSettingsWorkspace(page);
    const settingsSwitch = page.getByTestId("terminal-settings-toggle");
    assert.equal(await settingsSwitch.isChecked(), false, "Settings switch starts off");
    assert.match(await page.getByTestId("workspace-group").textContent(), /Terminal/);
    await page.keyboard.press("Escape");

    await railToggle.click();
    await pane.waitFor({ state: "visible" });
    assert.equal(await railToggle.getAttribute("aria-pressed"), "true", "rail reports pressed");
    assert.equal(await focusedId(page), "terminal-input", "opening focuses the terminal input");
    await waitSaved(page, "terminal_open", true);

    await page.keyboard.press("Control+Backquote");
    await pane.waitFor({ state: "hidden" });
    assert.equal(await railToggle.getAttribute("aria-pressed"), "false", "shortcut closes it");
    assert.equal(await focusedId(page), "terminal-toggle", "closing with focus inside moves it to the rail button");
    await waitSaved(page, "terminal_open", false);

    await openSettingsWorkspace(page);
    assert.equal(await settingsSwitch.isChecked(), false, "switch follows the shortcut");
    await settingsSwitch.check();
    await pane.waitFor({ state: "visible" });
    assert.equal(await railToggle.getAttribute("aria-pressed"), "true", "rail follows the switch");
    await waitSaved(page, "terminal_open", true);
    await page.keyboard.press("Escape");

    // 2. Opening starts a real session (201); the reopen of step 1 reattaches (200). The cwd label shows
    // and a typed command runs.
    await until(() => log.starts.length === 2, "each open calls the start route");
    assert.equal(log.starts[0].status, 201, "the first open creates the session");
    assert.equal(log.starts[1].status, 200, "the reopen in step 1 reattaches (200)");
    const first = log.starts[0].body;
    assert.ok(first.session_id, "the session id comes back");
    await page.waitForFunction(
      (label) => document.querySelector('[data-testid="terminal-cwd"]')?.textContent.trim() === label,
      first.cwd_label,
    );

    await runLine(page, "echo kh-term-ok");
    await waitForOutput(page, "kh-term-ok", 2);
    await until(() => log.inputs.includes("echo kh-term-ok\n"), "Enter sends the line plus a newline");

    // 3. A hidden prompt masks the input, and the typed secret never reaches the output.
    const input = page.getByTestId("terminal-input");
    // dash (the harness shell on this host) has no `read -s`, so the hidden read turns echo off itself.
    await runLine(page, "stty -echo; read x; stty echo");
    await page.waitForFunction(
      () => document.querySelector('[data-testid="terminal-input"]')?.type === "password",
      null,
      { timeout: 8000 },
    );
    await input.fill("kh-secret-123");
    await input.press("Enter");
    await page.waitForFunction(
      () => document.querySelector('[data-testid="terminal-input"]')?.type === "text",
      null,
      { timeout: 8000 },
    );
    await until(() => log.inputs.includes("kh-secret-123\n"), "the secret reaches the shell");
    const outputAfterSecret = await page.getByTestId("terminal-output").textContent();
    assert.equal(outputAfterSecret.includes("kh-secret-123"), false, "the secret never shows in the output");

    // 4. Ctrl+C interrupts a running command; a new command then runs. Tab and Up send their bytes.
    await runLine(page, "sleep 30");
    await delay(1000);
    await page.getByTestId("terminal-send-ctrl-c").click();
    await until(() => log.inputs.includes("\x03"), "Ctrl+C button sends \\x03");
    await runLine(page, "echo kh-after-int");
    await waitForOutput(page, "kh-after-int", 2);
    await page.getByTestId("terminal-send-tab").click();
    await page.getByTestId("terminal-send-up").click();
    await until(
      () => log.inputs.includes("\t") && log.inputs.includes("\x1b[A"),
      "Tab and Up buttons send their bytes",
    );
    assert.equal(await page.getByTestId("terminal-send-ctrl-d").isVisible(), true, "Ctrl+D button shown");
    await page.getByTestId("terminal-send-ctrl-c").click(); // clears the half-typed Tab and Up bytes

    // 5. Closing and reopening the pane reattaches (200) to the same session; earlier output stays.
    await railToggle.click();
    await pane.waitFor({ state: "hidden" });
    await railToggle.click();
    await pane.waitFor({ state: "visible" });
    await until(() => log.starts.length === 3, "reopening calls the start route again");
    assert.equal(log.starts[2].status, 200, "reopening reattaches (200)");
    assert.equal(log.starts[2].body.created, false, "reattach does not create");
    assert.equal(log.starts[2].body.session_id, first.session_id, "same session");
    await waitForOutput(page, "kh-term-ok", 2);

    // 6. End session sends DELETE (204); the next open starts a fresh session (201).
    await page.getByTestId("terminal-end-session").click();
    await until(() => log.ends.length === 1, "End session sends DELETE");
    assert.equal(log.ends[0].status, 204, "DELETE answers 204");
    assert.equal(log.ends[0].id, first.session_id, "DELETE names the ended session");
    await railToggle.click();
    await pane.waitFor({ state: "hidden" });
    await railToggle.click();
    await pane.waitFor({ state: "visible" });
    await until(() => log.starts.length === 4, "reopening after End session starts a session");
    assert.equal(log.starts[3].status, 201, "a fresh session is created");
    assert.notEqual(log.starts[3].body.session_id, first.session_id, "the new session differs");

    // 7. With the run console open too, the terminal sits above it.
    await page.evaluate(() => document.activeElement?.blur());
    await page.keyboard.press("Control+j");
    const consoleDrawer = page.locator("#run-console");
    await consoleDrawer.waitFor({ state: "visible" });
    const paneBox = await pane.boundingBox();
    const consoleBox = await consoleDrawer.boundingBox();
    assert.ok(paneBox && consoleBox, "both panes are laid out");
    assert.ok(
      paneBox.y + paneBox.height <= consoleBox.y + 1,
      "terminal pane sits above the run console",
    );
    assert.equal(await pane.isVisible(), true, "the terminal stays open next to the console");
    await page.keyboard.press("Control+j");
    await consoleDrawer.waitFor({ state: "hidden" });

    // 8. Below 1000 px only one overlay at a time: opening the terminal closes the file editor.
    await page.setViewportSize({ width: 900, height: 800 });
    await railToggle.click();
    await pane.waitFor({ state: "hidden" });
    await page.getByTestId("editor-toggle").click();
    await page.getByTestId("editor-pane").waitFor({ state: "visible" });
    // The open editor overlay covers the header toggles below 1000 px, so the shortcut opens the terminal.
    await page.keyboard.press("Control+Backquote");
    await pane.waitFor({ state: "visible" });
    await page.getByTestId("editor-pane").waitFor({ state: "hidden" });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 9. The shortcuts dialog and the Settings help text both name the terminal and its key.
    await page.evaluate(() => document.activeElement?.blur());
    await page.keyboard.press("Control+/");
    const shortcutList = page.locator("#keyboard-shortcuts-list");
    await shortcutList.waitFor({ state: "visible" });
    const listText = await shortcutList.textContent();
    assert.match(listText, /Terminal/, "the shortcuts list has a Terminal row");
    assert.ok(listText.includes("`"), "the Terminal row shows the backquote key");
    await page.keyboard.press("Escape");
    await openSettingsWorkspace(page);
    assert.match(
      await page.locator("#settings-appearance .shortcut-help").textContent(),
      /terminal/i,
      "the Settings help text names the terminal",
    );
    await page.keyboard.press("Escape");

    // 10. The keyboard resize saves terminal_height and the height survives a reload.
    await pane.waitFor({ state: "visible" });
    const handle = page.getByTestId("terminal-resize-handle");
    const heightBefore = Math.round((await pane.boundingBox()).height);
    await handle.focus();
    for (let step = 0; step < 3; step++) await page.keyboard.press("ArrowUp");
    await until(
      async () => Math.round((await pane.boundingBox()).height) !== heightBefore,
      "ArrowUp on the handle resizes the pane",
    );
    const heightAfter = Math.round((await pane.boundingBox()).height);
    await waitSavedNear(page, "terminal_height", heightAfter);

    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await pane.waitFor({ state: "visible" });
    assert.ok(
      Math.abs(Math.round((await pane.boundingBox()).height) - heightAfter) <= 2,
      "terminal height survives a reload",
    );

    // 11. A refused start (409 terminal_limit) leaves the pane with no session: typed input is not sent,
    // so it cannot reach the shell of the previous open.
    const sessionPosts = [];
    const inputPosts = [];
    const countPost = (request) => {
      const { pathname } = new URL(request.url());
      if (request.method() !== "POST") return;
      if (pathname === "/v1/terminal/sessions") sessionPosts.push(request);
      else if (/\/v1\/terminal\/sessions\/[^/]+\/input$/.test(pathname)) inputPosts.push(request);
    };
    page.on("request", countPost);
    await page.route("**/v1/terminal/sessions", (route) =>
      route.request().method() === "POST"
        ? route.fulfill({
            status: 409,
            contentType: "application/json",
            body: JSON.stringify({ error: { code: "terminal_limit" } }),
          })
        : route.continue(),
    );
    await railToggle.click();
    await pane.waitFor({ state: "hidden" });
    await railToggle.click();
    await pane.waitFor({ state: "visible" });
    await until(() => sessionPosts.length === 1, "the reopen asks the server for a session");
    await runLine(page, "echo kh-refused-target");
    await delay(500);
    assert.equal(inputPosts.length, 0, "no input is sent after a refused start");
    assert.equal(await page.getByTestId("terminal-cwd").textContent(), "", "no cwd label from the old session");
    assert.match(
      await page.getByTestId("terminal-notice").textContent(),
      /Open the terminal to start a session/,
      "the pane says it has no session",
    );
    page.off("request", countPost);
    await page.unroute("**/v1/terminal/sessions");

    assert.deepEqual(pageErrors, [], "no uncaught page errors");
    console.log("PASS workspace-terminal");
  } finally {
    // Best effort: end the session this spec left running (the owner's shell belongs to the harness).
    // A refused start (409) carries no session id, so take the last start that succeeded.
    const live = log.starts.filter((start) => start.status < 300).at(-1)?.body?.session_id;
    if (page && live && !log.ends.some((end) => end.id === live))
      await page.request
        .delete(harness + "/v1/terminal/sessions/" + live)
        .catch(() => {});
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
