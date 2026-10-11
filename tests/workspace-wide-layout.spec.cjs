// D-055 P2, wide layout: the header button, its shortcut, the Appearance help text and the
// Settings "Workspace" switch drive one preference (body.wide-layout), saved through /v1/ui-state.
// Runs against the shared harness started by scripts/test-ui.sh (HARNESS_URL), so the save is a
// real write and the reload reads it back from the server, not from memory.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");

const harness = (process.env.HARNESS_URL || "").replace(/\/$/, "");
if (!harness)
  throw new Error("HARNESS_URL is required (run through scripts/test-ui.sh)");

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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
async function savedWideLayout(page) {
  const response = await getUiState(page);
  assert.equal(response.status(), 200, "GET /v1/ui-state");
  return (await response.json()).values.wide_layout ?? false;
}

async function waitSaved(page, expected) {
  for (let attempt = 0; attempt < 60; attempt++) {
    if ((await savedWideLayout(page)) === expected) return;
    await delay(100);
  }
  assert.fail("wide_layout never reached " + expected + " on the server");
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
    const foreground = channels(
      getComputedStyle(document.querySelector(target)).color,
    );
    const light = Math.max(luminance(foreground), luminance(background));
    const dark = Math.min(luminance(foreground), luminance(background));
    return (light + 0.05) / (dark + 0.05);
  }, selector);

(async () => {
  const browser = await chromium.launch();
  const pageErrors = [];
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 900 },
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.addInitScript(() =>
      localStorage.setItem("keepharness-tour-seen", "0.16.0"),
    );
    await page.goto(harness + "/");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });

    const layout = () =>
      page.evaluate(() => {
        const messages = document.querySelector("#messages");
        const composer = document.querySelector(".composer-area");
        return {
          wide: document.body.classList.contains("wide-layout"),
          messagesMax: getComputedStyle(messages).maxWidth,
          messagesPadding: getComputedStyle(messages).paddingLeft,
          messagesWidth: Math.round(messages.getBoundingClientRect().width),
          composerMax: getComputedStyle(composer).maxWidth,
          pressed: document
            .querySelector('[data-testid="wide-layout-header-toggle"]')
            .getAttribute("aria-pressed"),
          switchOn: document.querySelector('[data-testid="wide-layout-toggle"]')
            .checked,
        };
      });

    // Default: no class, the 1040 px column and the 768 px reading padding are unchanged.
    const headerButton = page.getByTestId("wide-layout-header-toggle");
    assert.equal(await headerButton.getAttribute("aria-label"), "Wide layout");
    const defaults = await layout();
    assert.equal(defaults.wide, false, "no class by default");
    assert.equal(defaults.pressed, "false", "header button starts unpressed");
    assert.equal(defaults.switchOn, false, "Settings switch starts off");
    assert.equal(defaults.messagesMax, "1040px", "default column unchanged");
    assert.equal(defaults.composerMax, "672px", "default composer unchanged");
    assert.equal(await savedWideLayout(page), false, "default is saved as false");

    // Header button: class, aria-pressed, the widened column and the server write.
    await headerButton.click();
    await page.waitForFunction(() =>
      document.body.classList.contains("wide-layout"),
    );
    const widened = await layout();
    assert.equal(widened.pressed, "true", "header button reports pressed");
    assert.equal(widened.switchOn, true, "Settings switch follows the header");
    assert.equal(widened.messagesMax, "none");
    assert.equal(widened.composerMax, "none");
    assert.equal(widened.messagesPadding, "24px");
    assert.ok(
      widened.messagesWidth > defaults.messagesWidth,
      "the chat column is wider than the 1040 px default",
    );
    await waitSaved(page, true);

    // Shortcut: Ctrl+Shift+L on Linux (Cmd+Shift+L on macOS) toggles it back off and on again.
    await page.keyboard.press("Control+Shift+L");
    await page.waitForFunction(() =>
      !document.body.classList.contains("wide-layout"),
    );
    assert.equal((await layout()).pressed, "false");
    await waitSaved(page, false);
    await page.keyboard.press("Control+Shift+L");
    await page.waitForFunction(() =>
      document.body.classList.contains("wide-layout"),
    );
    await waitSaved(page, true);

    // The shortcuts dialog lists the new row.
    await page.keyboard.press("Control+/");
    const shortcutList = page.locator("#keyboard-shortcuts-list");
    await shortcutList.waitFor({ state: "visible" });
    assert.match(await shortcutList.textContent(), /Wide layout/);
    await page.keyboard.press("Escape");

    // Settings > Appearance > Workspace: one switch, help text, and the replaced save line.
    await page.evaluate(() => openSettings("appearance"));
    const group = page.getByTestId("workspace-group");
    await group.waitFor({ state: "visible" });
    assert.equal(
      await group.locator("input").count(),
      3,
      "the Workspace group holds the wide-layout, file-editor and terminal switches",
    );
    const appearance = page.locator("#settings-appearance");
    const help = await appearance.locator(".shortcut-help").textContent();
    assert.match(help, /\+Shift\+L: wide layout/);
    const appearanceText = await appearance.textContent();
    assert.doesNotMatch(appearanceText, /saved in this browser/);
    assert.match(appearanceText, /Saved for you on this computer\./);

    // The Settings switch and the header button share one function: each follows the other.
    const settingsSwitch = page.getByTestId("wide-layout-toggle");
    assert.equal(await settingsSwitch.isChecked(), true);
    await settingsSwitch.uncheck();
    await page.waitForFunction(() =>
      !document.body.classList.contains("wide-layout"),
    );
    assert.equal((await layout()).pressed, "false", "header follows the switch");
    await waitSaved(page, false);
    await settingsSwitch.check();
    await page.waitForFunction(() =>
      document.body.classList.contains("wide-layout"),
    );
    assert.equal((await layout()).pressed, "true");
    await waitSaved(page, true);

    // Reload: the choice is read back from /v1/ui-state.
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.waitForFunction(() =>
      document.body.classList.contains("wide-layout"),
    );
    assert.equal((await layout()).pressed, "true", "survives a reload");

    // The preference never reaches localStorage under a new key.
    const wideKeys = await page.evaluate(() =>
      Object.keys(localStorage).filter((key) => /wide/i.test(key)),
    );
    assert.deepEqual(wideKeys, [], "no wide-layout localStorage key");

    // Contrast in a light and a dark palette, with the wide layout on.
    await openSettingsContrast(page);
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
      const icon = await contrastOf(page, '[data-testid="wide-layout-header-toggle"]');
      const label = await contrastOf(page, '[data-testid="workspace-group"] label');
      const text = await contrastOf(page, "#settings-appearance .shortcut-help");
      assert.ok(icon >= 3, palette + " header icon contrast " + icon.toFixed(2));
      assert.ok(label >= 4.5, palette + " switch label contrast " + label.toFixed(2));
      assert.ok(text >= 4.5, palette + " help text contrast " + text.toFixed(2));
    }

    assert.deepEqual(pageErrors, [], "no uncaught page errors");
    console.log("PASS workspace-wide-layout");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

async function openSettingsContrast(page) {
  await page.evaluate(() => openSettings("appearance"));
  await page.getByTestId("workspace-group").waitFor({ state: "visible" });
}
