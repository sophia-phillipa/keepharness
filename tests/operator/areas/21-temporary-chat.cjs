// Temporary chat (#60, D-047): start from Home with a draft, talk, confirm nothing is saved
// in the browser, the sidebar, the conversation list or on disk, then discard and return.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const {
  home,
  newChat,
  chooseModel,
  send,
  ask,
  catalog,
} = require("../lib/app.cjs");

const PROVIDERS = ["codex", "claude", "deepseek"];
const HOME_DRAFT = "OP60-HOME-DRAFT";
const HOME_DRAFT_KEY = "conversation-draft:new:sem-projeto";
const SID = /^[a-f0-9]{32}$/;

// The fixture's harness state folder and the provider run folders beside it (D-047).
function privateRoots(op) {
  const chat = path.join(op.fixture.root, "chat");
  return [
    path.join(chat, "temporary-chats"),
    path.join(chat, "sessions", "temporary-chats"),
  ];
}

function privateFolders(op) {
  return privateRoots(op).flatMap((root) =>
    fs.existsSync(root)
      ? fs.readdirSync(root).filter((name) => SID.test(name))
      : [],
  );
}

// Files under the fixture root that contain the text. Only the temporary trees are exempt,
// and only while the chat is open (they are the private storage that discard must remove).
function filesWith(op, text, { exemptPrivate }) {
  const hits = [];
  const needle = Buffer.from(text);
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const file = path.join(dir, entry.name);
      if (entry.isSymbolicLink()) continue;
      if (entry.isDirectory()) {
        if (!(exemptPrivate && entry.name === "temporary-chats")) walk(file);
      } else if (entry.isFile() && fs.readFileSync(file).includes(needle))
        hits.push(path.relative(op.fixture.root, file));
    }
  };
  walk(op.fixture.root);
  return hits;
}

const storageHolds = (page, text) =>
  page.evaluate(
    (t) => JSON.stringify({ ...sessionStorage, ...localStorage }).includes(t),
    text,
  );

const savedConversations = (page) =>
  page
    .evaluate(() => fetch("/v1/conversations").then((r) => r.json()))
    .then((data) => (data.conversations || []).map((c) => c.id).sort());

const homeDraftStored = (page) =>
  page.evaluate(
    (key) => JSON.parse(sessionStorage.getItem(key) || "null")?.draft,
    HOME_DRAFT_KEY,
  );

// Accept (or dismiss) the confirmation the next click or key raises, then run the action.
async function confirmWith(page, accept, action) {
  const answer = (dialog) => (accept ? dialog.accept() : dialog.dismiss());
  page.once("dialog", answer);
  try {
    await action();
  } finally {
    page.off("dialog", answer);
  }
}

// A temporary chat has no conversation header state: the reply is done when it shows and the page is idle.
async function askTemporary(op, text, pattern) {
  await send(op, text);
  await op.seeText(
    op.page.locator("#messages").getByRole("article").last(),
    pattern,
    60000,
  );
  await op.until(
    () => op.page.evaluate(() => !busy && !submitting),
    "the temporary reply did not finish",
    30000,
  );
}

async function startTemporary(op, via = "#composer-temporary") {
  const page = op.page;
  await op.click(page.locator(via));
  await op.see(page.locator("#temporary-chat-notice"));
  await op.until(
    () => page.evaluate(() => !temporaryStarting && !!temporarySession),
    "the temporary chat did not start",
  );
}

async function closeTemporary(op) {
  const page = op.page;
  await confirmWith(page, true, () =>
    op.click(page.locator("#close-temporary-chat")),
  );
  await op.gone(page.locator("#temporary-chat-notice"));
  await op.until(
    () => page.evaluate(() => !temporarySession && !loading),
    "the temporary chat did not close",
  );
}

// The composer context row, the notice and the page stay inside the viewport.
async function fits(page) {
  return page.evaluate(() => {
    const inside = (selector) => {
      const el = document.querySelector(selector);
      if (!el || !el.checkVisibility()) return true;
      const box = el.getBoundingClientRect();
      return box.left >= 0 && box.right <= innerWidth;
    };
    const row = document.querySelector(".composer-context");
    return {
      page: document.documentElement.scrollWidth <= innerWidth,
      row: row.scrollWidth <= row.clientWidth,
      note: inside("#draft-limit"),
      button: inside("#composer-temporary"),
      notice: inside("#temporary-chat-notice"),
      close: inside("#close-temporary-chat"),
    };
  });
}

module.exports = {
  id: "temporary-chat",
  title: "Temporary chat: private session, nothing saved, discard and return",
  async run(op) {
    const page = op.page;
    // Filled by the steps of the first connected provider, read by the origin steps.
    const state = { originId: "" };

    for (const provider of PROVIDERS) {
      const private_ = `OP60-PRIVATE-${provider.toUpperCase()}`;
      const ctx = { model: null, before: [], sid: "" };
      const needModel = () =>
        ctx.model || op.skip(`${provider} is not connected in the fixture`);
      const modelFor = async () => {
        if (!op.fixtureMode)
          op.skip("fixture mode only: temporary chats send real prompts");
        const { models } = await catalog(op);
        const found = models.filter((m) => m.backend === provider);
        const wanted =
          found.find((m) => m.id === "claude-sonnet-5-5") || found[0];
        if (!wanted)
          op.skip(
            `${provider} is not connected in the fixture (it only has Claude and Gemini stand-ins)`,
          );
        return wanted.id;
      };

      await op.step(
        provider + "-start",
        `${provider}: start a temporary chat from Home with a draft present`,
        async () => {
          await home(op);
          ctx.model = await modelFor();
          await newChat(op);
          ctx.before = await savedConversations(page);
          await op.fill(page.locator("#prompt"), HOME_DRAFT);
          await startTemporary(op);
          op.check(
            (await page.locator("#prompt").inputValue()) === "",
            "the temporary composer starts with the Home draft in it",
          );
          op.check(
            await page.evaluate(
              () =>
                document.activeElement === document.getElementById("prompt"),
            ),
            "the composer is not focused",
          );
          await op.seeText(
            page.locator("#temporary-chat-notice"),
            /Nothing is saved in KeepHarness/,
          );
          ctx.sid = await page.evaluate(() => temporarySession);
          op.check(
            privateFolders(op).includes(ctx.sid),
            "no private folder exists for session " + ctx.sid,
          );
          await chooseModel(op, ctx.model);
        },
        { critical: true },
      );

      await op.step(
        provider + "-send",
        `${provider}: send a message and get the fixture reply`,
        async () => {
          needModel();
          await askTemporary(op, `${private_} hello`, /OPERATOR_OK/);
          await op.seeText(
            page.locator("#temporary-chat-notice"),
            /Temporary chat/,
          );
        },
      );

      await op.step(
        provider + "-nothing-saved",
        `${provider}: nothing lands in storage, sidebar, conversation list or on disk`,
        async () => {
          needModel();
          op.check(
            !(await storageHolds(page, private_)),
            "the marker is in sessionStorage or localStorage",
          );
          const sidebar = await page.locator("#sidebar").innerText();
          op.check(!sidebar.includes(private_), "the marker is in the sidebar");
          op.check(
            JSON.stringify(await savedConversations(page)) ===
              JSON.stringify(ctx.before),
            "the saved conversation list changed during the chat",
          );
          const hits = filesWith(op, private_, { exemptPrivate: true });
          op.check(
            !hits.length,
            "the marker is on disk outside the private folders: " +
              hits.join(", "),
          );
        },
      );

      await op.step(
        provider + "-discard",
        `${provider}: discard removes the private folders and returns to the Home draft`,
        async () => {
          needModel();
          await confirmWith(page, false, () =>
            op.click(page.locator("#close-temporary-chat")),
          );
          op.check(
            await page.locator("#temporary-chat-notice").isVisible(),
            "cancelling the confirmation closed the chat",
          );
          await closeTemporary(op);
          op.check(
            (await page.locator("#prompt").inputValue()) === HOME_DRAFT,
            "the Home draft was not restored",
          );
          await op.until(
            async () => !privateFolders(op).includes(ctx.sid),
            "the private folders of session " + ctx.sid + " are still there",
          );
          const status = await page.evaluate(
            (sid) => fetch("/v1/temporary/" + sid).then((r) => r.status),
            ctx.sid,
          );
          op.check(
            status === 404,
            `the discarded session still answers ${status}`,
          );
          const hits = filesWith(op, private_, { exemptPrivate: false });
          op.check(
            !hits.length,
            "the marker survives the discard in: " + hits.join(", "),
          );
        },
      );

      await op.step(
        provider + "-after-return",
        `${provider}: after returning, history, sidebar and storage hold nothing from the chat`,
        async () => {
          needModel();
          op.check(
            JSON.stringify(await savedConversations(page)) ===
              JSON.stringify(ctx.before),
            "the saved conversation list changed",
          );
          op.check(
            !(await page.locator("#sidebar").innerText()).includes(private_),
            "the marker is in the sidebar",
          );
          op.check(
            !(await storageHolds(page, private_)),
            "the marker is in browser storage",
          );
          op.check(
            (await homeDraftStored(page)) === HOME_DRAFT ||
              (await page.locator("#prompt").inputValue()) === HOME_DRAFT,
            "the Home draft is gone",
          );
          await page.locator("#prompt").fill("");
        },
      );
      if (provider === "claude") state.model = ctx.model;
    }

    await op.step(
      "origin-conversation",
      "Close a temporary chat started from a conversation: back to it, both drafts intact",
      async () => {
        op.check(state.model, "no connected provider ran the chat steps");
        await home(op);
        await newChat(op);
        await chooseModel(op, state.model);
        await ask(op, "OP60-ORIGIN question", /OPERATOR_OK/);
        state.originId = await page.evaluate(() => conversation);
        // Opening a conversation carries the Home text along, so the stored Home draft is seeded
        // directly: it is what a reload or an earlier visit leaves behind.
        await page.evaluate(
          ([key, draft]) =>
            sessionStorage.setItem(
              key,
              JSON.stringify({
                draft,
                project: document.getElementById("project").value,
                files: [],
              }),
            ),
          [HOME_DRAFT_KEY, HOME_DRAFT],
        );
        await op.fill(page.locator("#prompt"), "OP60-ORIGIN-DRAFT");
        await startTemporary(op, "#new-temporary");
        await op.fill(page.locator("#prompt"), "OP60-PRIVATE-ORIGIN");
        await closeTemporary(op);
        await op.until(
          () =>
            page.evaluate(
              (id) => conversation === id && !loading,
              state.originId,
            ),
          "the origin conversation was not restored",
        );
        op.check(
          (await page.locator("#prompt").inputValue()) === "OP60-ORIGIN-DRAFT",
          "the origin draft was lost",
        );
        await page.evaluate(() => saveView());
        op.check(
          (await homeDraftStored(page)) === HOME_DRAFT,
          "the Home draft was lost ",
        );
        op.check(
          !(await storageHolds(page, "OP60-PRIVATE")),
          "a private marker is in browser storage",
        );
      },
      { fixtureOnly: true },
    );

    await op.step(
      "origin-deleted",
      "Origin deleted while the chat is open: the error stays visible and the Home draft survives",
      async () => {
        op.check(state.originId, "no origin conversation was created");
        await startTemporary(op, "#new-temporary");
        await op.fill(page.locator("#prompt"), "OP60-PRIVATE-DELETED");
        const deleted = await page.evaluate(
          (id) =>
            fetch("/v1/conversations/" + id, { method: "DELETE" }).then(
              (r) => r.status,
            ),
          state.originId,
        );
        op.check(
          deleted === 200 || deleted === 204,
          "deleting the origin answered " + deleted,
        );
        await closeTemporary(op);
        await op.seeText(
          page.locator("#status"),
          /Couldn't open the conversation/,
        );
        await page.evaluate(() => saveView());
        await op.seeText(
          page.locator("#status"),
          /Couldn't open the conversation/,
        );
        op.check(
          (await homeDraftStored(page)) === HOME_DRAFT,
          "the Home draft did not survive",
        );
        op.check(
          (await page.locator("#prompt").inputValue()) === HOME_DRAFT,
          "the composer does not show the Home draft",
        );
        op.check(
          !(await storageHolds(page, "OP60-PRIVATE")),
          "a private marker is in browser storage",
        );
        op.check(
          !privateFolders(op).length,
          "private folders remain after the discard",
        );
        await page.locator("#prompt").fill("");
      },
      { fixtureOnly: true },
    );

    // The composer context row holds the long-draft note and the entry button; it must not
    // overflow at phone or desktop width, on Home or inside a temporary chat.
    const wide = { width: 1440, height: 900 };
    for (const [width, height] of [
      [1280, 860],
      [390, 844],
    ]) {
      for (const where of ["home", "temporary"]) {
        await op.step(
          `layout-${width}-${where}`,
          `${width}px, ${where === "home" ? "Home" : "inside a temporary chat"}: the composer context row does not overflow`,
          async () => {
            if (!(await op.session.resize(width, height)))
              op.skip(`below the ${op.session.target} window's minimum size`);
            await home(op);
            if (where === "temporary") await startTemporary(op);
            await page.locator("#prompt").fill("x".repeat(130000));
            await op.see(page.locator("#draft-limit"));
            const fit = await fits(page);
            if (where === "temporary") await page.locator("#prompt").fill("");
            if (where === "temporary") await closeTemporary(op);
            await page.locator("#prompt").fill("");
            op.check(
              Object.values(fit).every(Boolean),
              `${width}px ${where}: ${JSON.stringify(fit)}`,
            );
          },
        );
      }
    }
    await op.step(
      "layout-restore",
      "Restore the window size",
      async () => {
        await op.session.resize(wide.width, wide.height);
      },
      { always: true, lint: false },
    );
  },
};
