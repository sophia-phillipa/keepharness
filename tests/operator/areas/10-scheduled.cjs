// Scheduled tasks: validation, create, Run now (and its "Scheduled" mark in Chats),
// pause, delete.
"use strict";
const { home } = require("../lib/app.cjs");

module.exports = {
  id: "scheduled",
  title: "Scheduled tasks",
  async run(op) {
    const page = op.page;
    const dialog = page.locator("#scheduled-dialog");
    const list = page.locator("#schedules-list");
    const title = "Operator digest";
    const item = () => list.getByRole("button", { name: new RegExp("^" + title) });
    const openScheduled = async () => {
      if (!(await dialog.isVisible())) await op.click(page.locator("#rail-scheduled"));
      await op.see(dialog);
    };

    await op.step("open", "Open Scheduled from the rail", async () => {
      await home(op);
      await openScheduled();
      await op.seeText(page.locator("#scheduled-title"), /Scheduled/);
    }, { critical: true });

    await op.step("validation", "Saving an empty task explains what is missing", async () => {
      await op.click(page.locator("#schedule-new"));
      await op.see(page.locator("#schedule-editor"));
      await op.click(page.locator("#schedule-save"));
      await op.until(
        async () => (await page.locator("#schedule-error").innerText()).trim().length > 0 || (await page.locator("#schedule-editor [aria-invalid=true]").count()) > 0 || (await page.locator("#schedule-title").evaluate((el) => !el.checkValidity())),
        "no validation message for an empty task",
      );
      op.check(!(await item().count()), "an empty task was saved");
    }, { writes: true });

    await op.step("fill", "Fill title, prompt, provider, model and a daily time", async () => {
      await op.fill(page.locator("#schedule-title"), title);
      await op.fill(page.locator("#schedule-prompt"), op.fixtureMode ? "OP-TOOLS scheduled check" : "Reply with exactly the word READY.");
      await op.select(page.locator("#schedule-backend"), op.fixtureMode ? "claude" : { index: 0 });
      await op.until(async () => (await page.locator("#schedule-model option").count()) > 0, "no model to schedule");
      if (op.fixtureMode) await op.select(page.locator("#schedule-model"), "claude-sonnet-5-5");
      await op.select(page.locator("#schedule-kind"), "daily");
      await op.fill(page.locator("#schedule-time"), "09:00");
    }, { writes: true, critical: true });

    await op.step("create", "Create the task: it is listed as active and daily", async () => {
      await op.click(page.locator("#schedule-save"));
      await op.see(item());
      await op.seeText(item(), /Active/);
      await op.seeText(page.locator("#schedule-last"), /Created/);
    }, { writes: true, critical: true });

    await op.step("run-now", "Run it now", async () => {
      await op.click(page.locator("#schedule-run"));
      await op.seeText(page.locator("#schedule-last"), /Started now/);
    }, { writes: true, fixtureOnly: true });

    await op.step("pause-after-run", "Pause it right after Run now and save", async () => {
      await op.click(item());
      const enabled = page.locator("#schedule-enabled");
      if (await enabled.isChecked()) await op.click(enabled);
      await op.click(page.locator("#schedule-save"));
      await op.seeText(item(), /Paused/);
      op.check(!(await page.locator("#schedule-error").innerText()).trim(), "Save failed: " + (await page.locator("#schedule-error").innerText()));
    }, { writes: true });

    await op.step("chats-mark", "The scheduled run appears in Chats with a Scheduled mark", async () => {
      await op.click(page.locator("#scheduled-close"));
      await op.gone(dialog);
      const run = page.locator("#sidebar").getByRole("button", { name: new RegExp(title + "|OP-TOOLS scheduled check") }).filter({ visible: true }).first();
      await op.see(run, 30000);
      await op.seeText(run, /Scheduled/);
    }, { writes: true, fixtureOnly: true });

    await op.step("delete", "Delete the task (two clicks to confirm)", async () => {
      await openScheduled();
      await op.click(item());
      await op.click(page.locator("#schedule-delete"));
      await op.seeText(page.locator("#schedule-delete"), /Confirm delete/);
      await op.click(page.locator("#schedule-delete"));
      await op.gone(item());
      await op.click(page.locator("#scheduled-close"));
    }, { writes: true });
  },
};
