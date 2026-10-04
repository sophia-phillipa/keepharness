// Approvals and needs-you: an approval request from the fixture provider, the attention
// bell and popover, the needs-you inbox, enrolling this browser, Allow once, Deny and a
// multiple-choice question. Fixture mode only: enrolling a browser is a deliberate act.
"use strict";
const { home, row, cancelWaiting, newChat, chooseModel, chooseAccess, send, waitAnswer } = require("../lib/app.cjs");

module.exports = {
  id: "approvals",
  title: "Approvals, needs you, attention",
  async run(op) {
    const page = op.page;
    const messages = page.locator("#messages");
    const card = messages.locator("section", { hasText: "Allow once" }).last();
    let before = 0;
    if (!op.fixtureMode) {
      await op.step("fixture-only", "Approvals need an enrolled browser; real mode does not enroll one", async () => op.skip("fixture mode only"));
      return;
    }

    await op.step("home", "New chat with Claude in Ask for approval mode", async () => {
      await home(op);
      await newChat(op);
      await chooseModel(op, "claude-sonnet-5-5");
      await chooseAccess(op, "Ask for approval");
    }, { critical: true });

    await op.step("request", "The provider asks to run a command: an approval card appears", async () => {
      before = await send(op, "OP-APPROVAL run the fixture command");
      await op.until(() => card.isVisible(), "no approval card appeared", 60000);
      await op.see(card.getByRole("button", { name: "Allow once" }));
      await op.see(card.getByRole("button", { name: "Deny" }));
    }, { critical: true });

    await op.step("needs-you-strip", "The status strip counts one item that needs you", async () => {
      await op.seeText(page.locator("#run-status-toggle"), /1 needs you/);
      await op.seeText(page.locator("#needs-you-toggle"), /\(1\)/);
    });

    await op.step("needs-you-row", "The conversation row says it is waiting for you", async () => {
      await op.seeText(row(op, "OP-APPROVAL run the fixture"), /Waiting for your/);
    });

    await op.step("attention-bell", "The attention bell counts the request", async () => {
      await op.until(async () => /Attention, [1-9]/.test(await page.locator("#attention-bell").getAttribute("aria-label")), "the bell does not count the request");
    });

    await op.step("attention-popover", "Open the attention popover: it filters and links to the inbox", async () => {
      await op.click(page.locator("#attention-bell"));
      const popover = page.locator("#attention-popover");
      await op.see(popover);
      for (const name of ["Complete", "Request", "Error"]) await op.see(popover.getByRole("button", { name }));
      await op.see(page.locator("#attention-open-inbox"));
    });

    await op.step("attention-explains", "The popover names the pending request", async () => {
      await op.seeText(page.locator("#attention-popover"), /OP-APPROVAL|approval|command/i, 3000);
    }, { recover: false });

    await op.step("needs-you-inbox", "Open the needs-you inbox from the popover", async () => {
      if (!(await page.locator("#attention-popover").isVisible())) await op.click(page.locator("#attention-bell"));
      await op.click(page.locator("#attention-open-inbox"));
      await op.see(page.getByRole("button", { name: "Close inbox" }));
      await op.click(page.getByRole("button", { name: "Close inbox" }));
    });

    await op.step("not-enrolled", "Allow once on an unenrolled browser explains how to enroll", async () => {
      await op.click(card.getByRole("button", { name: "Allow once" }));
      await op.seeText(card, /not enrolled/);
    });

    await op.step("enroll", "Enroll this browser with the one-time link from the owner", async () => {
      const url = page.url();
      // The link is single-use: a retried step must not spend it twice.
      const enrolled = (await page.context().cookies()).some((cookie) => cookie.name === "harness_session");
      if (!enrolled) {
        await page.goto(op.session.base + "/approve-device?nonce=" + encodeURIComponent(op.fixture.nonce));
        await op.click(page.getByRole("button", { name: "Enable approvals on this browser" }));
        await page.waitForURL((u) => !u.pathname.startsWith("/approve-device"), { timeout: 15000 }).catch(() => {});
      }
      await page.goto(url);
      await page.locator("#startup-gate").waitFor({ state: "hidden", timeout: 30000 });
      await op.click(row(op, "OP-APPROVAL run the fixture"));
      await op.until(() => card.isVisible(), "no approval card appeared", 60000);
    }, { critical: true });

    await op.step("allow-once", "Allow once: the command runs and the answer finishes", async () => {
      await op.click(card.getByRole("button", { name: "Allow once" }));
      await waitAnswer(op, before, /Approval granted: the fixture command ran/);
      await op.until(async () => /0 needs you/.test(await page.locator("#run-status-toggle").innerText()), "the needs-you count did not clear");
    });

    await op.step("deny", "A second request, denied: nothing runs", async () => {
      before = await send(op, "OP-APPROVAL try the command again");
      await op.until(() => card.isVisible(), "no approval card appeared", 60000);
      await op.click(card.getByRole("button", { name: "Deny" }));
      await waitAnswer(op, before, /Approval denied: nothing ran/);
    });

    await op.step("question", "A multiple-choice question: pick Alpha and confirm", async () => {
      before = await send(op, "OP-QUESTION which option?");
      const gate = messages.locator("section", { hasText: "Which fixture option should run?" }).last();
      await op.until(() => gate.isVisible(), "no question appeared", 60000);
      const alpha = gate.getByRole("radio", { name: /Alpha/ });
      await op.click((await alpha.count()) ? alpha : gate.getByText("Alpha", { exact: true }));
      await op.click(gate.getByRole("button", { name: "Confirm choice" }));
      await waitAnswer(op, before, /You chose Alpha/);
    });

    await op.step("question-labels", "Each choice is announced by its label", async () => {
      await newChat(op);
      before = await send(op, "OP-QUESTION label check");
      const gate = messages.locator("section", { hasText: "Which fixture option should run?" }).last();
      await op.until(() => gate.isVisible(), "no question appeared", 60000);
      const labelled = (await gate.getByRole("radio", { name: /Alpha/ }).count()) === 1;
      // Answer it either way, so no run is left holding the provider.
      await op.click(labelled ? gate.getByRole("radio", { name: /Alpha/ }) : gate.getByText("Alpha", { exact: true }));
      await op.click(gate.getByRole("button", { name: "Confirm choice" }));
      await waitAnswer(op, before, /You chose Alpha/);
      op.check(labelled, "the Alpha option has no accessible label");
    }, { recover: false });

    await op.step("cleanup", "Stop anything still waiting for an answer", async () => {
      await cancelWaiting(op);
      await op.seeText(page.locator("#run-status-toggle"), /0 needs you/, 20000);
      // Later areas run as an unenrolled browser again (the enrolled lane has its own limits).
      if (op.session.target === "browser") await page.context().clearCookies({ name: "harness_session" });
    }, { always: true });
  },
};
