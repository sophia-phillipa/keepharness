// Sidebar: chats, projects with folders, row states, rename, delete, search (Ctrl+K).
"use strict";
const { home, newChat, chooseModel, ask, row: sidebarRow } = require("../lib/app.cjs");

module.exports = {
  id: "sidebar",
  title: "Sidebar: chats, projects, search",
  async run(op) {
    const page = op.page;
    const sidebar = page.locator("#sidebar");
    const title = "Operator sidebar chat " + Date.now().toString(36);
    const renamed = "Operator renamed " + Date.now().toString(36);
    const project = "Operator project";
    const row = (name) => sidebarRow(op, name);

    await op.step("home", "Start from a fresh New Conversation", async () => {
      await home(op);
      await newChat(op);
    }, { critical: true });

    await op.step("chat-row", "Send a message; its conversation appears under Chats", async () => {
      await chooseModel(op, "claude-sonnet-5-5");
      await ask(op, title);
      await op.see(row(title), 15000);
    }, { critical: true });

    await op.step("row-state", "The row shows the conversation state and provider", async () => {
      await op.seeText(row(title), /Completed/);
      await op.seeText(row(title), /claude|codex|deepseek|gemini/i);
    });

    await op.step("row-no-internal-id", "The row never shows the internal 'sem-projeto' id", async () => {
      const text = await row(title).innerText();
      op.check(!/sem-projeto/.test(text), `row text shows the internal id: ${JSON.stringify(text)}`);
    }, { recover: false });

    await op.step("row-actions", "Open the row's actions menu", async () => {
      await row(title).hover();
      await op.click(sidebar.getByLabel("Actions for " + title));
      await op.see(sidebar.getByRole("button", { name: "Rename conversation" }));
      await op.see(sidebar.getByRole("button", { name: "Delete conversation" }));
    });

    await op.step("archive-offered", "The row offers Archive next to Delete", async () => {
      op.check(await sidebar.getByRole("button", { name: /Archive/ }).count() > 0, "no Archive action in the row menu");
    }, { recover: false });

    await op.step("rename", "Rename the conversation from its row", async () => {
      if (!(await sidebar.getByRole("button", { name: "Rename conversation" }).isVisible())) {
        await row(title).hover();
        await op.click(sidebar.getByLabel("Actions for " + title));
      }
      await op.click(sidebar.getByRole("button", { name: "Rename conversation" }));
      await op.see(page.locator("#rename-conversation-dialog"));
      await op.fill(page.locator("#rename-conversation-name"), renamed);
      await op.click(page.locator("#rename-conversation-save"));
      await op.gone(page.locator("#rename-conversation-dialog"));
      await op.see(row(renamed));
    }, { writes: true });

    await op.step("search-open", "Press Ctrl+K to open search", async () => {
      await page.locator("#prompt").click();
      await op.press("Control+K");
      await op.see(page.locator("#conversation-search-dialog"));
      await op.until(async () => page.evaluate(() => document.activeElement?.id === "conversation-search"), "search field is not focused");
    });

    await op.step("search-find", "Type a query; the renamed conversation is found", async () => {
      await op.fill(page.locator("#conversation-search"), "Operator renamed");
      await op.seeText(page.locator("#conversation-search-list"), /Operator renamed/);
    }, { writes: true });

    await op.step("search-clear", "Clear the query with the clear button", async () => {
      await op.fill(page.locator("#conversation-search"), "anything");
      await op.click(page.locator("#search-clear"));
      op.check((await page.locator("#conversation-search").inputValue()) === "", "the query was not cleared");
    });

    await op.step("search-open-result", "Open a conversation from the results", async () => {
      await op.fill(page.locator("#conversation-search"), "Operator renamed");
      await op.click(page.locator("#conversation-search-list").getByText(/Operator renamed/).first());
      await op.gone(page.locator("#conversation-search-dialog"));
      await op.seeText(page.locator("#conversation-title"), /Operator renamed/);
    }, { writes: true });

    await op.step("search-escape", "Escape closes search", async () => {
      await page.locator("#prompt").click();
      await op.press("Control+K");
      await op.see(page.locator("#conversation-search-dialog"));
      await op.press("Escape");
      await op.gone(page.locator("#conversation-search-dialog"));
    });

    await op.step("delete", "Delete the conversation; its row disappears", async () => {
      const name = (await row(renamed).count()) ? renamed : title;
      await row(name).hover();
      await op.click(sidebar.getByLabel("Actions for " + name));
      await op.click(sidebar.getByRole("button", { name: "Delete conversation" }));
      await op.see(page.locator("#delete-conversation-dialog"));
      await op.seeText(page.locator("#delete-conversation-name"), name);
      await op.click(page.locator("#delete-conversation-confirm"));
      await op.gone(page.locator("#delete-conversation-dialog"));
      await op.gone(row(name));
    }, { writes: true });

    await op.step("project-listed", "The fixture project 'Alpha research' is listed with its folder", async () => {
      await op.see(sidebar.getByRole("button", { name: "Alpha research", exact: true }));
      await op.see(sidebar.getByRole("button", { name: "New Conversation in Alpha research" }));
    }, { fixtureOnly: true });

    await op.step("project-new-chat", "Start a chat inside the project from its row", async () => {
      await op.click(sidebar.getByRole("button", { name: "New Conversation in Alpha research" }));
      await op.seeText(page.locator("#project-button-label"), /Alpha research/);
    }, { fixtureOnly: true });

    await op.step("project-chat-grouped", "A chat sent in the project is listed inside its group", async () => {
      await chooseModel(op, "claude-sonnet-5-5");
      await ask(op, "Operator project chat");
      const group = sidebar.locator("details", { has: page.getByRole("button", { name: "Alpha research", exact: true }) }).first();
      await op.see(group.getByRole("button", { name: /^Operator project chat/ }), 15000);
    }, { fixtureOnly: true });

    await op.step("project-collapse", "Collapse and expand the project group", async () => {
      const toggle = sidebar.getByRole("button", { name: "Alpha research", exact: true });
      const inner = sidebar.getByRole("button", { name: /^Operator project chat/ });
      await op.click(toggle);
      await op.gone(inner);
      await op.click(toggle);
      await op.see(inner);
    }, { fixtureOnly: true });

    await op.step("add-project-dialog", "Open Add project and pick a folder in the tree", async () => {
      await op.click(page.locator("#add-project"));
      await op.see(page.locator("#project-dialog"));
      await op.fill(page.locator("#project-name"), project);
      const tree = page.locator("#project-directory-list");
      await op.click(tree.getByRole("button", { name: "Expand projects" }));
      await op.click(tree.getByText("beta", { exact: true }));
      await op.seeText(page.locator("#project-directory-selection"), /beta$/);
      await op.click(page.locator("#project-directory-add-current"));
      await op.seeText(page.locator("#project-selected-paths"), /beta/);
    }, { fixtureOnly: true });

    await op.step("add-project-create", "Create the project; it appears in the sidebar", async () => {
      await op.click(page.locator("#project-create"));
      await op.gone(page.locator("#project-dialog"));
      await op.see(sidebar.getByRole("button", { name: project, exact: true }));
    }, { fixtureOnly: true });

    await op.step("project-actions", "Open the project's actions menu", async () => {
      await op.click(sidebar.getByRole("button", { name: "Actions for project " + project }));
      for (const name of ["Edit project", "Add to favorites", "Remove from list", "Delete folder"])
        await op.see(sidebar.getByRole("button", { name }));
    }, { fixtureOnly: true });

    await op.step("project-edit", "Edit the project: the dialog shows its name; cancel", async () => {
      if (!(await sidebar.getByRole("button", { name: "Edit project" }).isVisible()))
        await op.click(sidebar.getByRole("button", { name: "Actions for project " + project }));
      await op.click(sidebar.getByRole("button", { name: "Edit project" }));
      await op.see(page.locator("#project-dialog"));
      op.check((await page.locator("#project-name").inputValue()) === project, "the edit dialog lost the project name");
      await op.click(page.locator("#project-dialog-cancel"));
      await op.gone(page.locator("#project-dialog"));
    }, { fixtureOnly: true });

    await op.step("project-delete-folder-dialog", "Delete folder asks for an explicit confirmation; cancel", async () => {
      await op.click(sidebar.getByRole("button", { name: "Actions for project " + project }));
      await op.click(sidebar.getByRole("button", { name: "Delete folder" }));
      await op.see(page.locator("#delete-project-folder-dialog"));
      op.check(await page.locator("#delete-project-folder-confirm").isDisabled(), "Delete is enabled before the confirmation box is ticked");
      await op.click(page.locator("#delete-project-folder-cancel"));
      await op.gone(page.locator("#delete-project-folder-dialog"));
    }, { fixtureOnly: true });

    await op.step("project-remove", "Remove the project from the list", async () => {
      await op.click(sidebar.getByRole("button", { name: "Actions for project " + project }));
      await op.click(sidebar.getByRole("button", { name: "Remove from list" }));
      await op.gone(sidebar.getByRole("button", { name: project, exact: true }));
    }, { fixtureOnly: true });
  },
};
