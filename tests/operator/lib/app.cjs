// Page-object helpers shared by the area modules: reach a calm home screen, start a
// chat, pick a model, send a message and wait for the answer. Locators use ids,
// roles and accessible names only, never layout classes.
"use strict";

const ANSWER_DONE = /OPERATOR_OK|Approval (granted|denied)|You chose|declined/;

async function home(op, path = "/") {
  const page = op.page;
  await page.goto(op.session.base + path);
  // Mark the tour as seen for this release, as a returning user would have it.
  await page.evaluate(async () => {
    const { version } = await fetch("/v1/version").then((r) => r.json());
    if (!localStorage.getItem("keepharness-tour-seen")) localStorage.setItem("keepharness-tour-seen", version);
  });
  await page.locator("#startup-gate").waitFor({ state: "hidden", timeout: 30000 });
  await dismissTour(op);
  await page.locator("#prompt").waitFor({ state: "visible", timeout: 15000 });
}

async function dismissTour(op) {
  const skip = op.page.locator("#tour-skip");
  if (await skip.isVisible().catch(() => false)) await skip.click();
  await op.page.locator("#tour-root").waitFor({ state: "detached", timeout: 5000 }).catch(() => {});
}

async function newChat(op) {
  await op.click(op.page.locator("#new"));
  await op.seeText(op.page.locator("#conversation-title"), /New Conversation/);
}

async function catalog(op) {
  return op.page.evaluate(() => fetch("/v1/models").then((r) => r.json()));
}

// Fixture: the named model. Real mode: the cheapest sensible choice, Claude before Codex.
async function chooseModel(op, wanted) {
  let id = wanted;
  if (!id || !op.fixtureMode) {
    const { models } = await catalog(op);
    const usable = models.filter((m) => m.backend !== "maestro");
    const pick =
      usable.find((m) => m.backend === "claude" && /sonnet|haiku/.test(m.id)) ||
      usable.find((m) => m.backend === "claude") ||
      usable.find((m) => m.backend === "codex") ||
      usable[0];
    if (!pick) op.skip("no model is configured");
    if (!wanted || !usable.some((m) => m.id === wanted)) id = pick.id;
  }
  const menu = op.page.locator("#model-menu");
  await op.click(op.page.locator("#model-trigger"));
  const option = menu.getByRole("option", { name: new RegExp(escapeRegex(id)) }).first();
  // Only the selected provider's group starts open; open the model's group first.
  if (!(await option.isVisible())) {
    // A filter's inner locator is matched inside each group, so it must not be rooted at the menu.
    const hidden = op.page.getByRole("option", { name: new RegExp(escapeRegex(id)), includeHidden: true });
    await op.click(menu.getByRole("group", { includeHidden: true }).filter({ has: hidden }).locator("summary").first());
  }
  await op.click(option);
  await op.until(async () => (await op.page.locator("#model").inputValue()) === id, "model " + id + " was not selected");
  return id;
}

async function chooseAccess(op, label) {
  await op.click(op.page.locator("#access-trigger"));
  await op.click(op.page.locator("#access-menu").getByRole("option", { name: new RegExp("^\\W*" + escapeRegex(label)) }));
  await op.seeText(op.page.locator("#access-label"), label);
}

async function send(op, text) {
  await op.fill(op.page.locator("#prompt"), text);
  return submit(op);
}

// Send what is already in the composer (keeps chips and mentions intact).
async function submit(op) {
  op.spendPrompt();
  await op.paceSubmission();
  const before = await op.page.locator("#messages").getByRole("button", { name: "View run" }).count();
  await op.click(op.page.locator("#send"));
  return before;
}

// The newest turn has started once its "View run" button exists; it is finished when
// the header pill leaves running/queued/needs-you. Returns the newest message's text.
async function waitAnswer(op, before, pattern = op.fixtureMode ? ANSWER_DONE : /\S/, timeout = 90000) {
  const page = op.page;
  const runs = page.locator("#messages").getByRole("button", { name: "View run" });
  await op.until(async () => (await runs.count()) > before, "the run did not start", 30000);
  const pill = page.locator("#conversation-state-pill");
  await op.until(async () => (await pill.getAttribute("data-state")) === "done" || /Failed|Cancelled|Interrupted/.test(await pill.innerText()), "the answer did not finish in time", timeout);
  return op.seeText(page.locator("#messages").getByRole("article").last(), pattern, 10000);
}

// A sidebar conversation row (its accessible name may start with a status label).
function row(op, name) {
  return op.page.locator("#sidebar").getByRole("button", { name: new RegExp(escapeRegex(name)) }).filter({ visible: true }).first();
}

// Stop every conversation still waiting for an answer from the user, so a pending
// approval does not hold the provider for the areas that follow.
async function cancelWaiting(op) {
  const waiting = op.page.locator("#sidebar").getByRole("button").filter({ hasText: /Waiting for your/ }).filter({ visible: true });
  for (let i = 0; i < 5 && (await waiting.count()); i++) {
    await op.click(waiting.first());
    const cancel = op.page.locator("#cancel");
    if (await cancel.isVisible()) await op.click(cancel);
    await op.until(async () => !/needs-you|running|queued/.test(await op.page.locator("#conversation-state-pill").getAttribute("data-state")), "the waiting run did not stop", 20000);
  }
}

async function ask(op, text, pattern) {
  const before = await send(op, text);
  return waitAnswer(op, before, pattern);
}

function escapeRegex(text) {
  return String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

module.exports = { home, row, cancelWaiting, dismissTour, newChat, chooseModel, chooseAccess, send, submit, waitAnswer, ask, catalog, escapeRegex };
