// Page-object helpers shared by the area modules: reach a calm home screen, start a
// chat, pick a model, send a message and wait for the answer. Locators use ids,
// roles and accessible names only, never layout classes.
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const ANSWER_DONE = /OPERATOR_OK|Approval (granted|denied)|You chose|declined/;

async function home(op, route = "/") {
  const page = op.page;
  await page.goto(op.session.base + route);
  // Mark the tour as seen for this release, as a returning user would have it. The flag lives in
  // the backend UI-state store (HarnessPrefs), not in localStorage.
  await page.evaluate(async () => {
    const { version } = await fetch("/v1/version").then((r) => r.json());
    if (!HarnessPrefs.get("tour_seen", ""))
      HarnessPrefs.set("tour_seen", version);
  });
  await page
    .locator("#startup-gate")
    .waitFor({ state: "hidden", timeout: 30000 });
  await dismissTour(op);
  await page.locator("#prompt").waitFor({ state: "visible", timeout: 15000 });
}

async function dismissTour(op) {
  const skip = op.page.locator("#tour-skip");
  if (await skip.isVisible().catch(() => false)) await skip.click();
  await op.page
    .locator("#tour-root")
    .waitFor({ state: "detached", timeout: 5000 })
    .catch(() => {});
}

async function newChat(op) {
  await op.click(op.page.locator("#new"));
  await op.seeText(op.page.locator("#conversation-title"), /New Conversation/);
}

async function catalog(op) {
  return op.page.evaluate(() => fetch("/v1/models").then((r) => r.json()));
}

// Fixture: the named model. Real mode: the cheapest sensible choice from the providers in
// OPERATOR_REAL_BACKENDS (comma list, in order; default Claude before Codex).
async function chooseModel(op, wanted) {
  let id = wanted;
  if (!id || !op.fixtureMode) {
    const { models } = await catalog(op);
    const usable = models;
    const order = (process.env.OPERATOR_REAL_BACKENDS || "claude,codex").split(",").map((b) => b.trim());
    const pick =
      usable.find((m) => order[0] === "claude" && m.backend === "claude" && /sonnet|haiku/.test(m.id)) ||
      order.map((b) => usable.find((m) => m.backend === b)).find(Boolean) ||
      usable[0];
    if (!pick) op.skip("no model is configured");
    if (!wanted || !usable.some((m) => m.id === wanted)) id = pick.id;
  }
  const menu = op.page.locator("#model-menu");
  await op.click(op.page.locator("#model-trigger"));
  // The option's data-value is the model id; its visible name is a friendly label.
  const selector = `[role="option"][data-value=${JSON.stringify(id)}]`;
  const option = menu.locator(selector);
  // Only the selected provider's group starts open, and older Claude models sit in a nested
  // "More models" group: open each closed group around the option, outermost first.
  // A filter's inner locator is matched inside each group, so it must not be rooted at the menu.
  for (const group of ["details[data-provider]", "details.model-more"]) {
    const closed = menu
      .locator(group)
      .filter({ has: op.page.locator(selector) })
      .first();
    if ((await closed.count()) && !(await closed.evaluate((n) => n.open)))
      await op.click(closed.locator(":scope > summary"));
  }
  await op.click(option);
  await op.until(
    async () => (await op.page.locator("#model").inputValue()) === id,
    "model " + id + " was not selected",
  );
  return id;
}

// Fixture only: the admin answers the owner, who holds the install secret (D09). Mint the
// one-time link `keepharness open` prints, from the secret in the fixture's admin state.
function adminOpenUrl(op) {
  const secret = fs
    .readFileSync(path.join(op.fixture.root, "admin", "local.key"), "utf8")
    .trim();
  const expires = Math.floor(Date.now() / 1000) + 300;
  const nonce = crypto.randomBytes(16).toString("base64url");
  const signature = crypto
    .createHmac("sha256", secret)
    .update(`open:${expires}.${nonce}`)
    .digest("hex");
  return `${op.options.adminUrl}/open?ticket=${expires}.${nonce}.${signature}`;
}

async function chooseAccess(op, label) {
  await op.click(op.page.locator("#access-trigger"));
  await op.click(
    op.page
      .locator("#access-menu")
      .getByRole("option", { name: new RegExp("^\\W*" + escapeRegex(label)) }),
  );
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
  const before = await op.page
    .locator("#messages")
    .getByRole("button", { name: "View run" })
    .count();
  await op.click(op.page.locator("#send"));
  return before;
}

// The newest turn has started once its "View run" button exists; it is finished when
// the header pill leaves running/queued/needs-you. Returns the newest message's text.
async function waitAnswer(
  op,
  before,
  pattern = op.fixtureMode ? ANSWER_DONE : /\S/,
  timeout = 90000,
) {
  const page = op.page;
  const runs = page
    .locator("#messages")
    .getByRole("button", { name: "View run" });
  await op.until(
    async () => (await runs.count()) > before,
    "the run did not start",
    30000,
  );
  const pill = page.locator("#conversation-state-pill");
  await op.until(
    async () =>
      (await pill.getAttribute("data-state")) === "done" ||
      /Failed|Cancelled|Interrupted/.test(await pill.innerText()),
    "the answer did not finish in time",
    timeout,
  );
  return op.seeText(
    page.locator("#messages").getByRole("article").last(),
    pattern,
    10000,
  );
}

// A sidebar conversation row (its accessible name may start with a status label).
function row(op, name) {
  return op.page
    .locator("#sidebar")
    .getByRole("button", { name: new RegExp(escapeRegex(name)) })
    .filter({ visible: true })
    .first();
}

// Stop every conversation still waiting for an answer from the user, so a pending
// approval does not hold the provider for the areas that follow.
async function cancelWaiting(op) {
  const waiting = op.page
    .locator("#sidebar")
    .getByRole("button")
    .filter({ hasText: /Waiting for your/ })
    .filter({ visible: true });
  for (let i = 0; i < 5 && (await waiting.count()); i++) {
    await op.click(waiting.first());
    const cancel = op.page.locator("#cancel");
    if (await cancel.isVisible()) await op.click(cancel);
    await op.until(
      async () =>
        !/needs-you|running|queued/.test(
          await op.page
            .locator("#conversation-state-pill")
            .getAttribute("data-state"),
        ),
      "the waiting run did not stop",
      20000,
    );
  }
}

async function ask(op, text, pattern) {
  const before = await send(op, text);
  return waitAnswer(op, before, pattern);
}

function escapeRegex(text) {
  return String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

module.exports = {
  home,
  row,
  cancelWaiting,
  dismissTour,
  newChat,
  chooseModel,
  adminOpenUrl,
  chooseAccess,
  send,
  submit,
  waitAnswer,
  ask,
  catalog,
  escapeRegex,
};
