// Light and dark: the topbar theme toggle, both palettes linted on the main screens, and
// the muted-text contrast of each.
"use strict";
const { home } = require("../lib/app.cjs");

function contrast(a, b) {
  const lum = (hex) => {
    const [r, g, bl] = hex.match(/\w\w/g).map((x) => parseInt(x, 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl;
  };
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

module.exports = {
  id: "themes",
  title: "Light and dark themes",
  async run(op) {
    const page = op.page;
    const palette = () => page.evaluate(() => document.documentElement.dataset.palette);
    const tokens = () =>
      page.evaluate(() => {
        const style = getComputedStyle(document.documentElement);
        return { muted: style.getPropertyValue("--th-muted").trim(), panel: style.getPropertyValue("--th-panel").trim() };
      });

    await op.step("home", "Start in the light theme", async () => {
      await home(op);
      if ((await palette()) !== "paper") await page.evaluate(() => window.HarnessTheme?.apply("paper"));
      await op.until(async () => (await palette()) === "paper", "not in Paper");
    }, { critical: true });

    await op.step("light-contrast", "Light theme: muted text has at least 4.5:1 contrast", async () => {
      const { muted, panel } = await tokens();
      const ratio = contrast(muted, panel);
      op.check(ratio >= 4.5, `muted ${muted} on ${panel} is ${ratio.toFixed(2)}:1`);
    });

    await op.step("toggle-dark", "The topbar toggle switches to the dark theme", async () => {
      await op.click(page.locator("#theme-toggle"));
      await op.until(async () => (await palette()) === "graphite", "the toggle did not switch to Graphite");
      op.check((await page.evaluate(() => document.documentElement.dataset.theme)) === "dark", "data-theme is not dark");
    });

    await op.step("dark-contrast", "Dark theme: muted text has at least 4.5:1 contrast", async () => {
      const { muted, panel } = await tokens();
      const ratio = contrast(muted, panel);
      op.check(ratio >= 4.5, `muted ${muted} on ${panel} is ${ratio.toFixed(2)}:1`);
    }, { recover: false });

    await op.step("dark-persists", "The dark theme survives a reload", async () => {
      await page.reload();
      await page.locator("#startup-gate").waitFor({ state: "hidden", timeout: 30000 });
      await op.until(async () => (await palette()) === "graphite", "the theme reset after a reload");
    });

    await op.step("dark-conversation", "Dark theme: an existing conversation (linted)", async () => {
      const last = page.locator("#sidebar").getByRole("button").filter({ hasText: /Completed/ }).filter({ visible: true }).first();
      if (!(await last.count())) op.skip("no finished conversation to open");
      await op.click(last);
      await op.see(page.locator("#messages").getByRole("article").first());
    });

    await op.step("dark-settings", "Dark theme: Settings (linted)", async () => {
      await op.click(page.locator("#settings"));
      await op.see(page.locator("#settings-dialog"));
      await op.click(page.locator("#settings-close"));
    });

    await op.step("dark-admin-follows", "The embedded admin follows the dark theme", async () => {
      await op.click(page.locator("#settings"));
      const providers = page.locator("#settings-dialog").getByRole("button", { name: "Providers", exact: true });
      if (!(await providers.isVisible())) op.skip("System is shown only on the admin's own host");
      await op.click(providers);
      await op.see(page.locator("#admin-frame"), 15000);
      await op.until(async () => (await page.frameLocator("#admin-frame").locator("html").getAttribute("data-palette")) === "graphite", "the admin stayed light");
      await op.click(page.locator("#settings-close"));
    });

    await op.step("toggle-light", "Toggle back to the light theme", async () => {
      await op.click(page.locator("#theme-toggle"));
      await op.until(async () => (await palette()) === "paper", "the toggle did not return to Paper");
    });
  },
};
