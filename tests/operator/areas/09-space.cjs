// Space pages: create, preview, edit, attach to a message, start a chat, switch project,
// unsaved-change safety and delete.
"use strict";
const { home, newChat } = require("../lib/app.cjs");

module.exports = {
  id: "space",
  title: "Space pages",
  async run(op) {
    const page = op.page;
    const dialog = page.locator("#space-dialog");
    const list = page.locator("#pages-list");
    const title = "Operator page";
    const openSpace = async () => {
      if (!(await dialog.isVisible())) await op.click(page.locator("#rail-space"));
      await op.see(dialog);
    };
    const openPage = async () => {
      await openSpace();
      await op.click(list.getByRole("button", { name: new RegExp("^" + title) }));
      await op.until(async () => (await page.locator("#page-title").inputValue()) === title, "the page did not open");
    };

    await op.step("open", "Open Space from the rail", async () => {
      await home(op);
      await openSpace();
      await op.seeText(page.locator("#space-title"), /Space/);
      await op.see(page.locator("#space-project"));
    }, { critical: true });

    await op.step("create", "Create a Markdown page and save it", async () => {
      await op.click(page.locator("#page-new"));
      await op.see(page.locator("#page-editor"));
      await op.fill(page.locator("#page-title"), title);
      await op.fill(page.locator("#page-body"), "# Operator page\n\n- first point\n- **bold** second point\n");
      await op.click(page.locator("#page-save"));
      await op.see(list.getByRole("button", { name: new RegExp("^" + title) }));
    }, { writes: true, critical: true });

    await op.step("preview", "Preview renders the Markdown", async () => {
      await op.click(page.locator("#page-preview-toggle"));
      const preview = page.locator("#page-preview");
      await op.see(preview);
      await op.see(preview.getByRole("heading", { name: "Operator page" }));
      await op.see(preview.getByRole("listitem").first());
      await op.click(page.locator("#page-preview-toggle"));
      await op.see(page.locator("#page-body"));
    }, { writes: true });

    await op.step("edit-ctrl-s", "Edit the page and save with Ctrl+S", async () => {
      await page.locator("#page-body").click();
      await page.locator("#page-body").press("End");
      await op.type("\n- third point added by the operator");
      await op.press("Control+S");
      await op.until(async () => /saved/i.test(await page.locator("#page-status").innerText()), "Ctrl+S did not save");
    }, { writes: true });

    await op.step("attach", "In a new chat, attach the page to the next message", async () => {
      if (await dialog.isVisible()) await op.click(page.locator("#space-close"));
      await newChat(op);
      await openPage();
      await op.click(page.locator("#page-attach"));
      await op.gone(dialog);
      await op.see(page.locator("#attachments").getByRole("button", { name: "Remove attachment Operator-page.md" }), 20000);
      await op.click(page.locator("#attachments").getByRole("button", { name: "Remove attachment Operator-page.md" }));
    }, { writes: true });

    await op.step("start-chat", "Start a chat from the page", async () => {
      await openPage();
      await op.click(page.locator("#page-chat"));
      await op.gone(dialog);
      await op.see(page.locator("#attachments").getByRole("button", { name: "Remove attachment Operator-page.md" }), 20000);
      await op.seeText(page.locator("#conversation-state-pill"), /Draft/);
      await op.click(page.locator("#attachments").getByRole("button", { name: "Remove attachment Operator-page.md" }));
    }, { writes: true });

    await op.step("project-switch", "Switch the Space to another project: its own (empty) page list", async () => {
      await openSpace();
      const projects = await page.locator("#space-project option").evaluateAll((o) => o.map((x) => x.value));
      const other = projects.find((p) => p !== "sem-projeto");
      if (!other) op.skip("only one project");
      const current = await page.locator("#space-project").inputValue();
      await op.select(page.locator("#space-project"), other === current ? "sem-projeto" : other);
      await op.until(async () => !(await list.innerText()).includes(title), "the other project shows this page");
      await op.select(page.locator("#space-project"), current);
      await op.see(list.getByRole("button", { name: new RegExp("^" + title) }));
    }, { writes: true });

    await op.step("unsaved-guard", "Unsaved edits survive switching project and back", async () => {
      await openPage();
      await page.locator("#page-body").click();
      await page.locator("#page-body").press("End");
      await op.type("\nunsaved operator line");
      const current = await page.locator("#space-project").inputValue();
      const other = (await page.locator("#space-project option").evaluateAll((o) => o.map((x) => x.value))).find((p) => p !== current);
      if (!other) op.skip("only one project");
      page.once("dialog", (d) => d.dismiss());
      await op.select(page.locator("#space-project"), other);
      await op.select(page.locator("#space-project"), current);
      await openPage();
      op.check((await page.locator("#page-body").inputValue()).includes("unsaved operator line"), "unsaved edits were dropped without asking");
    }, { writes: true });

    await op.step("delete", "Delete the page (two clicks to confirm)", async () => {
      await openPage();
      await op.click(page.locator("#page-delete"));
      await op.seeText(page.locator("#page-delete"), /Confirm delete/);
      await op.click(page.locator("#page-delete"));
      await op.gone(list.getByRole("button", { name: new RegExp("^" + title) }));
    }, { writes: true });

    await op.step("close", "Close Space", async () => {
      await openSpace();
      await op.click(page.locator("#space-close"));
      await op.gone(dialog);
    });
  },
};
