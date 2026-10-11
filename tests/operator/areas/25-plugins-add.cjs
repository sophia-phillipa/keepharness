// Plugins Add actions in the admin (#27 P6): the Add menu, the marketplace dialog and an accepted
// source for each CLI. Fixture mode drives the fake CLIs, which advertise marketplace_add; the
// accepted-source steps change the real machine and run only in real mode (--allow-writes).
"use strict";
const { adminOpenUrl, escapeRegex } = require("../lib/app.cjs");

const ADD_TIMEOUT = 120000;

module.exports = {
  id: "plugins-add",
  title: "Plugins Add actions (admin)",
  async run(op) {
    let page = op.page;
    let own = null;
    // Built from the current page, so they follow the admin page the open step may create.
    const ui = () => ({
      addButton: page.locator("#plugins-add"),
      menu: page.locator('[data-testid="plugins-add-menu"]'),
      menuItems: page.locator('[data-testid="plugins-add-menu"] [role="menuitem"]'),
      dialog: page.locator("#plugins-add-dialog"),
      source: page.locator('[data-testid="plugins-add-source"]'),
      submit: page.locator('[data-testid="plugins-add-submit"]'),
      cancel: page.locator('[data-testid="plugins-add-cancel"]'),
      error: page.locator('[data-testid="plugins-add-error"]'),
      refresh: page.locator('[data-testid="plugins-refresh"]'),
      list: page.locator('[data-testid="plugins-list"]'),
    });

    // Open the Add menu and pick the entry for one provider; skip when the page does not offer it.
    const pickAddItem = async (pattern, what) => {
      const { addButton, menu, menuItems, dialog } = ui();
      await op.click(addButton);
      await op.see(menu);
      const item = menuItems.filter({ hasText: pattern });
      if ((await item.count()) === 0) {
        await op.press("Escape");
        op.skip(`the Add menu offers no "${what}" entry on this page`);
      }
      await op.click(item.first());
      await op.see(dialog);
    };
    const closeDialog = async () => {
      const { dialog, cancel } = ui();
      if (await dialog.evaluate((el) => el.open).catch(() => false)) {
        await op.click(cancel);
        await op.until(async () => !(await dialog.evaluate((el) => el.open)), "the dialog did not close");
      }
    };

    try {
      await op.step(
        "open",
        "Open the admin Plugins page",
        async () => {
          if (op.session.target === "browser") {
            own = await op.session.newPage();
            page = own;
            op.session.page = own;
          }
          // The fixture admin answers only the owner: sign this browser in the way `keepharness open` does.
          if (op.fixtureMode) await page.goto(adminOpenUrl(op));
          await page.goto(op.options.adminUrl + "/#plugins");
          // The Add button appears once the CLIs' catalogs load; the fixture probes them, so allow more than the default wait.
          await op.see(ui().addButton, 40000);
          await op.see(ui().refresh);
        },
        { critical: true },
      );

      await op.step("add-menu", "The Add menu lists marketplace and MCP entries, never DeepSeek", async () => {
        const { addButton, menu, menuItems } = ui();
        await op.click(addButton);
        await op.see(menu);
        const texts = await menuItems.allInnerTexts();
        op.check(
          texts.some((text) => /^Add a marketplace/.test(text)),
          `no "Add a marketplace" entry; got ${JSON.stringify(texts)}`,
        );
        op.check(
          texts.every((text) => /^Add a marketplace to (Codex|Claude)( Code)?$/.test(text) || /^Add MCP server( to (Codex|Claude)( Code)?)?$/.test(text)),
          `unexpected Add entry; got ${JSON.stringify(texts)}`,
        );
        op.check(
          !texts.some((text) => /DeepSeek/i.test(text)),
          "DeepSeek has an Add entry, but it has no catalog command",
        );
        await op.press("Escape");
        await op.gone(menu);
      });

      await op.step(
        "rejected-source",
        "A malformed marketplace source keeps the dialog open with an error",
        async () => {
          await pickAddItem(/^Add a marketplace to Claude/, "Add a marketplace to Claude");
          const { dialog, source, submit, error } = ui();
          await op.fill(source, "not a source!");
          await op.click(submit);
          await op.seeText(error, /\S/, ADD_TIMEOUT);
          op.check(await dialog.evaluate((el) => el.open), "the dialog closed on a rejected source");
          await closeDialog();
        },
        { writes: true },
      );

      // The accepted-source steps add a real marketplace to the named CLI, then check it is listed.
      const acceptedSource = async (provider, label) => {
        const sourceValue = process.env.OPERATOR_MARKETPLACE_SOURCE;
        const name = process.env.OPERATOR_MARKETPLACE_NAME;
        if (!sourceValue) op.skip("OPERATOR_MARKETPLACE_SOURCE is not set");
        if (!name) op.skip("OPERATOR_MARKETPLACE_NAME is not set");
        await pickAddItem(new RegExp("^Add a marketplace to " + provider), label);
        const { dialog, source, submit, error, refresh, list } = ui();
        await op.fill(source, sourceValue);
        await op.click(submit);
        await op.until(
          async () => !(await dialog.evaluate((el) => el.open)),
          `the ${provider} dialog stayed open: ${(await error.innerText().catch(() => "")).trim()}`,
          ADD_TIMEOUT,
        );
        await op.click(refresh);
        await op.seeText(list, new RegExp(escapeRegex(name)), ADD_TIMEOUT);
      };

      await op.step(
        "accepted-source",
        "Add the configured marketplace to Claude and see it listed",
        () => acceptedSource("Claude", "Add a marketplace to Claude"),
        { writes: true, realOnly: true },
      );

      await op.step(
        "accepted-source-codex",
        "Add the configured marketplace to Codex and see it listed",
        () => acceptedSource("Codex", "Add a marketplace to Codex"),
        { writes: true, realOnly: true },
      );
    } finally {
      await closeDialog().catch(() => {});
      if (own) {
        await own.close().catch(() => {});
        op.session.page = op.session.context.pages()[0];
      }
    }
  },
};
