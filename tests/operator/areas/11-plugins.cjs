// Plugins and connectors: the composer panel, a connector's detail and the way to manage it.
"use strict";
const { home, newChat, chooseModel } = require("../lib/app.cjs");

module.exports = {
  id: "plugins",
  title: "Plugins and connectors",
  async run(op) {
    const page = op.page;
    const menu = page.locator("#plugins-menu");

    await op.step(
      "open",
      "Open the Plugins chip in a new chat",
      async () => {
        await home(op);
        await newChat(op);
        await chooseModel(op, "claude-sonnet-5-5");
        await op.click(page.locator("#plugins-chip"));
        await op.see(menu);
        await op.seeText(menu, /Connectors and plugins · /);
      },
      { critical: true },
    );

    // Facade (#45, #61): the panel lists what the native CLI has configured; KeepHarness does not
    // filter it. The owner's personal-setup switch is gone (#46), so there is nothing to turn on.
    await op.step(
      "sections",
      "The panel lists what is configured in the native CLI",
      async () => {
        await op.seeText(
          menu,
          /Availability and approvals follow the native CLI configuration and selected access mode\./,
        );
        await op.seeText(menu, /Configured in the native CLI/);
      },
    );

    await op.step(
      "native-cli",
      "The installed connector appears with no personal-setup switch",
      async () => {
        await op.seeText(menu, /fixture-docs/);
        await op.seeText(menu, /Connector · stdio/);
        op.check(
          !/personal setup/i.test(await menu.innerText()),
          "the panel still mentions a personal setup",
        );
      },
      { fixtureOnly: true },
    );

    await op.step("detail", "Open a connector's detail", async () => {
      const first = menu
        .getByRole("button")
        .filter({ hasText: /Connector|Plugin/ })
        .first();
      if (!(await first.count())) op.skip("no connector is installed");
      await op.click(first);
      await op.seeText(menu, /Allowed for this provider/);
      await op.seeText(menu, /Installed/);
    });

    await op.step(
      "detail-reason",
      "The detail says the native CLI controls availability and shows the connector as configured",
      async () => {
        await op.seeText(
          menu,
          /Allowed for this provider\s*Controlled by the native CLI/,
        );
        await op.seeText(menu, /Installed\s*Yes · configured/);
      },
      { fixtureOnly: true },
    );

    await op.step("back", "Go back to the list", async () => {
      await op.click(
        menu.getByRole("button", {
          name: "Connectors and plugins",
          exact: true,
        }),
      );
      await op.seeText(menu, /Configured in the native CLI/);
    });

    await op.step(
      "manage",
      "Discover and manage plugins opens Settings › Plugins",
      async () => {
        const manage = menu.getByRole("button", {
          name: "Discover and manage plugins",
        });
        if (!(await manage.count()))
          op.skip("Settings › System is shown only on the admin's own host");
        await op.click(manage);
        const settings = page.locator("#settings-dialog");
        await op.see(settings);
        await op.see(page.locator("#admin-frame"), 15000);
        await op.until(
          async () =>
            (await page.locator("#admin-frame").getAttribute("title")) ===
            "Administration: Plugins",
          "the frame title does not name Plugins",
        );
        await op.click(page.locator("#settings-close"));
      },
    );
  },
};
