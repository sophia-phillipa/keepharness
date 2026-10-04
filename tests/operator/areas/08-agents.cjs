// User agents: create one in Settings, use it with "@@" and from Settings, check the
// agent composer, end the agent conversation, edit it and delete it.
"use strict";
const { home, newChat, chooseModel, send, submit, waitAnswer } = require("../lib/app.cjs");

module.exports = {
  id: "agents",
  title: "User agents",
  async run(op) {
    const page = op.page;
    const name = "operator-reviewer";
    const dialog = page.locator("#agent-dialog");
    const list = page.locator("#harness-agents-list");
    const palette = page.locator("#resource-menu");
    const persona = page.locator("#persona-control");
    const openAgents = async () => {
      if (!(await page.locator("#settings-dialog").isVisible())) await op.click(page.locator("#settings"));
      await op.click(page.locator("#settings-dialog").getByRole("button", { name: "Agents and models", exact: true }));
      await op.see(page.locator("#settings-agents"));
    };

    await op.step("home", "Open Settings › Agents and models", async () => {
      await home(op);
      await openAgents();
      await op.see(page.locator("#agent-create"));
    }, { critical: true });

    await op.step("create-dialog", "Create agent: fill name, purpose, instructions, tasks and output", async () => {
      await op.click(page.locator("#agent-create"));
      await op.see(dialog);
      await op.fill(page.locator("#agent-name"), name);
      await op.fill(page.locator("#agent-purpose"), "Review operator notes for gaps");
      await op.fill(page.locator("#agent-instructions"), "Read the notes and list what is missing, briefly.");
      await op.fill(page.locator("#agent-tasks"), "Read the notes\nList the gaps");
      await op.fill(page.locator("#agent-target-output"), "A short Markdown list");
    }, { writes: true, critical: true });

    await op.step("create-provider", "Pick the provider, model and effort for the agent", async () => {
      await op.select(page.locator("#agent-backend"), "claude");
      await op.until(async () => (await page.locator("#agent-model option").count()) > 0, "no model for Claude");
      if (op.fixtureMode) await op.select(page.locator("#agent-model"), "claude-sonnet-5-5");
      const efforts = await page.locator("#agent-effort option").evaluateAll((o) => o.map((x) => x.value));
      if (efforts.includes("medium")) await op.select(page.locator("#agent-effort"), "medium");
    }, { writes: true });

    await op.step("dialog-buttons-visible", "The dialog's Cancel and Create buttons are fully visible", async () => {
      const inside = await page.evaluate(() => {
        const d = document.getElementById("agent-dialog").getBoundingClientRect();
        return ["agent-save", "agent-cancel"].every((id) => {
          const r = document.getElementById(id).getBoundingClientRect();
          return r.top >= d.top - 1 && r.bottom <= d.bottom + 1 && r.bottom <= innerHeight;
        });
      });
      op.check(inside, "Cancel/Create are cut by the dialog edge");
    }, { writes: true, recover: false });

    await op.step("create-save", "Save: the agent is listed as @@" + name, async () => {
      await op.click(page.locator("#agent-save"));
      await op.gone(dialog);
      await op.see(list.getByText("@@" + name));
    }, { writes: true, critical: true });

    await op.step("use-from-settings", "Use the agent from Settings: the message starts with @@", async () => {
      await op.click(list.getByRole("button", { name: "Use @@" + name + " in the message" }));
      await op.gone(page.locator("#settings-dialog"));
      const chip = page.getByRole("button", { name: "Remove @@" + name });
      await op.see(chip);
      await op.click(chip);
      await op.gone(chip);
      await page.locator("#prompt").fill("");
    }, { writes: true });

    await op.step("at-mention", 'Mention it with "@@" in a new chat and pick it', async () => {
      await newChat(op);
      await chooseModel(op, "claude-sonnet-5-5");
      await page.locator("#prompt").fill("");
      await page.locator("#prompt").click();
      await op.type("@@operator");
      const option = palette.getByRole("option", { name: new RegExp(name) });
      await op.see(option);
      await op.press("Enter");
      await op.until(async () => (await page.locator("#prompt").inputValue()).includes("@@" + name), "the agent was not inserted");
    }, { writes: true });

    await op.step("agent-run", "Send to the agent: an agent conversation starts", async () => {
      await op.type(" check the notes");
      const before = await submit(op);
      await waitAnswer(op, before);
      await op.see(persona);
      await op.seeText(persona, new RegExp("Agent conversation: " + name));
    }, { writes: true, fixtureOnly: true });

    await op.step("agent-composer-no-overlap", "In an agent conversation, Send does not cover the effort picker", async () => {
      const overlap = await page.evaluate(() => {
        const a = document.getElementById("send").getBoundingClientRect(), b = document.getElementById("effort-trigger").getBoundingClientRect();
        return [Math.min(a.right, b.right) - Math.max(a.left, b.left), Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top)];
      });
      op.check(!(overlap[0] > 1 && overlap[1] > 1), `Send overlaps the effort picker by ${overlap.map((n) => n.toFixed(0)).join("x")} px`);
    }, { writes: true, fixtureOnly: true, recover: false });

    await op.step("slash-lists-agent", 'The "/" palette also offers the agent', async () => {
      await page.locator("#prompt").fill("");
      await page.locator("#prompt").click();
      await op.type("/operator");
      await op.see(palette);
      const found = await palette.getByRole("option", { name: new RegExp(name) }).count();
      await op.press("Escape");
      await page.locator("#prompt").fill("");
      op.check(found > 0, '"/" does not list the KeepHarness agent');
    }, { writes: true });

    await op.step("end-agent", "End the agent conversation: it ends after the next message", async () => {
      await op.click(persona.getByRole("button", { name: "End agent conversation" }));
      await op.seeText(persona, /Ending after your next message/);
      const before = await send(op, "thanks, that is all");
      await waitAnswer(op, before);
      await op.until(async () => !(await persona.isVisible()), "the agent conversation did not end");
    }, { writes: true, fixtureOnly: true });

    await op.step("edit", "Edit the agent's purpose and save", async () => {
      await openAgents();
      await op.click(list.getByRole("button", { name: "Edit @@" + name }));
      await op.see(dialog);
      await op.fill(page.locator("#agent-purpose"), "Review operator notes, edited");
      await op.click(page.locator("#agent-save"));
      await op.gone(dialog);
      await op.see(list.getByText("Review operator notes, edited"));
    }, { writes: true });

    await op.step("delete", "Delete the agent (two clicks to confirm)", async () => {
      await op.click(list.getByRole("button", { name: "Edit @@" + name }));
      await op.see(dialog);
      await op.click(page.locator("#agent-delete"));
      await op.seeText(page.locator("#agent-delete"), /Confirm delete/);
      await op.click(page.locator("#agent-delete"));
      await op.gone(dialog);
      await op.gone(list.getByText("@@" + name));
      await op.click(page.locator("#settings-close"));
    }, { writes: true });
  },
};
