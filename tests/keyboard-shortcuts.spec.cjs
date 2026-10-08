const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");

const origin = process.env.HARNESS_URL;
if (!origin) throw new Error("HARNESS_URL is required (run through scripts/test-ui.sh)");

function contrast(rgb1, rgb2) {
  const values = (rgb) => {
    const channels = rgb.match(/[\d.]+/g).slice(0, 3).map(Number);
    return rgb.startsWith("color(srgb") ? channels.map((value) => value * 255) : channels;
  };
  const luminance = (rgb) => {
    const [r, g, b] = values(rgb).map((value) => {
      value /= 255;
      return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const [lighter, darker] = [luminance(rgb1), luminance(rgb2)].sort((a, b) => b - a);
  return (lighter + 0.05) / (darker + 0.05);
}

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 }, colorScheme: "dark" });
    let submissions = 0;
    await page.context().route("**/v1/**", (route) => {
      const url = new URL(route.request().url());
      if (route.request().method() === "POST" && url.pathname === "/v1/jobs") submissions += 1;
      const data =
        url.pathname === "/v1/projects"
          ? { projects: ["project-a", "sem-projeto"], details: { "project-a": { label: "Project Alpha" } } }
          : url.pathname === "/v1/models"
            ? { models: [{ id: "fixture", name: "Fixture", backend: "local", efforts: ["low"], permissions: { upload: true } }], providers: { local: true }, uploads_enabled: true }
            : url.pathname === "/v1/conversations"
              ? { conversations: [] }
              : url.pathname === "/v1/files"
                ? { file_id: "shortcut-attachment" }
              : url.pathname === "/v1/activity"
                ? { jobs: [], counts: { running: 0, queued: 0, needs_you: 0 }, providers: [] }
                : url.pathname === "/v1/version"
                  ? { version: "fixture", build: "fixture" }
                  : {};
      return route.fulfill({ json: data });
    });
    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
    await page.goto(origin);
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.selectOption("#project", "project-a", { force: true });
    await page.fill("#prompt", "Draft with / ordinary slash");
    await page.locator("#file").setInputFiles({ name: "reference.txt", mimeType: "text/plain", buffer: Buffer.from("fixture") });
    await page.waitForFunction(() => files.length === 1 && uploads === 0);

    const dialog = page.locator("#keyboard-shortcuts-dialog");
    const input = page.locator("#keyboard-shortcuts-search");
    const before = {
      draft: await page.locator("#prompt").inputValue(),
      project: await page.locator("#project").inputValue(),
      provider: await page.locator("#model").inputValue(),
      attachments: await page.locator("#attachments > *").count(),
    };

    await page.locator("#prompt").focus();
    await page.keyboard.type(" / still typing");
    assert.equal(await dialog.count(), 1);
    assert.equal(await dialog.isVisible(), false, "an ordinary slash in the composer does not open a dialog");
    await page.keyboard.press("Control+/");
    await dialog.waitFor({ state: "visible" });
    assert.equal(await input.evaluate((node) => node === document.activeElement), true);
    assert.deepEqual(await dialog.locator(".keyboard-shortcut-row").allTextContents(), [
      "Search KeepHarnessCtrl K",
      "Search KeepHarnessCtrl Shift P",
      "Open SettingsCtrl ,",
      "Toggle run consoleCtrl J",
      "Go backCtrl [",
      "Go forwardCtrl ]",
      "Keyboard shortcutsCtrl /",
    ]);
    assert.equal(await page.locator("#keyboard-shortcuts-list").getAttribute("role"), "list");
    assert.equal(await dialog.locator('.keyboard-shortcut-row[role="listitem"]').count(), 7);
    assert.equal(await dialog.getByText("Focus composer", { exact: true }).count(), 0, "Focus composer has no invented binding");
    console.log("PASS B1 reference lists only supported bindings and omits a Focus composer binding");

    await input.fill("shift p");
    assert.equal(await dialog.locator(".keyboard-shortcut-row").count(), 1, "searches by key");
    await input.fill("Ctrl+Shift+P");
    assert.equal(await dialog.locator(".keyboard-shortcut-row").count(), 1, "searches by advertised key notation");
    await input.fill("settings");
    assert.equal(await dialog.locator(".keyboard-shortcut-row").count(), 1, "searches by action");
    await page.keyboard.press("ArrowDown");
    assert.equal(await dialog.locator(".keyboard-shortcut-row").evaluate((node) => node === document.activeElement), true);
    await input.fill("unsupported action");
    assert.match(await page.locator("#keyboard-shortcuts-status").innerText(), /No shortcut matched/);
    console.log("PASS B2 action/key filtering, arrow navigation, and explicit empty state");
    await page.keyboard.press("Escape");
    await dialog.waitFor({ state: "hidden" });
    assert.equal(await page.locator("#prompt").evaluate((node) => node === document.activeElement), true, "Escape returns focus");
    assert.deepEqual({
      draft: await page.locator("#prompt").inputValue(),
      project: await page.locator("#project").inputValue(),
      provider: await page.locator("#model").inputValue(),
      attachments: await page.locator("#attachments > *").count(),
    }, { ...before, draft: before.draft + " / still typing" });
    assert.equal(submissions, 0, "opening and filtering never submits a turn");
    console.log("PASS B3 keyboard close returns focus and preserves draft, route, provider, and attachments");

    await page.click("#search-conversations");
    await page.fill("#conversation-search", "keyboard shortcuts");
    await page.getByRole("button", { name: /Keyboard shortcuts/ }).click();
    await dialog.waitFor({ state: "visible" });
    await page.keyboard.press("Escape");
    assert.equal(await page.locator("#search-conversations").evaluate((node) => node === document.activeElement), true, "command activation returns focus to the search trigger");
    console.log("PASS B3b command search opens the reference and returns focus to its trigger");

    // Every advertised binding invokes its named action in a supported context.
    await page.keyboard.press("Control+k");
    await page.locator("#conversation-search-dialog").waitFor({ state: "visible" });
    await page.keyboard.press("Escape");
    await page.keyboard.press("Control+Shift+p");
    await page.locator("#conversation-search-dialog").waitFor({ state: "visible" });
    await page.keyboard.press("Escape");
    await page.keyboard.press("Control+,");
    await page.locator("#settings-dialog").waitFor({ state: "visible" });
    await page.keyboard.press("Control+[");
    await page.locator("#settings-dialog").waitFor({ state: "hidden" });
    await page.keyboard.press("Control+]");
    await page.locator("#settings-dialog").waitFor({ state: "visible" });
    await page.click("#settings-close");
    await page.keyboard.press("Control+j");
    await page.locator("#run-console").waitFor({ state: "visible" });
    await page.keyboard.press("Control+j");
    await page.locator("#run-console").waitFor({ state: "hidden" });
    await page.keyboard.press("Control+/");
    await dialog.waitFor({ state: "visible" });
    assert.equal(await page.locator("#keyboard-shortcuts-dialog[open]").count(), 1, "one handler opens one reference");
    await page.click("#keyboard-shortcuts-close");
    console.log("PASS B4 every advertised binding invokes its named action without duplicate submission");

    for (const palette of ["paper", "amethyst"]) {
      await page.evaluate((name) => HarnessTheme.apply(name, false), palette);
      await page.keyboard.press("Control+/");
      const colors = await dialog.locator(".keyboard-shortcut-row").first().evaluate((node) => ({
        color: getComputedStyle(node).color,
        background: getComputedStyle(node.closest("dialog")).backgroundColor,
      }));
      assert(contrast(colors.color, colors.background) >= 4.5, palette + " shortcut text contrast: " + JSON.stringify(colors));
      await page.keyboard.press("Escape");
    }
    console.log("PASS B5 paper and amethyst shortcut reference uses readable theme colors");

    await page.addInitScript(() => {
      Object.defineProperty(navigator, "platform", { configurable: true, value: "MacIntel" });
      localStorage.setItem("keepharness-tour-seen", "0.16.0");
    });
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.keyboard.press("Meta+k");
    await page.locator("#conversation-search-dialog").waitFor({ state: "visible" });
    await page.keyboard.press("Escape");
    await page.keyboard.press("Meta+Shift+p");
    await page.locator("#conversation-search-dialog").waitFor({ state: "visible" });
    await page.keyboard.press("Escape");
    await page.keyboard.press("Meta+,");
    await page.locator("#settings-dialog").waitFor({ state: "visible" });
    await page.keyboard.press("Meta+[");
    await page.locator("#settings-dialog").waitFor({ state: "hidden" });
    await page.keyboard.press("Meta+]");
    await page.locator("#settings-dialog").waitFor({ state: "visible" });
    await page.click("#settings-close");
    await page.keyboard.press("Meta+j");
    await page.locator("#run-console").waitFor({ state: "visible" });
    await page.keyboard.press("Meta+j");
    await page.locator("#run-console").waitFor({ state: "hidden" });
    await page.keyboard.press("Meta+/");
    assert.match(await page.locator(".keyboard-shortcut-row").first().innerText(), /⌘ K/);
    console.log("PASS B6 every macOS Command binding invokes its named action and uses the Command symbol");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
