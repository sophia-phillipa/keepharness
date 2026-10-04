// Plugins and connectors: the composer panel, a connector's detail and the way to manage it.
"use strict";
const { home, newChat, chooseModel, setPersonalSetup } = require("../lib/app.cjs");

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

    // D01: the owner's Codex and Claude Code setup is off unless they opt in.
    await op.step("personal-setup-off", "By default no personal connector shows, and the panel says how to turn them on", async () => {
      await op.seeText(menu, /None for this project and model\./);
      await op.seeText(menu, /Your Codex and Claude Code connectors and plugins come with your personal setup, which is off\. Turn it on in Settings › System\./);
      op.check(!/fixture-docs/.test(await menu.innerText()), "a personal connector shows with the personal setup off");
    }, { fixtureOnly: true });

    await op.step("personal-setup-on", "With the personal setup on, the installed connector appears", async () => {
      await setPersonalSetup(op, true);
      await op.press("Escape");
      await op.gone(menu);
      await op.click(page.locator("#plugins-chip"));
      await op.seeText(menu, /Installed, not available here/);
      await op.seeText(menu, /fixture-docs/);
    }, { fixtureOnly: true });

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

    await op.step("personal-setup-reset", "Turn the personal setup off again", async () => {
      await setPersonalSetup(op, false);
    }, { fixtureOnly: true, always: true });
  },
};
