// Keyboard shortcuts and keyboard-only paths.
"use strict";
const { home, dismissTour } = require("../lib/app.cjs");

module.exports = {
  id: "keyboard",
  title: "Keyboard shortcuts",
  async run(op) {
    const page = op.page;
    const active = () => page.evaluate(() => document.activeElement?.id || document.activeElement?.tagName);

    await op.step("home", "Start from the home screen, focus on the page", async () => {
      await home(op);
      await page.evaluate(async () => {
        const { version } = await fetch("/v1/version").then((response) => response.json());
        HarnessPrefs.set("tour_seen", version);
      });
      await dismissTour(op);
      await page.locator("#conversation-title").click();
    }, { critical: true });

    await op.step("ctrl-slash", "Ctrl+/ opens the searchable shortcut reference", async () => {
      await op.press("Control+/");
      await op.see(page.locator("#keyboard-shortcuts-dialog"));
      await op.until(async () => page.evaluate(() => !!document.activeElement?.closest("#keyboard-shortcuts-dialog")), "focus is outside the shortcut dialog");
      await op.press("Escape");
      await op.gone(page.locator("#keyboard-shortcuts-dialog"));
    });

    await op.step("ctrl-k", "Ctrl+K opens search with its field focused; Escape closes it", async () => {
      await op.press("Control+K");
      await op.see(page.locator("#conversation-search-dialog"));
      await op.until(async () => (await active()) === "conversation-search", "search field not focused");
      await op.press("Escape");
      await op.gone(page.locator("#conversation-search-dialog"));
    });

    await op.step("ctrl-j", "Ctrl+J opens and closes the run console", async () => {
      const consolePanel = page.locator("#run-console");
      const wasOpen = await consolePanel.isVisible();
      await op.press("Control+J");
      await op.until(async () => (await consolePanel.isVisible()) !== wasOpen, "Ctrl+J did not toggle the console");
      await op.press("Control+J");
      await op.until(async () => (await consolePanel.isVisible()) === wasOpen, "Ctrl+J did not toggle it back");
    });

    await op.step("escape-panel", "Escape closes the files and activity panel", async () => {
      if (await page.locator("#run-console").isVisible()) {
        await page.keyboard.press("Control+J");
        await op.gone(page.locator("#run-console"));
      }
      await page.evaluate(() => {
        document.querySelector("#attention-popover").hidden = true;
        setQuotaOpen(false);
        setPanelOpen(true);
      });
      await op.see(page.locator("#activity-panel"));
      await op.press("Escape");
      await op.gone(page.locator("#activity-panel"));
    });

    await op.step("dialog-focus-trap", "Tab stays inside an open dialog", async () => {
      await op.press("Control+,");
      await op.see(page.locator("#settings-dialog"));
      for (let i = 0; i < 25; i++) await page.keyboard.press("Tab");
      op.check(await page.evaluate(() => !!document.activeElement?.closest("#settings-dialog")), "focus left the Settings dialog");
      await op.press("Escape");
      await op.gone(page.locator("#settings-dialog"));
    });

    await op.step("palette-arrows", 'Arrow keys move through the "/" palette', async () => {
      await page.locator("#prompt").fill("");
      await page.locator("#prompt").click();
      await op.type("/");
      const menu = page.locator("#resource-menu");
      await op.see(menu);
      // ArrowDown moves focus from the message box into the list, then down the options.
      // The list is rebuilt when its results arrive, so a press made before that is lost.
      await op.see(menu.getByRole("option").first());
      for (let i = 0; i < 5 && !/^resource-option-/.test(await active()); i++) {
        if ((await active()) !== "prompt") await page.locator("#prompt").focus();
        await op.press("ArrowDown");
      }
      op.check(/^resource-option-/.test(await active()), "ArrowDown did not enter the list");
      const first = await active();
      await op.press("ArrowDown");
      await op.until(async () => /^resource-option-/.test(await active()) && (await active()) !== first, "ArrowDown did not move to the next option");
      await op.press("Escape");
      await page.locator("#prompt").fill("");
    });

    await op.step("sidebar-resize-keys", "The conversations panel width follows the arrow keys", async () => {
      const handle = page.locator("#sidebar-resize");
      const width = () => page.locator("#sidebar").evaluate((el) => el.getBoundingClientRect().width);
      await page.evaluate(() => {
        document.body.classList.remove("sidebar-collapsed");
        applyPanelOrder("conversations-left", false);
        const { min, max } = panelLimits("sidebar");
        sizePanel("sidebar", (min + max) / 2);
      });
      const before = await width();
      await handle.focus();
      await op.press("ArrowRight");
      await op.until(async () => (await width()) > before, "ArrowRight did not widen the left sidebar");
      const wider = await width();
      await op.press("ArrowLeft");
      await op.until(async () => (await width()) < wider, "ArrowLeft did not narrow the left sidebar");
    });

    await op.step("skip-link", "The skip link jumps to the message box", async () => {
      await home(op);
      await page.keyboard.press("Tab");
      const link = page.getByRole("link", { name: "Skip to message" });
      await op.see(link);
      await op.press("Enter");
      await op.until(async () => (await active()) === "prompt", "the skip link did not reach the message box");
    });
  },
};
