// Files panel and Code mode: project files, attach a selection, the other panel sections,
// and the Code view.
"use strict";
const { home, newChat } = require("../lib/app.cjs");

module.exports = {
  id: "files-code",
  title: "Files panel and Code mode",
  async run(op) {
    const page = op.page;
    const panel = page.locator("#activity-panel");
    const ensurePanel = async () => {
      if (!(await panel.isVisible())) await op.click(page.locator("#panel-toggle"));
      await op.see(panel);
    };

    await op.step("open", "Open the side panel in a project chat", async () => {
      await home(op);
      if (op.fixtureMode) await op.click(page.locator("#sidebar").getByRole("button", { name: "New Conversation in Alpha research" }));
      else await newChat(op);
      if (!(await panel.isVisible())) await op.click(page.locator("#panel-toggle"));
      await op.see(panel);
      await op.see(page.locator("#files-toggle"));
      await op.see(page.locator("#activity-toggle"));
    }, { critical: true });

    await op.step("project-files", "The project's files are listed", async () => {
      await ensurePanel();
      await op.see(page.locator("#workspace-files").getByRole("button", { name: "README.md" }), 15000);
      await op.see(page.locator("#workspace-files").getByRole("button", { name: "notes.md" }));
    }, { fixtureOnly: true });

    await op.step("folder-opens", "A folder in the project list opens", async () => {
      await ensurePanel();
      const folder = page.locator("#workspace-files").getByText("src", { exact: true });
      await op.click(folder);
      await op.see(page.locator("#workspace-files").getByText("app.py"), 8000);
    }, { fixtureOnly: true, recover: false });

    await op.step("attach-file", "Clicking a file attaches it to the message", async () => {
      await ensurePanel();
      await op.click(page.locator("#workspace-files").getByRole("button", { name: "README.md" }));
      await op.until(async () => (await page.locator("#attachments").innerText()).includes("README.md"), "README.md was not attached", 15000);
      await op.click(page.locator("#attachments").getByRole("button", { name: "Remove attachment README.md" }));
    }, { fixtureOnly: true });

    await op.step("sections", "Background tasks, Resources and Activity sections are present", async () => {
      await ensurePanel();
      for (const name of [/Background tasks/, /Resources/, /Activity/]) await op.see(panel.locator("summary").filter({ hasText: name }).first());
    });

    await op.step("server-folders", "Collapse and reopen Browse authorized server folders", async () => {
      await ensurePanel();
      const browse = panel.locator("summary").filter({ hasText: "Browse authorized server folders" });
      const isOpen = async () => (await page.locator("#server-folder-browser").getAttribute("open")) !== null;
      if (!(await isOpen())) await op.click(browse);
      await op.see(page.locator("#files-roots, #files-no-roots, #files-tree, #files-error").filter({ visible: true }).first(), 15000);
      await op.click(browse);
      await op.until(async () => !(await isOpen()), "the folder browser did not collapse");
      await op.click(browse);
      await op.until(isOpen, "the folder browser did not reopen");
    });

    await op.step("activity-tab", "Switch the panel to Activity", async () => {
      await ensurePanel();
      await op.click(page.locator("#activity-toggle"));
      await op.see(page.locator("#activity-view"));
      await op.click(page.locator("#files-toggle"));
      await op.see(page.locator("#files-view, #workspace-files").first());
    });

    await op.step("code-mode", "Code mode asks what to build and keeps the files panel", async () => {
      await ensurePanel();
      await op.click(page.getByRole("tab", { name: "Code" }));
      await op.seeText(page.locator("#welcome"), /What should we build/);
      await op.see(panel);
    });

    await op.step("chat-mode", "Back to Chat mode", async () => {
      await op.click(page.getByRole("tab", { name: "Chat" }));
      await op.seeText(page.locator("#welcome"), /How can I help/);
      if (await panel.isVisible()) await op.click(page.locator("#panel-toggle"));
    });
  },
};
