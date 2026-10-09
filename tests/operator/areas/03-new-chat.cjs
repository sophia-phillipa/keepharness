// New chat per provider: the model picker, effort levels and access modes.
"use strict";
const { home, newChat, chooseModel, chooseAccess, ask, catalog, escapeRegex } = require("../lib/app.cjs");

module.exports = {
  id: "new-chat",
  title: "New chat: providers, model, effort, access",
  async run(op) {
    const page = op.page;
    const menu = page.locator("#model-menu");

    await op.step("home", "Open a new conversation", async () => {
      await home(op);
      await newChat(op);
    }, { critical: true });

    await op.step("picker-groups", "The model picker groups models by provider", async () => {
      await op.click(page.locator("#model-trigger"));
      await op.see(menu);
      const { models } = await catalog(op);
      op.check(models.length > 0, "the catalog lists no model");
      op.check((await menu.getByRole("option").count()) >= 1, "the picker shows no option");
      if (op.fixtureMode) {
        await op.seeText(menu, /Claude/);
        await op.seeText(menu, /Gemini/);
        // Options carry friendly names; the older Claude model sits under "More models".
        for (const [id, name] of [["claude-sonnet-5-5", "Claude Sonnet 5.5"], ["claude-opus-5-5", "Claude Opus 5.5"], ["claude-sonnet-4-5", "Claude Sonnet 4.5"]]) {
          const title = menu.locator(`[role="option"][data-value="${id}"] strong`);
          op.check((await title.allTextContents()).join() === name, `${id} is not listed as "${name}"`);
        }
        op.check((await menu.locator('.model-more [role="option"][data-value="claude-sonnet-4-5"]').count()) === 1, "claude-sonnet-4-5 is not under More models");
      }
      await op.press("Escape");
      await op.gone(menu);
    });

    const providers = op.fixtureMode
      ? [["claude", "claude-sonnet-5-5"], ["claude-opus", "claude-opus-5-5"], ["claude-legacy", "claude-sonnet-4-5"], ["gemini", "gemini-fixture"]]
      : [["real", null]];
    for (const [name, model] of providers) {
      await op.step("send-" + name, `New chat with ${model || "a real provider (OPERATOR_REAL_BACKENDS order)"}: send and get an answer`, async () => {
        await newChat(op);
        const chosen = await chooseModel(op, model);
        const text = op.fixtureMode ? `Hello ${chosen}` : "Reply with exactly the word READY and nothing else.";
        // Real mode: a signed-out provider answers with a renew card; that is the owner's state, not a defect.
        const answer = await ask(op, text, op.fixtureMode ? new RegExp("OPERATOR_OK from the " + escapeRegex(chosen)) : /READY|Renew \w+ access/i);
        const renew = !op.fixtureMode && answer.match(/Renew (\w+) access/);
        if (renew) op.skip(`${renew[1]} is signed out on this machine (${chosen}); set OPERATOR_REAL_BACKENDS to a signed-in provider`);
        op.check(answer.includes(chosen) || !op.fixtureMode, "the answer footer does not name " + chosen);
      });
    }

    await op.step("effort-menu", "Open the effort menu and pick High", async () => {
      await newChat(op);
      await chooseModel(op, "claude-sonnet-5-5");
      await op.click(page.locator("#effort-trigger"));
      const efforts = page.locator("#effort-menu");
      await op.see(efforts);
      await op.seeText(efforts, /Default/);
      const high = efforts.getByRole("option", { name: /High/ });
      if (!(await high.count())) op.skip("this model offers no High effort");
      await op.click(high.first());
      await op.seeText(page.locator("#effort-label"), /High/);
    });

    await op.step("effort-default", "Return effort to the provider's default", async () => {
      await op.click(page.locator("#effort-trigger"));
      await op.click(page.locator("#effort-menu").getByRole("option", { name: /Default/ }).first());
      await op.seeText(page.locator("#effort-label"), /^Default$/);
    });

    await op.step("effort-single", "A model with one effort offers only Default", async () => {
      await chooseModel(op, "gemini-fixture");
      const trigger = page.locator("#effort-trigger");
      if (await trigger.isDisabled()) return;
      await op.click(trigger);
      const count = await page.locator("#effort-menu").getByRole("option").count();
      await op.press("Escape");
      op.check(count <= 1, `a single-effort model offers ${count} effort options`);
    }, { fixtureOnly: true });

    // Full access is offered only when the owner turned it on (serve_fixture.py sets full_access;
    // ui.js offerFullAccess unhides data-access="full"); otherwise the option stays hidden.
    const fullAccessOption = page.locator('#access-menu [data-access="full"]');
    const fullAccessOffered = () => fullAccessOption.evaluate((el) => !el.hidden);
    const fullAccessSkip = "Full access not enabled by the owner (Control › Allow Full access)";

    await op.step("access-menu", "The access menu explains its modes", async () => {
      await op.click(page.locator("#access-trigger"));
      const access = page.locator("#access-menu");
      await op.see(access);
      const labels = ["Ask for approval", "Automatic", "Read only"];
      if (await fullAccessOffered()) labels.splice(2, 0, "Full access");
      else op.check(await fullAccessOption.isHidden(), "Full access is hidden but still shown in the menu");
      for (const label of labels) await op.seeText(access, label);
      await op.press("Escape");
    });

    for (const label of ["Automatic", "Read only", "Full access", "Ask for approval"])
      await op.step("access-" + label.toLowerCase().replace(/ /g, "-"), `Choose access mode "${label}"`, async () => {
        if (label === "Full access" && !(await fullAccessOffered())) op.skip(fullAccessSkip);
        await chooseAccess(op, label);
      });

    await op.step("access-header-readable", "The conversation header names the access mode in words", async () => {
      const text = (await page.locator("#header-access").innerText()).trim();
      op.check(/[A-Z ]/.test(text) && !/^(ask|auto|full|read_only)$/.test(text), `the header shows the raw mode "${text}"`);
    }, { recover: false });
  },
};
