// D-055 P5, file editor pane. Written before the implementation (red on purpose).
//
// Testids the implementer must render (the block is the contract for this spec):
//   editor-toggle          rail button #editor-toggle (icon file-code, title "File editor"), aria-pressed
//   editor-settings-toggle Settings > Appearance > Workspace switch "File editor" (same group as
//                          wide-layout-toggle, which stays)
//   editor-pane            #editor-pane column between the chat and the side panel; hidden when closed;
//                          below 1000 px role="dialog" + aria-modal="true" (overlay, focus trapped)
//   editor-empty           empty state, text "Open a file from the Files panel"
//   editor-path            file path label in the pane header (project-relative path)
//   editor-dirty           dirty marker on the pane title, visible only while the buffer differs
//   editor-textarea        <textarea> with the file text
//   editor-save            Save button (Ctrl/Cmd + S does the same)
//   editor-close           close button in the pane header
//   editor-resize-handle   separator between the chat and the pane; dragging sets editor_width
//   editor-notice          role="status" notices: read-only, running turn ("A turn is running in this
//                          project; the agent may also change this file")
//   editor-conflict        role="alert" notice after a 412, with editor-reload and editor-overwrite buttons
//   editor-dirty-dialog    <dialog> asked when closing or switching with unsaved text, with
//                          editor-dirty-save, editor-dirty-discard and editor-dirty-cancel buttons
// Shortcut: Control+Shift+E (Cmd+Shift+E on macOS). Escape closes the overlay and returns focus to
// editor-toggle. Settings help text (.shortcut-help) includes "+Shift+E: file editor".
// Existing hooks reused: #files-toggle, #files-view, .file-root[data-root-id], [role="treeitem"][data-path]
// (with the editor open, a double-click or Enter on a file row opens it in the editor; single click still
// only selects, and Enter attaches while the editor is closed).
//
// Runs against the shared harness started by scripts/test-ui.sh (HARNESS_URL). Preferences go through
// the real /v1/ui-state. The project file endpoints are answered by an in-page fake, because the
// test harness has no project folder on disk and the editor is local-owner only; the backend
// contract itself is covered by tests/test_project_file_content.py.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");

const harness = (process.env.HARNESS_URL || "").replace(/\/$/, "");
if (!harness)
  throw new Error("HARNESS_URL is required (run through scripts/test-ui.sh)");

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const PROJECT = "sem-projeto";

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

async function waitSaved(page, key, expected) {
  for (let attempt = 0; attempt < 60; attempt++) {
    if ((await savedValue(page, key)) === expected) return;
    await delay(100);
  }
  assert.fail(key + " never reached " + JSON.stringify(expected) + " on the server");
}

// A pixel width is saved as measured by the layout, so allow the rounding of the border.
async function waitSavedNear(page, key, expected) {
  for (let attempt = 0; attempt < 60; attempt++) {
    const value = await savedValue(page, key);
    if (typeof value === "number" && Math.abs(value - expected) <= 2) return;
    await delay(100);
  }
  assert.fail(key + " never reached about " + expected + "px on the server");
}

// Fake owner files: text, EOL class and the ETag the server would answer with.
function fakeFiles() {
  return {
    "notes.txt": { text: "alpha\nbeta\n", eol: "lf", etag: "etag-notes-1" },
    "windows.txt": { text: "one\r\ntwo\r\n", eol: "crlf", etag: "etag-win-1" },
  };
}

// Answers the Files tree (roots and one folder) and the content GET/PUT from `files`.
// Every PUT is recorded with its If-Match header and text; a stale If-Match answers 412 with the
// current ETag, like routes/files.py.
async function fakeProjectFiles(page, files, log) {
  await page.route(
    (url) => url.pathname === "/v1/project-files",
    async (route) => {
      const query = new URL(route.request().url()).searchParams;
      if (query.get("view") !== "tree") return route.fallback();
      if (!query.get("root_id"))
        return route.fulfill({
          json: { roots: [{ id: "home", label: "Home", path: "/fixture" }] },
        });
      if (query.get("root_id") === "home" && (query.get("path") ?? "") === "")
        return route.fulfill({
          json: {
            root_id: "home",
            path: "",
            limited: false,
            entries: Object.keys(files).map((name) => ({
              name,
              label: name,
              path: name,
              type: "file",
            })),
          },
        });
      return route.fallback();
    },
  );
  await page.route(
    (url) => url.pathname === "/v1/project-files/content",
    async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const name = url.searchParams.get("path");
      const file = files[name];
      if (!file)
        return route.fulfill({
          status: 404,
          json: { code: "file_not_found", message: "file_not_found", retryable: false },
        });
      if (request.method() === "GET") {
        log.gets.push({ project_id: url.searchParams.get("project_id"), path: name });
        return route.fulfill({
          status: 200,
          headers: { ETag: file.etag },
          json: {
            text: file.text,
            etag: file.etag,
            size: file.text.length,
            eol: file.eol,
            writable: true,
          },
        });
      }
      const ifMatch = request.headers()["if-match"];
      const body = request.postDataJSON();
      log.puts.push({ path: name, ifMatch, text: body.text });
      if (ifMatch !== file.etag)
        return route.fulfill({
          status: 412,
          headers: { ETag: file.etag },
          json: { code: "file_changed", message: "file_changed", retryable: false },
        });
      file.text = body.text;
      file.etag = "etag-" + name + "-" + log.puts.length;
      return route.fulfill({
        status: 200,
        headers: { ETag: file.etag },
        json: { etag: file.etag, size: file.text.length },
      });
    },
  );
}

// Opens the Files panel and the home folder of the fake roots, if they are not open yet.
async function showFiles(page) {
  if (await page.locator("#activity-panel").isHidden()) await page.locator("#panel-toggle").click();
  if (!(await page.locator("#files-view").isVisible()))
    await page.locator("#files-toggle").click();
  if (await page.locator("#workspace-system-files").isHidden()) await page.locator('[data-testid="accordion-head-system-files"]').click();
  if (await page.locator("#files-tree").isHidden())
    await page.locator('.file-root[data-root-id="home"]').click();
}

async function openFromFiles(page, name) {
  await showFiles(page);
  // Wait for this load's content response: the path label may already show the same name.
  const loaded = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      response.url().includes("/v1/project-files/content") &&
      new URL(response.url()).searchParams.get("path") === name,
  );
  await page.locator('[role="treeitem"][data-path="' + name + '"]').dblclick();
  await loaded;
  await page.waitForFunction(
    (expected) =>
      document.querySelector('[data-testid="editor-path"]')?.textContent.includes(expected),
    name,
  );
}

async function editorOpen(page) {
  return page.locator('[data-testid="editor-pane"]').isVisible();
}

// Settings > Appearance > Workspace, the same way the wide-layout spec opens it.
async function openSettingsWorkspace(page) {
  await page.evaluate(() => openSettings("appearance"));
  await page.getByTestId("workspace-group").waitFor({ state: "visible" });
}

// A running turn in this project, as the activity poll would report it.
function runningSnapshot(page) {
  return page.evaluate(
    (project) =>
      applyActivitySnapshot({
        jobs: [
          {
            id: "job-running",
            conversation_id: "conv-running",
            project,
            state: "running",
            work_item: "Editing",
          },
        ],
        needs_you: [],
        counts: { running: 1, queued: 0, needs_you: 0 },
      }),
    PROJECT,
  );
}

// WCAG contrast of the element's colour against the first opaque background above it.
const contrastOf = (page, selector) =>
  page.evaluate((target) => {
    const channels = (css) => css.match(/[\d.]+/g).map(Number);
    const luminance = ([r, g, b]) => {
      const [R, G, B] = [r, g, b].map((value) => {
        const c = value / 255;
        return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * R + 0.7152 * G + 0.0722 * B;
    };
    let background = [255, 255, 255];
    for (let node = document.querySelector(target); node; node = node.parentElement) {
      const [r, g, b, a = 1] = channels(getComputedStyle(node).backgroundColor);
      if (a > 0) {
        background = [r, g, b];
        break;
      }
    }
    const foreground = channels(getComputedStyle(document.querySelector(target)).color);
    const light = Math.max(luminance(foreground), luminance(background));
    const dark = Math.min(luminance(foreground), luminance(background));
    return (light + 0.05) / (dark + 0.05);
  }, selector);

(async () => {
  const browser = await chromium.launch();
  const pageErrors = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.addInitScript(() =>
      localStorage.setItem("keepharness-tour-seen", "0.16.0"),
    );
    const files = fakeFiles();
    const log = { gets: [], puts: [] };
    await fakeProjectFiles(page, files, log);
    await page.goto(harness + "/");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });

    // 1. Own switch, rail/header toggle with aria-pressed, shortcut row and help text.
    const railToggle = page.getByTestId("editor-toggle");
    assert.equal(await railToggle.getAttribute("title"), "File editor");
    assert.equal(await railToggle.getAttribute("aria-pressed"), "false", "starts unpressed");
    assert.equal(await editorOpen(page), false, "chat-first: the pane starts closed");
    assert.equal(await savedValue(page, "editor_open"), false, "default is saved as false");

    await openSettingsWorkspace(page);
    const group = page.getByTestId("workspace-group");
    const settingsSwitch = page.getByTestId("editor-settings-toggle");
    assert.equal(await settingsSwitch.isChecked(), false, "Settings switch starts off");
    assert.match(await group.textContent(), /File editor/);
    const appearance = page.locator("#settings-appearance");
    assert.match(
      await appearance.locator(".shortcut-help").textContent(),
      /\+Shift\+E: file editor/,
    );
    await page.keyboard.press("Escape");

    await railToggle.click();
    await page.getByTestId("editor-pane").waitFor({ state: "visible" });
    assert.equal(await railToggle.getAttribute("aria-pressed"), "true", "rail reports pressed");
    assert.match(
      await page.getByTestId("editor-empty").textContent(),
      /Open a file from the Files panel/,
    );
    await waitSaved(page, "editor_open", true);

    // The shortcut toggles it closed; the Settings switch follows the one shared owner.
    await page.keyboard.press("Control+Shift+E");
    await page.waitForFunction(() => !document.querySelector('[data-testid="editor-pane"]')?.offsetParent);
    assert.equal(await railToggle.getAttribute("aria-pressed"), "false");
    await waitSaved(page, "editor_open", false);
    await openSettingsWorkspace(page);
    assert.equal(await settingsSwitch.isChecked(), false, "switch follows the shortcut");
    await settingsSwitch.check();
    await page.getByTestId("editor-pane").waitFor({ state: "visible" });
    assert.equal(await railToggle.getAttribute("aria-pressed"), "true", "rail follows the switch");
    await waitSaved(page, "editor_open", true);
    await page.keyboard.press("Escape");

    await page.keyboard.press("Control+/");
    const shortcutList = page.locator("#keyboard-shortcuts-list");
    await shortcutList.waitFor({ state: "visible" });
    assert.match(await shortcutList.textContent(), /File editor/);
    await page.keyboard.press("Escape");

    // 2. Preferences persist through /v1/ui-state, never through localStorage.
    const pane = page.getByTestId("editor-pane");
    const handle = page.getByTestId("editor-resize-handle");
    const before = await handle.boundingBox();
    await page.mouse.move(before.x + before.width / 2, before.y + before.height / 2);
    await page.mouse.down();
    await page.mouse.move(before.x - 80, before.y + before.height / 2, { steps: 8 });
    await page.mouse.up();
    const width = Math.round((await pane.boundingBox()).width);
    await waitSavedNear(page, "editor_width", width);
    const editorKeys = await page.evaluate(() =>
      Object.keys(localStorage).filter((key) => /editor/i.test(key)),
    );
    assert.deepEqual(editorKeys, [], "no editor localStorage key");

    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await pane.waitFor({ state: "visible" });
    assert.equal(await railToggle.getAttribute("aria-pressed"), "true", "survives a reload");
    assert.ok(
      Math.abs(Math.round((await pane.boundingBox()).width) - width) <= 2,
      "editor width survives a reload",
    );

    // 3. Open a file from the Files panel, edit it and save with If-Match.
    await openFromFiles(page, "notes.txt");
    const textarea = page.getByTestId("editor-textarea");
    assert.equal(await textarea.inputValue(), "alpha\nbeta\n");
    assert.equal(log.gets.at(-1).project_id, PROJECT, "GET carries the project");
    assert.equal(log.gets.at(-1).path, "notes.txt", "GET carries the path");
    assert.equal(await page.getByTestId("editor-dirty").isVisible(), false, "clean at open");

    await textarea.fill("alpha\nbeta\ngamma\n");
    assert.equal(await page.getByTestId("editor-dirty").isVisible(), true, "dirty marker shows");
    await page.getByTestId("editor-save").click();
    await eventually(() => log.puts.length === 1, "one PUT after save");
    assert.equal(log.puts[0].path, "notes.txt");
    assert.equal(log.puts[0].ifMatch, "etag-notes-1", "PUT sends If-Match with the open ETag");
    assert.equal(log.puts[0].text, "alpha\nbeta\ngamma\n");
    await page.waitForFunction(() =>
      !document.querySelector('[data-testid="editor-dirty"]')?.offsetParent,
    );

    const etagAfterFirstSave = files["notes.txt"].etag;
    await textarea.fill("alpha\nbeta\ngamma\ndelta\n");
    await page.keyboard.press("Control+s");
    await eventually(() => log.puts.length === 2, "Ctrl+S saves too");
    assert.equal(log.puts[1].ifMatch, etagAfterFirstSave, "second PUT uses the ETag of the first save");
    assert.equal(log.puts[1].text, "alpha\nbeta\ngamma\ndelta\n");

    // 4. CRLF file: the client edits with \n, the save restores \r\n on every line.
    await openFromFiles(page, "windows.txt");
    await textarea.click();
    await page.keyboard.press("Control+End");
    await page.keyboard.type("three");
    await page.getByTestId("editor-save").click();
    await eventually(() => log.puts.length === 3, "CRLF save reaches the server");
    assert.equal(log.puts[2].path, "windows.txt");
    assert.equal(log.puts[2].text, "one\r\ntwo\r\nthree", "every line keeps CRLF, no bare LF");
    assert.equal(files["windows.txt"].text, "one\r\ntwo\r\nthree", "file keeps its CRLF lines");

    // 5. A 412 keeps the user's text and offers reload or overwrite.
    await openFromFiles(page, "notes.txt");
    files["notes.txt"].etag = "etag-agent-changed";
    await textarea.fill("mine only\n");
    await page.getByTestId("editor-save").click();
    const conflict = page.getByTestId("editor-conflict");
    await conflict.waitFor({ state: "visible" });
    assert.equal(await conflict.getAttribute("role"), "alert");
    assert.equal(await textarea.inputValue(), "mine only\n", "the user's text is kept");
    await page.getByTestId("editor-overwrite").click();
    await eventually(
      () => log.puts.at(-1).ifMatch === "etag-agent-changed" && log.puts.at(-1).text === "mine only\n",
      "overwrite retries with the ETag the server reported",
    );
    await conflict.waitFor({ state: "hidden" });

    // 6. Saving while a turn runs in the same project shows a notice; the save still goes through.
    await openFromFiles(page, "notes.txt");
    await runningSnapshot(page);
    await textarea.fill("running edit\n");
    const putsBefore = log.puts.length;
    await page.getByTestId("editor-save").click();
    const notice = page.getByTestId("editor-notice");
    await notice.waitFor({ state: "visible" });
    assert.match(await notice.textContent(), /A turn is running in this project/);
    await eventually(() => log.puts.length === putsBefore + 1, "save still reaches the server");

    // 7. Closing or switching with unsaved text asks Save / Discard / Cancel.
    await textarea.fill("unsaved draft\n");
    await railToggle.click();
    const dirtyDialog = page.getByTestId("editor-dirty-dialog");
    await dirtyDialog.waitFor({ state: "visible" });
    await page.getByTestId("editor-dirty-cancel").click();
    assert.equal(await pane.isVisible(), true, "cancel keeps the pane open");
    assert.equal(await textarea.inputValue(), "unsaved draft\n", "cancel keeps the text");
    await railToggle.click();
    await dirtyDialog.waitFor({ state: "visible" });
    await page.getByTestId("editor-dirty-discard").click();
    await pane.waitFor({ state: "hidden" });
    assert.equal(await railToggle.getAttribute("aria-pressed"), "false");

    await railToggle.click();
    await pane.waitFor({ state: "visible" });
    await openFromFiles(page, "notes.txt");
    await textarea.fill("switch draft\n");
    const putsBeforeSwitch = log.puts.length;
    await page.locator('[role="treeitem"][data-path="windows.txt"]').dblclick();
    await dirtyDialog.waitFor({ state: "visible" });
    await page.getByTestId("editor-dirty-save").click();
    await eventually(() => log.puts.length === putsBeforeSwitch + 1, "Save in the dialog writes first");
    assert.equal(log.puts.at(-1).path, "notes.txt");
    await page.waitForFunction(() =>
      document.querySelector('[data-testid="editor-path"]')?.textContent.includes("windows.txt"),
    );

    // 8. Below 1000 px the pane is an overlay: focus is trapped and returns to the toggle.
    await page.setViewportSize({ width: 900, height: 800 });
    await railToggle.click();
    await pane.waitFor({ state: "hidden" });
    await railToggle.click();
    await pane.waitFor({ state: "visible" });
    assert.equal(await pane.getAttribute("role"), "dialog", "overlay is a dialog");
    assert.equal(await pane.getAttribute("aria-modal"), "true");
    for (let step = 0; step < 25; step++) {
      await page.keyboard.press("Tab");
      const inside = await page.evaluate(
        () => !!document.activeElement?.closest('[data-testid="editor-pane"]'),
      );
      assert.ok(inside, "Tab stays inside the overlay at step " + step);
    }
    await page.keyboard.press("Escape");
    await pane.waitFor({ state: "hidden" });
    const focusedAfterClose = await page.evaluate(
      () => document.activeElement?.getAttribute("data-testid"),
    );
    assert.equal(focusedAfterClose, "editor-toggle", "focus returns to the toggle");

    // Phone width (≤ 620 px): the header toggle is dropped (the Settings switch stays).
    await page.setViewportSize({ width: 600, height: 800 });
    assert.equal(await railToggle.count(), 1);
    assert.equal(await railToggle.isVisible(), false, "editor header toggle is hidden at 600 px");
    await page.setViewportSize({ width: 1440, height: 900 });

    // With the editor open, Enter on a file row opens that file in the editor.
    await railToggle.click();
    await pane.waitFor({ state: "visible" });
    await showFiles(page);
    await page.locator('[role="treeitem"][data-path="notes.txt"]').focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(() =>
      document.querySelector('[data-testid="editor-path"]')?.textContent.includes("notes.txt"),
    );
    await railToggle.click();
    await pane.waitFor({ state: "hidden" });

    // A failed Save from the dirty prompt keeps the pane open; the Settings switch and header toggle follow it.
    await openFromFiles(page, "notes.txt");
    files["notes.txt"].etag = "etag-changed-before-close";
    await textarea.fill("stale edit\n");
    await openSettingsWorkspace(page);
    await settingsSwitch.uncheck();
    await dirtyDialog.waitFor({ state: "visible" });
    await page.getByTestId("editor-dirty-save").click();
    await page.getByTestId("editor-conflict").waitFor({ state: "visible" });
    assert.equal(await pane.isVisible(), true, "a failed save keeps the pane open");
    assert.equal(await settingsSwitch.isChecked(), true, "Settings switch matches the open pane");
    assert.equal(await railToggle.getAttribute("aria-pressed"), "true", "header toggle matches the open pane");
    await page.keyboard.press("Escape");
    await railToggle.click();
    await dirtyDialog.waitFor({ state: "visible" });
    await page.getByTestId("editor-dirty-discard").click();
    await pane.waitFor({ state: "hidden" });

    // 9. Contrast in the light (paper) and dark (graphite) palettes.
    await railToggle.click();
    await pane.waitFor({ state: "visible" });
    await openFromFiles(page, "notes.txt");
    for (const [palette, dark] of [
      ["paper", false],
      ["graphite", true],
    ]) {
      await page.evaluate(
        ([name, isDark]) => {
          document.documentElement.dataset.palette = name;
          document.documentElement.dataset.bsTheme = isDark ? "dark" : "light";
        },
        [palette, dark],
      );
      const path = await contrastOf(page, '[data-testid="editor-path"]');
      const body = await contrastOf(page, '[data-testid="editor-textarea"]');
      assert.ok(path >= 4.5, palette + " path contrast " + path.toFixed(2));
      assert.ok(body >= 4.5, palette + " text contrast " + body.toFixed(2));
    }

    assert.deepEqual(pageErrors, [], "no uncaught page errors");
    console.log("PASS workspace-editor");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

// Polls a condition for up to 6 s; used for server-side effects the page only reaches later.
async function eventually(condition, message) {
  for (let attempt = 0; attempt < 60; attempt++) {
    if (condition()) return;
    await delay(100);
  }
  assert.fail(message);
}
