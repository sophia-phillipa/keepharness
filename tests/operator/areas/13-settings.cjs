// Settings: Appearance (themes, panel position, text size), Agents and models, Skills,
// System (the embedded admin: Providers, Operations, Runs, Catalogs), Connection/MCP,
// Provider usage and the tour.
"use strict";
const { home } = require("../lib/app.cjs");

module.exports = {
  id: "settings",
  title: "Settings",
  async run(op) {
    const page = op.page;
    const dialog = page.locator("#settings-dialog");
    const nav = (name) => dialog.getByRole("button", { name, exact: true });
    const palette = () => page.evaluate(() => document.documentElement.dataset.palette);
    const open = async () => {
      if (!(await dialog.isVisible())) await op.click(page.locator("#settings"));
      await op.see(dialog);
    };

    await op.step("open", "Open Settings: it shows the release", async () => {
      await home(op);
      await open();
      await op.seeText(page.locator("#version"), new RegExp("Release: " + op.options.version.replace(/\./g, "\\.")));
    }, { critical: true });

    await op.step("themes", "Appearance lists the themes; pick Graphite, then Paper", async () => {
      await op.click(nav("Appearance"));
      const themes = dialog.getByRole("group", { name: "Theme for this interface" });
      await op.see(themes);
      op.check((await themes.getByRole("button").count()) >= 8 || (await themes.getByRole("radio").count()) >= 8, "fewer than 8 themes");
      await op.click(themes.getByRole("button", { name: /Graphite/ }).or(themes.getByRole("radio", { name: /Graphite/ })).first());
      await op.until(async () => (await palette()) === "graphite", "Graphite was not applied");
      await op.click(themes.getByRole("button", { name: /Paper/ }).or(themes.getByRole("radio", { name: /Paper/ })).first());
      await op.until(async () => (await palette()) === "paper", "Paper was not applied");
    });

    await op.step("panel-order", "Move conversations to the right, then restore the default", async () => {
      const right = dialog.getByRole("button", { name: /Conversations on the right/ });
      await op.click(right);
      await op.until(async () => (await right.getAttribute("aria-pressed")) === "true", "the right layout was not chosen");
      await op.click(page.locator("#panel-order-reset"));
      await op.until(async () => (await dialog.getByRole("button", { name: /Conversations on the left/ }).getAttribute("aria-pressed")) === "true", "the default was not restored");
    });

    await op.step("text-size", "Make message text larger, then back to default", async () => {
      await op.select(page.locator("#reading-size"), "17");
      await op.until(async () => (await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--th-reading-size").trim())) === "17px", "the text size did not change");
      await op.select(page.locator("#reading-size"), "15");
    });

    await op.step("agents-models", "Agents and models lists your agents and the model catalog", async () => {
      await op.click(nav("Agents and models"));
      await op.see(page.locator("#settings-agents"));
      await op.see(page.locator("#agent-create"));
      await op.see(page.locator("#maestro-plan-policy"));
      await op.see(page.locator("#catalog-models"));
    });

    await op.step("skills", "Skills lists the skill catalog", async () => {
      await op.click(nav("Skills"));
      await op.see(page.locator("#settings-skills"));
      await op.see(page.locator("#catalog-skills"));
    });

    for (const [name, heading] of [["Providers", /AI Providers/], ["Operations", /\S/], ["Runs", /^Runs$/], ["Catalogs and vault", /Catalogs and vault/]])
      await op.step("system-" + name.split(" ")[0].toLowerCase(), `System › ${name} shows the admin inside Settings`, async () => {
        if (!(await dialog.getByRole("group", { name: "System" }).isVisible())) op.skip("System is shown only on the admin's own host");
        await op.click(nav(name));
        const frame = page.locator("#admin-frame");
        await op.see(frame, 15000);
        await op.until(async () => (await frame.getAttribute("title")) === "Administration: " + name, "the frame title does not name " + name);
        const content = page.frameLocator("#admin-frame");
        await op.until(async () => (await content.locator("h1:visible").allInnerTexts()).some((t) => heading.test(t.trim())), `the admin did not show ${name}`, 20000);
      });

    await op.step("system-no-nav", "The embedded admin hides its own navigation", async () => {
      if (!(await page.locator("#admin-frame").isVisible())) op.skip("no embedded admin");
      op.check(await page.frameLocator("#admin-frame").locator(".sidebar").isHidden(), "the embedded admin shows its own sidebar");
    });

    await op.step("connection-mcp", "Connection / MCP shows how to connect a client", async () => {
      await op.click(page.locator("#setup"));
      await op.see(page.locator("#setup-dialog"));
      await op.seeText(page.locator("#setup-code"), /\S/);
      await op.click(page.locator("#setup-close"));
      await op.gone(page.locator("#setup-dialog"));
    });

    await op.step("provider-usage", "Provider usage opens the quota panel", async () => {
      await open();
      await op.click(page.locator("#settings-quota"));
      await op.see(page.locator("#quota-panel"));
      // The fixture reports 42% and 17% used, shown as remaining.
      await op.seeText(page.locator("#quota-panel"), op.fixtureMode ? /58% remaining[\s\S]*83% remaining/ : /\S/);
    });

    await op.step("usage-escape", "Escape closes the quota panel and returns to Settings", async () => {
      await op.press("Escape");
      await op.gone(page.locator("#quota-panel"));
      await op.see(dialog);
    });

    await op.step("settings-tour", "Take the tour from Settings, then skip it", async () => {
      await open();
      await op.click(page.locator("#settings-tour"));
      await op.see(page.locator("#tour-root"));
      await op.click(page.locator("#tour-skip"));
      await op.gone(page.locator("#tour-root"));
    });

    await op.step("close", "Close Settings", async () => {
      await open();
      await op.click(page.locator("#settings-close"));
      await op.gone(dialog);
    });
  },
};
