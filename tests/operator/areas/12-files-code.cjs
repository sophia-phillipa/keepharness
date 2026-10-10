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

    // D-033: Files is an accordion; the folder tree lives in System Files, the authorized project roots in Project Files.
    const showFilesSection = async (name) => {
      await ensurePanel();
      await op.click(page.locator("#files-toggle"));
      const head = page.locator("#workspace-" + name + "-head");
      if ((await head.getAttribute("aria-disabled")) !== "true") await op.click(head);
      await op.see(page.locator("#workspace-" + name));
    };

    // D-033: System Files lists the allowed roots (Local Folders > projects); the project folder is two folders down.
    // A folder's chevron is labelled "Expand <name>" when collapsed and "Collapse <name>" when open.
    const expandSystemFolder = async (name) => {
      const tree = page.locator("#workspace-system-files");
      await op.see(tree.getByRole("button", { name: new RegExp("^(Expand|Collapse) " + name + "$") }), 15000);
      const collapsed = tree.getByRole("button", { name: "Expand " + name, exact: true });
      if (await collapsed.count()) await op.click(collapsed);
    };

    await op.step("system-tree-files", "The project folder's files are listed in System Files", async () => {
      await showFilesSection("system-files");
      await expandSystemFolder("projects");
      await expandSystemFolder("alpha");
      const tree = page.locator("#workspace-system-files");
      await op.see(tree.getByRole("treeitem", { name: "README.md", exact: true }), 15000);
      await op.see(tree.getByRole("treeitem", { name: "notes.md", exact: true }));
    }, { fixtureOnly: true });

    await op.step("folder-opens", "A folder in the project list opens", async () => {
      await showFilesSection("system-files");
      const folder = page.locator("#workspace-system-files").getByText("src", { exact: true });
      await op.click(folder);
      await op.see(page.locator("#workspace-system-files").getByText("app.py"), 8000);
    }, { fixtureOnly: true, recover: false });

    // A click selects the row (aria-selected); Enter attaches the selection to the message.
    await op.step("attach-file", "A selected file attaches to the message with Enter", async () => {
      await showFilesSection("system-files");
      await expandSystemFolder("projects");
      await expandSystemFolder("alpha");
      const readme = page.locator("#workspace-system-files").getByRole("treeitem", { name: "README.md", exact: true });
      await op.click(readme);
      await op.until(async () => (await readme.getAttribute("aria-selected")) === "true", "a click did not select README.md");
      await readme.press("Enter");
      // The chip names the file by its path under the root: "projects/alpha/README.md".
      await op.until(async () => (await page.locator("#attachments").innerText()).includes("projects/alpha/README.md"), "README.md was not attached", 15000);
      await op.click(page.locator("#attachments").getByRole("button", { name: "Remove attachment projects/alpha/README.md" }));
    }, { fixtureOnly: true });

    await op.step("sections", "Background tasks, Resources and Activity sections are present", async () => {
      await ensurePanel();
      await op.click(page.locator("#activity-toggle"));
      for (const name of [/Background tasks/, /Resources/, /Activity/]) await op.see(panel.locator("#activities-view .accordion-head").filter({ hasText: name }).first());
    });

    await op.step("server-folders", "Switch between Project Files and System Files", async () => {
      await showFilesSection("system-files");
      await op.see(page.locator("#files-roots, #files-no-roots, #files-tree, #files-error").filter({ visible: true }).first(), 15000);
      const isOpen = async (name) => (await page.locator("#workspace-" + name + "-head").getAttribute("aria-expanded")) === "true";
      await op.click(page.locator("#workspace-project-files-head"));
      await op.until(async () => (await isOpen("project-files")) && !(await isOpen("system-files")), "Project Files did not replace System Files");
      await op.click(page.locator("#workspace-system-files-head"));
      await op.until(async () => (await isOpen("system-files")) && !(await isOpen("project-files")), "System Files did not reopen");
    });

    await op.step("activity-tab", "Switch the panel to Activity", async () => {
      await ensurePanel();
      await op.click(page.locator("#activity-toggle"));
      await op.see(page.locator("#activities-view"));
      await op.click(page.locator("#files-toggle"));
      await op.see(page.locator("#files-view"));
    });

    // D-054: the Chat | Code switch is gone; #panel-toggle is the only control for the side panel.
    await op.step("panel-close", "The panel toggle closes the files panel and the greeting stays", async () => {
      await ensurePanel();
      await op.click(page.locator("#panel-toggle"));
      await op.gone(panel);
      await op.seeText(page.locator("#welcome"), /How can I help/);
    });

    await op.step("panel-open", "The panel toggle opens the files panel and the greeting stays", async () => {
      await op.click(page.locator("#panel-toggle"));
      await op.see(panel);
      await op.seeText(page.locator("#welcome"), /How can I help/);
    });
  },
};
