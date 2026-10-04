// Run console: open it from the status strip, walk its five tabs, maximize and collapse.
"use strict";
const { home, newChat, chooseModel, ask } = require("../lib/app.cjs");

module.exports = {
  id: "run-console",
  title: "Run console: pipeline, timeline, logs, runs, agents",
  async run(op) {
    const page = op.page;
    const consolePanel = page.locator("#run-console");
    const tab = (name) => consolePanel.getByRole("tab", { name });
    const panelOf = (name) => page.locator(`[role=tabpanel][aria-labelledby="run-tab-${name.toLowerCase()}"]`);

    await op.step("home", "Run a task that reads files, so the console has spans", async () => {
      await home(op);
      await newChat(op);
      await chooseModel(op, "claude-sonnet-5-5");
      await ask(op, op.fixtureMode ? "OP-TOOLS read the readme" : "Reply with exactly the word READY and nothing else.", op.fixtureMode ? undefined : /READY/i);
    }, { critical: true });

    await op.step("open", "Open the console from the status strip", async () => {
      if (await consolePanel.isVisible()) await op.click(page.locator("#run-status-action"));
      await op.gone(consolePanel);
      await op.click(page.locator("#run-status-action"));
      await op.see(consolePanel);
      await op.see(consolePanel.getByRole("tablist", { name: "Run console views" }));
    }, { critical: true });

    for (const [name, expectation] of [
      ["Pipeline", /claude|Completed|Fixture|model/i],
      ["Timeline", /\S/],
      ["Logs", /\S/],
      ["Runs", /OP-TOOLS|READY|claude/i],
      ["Agents", /\S/],
    ])
      await op.step("tab-" + name.toLowerCase(), `Open the ${name} tab`, async () => {
        await op.click(tab(name));
        await op.until(async () => (await tab(name).getAttribute("aria-selected")) === "true", name + " is not selected");
        await op.see(panelOf(name));
        await op.seeText(panelOf(name), expectation);
      });

    await op.step("timeline-tools", "The timeline lists the Read and Grep tool calls", async () => {
      await op.click(tab("Timeline"));
      await op.seeText(panelOf("Timeline"), /Read/);
      await op.seeText(panelOf("Timeline"), /Grep/);
    }, { fixtureOnly: true });

    await op.step("tab-keys", "Arrow keys move between console tabs", async () => {
      await tab("Pipeline").focus();
      await op.press("ArrowRight");
      await op.until(async () => (await tab("Timeline").getAttribute("aria-selected")) === "true", "ArrowRight did not select Timeline");
      await op.press("End");
      await op.until(async () => (await tab("Agents").getAttribute("aria-selected")) === "true", "End did not select the last tab");
    });

    await op.step("maximize", "Maximize the console, then restore it", async () => {
      await op.click(consolePanel.getByRole("button", { name: "Maximize run console" }));
      await op.see(consolePanel.getByRole("button", { name: "Restore run console" }));
      await op.click(consolePanel.getByRole("button", { name: "Restore run console" }));
      await op.see(consolePanel.getByRole("button", { name: "Maximize run console" }));
    });

    await op.step("collapse", "Collapse the console", async () => {
      await op.click(consolePanel.getByRole("button", { name: "Collapse run console" }));
      await op.gone(consolePanel);
    });

    await op.step("rail-runs", "The Runs rail button opens the console too", async () => {
      await op.click(page.locator("#rail-runs"));
      await op.see(consolePanel);
      await op.click(consolePanel.getByRole("button", { name: "Collapse run console" }));
      await op.gone(consolePanel);
    });
  },
};
