// App shell: first load, the guided tour, the rail, the topbar and the status strip.
"use strict";
const { dismissTour } = require("../lib/app.cjs");

module.exports = {
  id: "shell",
  title: "App shell, rail and tour",
  async run(op) {
    const page = op.page;

    await op.step("first-load", "Open KeepHarness and wait for it to be ready", async () => {
      await page.goto(op.session.base + "/");
      await page.evaluate(() => localStorage.removeItem("keepharness-tour-seen"));
      await page.reload();
      await page.locator("#startup-gate").waitFor({ state: "hidden", timeout: 30000 });
      op.check(/KeepHarness/.test(await page.title()), "the page title names KeepHarness");
      await op.see(page.locator("#prompt"));
    }, { critical: true });

    await op.step("tour-opens", "The guided tour opens on the first visit", async () => {
      await op.see(page.locator("#tour-root"));
      await op.seeText(page.locator("#tour-root"), /1 of \d+/);
    });

    const counter = async () => Number((await page.locator("#tour-counter").innerText()).split(" of ")[0]);
    const tourOpen = async () => {
      if (await page.locator("#tour-root").isVisible().catch(() => false)) return;
      await page.evaluate(() => localStorage.removeItem("keepharness-tour-seen"));
      await page.reload();
      await op.see(page.locator("#tour-root"), 30000);
    };

    await op.step("tour-next-back", "Walk the tour forward twice and back once", async () => {
      await tourOpen();
      const first = await counter();
      await op.click(page.locator("#tour-next"));
      await op.until(async () => (await counter()) > first, "Next did not advance the tour");
      const second = await counter();
      await op.click(page.locator("#tour-next"));
      await op.until(async () => (await counter()) > second, "Next did not advance the tour again");
      const third = await counter();
      await op.click(page.locator("#tour-back"));
      await op.until(async () => (await counter()) < third, "Back did not return to an earlier step");
    });

    await op.step("tour-skip", "Skip the tour; it stays closed after a reload", async () => {
      await tourOpen();
      await op.click(page.locator("#tour-skip"));
      await op.gone(page.locator("#tour-root"));
      await page.reload();
      await page.locator("#startup-gate").waitFor({ state: "hidden", timeout: 30000 });
      await op.see(page.locator("#prompt"));
      op.check(!(await page.locator("#tour-root").isVisible().catch(() => false)), "the tour came back after a reload");
    });

    await op.step("rail-names", "Every rail and topbar icon has a readable name", async () => {
      for (const [id, name] of [
        ["menu", /conversations panel/i], ["search-conversations", /Search/], ["attention-bell", /Attention/],
        ["panel-toggle", /files and activity/i], ["rail-space", /Space/], ["rail-scheduled", /Scheduled/],
        ["rail-runs", /Runs/], ["rail-agents", /Plugins/], ["settings", /Settings/],
      ]) {
        const button = page.locator("#" + id);
        await op.see(button);
        const label = (await button.getAttribute("aria-label")) || (await button.getAttribute("title")) || "";
        op.check(name.test(label), `#${id} is named "${label}"`);
      }
    });

    await op.step("sidebar-toggle", "Collapse and reopen the conversations panel", async () => {
      const menu = page.locator("#menu");
      await op.click(menu);
      await op.until(async () => (await menu.getAttribute("aria-expanded")) === "false", "the panel did not collapse");
      await op.click(menu);
      await op.until(async () => (await menu.getAttribute("aria-expanded")) === "true", "the panel did not reopen");
    });

    await op.step("side-panel", "Open and close the files and activity panel", async () => {
      await op.click(page.locator("#panel-toggle"));
      await op.see(page.locator("#activity-panel"));
      await op.click(page.locator("#panel-toggle"));
      await op.gone(page.locator("#activity-panel"));
    });

    await op.step("about", "Open About from Settings: it describes the app and offers the tour", async () => {
      await page.keyboard.press("Control+,");
      await op.click(page.locator("#about"));
      await op.see(page.locator("#about-dialog"));
      await op.seeText(page.locator("#about-dialog"), /About KeepHarness/);
      await op.see(page.locator("#take-tour"));
    });

    await op.step("about-tour", "Restart the tour from About, then skip it", async () => {
      if (!(await page.locator("#about-dialog").isVisible())) {
        if (!(await page.locator("#settings-dialog").isVisible())) await page.keyboard.press("Control+,");
        await op.click(page.locator("#about"));
      }
      await op.click(page.locator("#take-tour"));
      await op.see(page.locator("#tour-root"));
      await dismissTour(op);
      await op.gone(page.locator("#tour-root"));
    });

    await op.step("status-strip", "The status strip counts running, queued and needs-you work", async () => {
      await op.seeText(page.locator("#run-status-toggle"), /running .* queued .* needs you/);
      await op.see(page.locator("#needs-you-toggle"));
    });

    await op.step("view-switch", "Switch between Chat and Code views", async () => {
      await op.click(page.getByRole("tab", { name: "Code" }));
      await op.until(async () => (await page.locator("#view-code").getAttribute("aria-selected")) === "true", "Code was not selected");
      await op.click(page.getByRole("tab", { name: "Chat" }));
      await op.until(async () => (await page.locator("#view-chat").getAttribute("aria-selected")) === "true", "Chat was not selected");
    });
  },
};
