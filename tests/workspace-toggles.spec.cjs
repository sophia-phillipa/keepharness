// D-055 P8, workspace toggles: all eight on/off combinations of wide layout, file editor and terminal
// at 1440x900 (header toggles, aria-pressed, body class, pane visibility, side-by-side geometry and
// no horizontal overflow), then the overlay rule at 900x800: the editor closes when the terminal opens.
//
// Runs against the shared harness started by scripts/test-ui.sh (HARNESS_URL) with the real backend.
// Preferences go through the real /v1/ui-state; the terminal pane opens a real shell in sem-projeto,
// so the browser context carries the owner's approval session, as tests/workspace-terminal.spec.cjs does.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const harness = (process.env.HARNESS_URL || "").replace(/\/$/, "");
if (!harness)
  throw new Error("HARNESS_URL is required (run through scripts/test-ui.sh)");

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const SESSION_COOKIE = "harness_session";

// Same enrollment as tests/workspace-terminal.spec.cjs: the owner's session, issued by the test server's config.
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

// The owner's ui-state read can answer 429 while the suite runs in parallel. Wait out Retry-After
// (seconds, 1 s by default) and read again; a wait is not a failed poll, so a deadline bounds it.
async function getUiState(page) {
  const deadline = Date.now() + 30000;
  for (;;) {
    const response = await page.request.get(harness + "/v1/ui-state");
    if (response.status() !== 429 || Date.now() > deadline) return response;
    await delay((Number(response.headers()["retry-after"]) || 1) * 1000);
  }
}

// /v1/ui-state answers the owner's session with { values }; a missing key reads as its default.
async function savedValue(page, key) {
  const response = await getUiState(page);
  assert.equal(response.status(), 200, "GET /v1/ui-state");
  return (await response.json()).values[key];
}

// A flag the UI never wrote reads as off (the key is missing), so off means "not true" here.
async function waitSavedFlag(page, key, on) {
  for (let attempt = 0; attempt < 300; attempt++) {
    if (((await savedValue(page, key)) === true) === on) return;
    await delay(100);
  }
  assert.fail(key + " never reached " + (on ? "on" : "off") + " on the server");
}

// The three options, with the header button and the Settings > Appearance > Workspace switch for each.
const OPTIONS = [
  {
    key: "wide_layout",
    button: "#wide-toggle",
    settings: "#wide-layout-toggle",
  },
  {
    key: "editor_open",
    button: "#editor-toggle",
    settings: "#editor-settings-toggle",
  },
  {
    key: "terminal_open",
    button: "#terminal-toggle",
    settings: "#terminal-settings-toggle",
  },
];

// The eight combinations, in a fixed order so a failure names its case.
const COMBINATIONS = [];
for (const wide_layout of [false, true])
  for (const editor_open of [false, true])
    for (const terminal_open of [false, true])
      COMBINATIONS.push({ wide_layout, editor_open, terminal_open });

// Clicks only the header buttons whose aria-pressed differs from the wanted state.
async function setOptions(page, want) {
  for (const option of OPTIONS) {
    const pressed =
      (await page.locator(option.button).getAttribute("aria-pressed")) ===
      "true";
    if (pressed !== want[option.key]) await page.locator(option.button).click();
  }
}

async function checkOptions(page, want, label) {
  for (const option of OPTIONS) {
    const on = want[option.key];
    assert.equal(
      await page.locator(option.button).getAttribute("aria-pressed"),
      String(on),
      label + " aria-pressed on " + option.button,
    );
    assert.equal(
      await page.locator(option.settings).evaluate((input) => input.checked),
      on,
      label + " Settings switch " + option.settings,
    );
  }
  assert.equal(
    await page.evaluate(() => document.body.classList.contains("wide-layout")),
    want.wide_layout,
    label + " body.wide-layout",
  );
  assert.equal(
    await page.locator("#editor-pane").isVisible(),
    want.editor_open,
    label + " editor pane visible",
  );
  assert.equal(
    await page.locator("#terminal-pane").isVisible(),
    want.terminal_open,
    label + " terminal pane visible",
  );
}

// The editor docks beside main: its left edge is at or past main's right edge, on the same top row.
// Nothing may overflow horizontally at this width.
async function checkGeometry(page, want, label) {
  const measured = await page.evaluate(() => {
    const box = (element) => {
      const rect = element.getBoundingClientRect();
      return { left: rect.left, right: rect.right, top: rect.top };
    };
    const editor = document.getElementById("editor-pane");
    return {
      main: box(document.querySelector("main")),
      editor: editor.hidden ? null : box(editor),
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
    };
  });
  assert(
    measured.scrollWidth <= measured.innerWidth,
    label + " horizontal overflow " + JSON.stringify(measured),
  );
  if (!want.editor_open) return;
  assert(measured.editor, label + " editor box");
  assert(
    measured.editor.left >= measured.main.right - 1,
    label + " editor is beside main " + JSON.stringify(measured),
  );
  assert(
    Math.abs(measured.editor.top - measured.main.top) <= 2,
    label + " editor shares the top row of main " + JSON.stringify(measured),
  );
}

(async () => {
  const browser = await chromium.launch();
  const pageErrors = [];
  const sessionIds = [];
  const sessionValue = enrolledSession();
  try {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 900 },
    });
    await context.addCookies([
      { name: SESSION_COOKIE, value: sessionValue, url: harness },
    ]);
    const page = await context.newPage();
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("response", async (response) => {
      const { pathname } = new URL(response.url());
      if (
        pathname === "/v1/terminal/sessions" &&
        response.request().method() === "POST"
      ) {
        const body = await response.json().catch(() => null);
        if (body?.session_id) sessionIds.push(body.session_id);
      }
    });
    await page.addInitScript(() =>
      localStorage.setItem("keepharness-tour-seen", "0.16.0"),
    );

    // The server starts from an empty store (test-ui.sh resets it), so every combination is a real change.
    await page.goto(harness + "/");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });

    for (const want of COMBINATIONS) {
      const label = JSON.stringify(want);
      await setOptions(page, want);
      await checkOptions(page, want, label);
      await checkGeometry(page, want, label);
      for (const key of Object.keys(want))
        await waitSavedFlag(page, key, want[key]);
    }

    // Overlay rule below 1000 px: the editor is a modal overlay; opening the terminal closes it.
    await setOptions(page, {
      wide_layout: false,
      editor_open: false,
      terminal_open: false,
    });
    await waitSavedFlag(page, "editor_open", false);
    await page.setViewportSize({ width: 900, height: 800 });
    await page.locator("#editor-toggle").click();
    await page.getByTestId("editor-pane").waitFor({ state: "visible" });
    assert.equal(
      await page.getByTestId("editor-pane").getAttribute("aria-modal"),
      "true",
      "editor is the overlay first",
    );

    // The editor overlay covers the header, so the terminal opens with its shortcut, as a user would.
    await page.keyboard.press("Control+Backquote");
    await page.getByTestId("terminal-pane").waitFor({ state: "visible" });
    await page.getByTestId("editor-pane").waitFor({ state: "hidden" });
    assert.equal(
      await page.locator("#editor-toggle").getAttribute("aria-pressed"),
      "false",
      "editor toggle released",
    );
    assert.equal(
      await page
        .locator("#editor-settings-toggle")
        .evaluate((input) => input.checked),
      false,
      "editor Settings switch released",
    );
    assert.equal(
      await page.evaluate(() =>
        document.body.classList.contains("editor-open"),
      ),
      false,
      "body no longer marks the editor open",
    );
    const overlays = await page.locator('[aria-modal="true"]:visible').count();
    assert(overlays <= 1, "at most one overlay at 900 px, found " + overlays);
    await waitSavedFlag(page, "editor_open", false);
    await waitSavedFlag(page, "terminal_open", true);

    assert.deepEqual(pageErrors, [], "no page errors");
    console.log(
      "workspace-toggles: " +
        COMBINATIONS.length +
        " combinations and the 900 px overlay rule passed",
    );
  } finally {
    // End the shells this run opened: DELETE is idempotent, so a session already gone is fine.
    for (const id of sessionIds)
      await fetch(harness + "/v1/terminal/sessions/" + id, {
        method: "DELETE",
        headers: { Cookie: SESSION_COOKIE + "=" + sessionValue },
      }).catch(() => {});
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
