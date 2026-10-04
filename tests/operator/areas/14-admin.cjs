// The standalone admin pages, explored read-only: home, providers, runs, catalogs,
// its settings dialog and the way back to the harness.
"use strict";
const { home, adminOpenUrl } = require("../lib/app.cjs");

module.exports = {
  id: "admin",
  title: "Admin pages (standalone)",
  async run(op) {
    let page = op.page;
    let own = null;
    const heading = (pattern) => op.until(async () => (await page.locator("h1:visible").allInnerTexts()).some((t) => pattern.test(t.trim())), "no heading " + pattern, 20000);

    try {
      await op.step("open", "Open the admin panel", async () => {
        if (op.session.target === "browser") {
          own = await op.session.newPage();
          page = own;
          op.session.page = own;
        }
        // The fixture admin answers only the owner: sign this browser in the way `keepharness open` does.
        await page.goto(op.fixtureMode ? adminOpenUrl(op) : op.options.adminUrl + "/");
        await heading(/^(Home|AI Providers)$/);
        await op.see(page.getByRole("link", { name: "Providers", exact: true }));
      }, { critical: true });

      for (const [name, pattern] of [["Runs", /^Runs$/], ["Catalogs and vault", /Catalogs and vault/], ["Providers", /AI Providers/], ["Home", /\S/]])
        await op.step("nav-" + name.split(" ")[0].toLowerCase(), `Admin navigation: ${name}`, async () => {
          await op.click(page.getByRole("link", { name, exact: true }).first());
          await heading(pattern);
        });

      await op.step("operations", "Open the operations list", async () => {
        await op.click(page.getByRole("button", { name: "Operations in progress and results" }));
        await op.see(page.locator("dialog[open], [role=dialog]:visible").first());
        await op.press("Escape");
      });

      await op.step("appearance", "The admin's Settings dialog opens and closes", async () => {
        await op.click(page.locator("#theme"));
        await op.see(page.locator("#appearance-title"));
        await op.click(page.locator("#appearance-close"));
        await op.gone(page.locator("#appearance-title"));
      });

      await op.step("brand-mark", "The admin uses the KeepHarness mark, not a stray glyph", async () => {
        const brand = (await page.locator("a.brand, [aria-label*=KeepHarness]").first().innerText()).trim();
        op.check(!brand.startsWith("⌘"), `the admin brand starts with "${brand.slice(0, 2)}"`);
      });

      await op.step("open-harness", "Open harness returns to the chat", async () => {
        const link = page.locator("#open-harness");
        if (!(await link.isVisible())) op.skip("no harness link on this admin");
        // The page ships no address; the admin's status supplies the harness's real one.
        const port = (href) => new URL(href, page.url()).port;
        await op.until(async () => port(await link.getAttribute("href")) === port(op.session.base), `Open harness points to ${await link.getAttribute("href")}`);
      });
    } finally {
      if (own) {
        await own.close().catch(() => {});
        op.session.page = op.session.context.pages()[0];
      } else if (op.session.target === "desktop") await home(op).catch(() => {});
    }
  },
};
