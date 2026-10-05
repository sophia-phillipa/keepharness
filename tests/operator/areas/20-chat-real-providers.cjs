// Real-provider chat campaign on the packaged desktop app. Self-run (see the command below): it
// attaches the app to the persistent campaign instance (tests/operator/chat-campaign/instance.cjs),
// drives real Codex turns visibly, and records per-turn metrics. Under run-operator.cjs without
// CHAT_SLICE (or in fixture mode) the area is skipped.
//
//   node tests/operator/chat-campaign/instance.cjs start
//   env -u WAYLAND_DISPLAY DISPLAY=:0 CHAT_SLICE=pilot CHAT_BUDGET=3 \
//     NODE_PATH=<node_modules with playwright> PLAYWRIGHT_MODULE=<same>/playwright \
//     node tests/operator/areas/20-chat-real-providers.cjs
//
// Env: CHAT_SLICE (pilot|luna|deepseek|s1a|s1b|s2a), CHAT_SCENARIOS (comma list, overrides the slice),
// CHAT_BUDGET (real prompts allowed), CHAT_DEEPSEEK_WAIT_MS, CHAT_APP (an inspect-enabled copy of the
// packaged binary: Playwright cannot attach to the production package, whose inspect fuse is off).
// Known limitation: the desktop attaches to the running admin, so closing the app does not stop
// the backend (the instance keeps running until `instance.cjs stop`).
"use strict";
const { spawn, execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const instance = require("../chat-campaign/instance.cjs");
const { chooseModel, chooseAccess, newChat, submit, waitAnswer, dismissTour, row } = require("../lib/app.cjs");

const { paths, FACTS, S1, S1B, seedSlice1b, PROJECT_NAME, adminApi, portOpen } = instance;
const REPO = path.resolve(__dirname, "../../..");
const ADMIN_PORT = "18641";
const HARNESS_PORT = "18640";
const APP = process.env.CHAT_APP || path.join(instance.paths.root, "app/keepharness-bin"); // inspect-enabled copy of the packaged build (see plan.md)
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const playwright = () => require(process.env.PLAYWRIGHT_MODULE || "playwright");
const LIMIT = /quota|credit|rate.?limit|usage.?limit|limit reached|too many requests|insufficient|billing|exhausted/i;
const harnessUrl = `http://127.0.0.1:${HARNESS_PORT}`;

let ctx = null; // set by the self-run main(): the live app handle, sampler and metrics

// ------------------------------------------------------------------ scenarios

const SLICES = {
  pilot: ["new-chat-short", "follow-up", "project-facts", "reopen"],
  luna: ["luna-short"],
  deepseek: ["await-deepseek-key"],
  s1a: ["s1-01", "s1-02", "s1-03", "s1-04", "s1-05"],
  s1b: ["s1-06", "s1-07", "s1-08", "s1-09", "s1-10"],
  s2a: ["s2-01", "s2-02", "s2-03", "s2-04", "s2-05"],
};
const PROJECT_LABEL = process.env.CHAT_PROJECT_LABEL || "Campaign notes"; // CHAT_PROJECT_LABEL: dry runs use a throwaway project
const MAIN_DIR = path.join(paths.home, S1.main);
const OUTSIDE_FILE = path.join(paths.projects, "outside/vault.txt");

const ask = async (op, name, text, pattern, opts) => turn(op, name, text, pattern, opts);

// Soft checks: every failed check is collected so the remaining prompts of a scenario still run.
function soft() {
  const failed = [];
  return { ok: (condition, message) => condition || failed.push(message), done() { if (failed.length) throw new Error(failed.join("; ")); } };
}

const SCENARIOS = {
  "new-chat-short": async (op) => {
    await op.step("new-chat-short", "Now: a new chat on Sol (Medium) asking for the highest mountain", async () => {
      await newChat(op);
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      await ask(op, "new-chat-short", "What is the highest mountain on Earth? Answer in one short sentence.", /Everest/);
    }, { lint: false });
  },
  "follow-up": async (op) => {
    await op.step("follow-up", "Now: a follow-up in the same conversation", async () => {
      await ask(op, "follow-up", "How tall is it, in meters?", /8[,.\s]?8\d\d/);
    }, { lint: false });
  },
  "project-facts": async (op) => {
    await op.step("project-facts", `Now: a chat in the project ${PROJECT_NAME}, Read only, asking what FACTS.md says`, async () => {
      await newChat(op);
      await chooseProject(op, "Chat facts");
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      await chooseAccess(op, "Read only");
      const all = new RegExp(`(?=[\\s\\S]*${FACTS.harbor})(?=[\\s\\S]*${FACTS.lamp})(?=[\\s\\S]*${FACTS.code})`);
      await ask(op, "project-facts", "Read FACTS.md in this project and quote exactly the harbor name, the lantern count with its wording, and the door code.", all);
    }, { lint: false });
  },
  reopen: async (op) => {
    await op.step("reopen", "Now: closing the app and opening it again", async () => {
      const before = await snapshot(op.page);
      before.bounds = await bounds(ctx.handle);
      await op.caption("Now: closing the app");
      await quitApp(ctx.handle);
      await sleep(1500);
      const started = Date.now();
      await openApp(op.session);
      const ms = Date.now() - started;
      await dismissTourSoon(op);
      const after = await snapshot(op.page);
      after.bounds = await bounds(ctx.handle);
      const restored = !!before.title && before.title === after.title && after.articles > 0;
      ctx.reopen = { ms, before, after, restored };
      record({ scenario: "reopen", reopen_ms: ms, bounds_before: before.bounds, bounds_after: after.bounds, restored, title_before: before.title, title_after: after.title });
      op.check(after.title !== undefined, "the reopened app showed no conversation header");
    }, { lint: false });
  },
  "luna-short": async (op) => {
    await op.step("luna-short", "Now: a new chat on Luna asking for the highest mountain", async () => {
      await newChat(op);
      await chooseModel(op, "gpt-5.6-luna");
      await chooseEffort(op, "Medium");
      await ask(op, "luna-short", "What is the highest mountain on Earth? Answer in one short sentence.", /Everest/);
    }, { lint: false });
  },
  "s1-01": async (op) => {
    await op.step("s1-01", "Now: S1-01, a new chat with a short question and a follow-up", async () => {
      const c = soft();
      await newChat(op);
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      await ask(op, "s1-01-q", "What is the capital of Australia? Answer in one short sentence.", /Canberra/);
      const q = ctx.last;
      c.ok(q.ttft_ms != null && q.ttft_ms > 0, "no TTFT was measured on the reply body");
      c.ok(JSON.stringify(q.send.busy) !== JSON.stringify(q.send.before) || JSON.stringify(q.send.busy) !== JSON.stringify(q.send.done), `the send button never changed state: ${JSON.stringify(q.send)}`);
      await ask(op, "s1-01-follow", "About how many people live there? Answer in one short sentence.", /\d|thousand|million/i);
      const h = await page(op).evaluate(() => ({ user: document.querySelectorAll("#messages article.user").length, assistant: document.querySelectorAll("#messages article.assistant").length }));
      c.ok(h.user === 2 && h.assistant === 2, `the history shows ${h.user} user and ${h.assistant} assistant messages, expected 2 and 2`);
      record({ scenario: "s1-01", history: h, send: q.send, first_text: q.first_text, dom: q.dom });
      c.done();
    }, { lint: false });
  },
  "s1-02": async (op) => {
    await op.step("s1-02", "Now: S1-02, Markdown, a table, code blocks in two languages and a 120-line listing", async () => {
      const c = soft();
      await newChat(op);
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      await ask(op, "s1-02-md", "Reply in Markdown with exactly: a level-2 heading, a level-3 heading, a bullet list of 3 items, a numbered list of 3 items and a table of 3 columns (Island, Harbor, Lanterns) with 3 rows of invented data. No code blocks, no other text.", /\S/);
      const md = await page(op).evaluate(() => {
        const a = [...document.querySelectorAll("#messages article.assistant")].pop();
        const body = a.querySelector(":scope > .text");
        const t = body.querySelector("table");
        const wrap = t && (t.closest(".table-wrap, .table-scroll") || t.parentElement);
        const ar = a.getBoundingClientRect();
        return { h: body.querySelectorAll("h1,h2,h3,h4").length, ul: body.querySelectorAll("ul li").length, ol: body.querySelectorAll("ol li").length, rows: t ? t.querySelectorAll("tr").length : 0, cols: t ? t.rows[0].cells.length : 0, tableFits: !!t && wrap.getBoundingClientRect().right <= ar.right + 1, articleOverflow: a.scrollWidth > a.clientWidth + 1 };
      });
      c.ok(md.h >= 2 && md.ul >= 3 && md.ol >= 3, `headings/lists not rendered: ${JSON.stringify(md)}`);
      c.ok(md.rows >= 4 && md.cols === 3, `table not rendered as 3 columns, 4 rows: ${JSON.stringify(md)}`);
      c.ok(md.tableFits && !md.articleOverflow, `the table does not fit the message: ${JSON.stringify(md)}`);
      record({ scenario: "s1-02-md", md });
      await ask(op, "s1-02-two-langs", "Give two short code blocks, one Python and one JavaScript, each a function with a loop and an if inside it, indented with 4 spaces. Only the two code blocks, no other text.", /\S/);
      const two = await codeBlocks(op);
      c.ok(two.length >= 2 && new Set(two.map((b) => b.lang)).size >= 2, `expected 2 languages, got ${JSON.stringify(two.map((b) => b.lang))}`);
      c.ok(two.every((b) => /\n {4}\S/.test(b.text)), "a code block lost its indentation");
      await copyCheck(op, c, 0, "first code block");
      await copyCheck(op, c, 1, "second code block");
      await ask(op, "s1-02-listing", "Write one Python code block of exactly 120 lines: a module of small functions with docstrings, indented with 4 spaces. Output only that code block.", /\S/);
      const big = await codeBlocks(op);
      const lines = big[0] ? big[0].text.replace(/\n$/, "").split("\n").length : 0;
      c.ok(lines >= 100 && lines <= 140, `the listing has ${lines} lines, expected about 120`);
      c.ok(big[0] && !big[0].clipped, `the code block clips its content: ${JSON.stringify(big[0] && { clipped: big[0].clipped, internal: big[0].scrolls })}`);
      c.ok(big[0] && /\n {4}\S/.test(big[0].text), "the listing lost its indentation");
      record({ scenario: "s1-02-listing", lines, scrolls_internally: big[0]?.scrolls, clipped: big[0]?.clipped });
      await copyCheck(op, c, 0, "120-line listing");
      await ask(op, "s1-02-wide-table", "Reply with only a Markdown table of 8 columns with long header names (for example HarborRegistrationNumber) and 3 rows of invented data.", /\S/);
      const wide = await page(op).evaluate(() => {
        const a = [...document.querySelectorAll("#messages article.assistant")].pop();
        const t = a.querySelector(":scope > .text table");
        const wrap = t && (t.closest(".table-wrap, .table-scroll") || t.parentElement);
        const ar = a.getBoundingClientRect();
        return { cols: t ? t.rows[0].cells.length : 0, fits: !!t && wrap.getBoundingClientRect().right <= ar.right + 1, scrollsInside: !!t && wrap.scrollWidth > wrap.clientWidth + 1, articleOverflow: a.scrollWidth > a.clientWidth + 1 };
      });
      c.ok(wide.cols >= 8 && wide.fits && !wide.articleOverflow, `the wide table breaks the message layout: ${JSON.stringify(wide)}`);
      record({ scenario: "s1-02-wide-table", wide });
      c.done();
    }, { lint: false });
  },
  "s1-03": async (op) => {
    await op.step("s1-03", "Now: S1-03, a No project chat asking about a file by path", async () => {
      const c = soft();
      await newChat(op);
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      c.ok(/No project/i.test(await op.page.locator("#project-button-label").innerText()), "the scope label does not show No project");
      const file = path.join(MAIN_DIR, "facts/alpha.txt");
      const reply = await ask(op, "s1-03-a", `Read the file ${file} and tell me the archive code word it contains. If you cannot read it, say so plainly.`, /\S/);
      const text = typeof reply === "string" ? reply : (await op.page.locator("#messages article.assistant").last().innerText());
      c.ok(!text.includes(S1.codeWord), "the No project chat revealed the code word of a file outside its scope");
      c.ok(/can't|cannot|can not|unable|not able|don't have|do not have|no access|not available|couldn't|could not|isn't|not allowed|outside|denied|refus|only the active|not in/i.test(text), `the reply does not say it cannot read the file: ${text.slice(0, 160)}`);
      await ask(op, "s1-03-b", `And what is the archive keeper named in ${file}? If you cannot read it, say so plainly.`, /\S/);
      const text2 = await op.page.locator("#messages article.assistant").last().innerText();
      c.ok(!text2.includes(S1.keeper), "the second reply revealed the keeper name of the out-of-scope file");
      c.ok(/No project/i.test(await op.page.locator("#project-button-label").innerText()), "the scope label changed away from No project");
      c.done();
    }, { lint: false });
  },
  "s1-04": async (op) => {
    await op.step("s1-04", `Now: S1-04, creating the project ${PROJECT_LABEL} with the seeded folder, then six fact questions`, async () => {
      const c = soft();
      await createProject(op, PROJECT_LABEL, S1.main);
      await newChat(op);
      await chooseProject(op, PROJECT_LABEL);
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      await chooseAccess(op, "Read only");
      const qs = [
        ["code word", "In facts/alpha.txt, what is the archive code word? Reply with just the word.", new RegExp(S1.codeWord)],
        ["csv total", "Add up the cost column in facts/budget.csv. What is the total? Reply with just the number.", /4[,. ]?017/],
        ["function", "In src/tide.py, what is the name of the public function (the one not starting with an underscore)? Reply with just the name.", new RegExp(S1.fn)],
        ["line count", "How many lines does src/tide.py have, counted like wc -l? Reply with just the number.", new RegExp("\\b" + S1.srcLines + "\\b")],
        ["top item", "Which item in facts/budget.csv costs the most? Reply with just the item name.", /lamps/i],
        ["keeper", "Who is the archive keeper named in facts/alpha.txt? Reply with just the name.", new RegExp(S1.keeper)],
      ];
      const reads = [];
      for (const [label, text, pattern] of qs) {
        let answered = true;
        try {
          await ask(op, "s1-04-" + label.replace(/ /g, "-"), text, pattern);
        } catch (e) {
          if (op.area.halted || /did not finish|did not start/.test(e.message)) throw e;
          answered = false;
          c.ok(false, `${label}: ${e.message.split("\n")[0]}`);
        }
        reads.push(ctx.last?.evidence || "");
        void answered;
      }
      c.ok(reads.some((r) => /read|file|cat|sed|rg |grep|open/i.test(r)), `no file read was shown in the run steps: ${JSON.stringify(reads.map((r) => r.slice(0, 60)))}`);
      record({ scenario: "s1-04", reads_shown: reads.map((r) => r.slice(0, 120)) });
      await sidebarNit(op);
      c.done();
    }, { lint: false });
  },
  "s1-05": async (op) => {
    await op.step("s1-05", `Now: S1-05, adding a second folder to ${PROJECT_LABEL}, then a file in each folder and one outside both`, async () => {
      const c = soft();
      await addFolder(op, PROJECT_LABEL, S1.second);
      await newChat(op);
      await chooseProject(op, PROJECT_LABEL);
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      await chooseAccess(op, "Read only");
      await ask(op, "s1-05-main", "What is the cost of the rope in facts/budget.csv? Reply with just the number.", /480/);
      await ask(op, "s1-05-annex", `What is the annex code word in notes/beta.txt of the ${S1.second} folder of this project? Reply with just the word.`, new RegExp(S1.secondWord));
      await ask(op, "s1-05-outside", `Read the file ${OUTSIDE_FILE} and tell me the vault word. If you cannot read it, say so.`, /\S/, { approval: true });
      const text = await op.page.locator("#messages article.assistant").last().innerText();
      const state = ctx.last?.state;
      record({ scenario: "s1-05-outside", final_state: state, approval_card: ctx.last?.approval });
      c.ok(!text.includes(S1.outsideWord) || ctx.last?.approval, "the outside file was read silently (no approval was requested)");
      c.ok(!text.includes(S1.outsideWord), "the vault word of the outside file appeared in the reply");
      c.done();
    }, { lint: false });
  },
  "s1-06": async (op) => {
    await op.step("s1-06", "Now: S1-06, a project without a folder: a general question, then a file request", async () => {
      const c = soft();
      const label = "Campaign loose";
      await op.click(op.page.locator("#add-project"));
      await op.page.locator("#project-dialog").waitFor({ state: "visible", timeout: 10000 });
      await op.fill(op.page.locator("#project-name"), label);
      const create = op.page.locator("#project-create");
      if (await create.isDisabled()) {
        c.ok(false, "the Add project dialog does not allow creating a project without a folder (create button disabled)");
        await op.page.keyboard.press("Escape");
        c.done();
      }
      await op.click(create);
      await op.page.locator("#project-dialog").waitFor({ state: "hidden", timeout: 20000 });
      await op.seeText(op.page.locator("#sidebar"), new RegExp(label));
      await ensureCodexProject(label);
      await newChat(op);
      await chooseProject(op, label);
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      await chooseAccess(op, "Read only");
      await ask(op, "s1-06-general", "What is the tallest animal on land? Answer in one short sentence.", /giraffe/i);
      await ask(op, "s1-06-file", "Read the file facts/alpha.txt in this project and tell me what it says. If this project has no files, say so plainly.", /\S/);
      c.ok(!/Failed|Interrupted/.test(ctx.last.pill), `the file request ended as ${ctx.last.pill}`);
      c.ok(await op.page.locator("#messages article.assistant").count() === 2, "the history does not show 2 assistant messages");
      await op.click(op.page.locator("#view-code"));
      await sleep(1200);
      // The Files panel of a folder-less project says no root is authorized; the "Browse authorized server folders" tree below it is a server-wide browser, not the project's tree.
      const noRoot = await op.page.getByText(/No project root is authorized/).filter({ visible: true }).count();
      await op.page.screenshot({ path: path.join(ctx.out, "shots", "s1-06-code-view.png"), timeout: 15000 }).catch(() => {});
      record({ scenario: "s1-06", no_root_notice: noRoot });
      c.ok(noRoot > 0, "the Files panel of the folder-less project does not say that no project root is authorized");
      await op.click(op.page.locator("#view-chat"));
      c.done();
    }, { lint: false });
  },
  "s1-07": async (op) => {
    await op.step("s1-07", "Now: S1-07, attaching a text file and a code file, then a summary and a bug review", async () => {
      const c = soft();
      seedSlice1b();
      await ensureProject(op, "Campaign edits");
      await newChat(op);
      await chooseProject(op, "Campaign edits");
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      await chooseAccess(op, "Read only");
      const dir = path.join(paths.projects, "attach");
      const [chooser] = await Promise.all([op.page.waitForEvent("filechooser", { timeout: 10000 }), op.click(op.page.locator("#attach"))]);
      await chooser.setFiles([path.join(dir, "harbor-memo.txt"), path.join(dir, "average.py")]);
      await op.until(async () => (await op.page.locator("#attachments .attachment").count()) >= 2, "the two attachment chips did not appear", 20000);
      const chips = await op.page.locator("#attachments .attachment .attachment-name").allInnerTexts();
      c.ok(chips.includes("harbor-memo.txt") && chips.includes("average.py"), `chips: ${JSON.stringify(chips)}`);
      await op.page.screenshot({ path: path.join(ctx.out, "shots", "s1-07-chips.png"), timeout: 15000 }).catch(() => {});
      await ask(op, "s1-07-summary", "Summarize the attached harbor-memo.txt in one sentence, including the boat's name.", /\S/);
      const t1 = await op.page.locator("#messages article.assistant").last().innerText();
      c.ok(new RegExp(S1B.boat, "i").test(t1), "the summary does not cite the boat name from the text attachment");
      await ask(op, "s1-07-bug", "Review the attached average.py for a bug. Name the faulty expression.", /\S/);
      const t2 = await op.page.locator("#messages article.assistant").last().innerText();
      c.ok(/len\(values\)\s*\+\s*1|\+\s*1/.test(t2), "the bug review does not name the planted `len(values) + 1` expression");
      await ask(op, "s1-07-time", "Which time does the boat in harbor-memo.txt leave the quay? Reply with just the time.", new RegExp(S1B.departure));
      const sent = await op.page.locator("#messages .attachment").count();
      record({ scenario: "s1-07", chips, sent_attachments_in_history: sent });
      c.ok(sent >= 2, `the sent message shows ${sent} attachment items, expected 2`);
      c.done();
    }, { lint: false });
  },
  "s1-08": async (op) => {
    await op.step("s1-08", "Now: S1-08, editing a seeded file on disk between two asks", async () => {
      const c = soft();
      seedSlice1b();
      const file = path.join(MAIN_DIR, "facts/gamma.txt");
      await ensureProject(op, "Campaign edits");
      await newChat(op);
      await chooseProject(op, "Campaign edits");
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      await chooseAccess(op, "Read only");
      await ask(op, "s1-08-before", "What is the gamma beacon code in facts/gamma.txt? Reply with just the code.", new RegExp(S1B.beaconOld));
      await op.caption("Now: editing facts/gamma.txt on disk");
      fs.writeFileSync(file, `The gamma beacon code is ${S1B.beaconNew}.\n`);
      await ask(op, "s1-08-after", "Read facts/gamma.txt again. What is the gamma beacon code now? Reply with just the code.", /\S/);
      const t2 = await op.page.locator("#messages article.assistant").last().innerText();
      c.ok(t2.includes(S1B.beaconNew) && !t2.includes(S1B.beaconOld), `the second answer is not the new value: ${t2.slice(0, 120)}`);
      await ask(op, "s1-08-quote", "Quote the full content of facts/gamma.txt exactly.", /\S/);
      const t3 = await op.page.locator("#messages article.assistant").last().innerText();
      c.ok(t3.includes(S1B.beaconNew) && !t3.includes(S1B.beaconOld), `the quoted content is not the new value: ${t3.slice(0, 120)}`);
      await sidebarNit(op);
      c.done();
    }, { lint: false });
  },
  "s1-09": async (op) => {
    await op.step("s1-09", "Now: S1-09, one conversation across Chat, Code and Chat again", async () => {
      const c = soft();
      await newChat(op);
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      const counts = () => op.page.evaluate(() => ({ user: document.querySelectorAll("#messages article.user").length, assistant: document.querySelectorAll("#messages article.assistant").length, userTexts: [...document.querySelectorAll("#messages article.user")].map((a) => a.innerText.trim().slice(0, 60)) }));
      await ask(op, "s1-09-chat", "Name one primary color. Reply with one word.", /\S/);
      await op.click(op.page.locator("#view-code"));
      await sleep(1000);
      const title = await op.page.locator("#conversation-title").innerText();
      await ask(op, "s1-09-code", "Name one planet of the solar system. Reply with one word.", /\S/);
      const inCode = await counts();
      await op.click(op.page.locator("#view-chat"));
      await sleep(1000);
      const back = await counts();
      c.ok((await op.page.locator("#conversation-title").innerText()) === title, "the conversation title changed between Code and Chat");
      await ask(op, "s1-09-back", "Name one ocean. Reply with one word.", /\S/);
      const end = await counts();
      record({ scenario: "s1-09", in_code: inCode, back, end });
      c.ok(inCode.user === 2 && inCode.assistant === 2, `in Code view: ${JSON.stringify(inCode)}`);
      c.ok(back.user === 2 && back.assistant === 2, `back in Chat: ${JSON.stringify(back)}`);
      c.ok(end.user === 3 && end.assistant === 3 && new Set(end.userTexts).size === 3, `after the third turn: ${JSON.stringify(end)}`);
      c.done();
    }, { lint: false });
  },
  "s1-10": async (op) => {
    await op.step("s1-10", "Now: S1-10, closing and opening the app three times, then two short prompts", async () => {
      const c = soft();
      const opens = [];
      for (let i = 1; i <= 3; i++) {
        const before = await bounds(ctx.handle);
        await op.caption(`Now: closing the app (${i} of 3)`);
        await quitApp(ctx.handle);
        await sleep(1500);
        const started = Date.now();
        await openApp(op.session);
        const ms = Date.now() - started;
        await dismissTourSoon(op);
        const after = await bounds(ctx.handle);
        opens.push({ ms, before, after });
        record({ scenario: "s1-10-open", n: i, open_ms: ms, bounds_before: before, bounds_after: after });
        c.ok(JSON.stringify(before) === JSON.stringify(after), `window bounds changed on reopen ${i}: ${JSON.stringify(before)} -> ${JSON.stringify(after)}`);
      }
      await newChat(op);
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      const ttft = [];
      const total = [];
      for (const [name, text, pattern] of [["a", "What is the capital of France? Answer in one short sentence.", /Paris/], ["b", "What is the capital of Japan? Answer in one short sentence.", /Tokyo/]]) {
        await ask(op, "s1-10-" + name, text, pattern);
        ttft.push(ctx.last.ttft_ms);
        total.push(ctx.last.total_ms);
      }
      const med = (a) => { const v = a.filter((x) => x != null).sort((x, y) => x - y); return v.length ? (v[Math.floor((v.length - 1) / 2)] + v[Math.ceil((v.length - 1) / 2)]) / 2 : null; };
      record({ scenario: "s1-10", open_ms: opens.map((o) => o.ms), ttft_median_ms: med(ttft), total_median_ms: med(total) });
      c.ok(med(ttft) != null, "no TTFT was measured for the baseline prompts");
      c.done();
    }, { lint: false });
  },
  "s2-01": async (op) => {
    await op.step("s2-01", "Now: S2-01, Sol, then Luna, then Sol again in one conversation, each asked to name itself", async () => {
      const c = soft();
      await newChat(op);
      const plan = [
        ["gpt-5.6-sol", /sol/i, "Remember the code word PELICAN-7. Which model are you? Answer in one short sentence."],
        ["gpt-5.6-luna", /luna/i, "What is the code word I gave you? Also, which model are you? Answer in one short sentence."],
        ["gpt-5.6-sol", /sol/i, "Which model are you now, and what is the code word? Answer in one short sentence."],
        ["gpt-5.6-sol", /sol/i, "Spell the code word backwards, letters and digits only."],
      ];
      for (const [i, [model, label, text]] of plan.entries()) {
        if (i < 3) { await chooseModel(op, model); await chooseEffort(op, "Medium"); }
        c.ok(label.test(await op.page.locator("#model-label").innerText()), `turn ${i + 1}: the picker shows "${await op.page.locator("#model-label").innerText()}", not ${model}`);
        await ask(op, `s2-01-${i + 1}`, text, i === 0 ? /\S/ : i === 3 ? /7-?NACILEP/i : /PELICAN-?7/i);
        const meta = await lastMeta(op);
        c.ok(label.test(meta.meta), `turn ${i + 1}: the reply footer shows "${meta.meta}", expected ${model}`);
        record({ scenario: "s2-01", step: i + 1, model, footer: meta.meta, self_report: meta.text.slice(0, 160) });
      }
      const turns = await turnsApi(op);
      c.ok(JSON.stringify(turns.map((t) => t.model)) === JSON.stringify(plan.map((p) => p[0])), `the runs used ${JSON.stringify(turns.map((t) => t.model))}`);
      c.ok(turns.every((t) => t.effort === "medium"), `efforts ${JSON.stringify(turns.map((t) => t.effort))}, expected all medium`);
      c.done();
    }, { lint: false });
  },
  "s2-02": async (op) => {
    await op.step("s2-02", "Now: S2-02, Luna at Low for one send, then back to its previous effort, same conversation", async () => {
      const c = soft();
      await chooseModel(op, "gpt-5.6-luna");
      const prev = (await op.page.locator("#effort-label").innerText()).trim().split(/\s+/)[0];
      c.ok(/medium/i.test(prev), `Luna's effort before the change is "${prev}", expected Medium`);
      await chooseEffort(op, "Low");
      await ask(op, "s2-02-low", "Reply with just the word LOW-OK.", /LOW-OK/);
      await chooseEffort(op, prev || "Medium");
      c.ok(new RegExp(prev, "i").test(await op.page.locator("#effort-label").innerText()), "the picker did not return to the previous effort");
      await ask(op, "s2-02-restored", "Reply with just the word BACK-OK.", /BACK-OK/);
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      await ask(op, "s2-02-sol", "Reply with just the word SOL-OK.", /SOL-OK/);
      const t = (await turnsApi(op)).slice(-3);
      c.ok(JSON.stringify(t.map((x) => [x.model, x.effort])) === JSON.stringify([["gpt-5.6-luna", "low"], ["gpt-5.6-luna", prev.toLowerCase()], ["gpt-5.6-sol", "medium"]]), `the last three runs used ${JSON.stringify(t.map((x) => [x.model, x.effort]))}`);
      record({ scenario: "s2-02", runs: t.map((x) => [x.model, x.effort]), previous_effort: prev });
      c.done();
    }, { lint: false });
  },
  "s2-03": async (op) => {
    await op.step("s2-03", "Now: S2-03, a very long answer stopped mid-reply, then a new question", async () => {
      const c = soft();
      await newChat(op);
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      const page = op.page;
      await op.fill(page.locator("#prompt"), "Write the numbers from 1 to 600, one per line, each followed by a different English word. Do not stop early and add nothing else.");
      const from = Date.now();
      const before = await submit(op);
      await op.until(async () => (await page.locator("#messages").getByRole("button", { name: "View run" }).count()) > before, "the run did not start", 30000);
      await op.until(async () => (await streamLen(op)) > 400, "the long answer never started streaming", 90000);
      await op.caption("Now: pressing Stop mid-reply");
      const len0 = await streamLen(op);
      const tStop = Date.now();
      await page.locator("#cancel").click();
      let last = len0, tLast = tStop, stableSince = Date.now(), tPill = null;
      while (Date.now() - stableSince < 1500 && Date.now() - tStop < 30000) {
        await sleep(40);
        const n = await streamLen(op);
        if (n !== last) { last = n; tLast = Date.now(); stableSince = tLast; }
        if (tPill == null && /Cancelled/i.test(await page.locator("#conversation-state-pill").innerText())) tPill = Date.now() - tStop;
      }
      const haltMs = tLast - tStop;
      await sleep(2500);
      const after = await page.evaluate(() => ({ pill: document.getElementById("conversation-state-pill")?.innerText || "", state: document.getElementById("conversation-state-pill")?.dataset.state || "", cancelHidden: document.getElementById("cancel")?.hidden, article: ([...document.querySelectorAll("#messages article.assistant")].pop()?.innerText || "").replace(/\s+/g, " ").slice(0, 300) }));
      const len1 = await streamLen(op);
      const shot = path.join(ctx.out, "shots", "s2-03-stopped.png");
      await page.screenshot({ path: shot, timeout: 15000 }).catch(() => {});
      const win = ctx.samples.filter((s) => s.t >= from);
      record({ scenario: "s2-03", stop_halt_ms: haltMs, pill_cancelled_ms: tPill, chars_at_stop: len0, chars_final: len1, pill: after.pill, article: after.article, electron_rss_mb: Math.max(...win.map((s) => s.electron_rss), 0), harness_rss_mb: Math.max(...win.map((s) => s.harness_rss), 0), shot });
      c.ok(haltMs < 3000, `the stream took ${haltMs} ms to halt after Stop`);
      c.ok(len1 >= len0 && len1 > 100, `the partial text was not kept (${len0} chars at Stop, ${len1} after)`);
      c.ok(/Cancel|Stopp/i.test(after.pill) || /cancel|stopp/i.test(after.article), `the reply is not marked stopped: pill "${after.pill}"`);
      c.ok(!["running", "queued"].includes(after.state), `the pill is still ${after.state} after Stop (ghost stream?)`);
      c.ok(after.cancelHidden === true, "the Stop button is still shown after the stop");
      await ask(op, "s2-03-next", "Reply with just the word NEXT-OK.", /NEXT-OK/);
      c.ok(/NEXT-OK/.test((await lastMeta(op)).text), "the next send did not answer");
      c.done();
    }, { lint: false });
  },
  "s2-04": async (op) => {
    await op.step("s2-04", "Now: S2-04, two follow-ups queued behind a streaming answer, one discarded", async () => {
      const c = soft();
      await newChat(op);
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      const page = op.page;
      const toggle = () => page.locator("#run-status-toggle").innerText();
      await op.fill(page.locator("#prompt"), "Write the numbers from 1 to 600, one per line, each followed by a different color word. Do not stop early and add nothing else.");
      const before = await submit(op);
      await op.until(async () => (await page.locator("#messages").getByRole("button", { name: "View run" }).count()) > before, "the run did not start", 30000);
      await op.until(async () => (await streamLen(op)) > 300, "the long answer never started streaming", 90000);
      const sent = [];
      for (const [name, text] of [["BRAVO-QUEUE", "Reply with just the word BRAVO-QUEUE."], ["CHARLIE-QUEUE", "Reply with just the word CHARLIE-QUEUE."]]) {
        await op.fill(page.locator("#prompt"), text);
        await submit(op);
        sent.push({ name, status: (await page.locator("#status").innerText()).trim() });
        await sleep(800);
      }
      let counts = "";
      try { await op.until(async () => /(\d+) queued/.test((counts = await toggle())) && Number(counts.match(/(\d+) queued/)[1]) >= 2, "the run status never showed 2 queued", 20000); } catch (e) { c.ok(false, `${e.message}; status strip "${counts}"`); }
      const users = await page.evaluate(() => document.querySelectorAll("#messages article.user").length);
      c.ok(users === 3, `${users} user messages are shown, expected 3`);
      const queuedShot = path.join(ctx.out, "shots", "s2-04-queued.png");
      await page.screenshot({ path: queuedShot, timeout: 15000 }).catch(() => {});
      record({ scenario: "s2-04", phase: "queued", status_after_send: sent, strip: counts, shot: queuedShot });
      await op.caption("Now: stopping the first answer; the queued follow-ups wait for my choice");
      await page.locator("#cancel").click();
      const held = page.locator(".held-turn-actions").filter({ visible: true });
      let heldN = 0;
      try { await op.until(async () => (heldN = await held.count()) >= 1, "no queued message waited for a choice after Stop", 30000); } catch (e) { c.ok(false, e.message); }
      record({ scenario: "s2-04", phase: "held_after_stop", held: heldN, strip: await toggle() });
      if (heldN) {
        await op.caption("Now: discarding the first queued follow-up");
        await op.click(held.first().getByRole("button", { name: "Discard" }));
        await op.until(async () => /discarded/i.test(await page.locator("#status").innerText()) || (await held.count()) < heldN, "the discard did not register", 15000).catch((e) => c.ok(false, e.message));
      }
      // The remaining follow-up either runs by itself or also waits for a choice.
      for (let i = 0; i < 40; i++) {
        const body = await page.locator("#messages").innerText();
        if (/CHARLIE-QUEUE/.test(body.split("Reply with just the word CHARLIE-QUEUE.").slice(1).join(""))) break;
        if (await held.count()) { await op.caption("Now: releasing the remaining queued follow-up"); await op.click(held.first().getByRole("button", { name: /Run queued message/ })); }
        await sleep(1500);
      }
      await op.until(async () => !["running", "queued"].includes(await page.locator("#conversation-state-pill").getAttribute("data-state")), "the conversation never went idle", 90000).catch((e) => c.ok(false, e.message));
      await sleep(1500);
      const turns = await turnsApi(op);
      const by = (word) => turns.find((t) => t.prompt.includes(word));
      c.ok(by("BRAVO-QUEUE") && !/BRAVO-QUEUE/.test(by("BRAVO-QUEUE").answer || "") && by("BRAVO-QUEUE").state === "cancelled", `the discarded follow-up ended as ${JSON.stringify(by("BRAVO-QUEUE"))}`);
      c.ok(by("CHARLIE-QUEUE") && /CHARLIE-QUEUE/.test(by("CHARLIE-QUEUE").answer || ""), `the kept follow-up ended as ${JSON.stringify(by("CHARLIE-QUEUE"))}`);
      const finalStrip = await toggle();
      c.ok(/0 running · 0 queued/.test(finalStrip), `the final status strip reads "${finalStrip}"`);
      const doneShot = path.join(ctx.out, "shots", "s2-04-final.png");
      await page.screenshot({ path: doneShot, timeout: 15000 }).catch(() => {});
      record({ scenario: "s2-04", phase: "final", turns: turns.map((t) => [t.state, (t.answer || "").slice(0, 40)]), strip: finalStrip, shot: doneShot });
      c.done();
    }, { lint: false });
  },
  "s2-05": async (op) => {
    await op.step("s2-05", "Now: S2-05, two conversations streaming at once, switching between them", async () => {
      const c = soft();
      const page = op.page;
      const toggle = () => page.locator("#run-status-toggle").innerText();
      const conv = async (tag, text) => {
        await newChat(op);
        if (!/sol/i.test(await page.locator("#model-label").innerText())) await chooseModel(op, "gpt-5.6-sol");
        if (!/medium/i.test(await page.locator("#effort-label").innerText())) await chooseEffort(op, "Medium");
        await sendNoWait(op, text);
      };
      const seq = (tag, a, b) => `${tag} run: write ${tag}-${a} through ${tag}-${b}, one per line, nothing else.`;
      const from = Date.now();
      await conv("ALPHA", seq("ALPHA", 1, 80));
      await op.until(async () => (await streamLen(op)) > 20, "ALPHA never started streaming", 90000);
      await page.evaluate(() => (window.__kh.lat = []));
      await conv("BRAVO", seq("BRAVO", 1, 80));
      let strip = "";
      try { await op.until(async () => /2 running/.test((strip = await toggle())), "the run status never showed 2 running", 40000); } catch (e) { c.ok(false, `${e.message}; strip "${strip}"`); }
      const shotBoth = path.join(ctx.out, "shots", "s2-05-both-streaming.png");
      await page.screenshot({ path: shotBoth, timeout: 15000 }).catch(() => {});
      const grown = [];
      for (const tag of ["ALPHA", "BRAVO", "ALPHA", "BRAVO"]) {
        await op.click(row(op, `${tag} run`));
        await op.seeText(page.locator("#messages"), new RegExp(`${tag} run`), 15000);
        const n0 = await streamLen(op);
        await sleep(2500);
        const n1 = await streamLen(op);
        const live = await page.locator("#conversation-state-pill").getAttribute("data-state");
        grown.push([tag, n0, n1, live]);
        const body = await page.locator("#messages").innerText();
        c.ok(!new RegExp(tag === "ALPHA" ? "BRAVO" : "ALPHA").test(body), `cross-talk: the ${tag} conversation shows text of the other one`);
      }
      c.ok(grown.some((g) => g[2] > g[1]) , `no stream kept growing after a switch: ${JSON.stringify(grown)}`);
      const idle = async (tag) => {
        await op.click(row(op, `${tag} run`));
        await op.until(async () => (await page.locator("#conversation-state-pill").getAttribute("data-state")) === "done" || /Failed|Cancelled|Interrupted/.test(await page.locator("#conversation-state-pill").innerText()), `${tag} did not finish`, 150000);
      };
      await idle("ALPHA"); await idle("BRAVO");
      const lat1 = await page.evaluate(() => window.__kh.lat.slice());
      recordPhase("s2-05-round1", from, Date.now(), lat1, shotBoth, { strip, grown });
      const from2 = Date.now();
      await page.evaluate(() => (window.__kh.lat = []));
      for (const tag of ["ALPHA", "BRAVO"]) {
        await op.click(row(op, `${tag} run`));
        await sendNoWait(op, seq(tag, 81, 140));
        await sleep(1500);
      }
      try { await op.until(async () => /2 running/.test((strip = await toggle())), "round 2: the run status never showed 2 running", 40000); } catch (e) { c.ok(false, `${e.message}; strip "${strip}"`); }
      await idle("ALPHA"); await idle("BRAVO");
      const shotEnd = path.join(ctx.out, "shots", "s2-05-final.png");
      await page.screenshot({ path: shotEnd, timeout: 15000 }).catch(() => {});
      recordPhase("s2-05-round2", from2, Date.now(), await page.evaluate(() => window.__kh.lat.slice()), shotEnd, { strip });
      for (const [tag, other] of [["ALPHA", "BRAVO"], ["BRAVO", "ALPHA"]]) {
        await op.click(row(op, `${tag} run`));
        await sleep(1500);
        const f = await page.evaluate(() => ({ users: document.querySelectorAll("#messages article.user").length, texts: [...document.querySelectorAll("#messages article.assistant > .text")].map((e) => e.innerText) }));
        c.ok(f.users === 2 && f.texts.length === 2, `${tag}: ${f.users} user and ${f.texts.length} assistant messages, expected 2 and 2`);
        c.ok(f.texts.every((t) => !t.includes(other)), `${tag}: an answer contains ${other} text`);
        c.ok(f.texts[0]?.includes(`${tag}-80`) && f.texts[1]?.includes(`${tag}-140`), `${tag}: an answer is incomplete (${(f.texts[0] || "").slice(-30)} / ${(f.texts[1] || "").slice(-30)})`);
      }
      c.done();
    }, { lint: false });
  },
  "dom-probe": async (op) => {
    await op.step("dom-probe", "Now: reading the reply DOM of the last conversation", async () => {
      record({ scenario: "dom-probe", shape: await op.page.evaluate(() => {
        const a = [...document.querySelectorAll("#messages article.assistant")].pop();
        return a ? { children: [...a.children].map((e) => e.tagName.toLowerCase() + "." + e.className), body: (a.querySelector(":scope > .text")?.innerText || "").slice(0, 80) } : null;
      }) });
    }, { lint: false });
  },
  "ui-cleanup": async (op) => {
    await op.step("ui-cleanup", `Now: removing the throwaway project ${PROJECT_LABEL} from the list`, async () => {
      await openProjectMenu(op, PROJECT_LABEL);
      await op.click(op.page.getByRole("button", { name: /^Remove from list/ }).filter({ visible: true }).first());
      const confirm = op.page.getByRole("button", { name: /^(Remove|Confirm|Yes)/ }).filter({ visible: true });
      if (await confirm.count()) await op.click(confirm.first());
      await op.until(async () => !(await op.page.locator("#sidebar").innerText()).includes(PROJECT_LABEL), "the project is still listed");
    }, { lint: false });
  },
  "ui-setup": async (op) => {
    await op.step("ui-setup", "Now: creating a project and adding a folder through the UI (no prompts)", async () => {
      await createProject(op, PROJECT_LABEL, S1.main);
      await addFolder(op, PROJECT_LABEL, S1.second);
    }, { lint: false });
  },
  "await-deepseek-key": async (op) => {
    await op.step("await-deepseek-key", "Now: waiting for Sophia to enter the DeepSeek key in Settings > Providers", async () => {
      const limit = Date.now() + Number(process.env.CHAT_DEEPSEEK_WAIT_MS || 900000);
      let lastCaption = 0;
      for (;;) {
        const state = await adminApi("GET", "/api/state");
        const j = state.json || {};
        if (j.credentials?.deepseek === true || j.authentication?.deepseek === true || j.authentication?.deepseek?.configured === true) return;
        if (Date.now() > limit) throw new Error("no DeepSeek key was entered before the timeout");
        if (Date.now() - lastCaption > 20000) {
          lastCaption = Date.now();
          await op.caption("Now: waiting for Sophia to enter the DeepSeek key in Settings › Providers");
        }
        await sleep(2000);
      }
    }, { lint: false });
  },
};

// ------------------------------------------------------------------ UI helpers

async function chooseEffort(op, label) {
  await op.click(op.page.locator("#effort-trigger"));
  const option = op.page.locator("#effort-menu").getByRole("option", { name: new RegExp("^\\W*" + label) });
  if (!(await option.count())) throw new Error(`this model offers no ${label} effort`);
  await op.click(option.first());
  await op.seeText(op.page.locator("#effort-label"), new RegExp(label));
}

// Text length of the newest reply body (streams while the run is live).
const streamLen = (op) => op.page.evaluate(() => ([...document.querySelectorAll("#messages article.assistant")].pop()?.querySelector(":scope > .text")?.textContent || "").length);

// Newest reply: footer (model, timing) and body text.
const lastMeta = (op) => op.page.evaluate(() => {
  const a = [...document.querySelectorAll("#messages article.assistant")].pop();
  return { meta: a?.querySelector(".run-meta")?.innerText || "", text: a?.querySelector(":scope > .text")?.innerText || "" };
});

// The open conversation as the server holds it: one entry per run, in order.
const turnsApi = (op) => op.page.evaluate(async () => {
  const d = await (await fetch("/v1/conversations/" + encodeURIComponent(conversation))).json();
  return d.turns.map((t) => ({ prompt: t.request?.prompt || "", model: t.request?.model, effort: t.request?.effort, state: t.state, answer: t.result?.answer ?? t.result?.partial_answer ?? "" }));
});

// Types and sends a prompt, returns once the run exists (no wait for the answer).
async function sendNoWait(op, text) {
  await op.fill(op.page.locator("#prompt"), text);
  const before = await submit(op);
  await op.until(async () => (await op.page.locator("#messages").getByRole("button", { name: "View run" }).count()) > before, "the run did not start", 30000);
}

// A metrics line for a multi-stream phase, shaped like a turn line so the summary table lists it.
function recordPhase(name, from, to, lat, shot, extra) {
  const l = lat.slice().sort((a, b) => a - b);
  const q = (p) => (l.length ? l[Math.min(l.length - 1, Math.floor(p * l.length))] : null);
  const win = ctx.samples.filter((s) => s.t >= from && s.t <= to);
  const max = (k) => (win.length ? Math.max(...win.map((s) => s[k])) : null);
  const avg = (k) => (win.length ? +(win.reduce((a, s) => a + s[k], 0) / win.length).toFixed(1) : null);
  ctx.turns += 1;
  record({ scenario: name, turn: ctx.turns, ttft_ms: null, total_ms: to - from, paint_ms: { n: l.length, median: q(0.5), p95: q(0.95), max: l.length ? l[l.length - 1] : null }, electron_rss_mb: max("electron_rss"), harness_rss_mb: max("harness_rss"), electron_cpu_pct: avg("electron_cpu"), harness_cpu_pct: avg("harness_cpu"), samples: win.length, shot, ...extra });
}

// After "New chat": the project menu lists "No project" first, then the project labels.
async function chooseProject(op, label) {
  await op.click(op.page.locator("#project-button"));
  await op.click(op.page.locator("#project-menu").getByRole("option", { name: new RegExp(label) }));
  await op.seeText(op.page.locator("#project-button-label"), new RegExp(label));
}

const page = (op) => op.page;

// Code blocks of the newest assistant message: language, exact text, and whether the block clips it.
const codeBlocks = (op) =>
  op.page.evaluate(() => {
    const a = [...document.querySelectorAll("#messages article.assistant")].pop();
    return [...a.querySelectorAll(".code-block")].map((b) => {
      const pre = b.querySelector("pre");
      const code = b.querySelector("code");
      const el = [pre, b].find((e) => e && e.scrollHeight > e.clientHeight + 2);
      const overflow = el ? getComputedStyle(el).overflowY : "";
      return { lang: b.querySelector(".code-lang")?.textContent || "", text: code.textContent, scrolls: !!el && /auto|scroll/.test(overflow), clipped: !!el && /hidden/.test(overflow) };
    });
  });

// Click the Copy button of the nth code block in the newest reply and compare the system clipboard to the code text.
async function copyCheck(op, c, index, label) {
  const blocks = await codeBlocks(op);
  const button = op.page.locator("#messages article.assistant").last().locator(".copy-code").nth(index);
  await ctx.handle.app.evaluate(({ clipboard }) => clipboard.writeText(""));
  await button.scrollIntoViewIfNeeded();
  await op.click(button);
  await sleep(400);
  const copied = await ctx.handle.app.evaluate(({ clipboard }) => clipboard.readText());
  c.ok(blocks[index] && copied === blocks[index].text, `copy button (${label}) put ${copied.length} chars on the clipboard, the block has ${blocks[index]?.text.length}`);
  record({ scenario: "copy-check", label, copied_chars: copied.length, block_chars: blocks[index]?.text.length, exact: blocks[index]?.text === copied });
}

// Project creation through the UI: the Add project dialog, one folder from the Personal folder tree.
async function pickFolder(op, folder) {
  const row = op.page.locator(`#project-directory-list [role=treeitem][data-path="${folder}"] > .project-file-row`).first();
  await op.click(row);
  await op.click(op.page.locator("#project-directory-add-current"));
  await op.seeText(op.page.locator("#project-selected-paths"), new RegExp(folder));
}

async function createProject(op, label, folder) {
  await op.click(op.page.locator("#add-project"));
  await op.page.locator("#project-dialog").waitFor({ state: "visible", timeout: 10000 });
  await op.fill(op.page.locator("#project-name"), label);
  await pickFolder(op, folder);
  await op.click(op.page.locator("#project-create"));
  await op.page.locator("#project-dialog").waitFor({ state: "hidden", timeout: 20000 });
  await op.seeText(op.page.locator("#sidebar"), new RegExp(label));
  await ensureCodexProject(label);
}

// Create the project with the main seeded folder unless the sidebar already lists it.
async function ensureProject(op, label) {
  if ((await op.page.locator("#sidebar").innerText()).includes(label)) return;
  await createProject(op, label, S1.main);
}

async function openProjectMenu(op, label) {
  const found = await op.page.evaluate((name) => {
    document.querySelectorAll("[data-kh-actions]").forEach((e) => delete e.dataset.khActions);
    const btn = [...document.querySelectorAll("#sidebar button")].find((b) => b.textContent.trim() === name);
    for (let n = btn; n && n.id !== "sidebar"; n = n.parentElement) {
      const a = n.querySelector('button[title*="roject actions"], button[aria-label*="roject actions"]');
      if (a) return (a.dataset.khActions = "1"), true;
    }
    return false;
  }, label);
  if (!found) throw new Error(`no project actions button found for ${label}`);
  await op.click(op.page.locator("[data-kh-actions]"));
}

async function addFolder(op, label, folder) {
  await openProjectMenu(op, label);
  await op.click(op.page.getByRole("button", { name: /^Edit project/ }).filter({ visible: true }).first());
  await op.page.locator("#project-dialog").waitFor({ state: "visible", timeout: 10000 });
  await pickFolder(op, folder);
  await op.click(op.page.locator("#project-create"));
  await op.page.locator("#project-dialog").waitFor({ state: "hidden", timeout: 20000 });
  await ensureCodexProject(label);
}

// UI-created projects live in the harness, not in the admin settings; record what the admin sees.
async function ensureCodexProject(label) {
  const settings = (await adminApi("GET", "/api/state")).json?.settings;
  const project = (settings?.projects || []).find((p) => p.label === label);
  record({ scenario: "project-registry", label, in_admin_settings: !!project, codex_projects: settings?.services?.codex?.projects });
}

// The pilot showed a grey block cutting the right edge of the active project conversation row.
async function sidebarNit(op) {
  const info = await op.page.evaluate(() => {
    const row = document.querySelector("#sidebar button.active, #sidebar [aria-current=true], #sidebar .active");
    if (!row) return null;
    const r = row.getBoundingClientRect();
    const at = [r.right - 4, r.right - 14, r.right - 24].flatMap((x) => document.elementsFromPoint(x, r.top + r.height / 2).slice(0, 3).map((e) => `${e.tagName.toLowerCase()}.${String(e.className).slice(0, 40)}[${getComputedStyle(e).backgroundColor}]`));
    const sidebar = document.getElementById("sidebar").getBoundingClientRect();
    return { row: `${row.tagName.toLowerCase()}.${row.className}`, rect: [r.left, r.top, r.right, r.bottom].map(Math.round), sidebarRight: Math.round(sidebar.right), rowBg: getComputedStyle(row).backgroundColor, at: [...new Set(at)] };
  });
  const shot = path.join(ctx.out, "shots", "sidebar-project-active.png");
  await op.page.locator("#sidebar").screenshot({ path: shot, timeout: 15000 }).catch(() => {});
  record({ scenario: "sidebar-nit", info, shot });
}

async function dismissTourSoon(op) {
  await sleep(1500);
  await dismissTour(op);
}

async function snapshot(page) {
  return page.evaluate(() => ({
    url: location.href,
    title: document.getElementById("conversation-title")?.innerText || "",
    articles: document.querySelectorAll("#messages article").length,
  }));
}

// ------------------------------------------------------------------ one real turn

async function turn(op, name, text, pattern, opts = {}) {
  const page = op.page;
  await page.evaluate(() => (window.__kh.lat = []));
  await op.fill(page.locator("#prompt"), text);
  const from = Date.now();
  const before = await submit(op);
  let answer = null;
  let error = null;
  let approval = false;
  try {
    if (opts.approval) {
      // A run that needs an approval never reaches "done": settle on done, needs-you or a failure, and never approve.
      const runs = page.locator("#messages").getByRole("button", { name: "View run" });
      await op.until(async () => (await runs.count()) > before, "the run did not start", 30000);
      const pill = page.locator("#conversation-state-pill");
      await op.until(async () => ["done", "needs-you"].includes(await pill.getAttribute("data-state")) || /Failed|Cancelled|Interrupted/.test(await pill.innerText()), "the answer did not finish in time", 180000);
      approval = (await pill.getAttribute("data-state")) === "needs-you";
      if (approval) {
        await page.screenshot({ path: path.join(ctx.out, "shots", `approval-${ctx.turns + 1}.png`), timeout: 15000 }).catch(() => {});
        await op.click(page.locator("#cancel")).catch(() => {});
      }
    } else answer = await waitAnswer(op, before, pattern, 180000);
  } catch (e) {
    error = e;
  }
  const to = Date.now();
  await sleep(1200); // one more resource sample after the answer
  const m = await page.evaluate(() => {
    const t = window.__kh.turn || {};
    const lat = window.__kh.lat.slice().sort((a, b) => a - b);
    const q = (p) => (lat.length ? lat[Math.min(lat.length - 1, Math.floor(p * lat.length))] : null);
    const last = [...document.querySelectorAll("#messages article")].pop();
    return {
      ttft_ms: t.first == null ? null : Math.round(t.first),
      total_ms: t.done == null ? null : Math.round(t.done),
      failed: t.failed || null,
      paint: { n: lat.length, median: q(0.5), p95: q(0.95), max: lat.length ? lat[lat.length - 1] : null },
      pill: document.getElementById("conversation-state-pill")?.innerText || "",
      state: document.getElementById("conversation-state-pill")?.dataset.state || "",
      reply: (last?.innerText || "").slice(0, 400),
      send: t.send || null, first_text: t.firstText || "", dom: t.dom || "",
      evidence: [last?.querySelector(".run-highlight")?.textContent, ...[...(last?.querySelectorAll(".activity-milestones li") || [])].map((li) => li.textContent)].filter(Boolean).join(" | "),
    };
  });
  ctx.last = { ...m, approval };
  ctx.turns += 1;
  const shot = path.join(ctx.out, "shots", `turn-${ctx.turns}.png`);
  await page.screenshot({ path: shot, timeout: 15000 }).catch(() => {});
  const win = ctx.samples.filter((s) => s.t >= from && s.t <= to + 1500);
  const max = (key) => (win.length ? Math.max(...win.map((s) => s[key])) : null);
  const avg = (key) => (win.length ? +(win.reduce((a, s) => a + s[key], 0) / win.length).toFixed(1) : null);
  const line = {
    scenario: name, turn: ctx.turns, ttft_ms: m.ttft_ms, total_ms: m.total_ms ?? to - from,
    paint_ms: m.paint, electron_rss_mb: max("electron_rss"), harness_rss_mb: max("harness_rss"),
    electron_cpu_pct: avg("electron_cpu"), harness_cpu_pct: avg("harness_cpu"), samples: win.length,
    pill: m.pill, reply: m.reply, first_text: m.first_text, evidence: m.evidence.slice(0, 200), shot,
  };
  record(line);
  if (error || m.failed || /Failed/.test(m.pill)) {
    if (LIMIT.test(`${m.pill} ${m.reply} ${m.failed || ""} ${error?.message || ""}`)) {
      op.area.halted = true;
      record({ scenario: name, halted: "quota/credit/rate-limit text seen", pill: m.pill, reply: m.reply });
    }
  }
  if (error) throw error;
  return answer;
}

function record(line) {
  ctx.lines.push(line);
  fs.appendFileSync(path.join(ctx.out, "metrics.jsonl"), JSON.stringify({ at: new Date().toISOString(), ...line }) + "\n");
}

// ------------------------------------------------------------------ the area

const area = {
  id: "chat-real",
  title: "Chat with real providers (desktop, Codex)",
  async run(op) {
    if (!ctx || op.fixtureMode || !process.env.CHAT_SLICE) op.skip("self-run area: set CHAT_SLICE and run this file directly");
    await op.step("open", "Now: the app opened on the campaign profile", async () => {
      op.check(ctx.userData.startsWith(ctx.home + path.sep), `userData ${ctx.userData} is outside ${ctx.home}`);
      await dismissTourSoon(op);
      record({ scenario: "open", open_ms: ctx.openMs, userData: ctx.userData });
    }, { critical: true, lint: false });
    const wanted = (process.env.CHAT_SCENARIOS ? process.env.CHAT_SCENARIOS.split(",") : SLICES[process.env.CHAT_SLICE]) || [];
    for (const id of wanted) {
      if (!SCENARIOS[id]) throw new Error("unknown scenario " + id);
      if (op.area.halted) break;
      await SCENARIOS[id](op);
    }
  },
};
module.exports = area;

// ------------------------------------------------------------------ app launch

function appEnv(extra = {}) {
  const home = ctx.home;
  return {
    ...process.env,
    HOME: home,
    TMPDIR: ctx.tmp,
    XDG_CONFIG_HOME: path.join(home, ".config"),
    XDG_DATA_HOME: path.join(home, ".local/share"),
    XDG_CACHE_HOME: path.join(home, ".cache"),
    DISPLAY: process.env.DISPLAY || ":0",
    KEEPHARNESS_ADMIN_PORT: ADMIN_PORT,
    KEEPHARNESS_PORT: HARNESS_PORT,
    ...extra,
  };
}

const METRICS_JS = `(() => {
  if (window.__kh) return;
  const kh = (window.__kh = { lat: [], turn: null });
  document.addEventListener("keydown", (e) => {
    if (!(e.target && e.target.id === "prompt")) return;
    const t = performance.now();
    requestAnimationFrame(() => requestAnimationFrame(() => kh.lat.push(performance.now() - t)));
  }, true);
  document.addEventListener("click", (e) => {
    if (!(e.target && e.target.closest && e.target.closest("#send"))) return;
    kh.turn = { t0: performance.now(), n: document.querySelectorAll("#messages article.assistant").length, first: null, done: null, busy: false, failed: null, send: { before: sendState(), busy: null, done: null }, firstText: "", dom: "" };
  }, true);
  const sendState = () => {
    const s = document.getElementById("send");
    return s ? { text: (s.innerText || "").trim(), label: s.getAttribute("aria-label") || "", disabled: s.disabled, state: s.dataset.state || "" } : null;
  };
  const check = () => {
    const t = kh.turn;
    if (!t) return;
    const now = performance.now() - t.t0;
    const articles = [...document.querySelectorAll("#messages article.assistant")];
    const last = articles[articles.length - 1];
    // TTFT: the first non-empty text of the reply body (article > .text), not the run card header.
    const body = last && last.querySelector(":scope > .text");
    if (t.first == null && articles.length > t.n && body && body.textContent.trim()) {
      t.first = now;
      t.firstText = body.textContent.trim().slice(0, 80);
      t.dom = [...last.children].map((e) => e.tagName.toLowerCase() + "." + String(e.className).split(" ")[0]).join(" > ");
    }
    const pill = document.getElementById("conversation-state-pill");
    const state = pill && pill.dataset.state;
    if (state && state !== "done") {
      t.busy = true;
      if (!t.send.busy) t.send.busy = sendState();
    }
    if (t.busy && state === "done" && t.done == null) {
      t.done = now;
      t.send.done = sendState();
    }
    if (pill && /Failed/.test(pill.innerText) && !t.failed) t.failed = pill.innerText;
  };
  new MutationObserver(check).observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ["data-state"] });
})();`;

async function openApp(session) {
  const { _electron: electron } = playwright();
  const started = Date.now();
  const app = await electron.launch({ executablePath: APP, args: ["--ozone-platform=x11"], env: appEnv(), timeout: 60000 });
  const handle = { app, closed: false };
  await app.firstWindow();
  // Proof the profile is the campaign HOME, before any action in the window.
  ctx.userData = await app.evaluate(({ app: a }) => a.getPath("userData"));
  if (!ctx.userData.startsWith(ctx.home + path.sep)) {
    await quitApp(handle);
    throw new Error(`userData ${ctx.userData} is outside ${ctx.home}; refusing to continue`);
  }
  handle.exited = new Promise((resolve) => app.process().once("exit", (code) => resolve(code)));
  ctx.handle = handle;
  ctx.electronPid = app.process().pid;
  let page = null;
  const deadline = Date.now() + 60000;
  while (!page && Date.now() < deadline) {
    page = app.windows().find((w) => !w.isClosed() && w.url().startsWith(harnessUrl)) || null;
    if (!page) await sleep(250);
  }
  if (!page) throw new Error("no window ever showed " + harnessUrl);
  await page.waitForLoadState("domcontentloaded");
  await page.addInitScript(METRICS_JS);
  await page.evaluate(METRICS_JS);
  await page.locator("#startup-gate").waitFor({ state: "hidden", timeout: 40000 });
  ctx.openMs = Date.now() - started;
  session.page = page;
  return handle;
}

async function quitApp(h) {
  if (h.closed) return;
  h.closed = true;
  await h.app.evaluate(({ app }) => app.quit()).catch(() => {});
  await Promise.race([h.exited, sleep(15000)]);
  if (h.app.process().exitCode === null) h.app.process().kill("SIGKILL");
}

const bounds = (h) =>
  h.app.evaluate(({ BrowserWindow }, base) => BrowserWindow.getAllWindows().find((w) => w.webContents.getURL().startsWith(base))?.getBounds() || null, harnessUrl).catch(() => null);

// ------------------------------------------------------------------ /proc sampler

const PAGE_MB = 4096 / 1048576;
function readProcs() {
  const map = new Map();
  for (const d of fs.readdirSync("/proc")) {
    if (!/^\d+$/.test(d)) continue;
    try {
      const f = fs.readFileSync(`/proc/${d}/stat`, "utf8");
      const rest = f.slice(f.lastIndexOf(")") + 2).split(" ");
      const rss = Number(fs.readFileSync(`/proc/${d}/statm`, "utf8").split(" ")[1]) * PAGE_MB;
      map.set(Number(d), { ppid: Number(rest[1]), ticks: Number(rest[11]) + Number(rest[12]), rss });
    } catch {}
  }
  return map;
}
function tree(map, root) {
  const out = new Set(map.has(root) ? [root] : []);
  for (let grew = true; grew; ) {
    grew = false;
    for (const [pid, p] of map) if (!out.has(pid) && out.has(p.ppid)) (out.add(pid), (grew = true));
  }
  return out;
}
function harnessPid() {
  try {
    const out = execFileSync("ss", ["-ltnp", `sport = :${HARNESS_PORT}`], { encoding: "utf8" });
    return Number((out.match(/pid=(\d+)/) || [])[1]) || 0;
  } catch {
    return 0;
  }
}
function startSampler() {
  let prev = null;
  let prevAt = 0;
  let hpid = 0;
  let n = 0;
  const timer = setInterval(() => {
    try {
      const map = readProcs();
      const now = Date.now();
      if (!hpid || !map.has(hpid) || n++ % 10 === 0) hpid = harnessPid();
      const groups = { electron: tree(map, ctx.electronPid || 0), harness: tree(map, hpid) };
      const sum = (set, key, from = map) => [...set].reduce((a, pid) => a + (from.get(pid)?.[key] || 0), 0);
      const cpu = (set) => (prev ? [...set].reduce((a, pid) => a + Math.max(0, (map.get(pid)?.ticks || 0) - (prev.get(pid)?.ticks ?? map.get(pid)?.ticks ?? 0)), 0) / ((now - prevAt) / 1000) : 0);
      ctx.samples.push({
        t: now, electron_rss: +sum(groups.electron, "rss").toFixed(1), harness_rss: +sum(groups.harness, "rss").toFixed(1),
        electron_cpu: +cpu(groups.electron).toFixed(1), harness_cpu: +cpu(groups.harness).toFixed(1),
      });
      prev = map;
      prevAt = now;
    } catch {}
  }, 1000);
  return () => clearInterval(timer);
}

// ------------------------------------------------------------------ self-run

function summaryMd(op, slice) {
  const steps = op.area?.steps || [];
  const passed = steps.filter((s) => s.status === "pass").length;
  const rows = ctx.lines.filter((l) => l.turn).map((l) => `| ${l.turn} | ${l.scenario} | ${l.ttft_ms ?? "-"} | ${l.total_ms ?? "-"} | ${l.paint_ms.median?.toFixed?.(1) ?? "-"} / ${l.paint_ms.p95?.toFixed?.(1) ?? "-"} | ${l.electron_rss_mb ?? "-"} | ${l.harness_rss_mb ?? "-"} | ${l.electron_cpu_pct ?? "-"} / ${l.harness_cpu_pct ?? "-"} |`);
  const re = ctx.reopen;
  return [
    `# Chat campaign: ${slice}`, "", `Checks passed: ${passed}/${steps.length}`,
    ...steps.map((s) => `- ${s.status.toUpperCase()} ${s.id}${s.detail ? ": " + s.detail : ""}`), "",
    "| turn | scenario | TTFT ms | total ms | input-to-paint median / p95 ms | Electron RSS MB | harness RSS MB | CPU % (Electron / harness) |",
    "|---|---|---|---|---|---|---|---|", ...rows, "",
    `App open: ${ctx.openMs} ms.`,
    re ? `Reopen: ${re.ms} ms; bounds before ${JSON.stringify(re.before.bounds)}, after ${JSON.stringify(re.after.bounds)}; last conversation restored: ${re.restored} ("${re.before.title}" -> "${re.after.title}").` : "",
    "Known limitation: the desktop attaches to the running admin, so closing the app does not stop the backend.", "",
  ].join("\n");
}

async function main() {
  const slice = process.env.CHAT_SLICE;
  if (!slice || !(SLICES[slice] || process.env.CHAT_SCENARIOS)) throw new Error("set CHAT_SLICE to one of " + Object.keys(SLICES).join(", "));
  if (!(await portOpen(Number(ADMIN_PORT))) || !(await portOpen(Number(HARNESS_PORT)))) throw new Error("the campaign instance is not running (node tests/operator/chat-campaign/instance.cjs start)");
  if (!fs.existsSync(APP)) throw new Error("packaged app not found: " + APP);
  const { Operator } = require("../lib/operator.cjs");
  const { Report } = require("../lib/report.cjs");
  const out = path.join(paths.runs, slice);
  fs.mkdirSync(path.join(out, "shots"), { recursive: true });
  ctx = { home: paths.home, tmp: fs.mkdtempSync(path.join(os.tmpdir(), "claude-kh-")), out, samples: [], lines: [], turns: 0, electronPid: 0 };
  const stopSampler = startSampler();
  const session = { page: null, target: "desktop", base: harnessUrl, close: async () => ctx.handle && quitApp(ctx.handle) };
  let op = null;
  let report = null;
  try {
    await openApp(session);
    const options = { mode: "real", visible: true, pace: 600, budget: Number(process.env.CHAT_BUDGET ?? 0), target: "desktop", url: harnessUrl, adminUrl: `http://127.0.0.1:${ADMIN_PORT}` };
    report = new Report(out, { mode: "real", target: "desktop", visible: true, url: harnessUrl, admin: options.adminUrl, started: new Date().toISOString(), budget: options.budget });
    op = new Operator({ session, options, known: { steps: {}, lint: [] }, report, fixture: null });
    await op.runArea(area);
  } finally {
    stopSampler();
    const summary = report?.write();
    if (op) fs.writeFileSync(path.join(out, "summary.md"), summaryMd(op, slice));
    await session.close().catch(() => {});
    fs.chmodSync(ctx.tmp, 0o700);
    fs.rmSync(ctx.tmp, { recursive: true, force: true });
    if (summary) {
      console.log(`SUMMARY ${JSON.stringify(summary)}\nout ${out}`);
      process.exitCode = summary.fail || report.areas.some((a) => a.error) ? 1 : 0;
    }
  }
}

if (require.main === module) main().catch((error) => ((process.exitCode = 2), console.error("chat campaign failed:", error.message)));
