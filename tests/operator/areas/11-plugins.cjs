// Plugins and connectors: the composer panel, a connector's detail and the way to manage it.
"use strict";
const { home, newChat, chooseModel } = require("../lib/app.cjs");

module.exports = {
  id: "plugins",
  title: "Plugins and connectors",
  async run(op) {
    const page = op.page;
    const menu = page.locator("#plugins-menu");

    await op.step("open", "Open the Plugins chip in a new chat", async () => {
      await home(op);
      await newChat(op);
      await chooseModel(op, "claude-sonnet-5-5");
      await op.click(page.locator("#plugins-chip"));
      await op.see(menu);
      await op.seeText(menu, /Connectors and plugins · /);
    }, { critical: true });

    await op.step("sections", "The panel lists what is available in this conversation", async () => {
      await op.seeText(menu, /Available in this conversation/);
    });

    await op.step("detail", "Open a connector's detail", async () => {
      const first = menu.getByRole("button").filter({ hasText: /Connector|Plugin/ }).first();
      if (!(await first.count())) op.skip("no connector is installed");
      await op.click(first);
      await op.seeText(menu, /Allowed for this provider/);
      await op.seeText(menu, /Installed/);
    });

    await op.step("detail-reason", "The detail says why the connector is not usable here and where to change it", async () => {
      await op.seeText(menu, /Settings › System › Providers/);
    }, { fixtureOnly: true });

    await op.step("back", "Go back to the list", async () => {
      await op.click(menu.getByRole("button", { name: "Connectors and plugins", exact: true }));
      await op.seeText(menu, /Available in this conversation/);
    });

    await op.step("manage", "Manage connectors and plugins opens Settings › System", async () => {
      await op.click(menu.getByRole("button", { name: "Manage connectors and plugins" }));
      const settings = page.locator("#settings-dialog");
      await op.see(settings);
      await op.see(settings.getByRole("group", { name: "System" }));
      await op.click(page.locator("#settings-close"));
    });
  },
};
