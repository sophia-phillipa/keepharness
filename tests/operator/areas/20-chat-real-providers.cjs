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
// Env: CHAT_SLICE (pilot|luna|deepseek|s1a|s1b|s2a|s2b|s3a|s3b|s4a|s4b), CHAT_SCENARIOS (comma list, overrides the slice),
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

const { paths, FACTS, S1, S1B, S2B, seedSlice1b, seedSlice2b, PROJECT_NAME, adminApi, portOpen } = instance;
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
  s2b: ["s1-03r", "s2-06", "s2-07", "s2-08", "s2-09", "s2-10"],
  s3b: ["s3-04", "s3-05", "s3-06", "s3-07"],
  s4a: ["s4-01"],
  s4b: ["s4-02", "s4-03", "s4-04", "s4-05", "s4-06", "s4-07", "s4-08"],
  s3a: ["s3-00", "s3-01", "s3-02", "s3-03", "s3-08", "s3-09", "s3-10"],
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
      await markdownChecks(op, c, "s1-02");
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
  "s2-03": (op) => stopScenario(op, "s2-03", "gpt-5.6-sol", "Medium"),
  "s3-08": (op) => stopScenario(op, "s3-08", "deepseek-flash", null),
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
  "s1-03r": async (op) => {
    await op.step("s1-03r", "Now: S1-03 rerun, No project chat on Read only asking for a file by path", async () => {
      const c = soft();
      const since = Date.now();
      await newChat(op);
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      await chooseAccessChecked(op, "Read only");
      c.ok(/No project/i.test(await op.page.locator("#project-button-label").innerText()), "the scope label does not show No project");
      const file = path.join(MAIN_DIR, "facts/alpha.txt");
      await ask(op, "s1-03r-a", `Read the file ${file} and tell me the archive code word it contains. If you cannot read it, say so plainly.`, /\S/);
      const text = await op.page.locator("#messages article.assistant").last().innerText();
      c.ok(!text.includes(S1.codeWord), "the Read only chat revealed the code word of a file outside its scope");
      c.ok(/can't|cannot|can not|unable|not able|don't have|do not have|no access|not available|couldn't|could not|isn't|not allowed|outside|denied|refus|only the active|not in|no way/i.test(text), `the reply does not say it cannot read the file: ${text.slice(0, 160)}`);
      await ask(op, "s1-03r-b", `And what is the archive keeper named in ${file}? If you cannot read it, say so plainly.`, /\S/);
      const text2 = await op.page.locator("#messages article.assistant").last().innerText();
      c.ok(!text2.includes(S1.keeper), "the second reply revealed the keeper name of the out-of-scope file");
      c.ok(/Read only/i.test(await op.page.locator("#access-label").innerText()), "the access label changed away from Read only");
      const ev = rolloutEvidence(since);
      record({ scenario: "s1-03r", rollout: ev });
      c.ok(ev.files > 0, "no Codex rollout file was written during the run");
      c.ok(ev.approval.length > 0 && ev.approval.every((a) => a === "never"), `rollout approval_policy values: ${JSON.stringify(ev.approval)}, expected only "never"`);
      c.ok(!ev.tools.some((t) => /exec_command|shell|local_shell/.test(t)), `the rollout ran shell tools: ${JSON.stringify(ev.tools)}`);
      c.done();
    }, { lint: false });
  },
  "s2-06": async (op) => {
    await op.step("s2-06", "Now: S2-06, a PNG with a known colour and text attached, then asked what it shows", async () => {
      const c = soft();
      await newChat(op);
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      seedSlice2b();
      await attachFiles(op, c, ["holm.png"]);
      const shot = path.join(ctx.out, "shots", "s2-06-chip.png");
      await op.page.screenshot({ path: shot, timeout: 15000 }).catch(() => {});
      c.ok((await op.page.locator("#attachments .attachment.image-attachment").count()) === 1, "the PNG chip has no image preview");
      await ask(op, "s2-06-a", "What does the attached picture show? Name the background colour and any text you can read in it.", /\S/);
      const t1 = await op.page.locator("#messages article.assistant").last().innerText();
      const notices = await op.page.locator("#messages article.assistant").evaluateAll((els) => els.filter((e) => /File skipped|does not support reading images|image support/i.test(e.innerText)).length);
      const sawImage = /red/i.test(t1) && /HOLM\s*73/i.test(t1);
      record({ scenario: "s2-06", saw_image: sawImage, notices, reply: t1.slice(0, 200) });
      c.ok(sawImage || notices === 1, `the answer does not match the image (red, HOLM 73) and no single image notice appeared (notices ${notices}): ${t1.slice(0, 160)}`);
      await ask(op, "s2-06-b", "Which colour was the picture's background? Reply with one word.", /\S/);
      const t2 = await op.page.locator("#messages article.assistant").last().innerText();
      c.ok(sawImage ? /red/i.test(t2) : true, `the follow-up does not say red: ${t2.slice(0, 80)}`);
      c.ok((await op.page.locator("#messages .attachment").count()) >= 1, "the sent message does not show the attachment");
      c.done();
    }, { lint: false });
  },
  "s2-07": async (op) => {
    await op.step("s2-07", "Now: S2-07, a code file attached once and used again after a model switch", async () => {
      const c = soft();
      await newChat(op);
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      seedSlice2b();
      await attachFiles(op, c, ["tally.py"]);
      await ask(op, "s2-07-1", "What is the value of HARBOR_FEE in the attached tally.py? Reply with just the number.", new RegExp(S2B.fee));
      c.ok((await op.page.locator("#attachments .attachment").count()) === 0, "the composer still holds the chip after sending");
      await chooseModel(op, "gpt-5.6-luna");
      await chooseEffort(op, "Medium");
      await ask(op, "s2-07-2", "Using the tally.py I attached earlier (nothing new is attached now), name the function that applies the fee. Reply with just the function name.", new RegExp(S2B.fn));
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      await ask(op, "s2-07-3", "In that same tally.py, what is HARBOR_FEE plus 3? Reply with just the number.", /420/);
      const turns = await turnsApi(op);
      c.ok(JSON.stringify(turns.map((t) => t.model)) === JSON.stringify(["gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-sol"]), `the runs used ${JSON.stringify(turns.map((t) => t.model))}`);
      const notices = await op.page.locator("#messages article.assistant").evaluateAll((els) => els.filter((e) => /File skipped|does not support|not supported/i.test(e.innerText)).length);
      c.ok(notices === 0, `${notices} notices appeared for a plain code file`);
      record({ scenario: "s2-07", answers: turns.map((t) => (t.answer || "").slice(0, 60)), notices });
      c.done();
    }, { lint: false });
  },
  "s2-08": async (op) => {
    await op.step("s2-08", "Now: S2-08, a text file over the excerpt limit, asked about its start and its end", async () => {
      const c = soft();
      await newChat(op);
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      seedSlice2b();
      await attachFiles(op, c, ["ledger-long.txt"]);
      const chip = await op.page.locator("#attachments .attachment-excerpt").count();
      c.ok(chip === 1, `the "excerpt sent" chip count is ${chip}, expected 1`);
      await op.page.screenshot({ path: path.join(ctx.out, "shots", "s2-08-chip.png"), timeout: 15000 }).catch(() => {});
      await ask(op, "s2-08-a", "In the attached ledger-long.txt, quote the marker word at the very start of the first line and the marker word at the very start of the last line. If you were not given the whole file, say exactly which part is missing.", /\S/);
      const t = await op.page.locator("#messages article.assistant").last().innerText();
      const hasEnd = t.includes(S2B.end);
      const admits = /not (included|sent|provided|visible|available|shown|given|see)|truncat|excerpt|only (the )?(first|part|beginning|start)|cut off|missing|partial|didn't (get|receive)|did not (get|receive)|wasn't|was not|no access|couldn't|cannot see|can't see|rest of the file/i.test(t);
      record({ scenario: "s2-08", has_start: t.includes(S2B.start), has_end: hasEnd, admits, reply: t.slice(0, 300) });
      c.ok(t.includes(S2B.start), "the answer does not quote the START marker that was sent");
      c.ok(hasEnd || admits, `the answer neither quotes the END marker nor admits the end was not sent: ${t.slice(0, 200)}`);
      await ask(op, "s2-08-b", "Was the end of ledger-long.txt included in what you were given as the attachment excerpt? Answer yes or no, then one sentence.", /\S/);
      const t2 = await op.page.locator("#messages article.assistant").last().innerText();
      c.ok(/\b(no|not|only|partial|truncat|excerpt)\b/i.test(t2) || hasEnd, `the follow-up does not state the file was cut: ${t2.slice(0, 160)}`);
      c.ok((await op.page.locator("#messages .attachment-excerpt").count()) >= 1, "the sent message does not keep the 'excerpt sent' chip");
      c.done();
    }, { lint: false });
  },
  "s2-09": async (op) => {
    await op.step("s2-09", "Now: S2-09, scrolling up and down while a long answer streams and two follow-ups queue", async () => {
      const c = soft();
      const page = op.page;
      await newChat(op);
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      const gap = () => page.evaluate(() => { const b = document.getElementById("messages"); return { top: Math.round(b.scrollTop), height: b.scrollHeight, gap: Math.round(b.scrollHeight - b.scrollTop - b.clientHeight), latest: !document.getElementById("latest-message").hidden }; });
      const running = async () => ["running", "queued"].includes(await page.locator("#conversation-state-pill").getAttribute("data-state"));
      const box = async () => { const b = await page.locator("#messages").boundingBox(); await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2); };
      await sendNoWait(op, "Write the numbers from 1 to 900, one per line, each followed by a different color word. Do not stop early and add nothing else.");
      await op.until(async () => (await streamLen(op)) > 1800, "the long answer did not reach 1800 characters", 120000);
      await op.until(async () => (await gap()).height > (await page.evaluate(() => document.getElementById("messages").clientHeight)) + 300, "the answer never overflowed the message list", 30000);
      const a = await gap();
      await sleep(1500);
      const b = await gap();
      c.ok(b.height > a.height && b.gap <= 250, `at the bottom the list did not follow the stream: ${JSON.stringify([a, b])}`);
      await op.caption("Now: scrolling up while the answer streams");
      await box();
      await page.mouse.wheel(0, -1500);
      await sleep(400);
      const up0 = await gap();
      await op.fill(page.locator("#prompt"), "Reply with just the word ECHO-ONE.");
      await submit(op);
      await sleep(600);
      await op.fill(page.locator("#prompt"), "Reply with just the word ECHO-TWO.");
      await submit(op);
      await sleep(1800);
      const up1 = await gap();
      const strip = await page.locator("#run-status-toggle").innerText();
      const upShot = path.join(ctx.out, "shots", "s2-09-scrolled-up.png");
      await page.screenshot({ path: upShot, timeout: 15000 }).catch(() => {});
      c.ok(up0.gap > 250, `the scroll up did not leave the bottom: ${JSON.stringify(up0)}`);
      c.ok(Math.abs(up1.top - up0.top) <= 3 && up1.height >= up0.height, `scrolled up, the view moved: ${JSON.stringify([up0, up1])}`);
      c.ok(up1.latest, "the jump-to-latest button is not shown while scrolled up");
      c.ok(/(\d+) queued/.test(strip) && Number(strip.match(/(\d+) queued/)[1]) >= 1, `the queue was not shown while the long answer streamed: "${strip}"`);
      await op.caption("Now: scrolling back down to the bottom");
      await box();
      for (let i = 0; i < 10; i++) await page.mouse.wheel(0, 4000);
      await sleep(400);
      const d0 = await gap();
      await sleep(1800);
      const d1 = await gap();
      const stillRunning = await running();
      record({ scenario: "s2-09", bottom: [a, b], up: [up0, up1], down: [d0, d1], strip, still_running: stillRunning, shot: upShot });
      c.ok(stillRunning, "the long answer ended before the resume check could be measured");
      c.ok(d1.gap <= 250 && d1.height > d0.height - 1, `after scrolling back down the list did not follow the stream: ${JSON.stringify([d0, d1])}`);
      await op.until(async () => !(await running()), "the conversation never went idle", 240000);
      await sleep(1500);
      const turns = await turnsApi(op);
      c.ok(turns.length === 3 && turns.every((t) => t.state === "completed"), `runs ended as ${JSON.stringify(turns.map((t) => t.state))}`);
      c.ok(turns.length === 3 && /ECHO-ONE/.test(turns[1].answer) && /ECHO-TWO/.test(turns[2].answer), "the queued follow-ups did not answer in order");
      const order = await page.evaluate(() => [...document.querySelectorAll("#messages article.user")].map((e) => e.innerText.slice(0, 60)));
      c.ok(/ECHO-ONE/.test(order[1] || "") && /ECHO-TWO/.test(order[2] || ""), `the user messages are ordered ${JSON.stringify(order)}`);
      c.done();
    }, { lint: false });
  },
  "s2-10": async (op) => {
    await op.step("s2-10", "Now: S2-10, three files of different kinds, one chip removed and re-added before sending", async () => {
      const c = soft();
      await newChat(op);
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      seedSlice2b();
      const names = ["harbor-memo.txt", "average.py", "tides.csv"];
      await attachFiles(op, c, names);
      const chips = () => op.page.locator("#attachments .attachment .attachment-name").allInnerTexts();
      await op.caption("Now: removing the code file chip before sending");
      await op.click(op.page.getByRole("button", { name: "Remove attachment average.py" }));
      await op.until(async () => (await chips()).length === 2, "the chip was not removed", 10000);
      c.ok(!(await chips()).includes("average.py"), "average.py is still listed after removal");
      await attachFiles(op, c, ["average.py"], 3);
      c.ok((await chips()).length === 3, `chips after re-adding: ${JSON.stringify(await chips())}`);
      await ask(op, "s2-10-a", "Give a one-line summary of each of the three attached files, one line per file, each starting with the file name: harbor-memo.txt, average.py, tides.csv.", /\S/);
      const t = await op.page.locator("#messages article.assistant").last().innerText();
      c.ok(/harbor-memo/i.test(t) && new RegExp(S1B.boat, "i").test(t), "the text file summary does not cite its content (the boat name)");
      c.ok(/average\.py/i.test(t) && /mean|average/i.test(t), "the code file summary does not cite its content");
      c.ok(/tides\.csv/i.test(t) && new RegExp(`${S2B.peak}|Wed|tide`, "i").test(t), "the CSV summary does not cite its content");
      await ask(op, "s2-10-b", "Which of those three files contains a bug? Reply with the file name only.", /average\.py/i);
      const sent = await op.page.locator("#messages .attachment").count();
      c.ok(sent === 3, `the sent message shows ${sent} attachments, expected 3`);
      record({ scenario: "s2-10", sent_attachments_in_history: sent });
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
  "s3-00": async (op) => {
    await op.step("s3-00", "Now: S3-00, checking that DeepSeek is configured and ready", async () => {
      const c = soft();
      const state = (await adminApi("GET", "/api/state")).json || {};
      c.ok(state.authentication?.deepseek === true && state.credentials?.deepseek === true, `the admin does not report DeepSeek as configured: auth=${state.authentication?.deepseek} credentials=${state.credentials?.deepseek}`);
      const svc = state.settings?.services?.deepseek || {};
      c.ok(svc.enabled === true && (svc.models || []).includes("deepseek-flash"), `DeepSeek is not enabled with deepseek-flash: ${JSON.stringify({ enabled: svc.enabled, models: svc.models })}`);
      c.ok((svc.projects || []).includes("sem-projeto") && (svc.projects || []).includes(PROJECT_NAME), `DeepSeek projects: ${JSON.stringify(svc.projects)}`);
      await newChat(op);
      const listed = await op.page.evaluate(() => fetch("/v1/models").then((r) => r.json()));
      c.ok((listed.models || []).some((m) => m.id === "deepseek-flash"), "the app's model list does not offer deepseek-flash");
      await op.click(op.page.locator("#model-trigger"));
      await sleep(800);
      const shot = path.join(ctx.out, "shots", "s3-00-models.png");
      await op.page.screenshot({ path: shot, timeout: 15000 }).catch(() => {});
      await op.page.keyboard.press("Escape");
      record({ scenario: "s3-00", authenticated: state.authentication?.deepseek, credentials: state.credentials?.deepseek, enabled: svc.enabled, models: svc.models, offered: (listed.models || []).map((m) => m.id), shot });
      c.done();
    }, { lint: false });
  },
  "s3-01": async (op) => {
    await op.step("s3-01", "Now: S3-01, a new DeepSeek chat with a question and a follow-up", async () => {
      const c = soft();
      await newChat(op);
      await chooseModel(op, "deepseek-flash");
      await askOn(op, "s3-01-q", "deepseek", "What is the capital of Australia? Answer in one short sentence.", /Canberra/);
      const q = ctx.last;
      c.ok(q.ttft_ms != null && q.ttft_ms > 0, "no TTFT was measured on the reply body");
      c.ok(!/Failed|error/i.test(q.pill) && !LIMIT.test(q.reply), `the first reply shows a problem: ${q.pill} ${q.reply.slice(0, 120)}`);
      await askOn(op, "s3-01-follow", "deepseek", "About how many people live there? Answer in one short sentence.", /\d|thousand|million/i);
      c.ok(!/Failed|error/i.test(ctx.last.pill), `the follow-up shows a problem: ${ctx.last.pill}`);
      const h = await op.page.evaluate(() => ({ user: document.querySelectorAll("#messages article.user").length, assistant: document.querySelectorAll("#messages article.assistant").length }));
      c.ok(h.user === 2 && h.assistant === 2, `the history shows ${h.user} user and ${h.assistant} assistant messages, expected 2 and 2`);
      const meta = (await lastMeta(op)).meta;
      c.ok(/deepseek/i.test(meta), `the reply footer does not name the DeepSeek model: "${meta}"`);
      record({ scenario: "s3-01", history: h, footer: meta });
      c.done();
    }, { lint: false });
  },
  "s3-02": async (op) => {
    await op.step("s3-02", "Now: S3-02, one conversation across Codex Sol, DeepSeek and Codex Sol, recalling earlier words", async () => {
      const c = soft();
      await newChat(op);
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      await askOn(op, "s3-02-codex1", "codex", "Remember this code word: HERON-5521. Reply with one short sentence confirming it.", /HERON/);
      await askOn(op, "s3-02-codex2", "codex", "Also remember a second code word: LARK-8830. Which was the first one I gave you? One short sentence.", /HERON-5521/);
      const sw1 = await switchModel(op, "deepseek-flash");
      record({ scenario: "s3-02-switch-to-deepseek", ...sw1 });
      if (!sw1.allowed) { c.ok(sw1.clear, `the switch to DeepSeek is refused without a clear message: ${JSON.stringify(sw1)}`); return c.done(); }
      await askOn(op, "s3-02-ds1", "deepseek", "What are the two code words I gave you earlier in this conversation? One short sentence.", /(?=[\s\S]*HERON-5521)(?=[\s\S]*LARK-8830)/);
      c.ok(/HERON-5521/.test(ctx.last.reply) && /LARK-8830/.test(ctx.last.reply), "DeepSeek did not recall both words planted on Codex");
      await askOn(op, "s3-02-ds2", "deepseek", "Add a third code word: PLOVER-1204. Then list all three words in the order I gave them, in one line.", /PLOVER-1204/);
      const sw2 = await switchModel(op, "gpt-5.6-sol");
      record({ scenario: "s3-02-switch-to-codex", ...sw2 });
      if (!sw2.allowed) { c.ok(sw2.clear, `the switch back to Codex is refused without a clear message: ${JSON.stringify(sw2)}`); return c.done(); }
      await askOn(op, "s3-02-codex3", "codex", "List all three code words I gave you so far, in the order I gave them, in one line.", /PLOVER-1204/);
      c.ok(/HERON-5521[\s\S]*LARK-8830[\s\S]*PLOVER-1204/.test(ctx.last.reply), "Codex did not recall all three words in order after the round trip");
      await askOn(op, "s3-02-codex4", "codex", "What was the very first thing I asked you in this conversation? One short sentence.", /HERON|code word|remember/i);
      const turns = await turnsApi(op);
      record({ scenario: "s3-02-turns", turns: turns.map((t) => ({ model: t.model, state: t.state })) });
      c.ok(turns.length === 6 && turns.every((t) => t.state === "done" || t.state === "completed"), `the server holds ${turns.length} runs: ${JSON.stringify(turns.map((t) => [t.model, t.state]))}`);
      c.done();
    }, { lint: false });
  },
  "s3-03": async (op) => {
    await op.step("s3-03", "Now: S3-03, a conversation that starts on DeepSeek and switches to Codex Sol", async () => {
      const c = soft();
      await newChat(op);
      await chooseModel(op, "deepseek-flash");
      await askOn(op, "s3-03-ds1", "deepseek", "Remember this code word: OTTER-3302. Reply with one short sentence confirming it.", /OTTER/);
      const sw = await switchModel(op, "gpt-5.6-sol");
      record({ scenario: "s3-03-switch-to-codex", ...sw });
      if (!sw.allowed) { c.ok(sw.clear, `the switch to Codex is refused without a clear message: ${JSON.stringify(sw)}`); return c.done(); }
      try { await chooseEffort(op, "Medium"); } catch (e) { record({ scenario: "s3-03-effort", note: e.message }); }
      await askOn(op, "s3-03-codex1", "codex", "What code word did I give you earlier in this conversation? Also remember a second one: WREN-7745. One short sentence.", /OTTER-3302/);
      await askOn(op, "s3-03-codex2", "codex", "List both code words in the order I gave them, in one line.", /WREN-7745/);
      c.ok(/OTTER-3302[\s\S]*WREN-7745/.test(ctx.last.reply), "Codex did not list both words in order");
      c.done();
    }, { lint: false });
  },
  "s4-01": async (op) => {
    await op.step("s4-01", "Now: S4-01, one conversation of 18 turns on Luna with four planted facts and recalls at turns 10, 15 and 18", async () => {
      const c = soft();
      await newChat(op);
      await chooseModel(op, "gpt-5.6-luna");
      await chooseEffort(op, "Medium");
      const F = { a: "OSPREY-4821", b: "Lindqvist", c: "Tuesday-Marrakesh", d: "7 amber lanterns" };
      const both = new RegExp(`(?=[\\s\\S]*${F.a})(?=[\\s\\S]*${F.b})`);
      const all = new RegExp(`(?=[\\s\\S]*${F.a})(?=[\\s\\S]*${F.b})(?=[\\s\\S]*Marrakesh)(?=[\\s\\S]*amber)`, "i");
      const recallAll = "Recall the four facts I gave you: the code word, the surname, the trip, and the lanterns. One line, comma separated.";
      const filler = (n, word) => askOn(op, `s4-01-t${n}`, "codex", `Reply with only the word ${word}.`, new RegExp(word, "i"));
      const words = ["PEAR", "CLOUD", "RIVER", "STONE", "MAPLE", "EMBER", "DELTA", "FROST", "QUILL", "HARBOR", "NORTH"];
      const checkpoints = {};
      const rss = (n) => { const l = ctx.lines.find((x) => x.scenario === `s4-01-t${n}`); checkpoints[n] = { electron: l?.electron_rss_mb ?? null, harness: l?.harness_rss_mb ?? null, paint_median: l?.paint_ms?.median ?? null, paint_p95: l?.paint_ms?.p95 ?? null }; };
      await askOn(op, "s4-01-t1", "codex", `Remember two facts. Fact 1: my code word is ${F.a}. Fact 2: my surname is ${F.b}. Reply with one short sentence confirming both.`, both);
      rss(1);
      await askOn(op, "s4-01-t2", "codex", `Fact 3: my trip is ${F.c}. Confirm in one short sentence.`, /Marrakesh/i);
      await askOn(op, "s4-01-t3", "codex", `Fact 4: I own ${F.d}. Confirm in one short sentence.`, /amber/i);
      for (let n = 4; n <= 9; n++) await filler(n, words[n - 4]);
      rss(9);
      await askOn(op, "s4-01-t10", "codex", recallAll, all);
      const r10 = ctx.last.reply;
      for (let n = 11; n <= 14; n++) await filler(n, words[n - 5]);
      await askOn(op, "s4-01-t15", "codex", "What is my code word and what is my surname? One short line.", both);
      const r15 = ctx.last.reply;
      await filler(16, words[9]);
      await filler(17, words[10]);
      await askOn(op, "s4-01-t18", "codex", recallAll, all);
      const r18 = ctx.last.reply;
      rss(18);
      await sleep(2500); // the harness answers 429 to quick repeats on /v1/conversations/:id
      const turns = await turnsApi(op).catch(async () => { await sleep(3000); return turnsApi(op).catch(() => []); });
      const blob = JSON.stringify(turns) + (await op.page.locator("#messages").innerText().catch(() => ""));
      const end = await runState(op);
      const shot = path.join(ctx.out, "shots", "s4-01-end.png");
      await op.page.screenshot({ path: shot, timeout: 15000 }).catch(() => {});
      record({ scenario: "s4-01-summary", turns_api: turns.length, rss_checkpoints: checkpoints, recall: { t10: r10, t15: r15, t18: r18 }, source_context_limit: /source_context_limit/.test(blob), states: [...new Set(turns.map((t) => t.state))], shot });
      c.ok(turns.length === 18 && end.turns === 18, `the conversation holds ${turns.length} turns on the server and ${end.turns} in the page, expected 18`);
      c.ok(turns.every((t) => t.state === "completed"), `turn states: ${turns.map((t) => t.state).join(",")}`);
      c.ok(!/source_context_limit/.test(blob), "source_context_limit appeared in the conversation");
      const med = checkpoints[18].paint_median;
      c.ok(med != null && med < 250, `input-to-paint median at turn 18 is ${med} ms`);
      c.done();
    }, { lint: false });
  },
  "s4-02": async (op) => {
    await op.step("s4-02", "Now: S4-02, renaming, archiving and unarchiving one conversation, then deleting another one permanently", async () => {
      const c = soft();
      const page = op.page;
      const tag = Date.now().toString(36).slice(-4).toUpperCase();
      const ids = {};
      for (const k of ["keep", "drop"]) {
        await newChat(op);
        await sol(op);
        await askOn(op, `s4-02-${k}`, "codex", `Reply with just the word ${k.toUpperCase()}-${tag}.`, new RegExp(`${k.toUpperCase()}-${tag}`));
        ids[k] = await convId(op);
        ctx.convs[`s402${k}`] = ids[k];
      }
      // Rename the first one from its row menu.
      const title = `Renamed ${tag} café`;
      await op.click(rowById(op, ids.keep).locator("button[data-conversation-id]"));
      await op.seeText(page.locator("#messages"), new RegExp(`KEEP-${tag}`), 15000);
      await rowMenu(op, ids.keep, "Rename conversation");
      await op.fill(page.locator("#rename-conversation-name"), title);
      await op.click(page.locator("#rename-conversation-save"));
      c.ok(await waitFor(async () => !(await page.locator("#rename-conversation-dialog").evaluate((d) => d.open))), "the rename dialog did not close after Save");
      const inHeader = await waitFor(async () => (await page.locator("#conversation-title").innerText()).includes(title));
      const inRow = await waitFor(async () => (await rowById(op, ids.keep).innerText()).includes(title));
      const inApi = (await listApi(op)).find((x) => x.id === ids.keep)?.title;
      c.ok(inHeader, "the header did not show the new title");
      c.ok(inRow, "the sidebar row did not show the new title");
      c.ok(inApi === title, `the server holds the title ${JSON.stringify(inApi)}`);
      const shotRename = await shotOf(op, "s4-02-renamed.png");
      // Archive it: the row goes away, the server lists it as archived.
      await rowMenu(op, ids.keep, "Archive conversation");
      const gone = await waitFor(async () => (await rowById(op, ids.keep).count()) === 0);
      const note = await page.locator("#status").innerText().catch(() => "");
      const archivedApi = (await listApi(op, "?archived=true")).some((x) => x.id === ids.keep);
      const liveApi = (await listApi(op)).some((x) => x.id === ids.keep);
      c.ok(gone && !liveApi, "the archived conversation is still in the sidebar or the normal list");
      c.ok(archivedApi, "the server does not list the conversation as archived");
      c.ok(/archived/i.test(note), `no archive notice (status "${note}")`);
      // Settings > Archived chats: find it by its new title, then Unarchive.
      await op.click(page.locator("#settings"));
      await op.click(page.locator('button[data-settings="archived"]'));
      const item = page.locator("#archived-list li").filter({ hasText: title });
      const found = await waitFor(async () => (await item.count()) === 1, 12000);
      c.ok(found, "the renamed conversation is not listed in Settings > Archived chats");
      const usage = await page.locator("#storage-usage").innerText().catch(() => "");
      const shotArchived = await shotOf(op, "s4-02-archived-list.png");
      if (found) await op.click(page.getByRole("button", { name: `Unarchive ${title}` }));
      c.ok(await waitFor(async () => (await item.count()) === 0), "the conversation stayed in the archived list after Unarchive");
      await op.click(page.locator("#settings-close"));
      c.ok(await waitFor(async () => (await rowById(op, ids.keep).count()) === 1), "the unarchived conversation did not return to the sidebar");
      await op.click(rowById(op, ids.keep).locator("button[data-conversation-id]"));
      await op.seeText(page.locator("#messages"), new RegExp(`KEEP-${tag}`), 15000).catch(() => c.ok(false, "the unarchived conversation lost its reply"));
      const back = await runState(op);
      c.ok(back.turns === 1 && back.users === 1 && back.articles === 2, `history after unarchive: ${back.turns} turns, ${back.users} user, ${back.articles} messages`);
      // Delete the second one permanently: the dialog is the explicit first step, the button the second.
      await op.click(rowById(op, ids.drop).locator("button[data-conversation-id]"));
      await op.seeText(page.locator("#messages"), new RegExp(`DROP-${tag}`), 15000);
      const conv = (await apiStatus(op, `/v1/conversations/${ids.drop}`)).json || {};
      const jobId = conv.turns?.[0]?.id || conv.turns?.[0]?.job_id || "";
      await rowMenu(op, ids.drop, "Delete permanently");
      const dialogOpen = await waitFor(async () => page.locator("#delete-conversation-dialog").evaluate((d) => d.open), 5000);
      const dialogText = await page.locator("#delete-conversation-dialog").innerText().catch(() => "");
      const shotDialog = await shotOf(op, "s4-02-delete-dialog.png");
      const beforeConfirm = (await apiStatus(op, `/v1/conversations/${ids.drop}`)).status;
      c.ok(dialogOpen && /Delete permanently\?/.test(dialogText) && dialogText.includes("can't be undone"), "no confirmation dialog before the delete");
      c.ok(beforeConfirm === 200, `the conversation was already gone (${beforeConfirm}) before the confirm step`);
      await op.click(page.locator("#delete-conversation-confirm"));
      c.ok(await waitFor(async () => !(await page.locator("#delete-conversation-dialog").evaluate((d) => d.open))), "the delete dialog did not close");
      c.ok(await waitFor(async () => (await rowById(op, ids.drop).count()) === 0), "the deleted conversation is still in the sidebar");
      const afterConv = (await apiStatus(op, `/v1/conversations/${ids.drop}`)).status;
      const afterJob = jobId ? (await apiStatus(op, `/v1/jobs/${jobId}`)).status : null;
      const inArchive = (await listApi(op, "?archived=true")).some((x) => x.id === ids.drop);
      c.ok(afterConv === 404, `the deleted conversation still answers ${afterConv}`);
      c.ok(!jobId || afterJob === 404, `the deleted conversation's run still answers ${afterJob}`);
      c.ok(!inArchive, "the deleted conversation appears in the archived list");
      record({ scenario: "s4-02-summary", tag, title, in_header: inHeader, in_row: inRow, in_api: inApi, archive_note: note, archived_api: archivedApi, found_in_settings: found, storage_line: usage, back, dialog_open: dialogOpen, status_before_confirm: beforeConfirm, status_after: afterConv, job_status_after: afterJob, job_id: !!jobId, shots: [shotRename, shotArchived, shotDialog] });
      c.done();
    }, { lint: false });
  },
  "s4-03": async (op) => {
    await op.step("s4-03", "Now: S4-03, taking the provider offline on the test instance, sending, restoring it and sending again", async () => {
      const c = soft();
      const page = op.page;
      const tag = Date.now().toString(36).slice(-4).toUpperCase();
      // The conversation from S4-02 (with history) when it ran, else a new one.
      if (ctx.convs.s402keep && (await rowById(op, ctx.convs.s402keep).count())) await op.click(rowById(op, ctx.convs.s402keep).locator("button[data-conversation-id]"));
      else { await newChat(op); await sol(op); }
      await sleep(1500);
      const before = await runState(op);
      const original = (await adminApi("GET", "/api/state")).json?.settings; // test instance settings only, kept in memory
      if (!original?.services?.codex) throw new Error("the test admin returned no Codex settings");
      let restored = false;
      const restore = async () => {
        if (restored) return;
        const saved = await adminApi("POST", "/api/settings", original);
        if (!(await portOpen(Number(HARNESS_PORT)))) await adminApi("POST", "/api/start", {});
        await op.until(() => portOpen(Number(HARNESS_PORT)), "the harness did not come back", 60000);
        restored = saved.status === 200;
      };
      let offline = null, off = null, offState = null, sent = false, error = null;
      try {
        await op.caption("Now: switching Codex off on the test instance");
        const copy = JSON.parse(JSON.stringify(original));
        copy.services.codex.enabled = false;
        off = await adminApi("POST", "/api/settings", copy);
        await sleep(4000);
        let harnessUp = await portOpen(Number(HARNESS_PORT));
        if (!harnessUp) { await adminApi("POST", "/api/start", {}); await waitFor(() => portOpen(Number(HARNESS_PORT)), 30000); harnessUp = await portOpen(Number(HARNESS_PORT)); }
        offline = { save_status: off.status, harness_up: harnessUp };
        await op.fill(page.locator("#prompt"), `Reply with just the word OFFLINE-${tag}.`);
        if (await page.locator("#send").isEnabled()) {
          const seen = await submit(op);
          sent = true;
          await waitFor(async () => { const s = await visibleState(op); return s.state === "failed" || /Failed|Couldn't|unavailable|not available|error/i.test(`${s.pill} ${s.status} ${s.alerts}`); }, 45000);
          await sleep(2500);
          offState = await visibleState(op);
        } else offState = { blocked: "the send button is disabled while the provider is off", ...(await visibleState(op)) };
        offState.shot = await shotOf(op, "s4-03-offline.png");
        offState.text = `${offState.pill} | ${offState.status} | ${offState.alerts} | ${offState.last}`;
        offState.raw = RAW_TEXT.test(offState.text);
        offState.limit = LIMIT.test(offState.text);
      } catch (e) {
        error = e;
      } finally {
        await restore();
      }
      record({ scenario: "s4-03-offline", offline, sent, ...offState, history_before: before, restored });
      if (error) throw error;
      c.ok(restored, "the test instance settings were not restored");
      c.ok(sent, offState.blocked || "the offline prompt was not sent");
      c.ok(!offState.raw, `raw error text is visible: ${offState.text.slice(0, 200)}`);
      c.ok(/\w{4,}/.test(`${offState.status} ${offState.alerts}`) || /Failed/.test(offState.pill), "no readable failure was shown");
      if (offState.limit) { op.area.halted = true; throw new Error("a provider limit text was seen, stopping"); }
      // Restored: the app reconnects on its own (or needs a reload), then the resend must answer.
      await sleep(3000);
      await dismissTourSoon(op);
      const mid = await runState(op).catch(() => null);
      await ask(op, "s4-03-resend", `Reply with just the word BACK-${tag}.`, new RegExp(`BACK-${tag}`));
      const after = await runState(op);
      const turns = await turnsApi(op);
      record({ scenario: "s4-03-resend", turns_before_offline: before.turns, turns_after_offline: mid?.turns, turns_after_resend: after.turns, states: turns.map((t) => t.state), users: after.users, articles: after.articles });
      c.ok(/BACK-/.test((await lastMeta(op)).text) && after.apiState === "completed", `the resend ended ${after.apiState}`);
      c.ok(turns.slice(0, before.turns).every((t) => t.state === "completed" && t.answer), "an earlier turn lost its answer");
      c.ok(turns.filter((t) => t.prompt.includes(`BACK-${tag}`)).length === 1 && turns.filter((t) => t.prompt.includes(`OFFLINE-${tag}`)).length <= 1, "a turn was duplicated");
      c.ok(after.users === after.turns, `the page shows ${after.users} user messages for ${after.turns} turns`);
      c.done();
    }, { lint: false });
  },
  "s4-04": async (op) => {
    await op.step("s4-04", "Now: S4-04, restarting the test harness during a stream, then retrying", async () => {
      const c = soft();
      const page = op.page;
      const tag = Date.now().toString(36).slice(-4).toUpperCase();
      await newChat(op);
      await sol(op);
      const prompt = `Run ${tag}: write the numbers from 1 to 150, one per line, each followed by a different English word. Do not stop early and add nothing else.`;
      await sendNoWait(op, prompt);
      await op.until(async () => (await streamLen(op)) > 120, "the answer never started streaming", 90000);
      const pre = await runState(op);
      const pidBefore = harnessPid();
      await op.caption("Now: stopping the harness in the middle of the stream");
      const t0 = Date.now();
      await adminApi("POST", "/api/stop", {});
      const seen = [];
      for (let i = 0; i < 8; i++) { const s = await visibleState(op).catch(() => ({})); seen.push({ t: Date.now() - t0, pill: s.pill, state: s.state, status: (s.status || "").slice(0, 80), alerts: (s.alerts || "").slice(0, 80), resume: s.resume }); await sleep(1000); }
      const shotDown = await shotOf(op, "s4-04-harness-down.png");
      c.ok(!(await portOpen(Number(HARNESS_PORT))), "the harness port stayed open after the admin stop");
      await adminApi("POST", "/api/start", {});
      await op.until(() => portOpen(Number(HARNESS_PORT)), "the harness did not come back", 60000);
      const restartMs = Date.now() - t0;
      // A person would press "Resume tracking" if the app offers it; record whether it was needed.
      let resumed = false;
      await waitFor(async () => (await page.locator("#startup-gate").evaluate((g) => g.hidden).catch(() => true)), 30000);
      if (await page.locator("#resume-execution").isVisible().catch(() => false)) { await page.locator("#resume-execution").click().catch(() => {}); resumed = true; }
      await sleep(4000);
      const after = await visibleState(op);
      let mid = await runState(op).catch(() => null);
      for (let i = 0; i < 10 && mid && ["running", "queued"].includes(mid.apiState || ""); i++) { await sleep(2500); mid = await runState(op).catch(() => mid); }
      const shotBack = await shotOf(op, "s4-04-harness-back.png");
      const shown = seen.some((s) => /Interrupted|Failed|Connection|lost|Couldn't|Resume/i.test(`${s.pill} ${s.status} ${s.alerts}`) || s.resume) || /Interrupted|Failed|Connection|lost|Couldn't/i.test(`${after.pill} ${after.status} ${after.alerts}`) || ["interrupted", "failed"].includes(mid?.apiState);
      record({ scenario: "s4-04-failure", pid_before: pidBefore, pid_after: harnessPid(), restart_ms: restartMs, chars_at_kill: pre.chars, turns_before: pre.turns, seen, resume_pressed: resumed, after_state: { pill: after.pill, status: after.status, alerts: after.alerts }, server_state: mid?.apiState, server_turns: mid?.turns, server_chars: mid?.apiChars, shots: [shotDown, shotBack] });
      c.ok(shown, `no failure was shown to the person (pill "${after.pill}", server state ${mid?.apiState})`);
      c.ok(pre.state === "running" && pre.chars > 100, `the harness was not stopped mid-stream (${pre.chars} chars, ${pre.state})`);
      // Retry: the same prompt again; the history must gain exactly one turn.
      const turnsBefore = mid?.turns ?? pre.turns, usersBefore = mid?.users ?? pre.users;
      await ask(op, "s4-04-retry", prompt, /(^|\D)150(\D|$)/);
      await settle(op, 120000);
      const fin = await runState(op);
      record({ scenario: "s4-04-retry", turns_before_retry: turnsBefore, turns_after_retry: fin.turns, users_before: usersBefore, users_after: fin.users, state: fin.apiState, tail: fin.tail });
      c.ok(fin.turns === turnsBefore + 1 && fin.users === usersBefore + 1, `the retry left ${fin.turns} turns and ${fin.users} user messages, expected ${turnsBefore + 1} and ${usersBefore + 1}`);
      c.ok(fin.apiState === "completed" && /(^|\D)150(\D|$)/.test(fin.tail), `the retry ended ${fin.apiState}: ${JSON.stringify(fin.tail)}`);
      c.done();
    }, { lint: false });
  },
  "s4-05": async (op) => {
    await op.step("s4-05", "Now: S4-05, the whole flow from the keyboard: send, model picker, switch conversation, slash palette, Stop", async () => {
      const c = soft();
      const page = op.page;
      const tag = Date.now().toString(36).slice(-4).toUpperCase();
      const log = {};
      await newChat(op);
      await sol(op);
      const keys = await convId(op);
      await op.caption("Now: keyboard only, no mouse");
      // 1. Focus the composer with the documented shortcut.
      await page.evaluate(() => document.activeElement?.blur());
      await page.keyboard.press("Control+/");
      log.composer = await focusState(op);
      c.ok(log.composer.id === "prompt", `Ctrl+/ focused ${log.composer.id || log.composer.tag}, not the composer`);
      c.ok(log.composer.ring, "the composer shows no visible focus");
      // 2. Shift+Enter writes a new line and does not send; Enter sends.
      const runs0 = await page.locator("#messages").getByRole("button", { name: "View run" }).count();
      await op.type("line one");
      await page.keyboard.press("Shift+Enter");
      await op.type("line two");
      log.shiftEnter = { value: await page.locator("#prompt").inputValue(), sent: (await page.locator("#messages").getByRole("button", { name: "View run" }).count()) > runs0 };
      c.ok(log.shiftEnter.value === "line one\nline two" && !log.shiftEnter.sent, `Shift+Enter: ${JSON.stringify(log.shiftEnter)}`);
      await page.keyboard.press("Control+A");
      await page.keyboard.press("Backspace");
      await op.type(`Reply with just the word KEYS-${tag}.`);
      op.spendPrompt();
      await op.paceSubmission();
      const from = Date.now();
      await page.keyboard.press("Enter");
      await waitAnswer(op, runs0, new RegExp(`KEYS-${tag}`), 120000);
      recordPhase("s4-05-send", from, Date.now(), [], await shotOf(op, "s4-05-sent.png"), { sent_with: "Enter" });
      c.ok(!/\n/.test(await page.locator("#prompt").inputValue()) && (await page.locator("#prompt").inputValue()) === "", "the composer was not cleared after Enter");
      // 3. Model picker: reach the trigger with Tab, open with Enter, move with the arrows, Escape closes.
      await page.keyboard.press("Control+/");
      log.modelReach = await reachKey(op, () => document.activeElement?.id === "model-trigger");
      log.modelFocus = await focusState(op);
      c.ok(log.modelReach.steps > 0, "the model picker trigger is not reachable with Tab");
      if (log.modelReach.steps > 0) {
        await page.keyboard.press("Enter");
        log.modelOpen = await waitFor(() => page.evaluate(() => document.getElementById("model-menu").matches(":popover-open")), 4000);
        await page.keyboard.press("ArrowDown");
        await sleep(300);
        log.modelInside = await focusState(op);
        log.modelShot = await shotOf(op, "s4-05-model-open.png");
        await page.keyboard.press("Escape");
        await sleep(400);
        log.modelClosed = !(await page.evaluate(() => document.getElementById("model-menu").matches(":popover-open")));
        log.modelAfter = await focusState(op);
        c.ok(log.modelFocus.ring, "the model trigger shows no visible focus");
        c.ok(log.modelOpen, "Enter on the model trigger did not open the picker");
        c.ok(log.modelInside.inModelMenu, `the arrows left the focus outside the picker (${log.modelInside.id || log.modelInside.tag})`);
        c.ok(log.modelClosed, "Escape did not close the model picker");
        c.ok(log.modelAfter.id === "model-trigger", `after Escape the focus is on ${log.modelAfter.id || log.modelAfter.tag}, not the trigger`);
      }
      // 4. Switch to another conversation from the sidebar by keyboard, then come back.
      log.switchOut = await reachKey(op, (keep) => { const e = document.activeElement; return !!e?.matches?.("#sidebar button[data-conversation-id]") && e.dataset.conversationId !== keep; }, keys);
      log.switchOutFocus = await focusState(op);
      c.ok(log.switchOut.steps > 0, "no other conversation row is reachable with Tab");
      if (log.switchOut.steps > 0) {
        const other = await page.evaluate(() => document.activeElement.dataset.conversationId);
        await page.keyboard.press("Enter");
        log.switchedOut = await waitFor(async () => (await convId(op)) === other, 8000);
        c.ok(log.switchOutFocus.ring, "the sidebar row shows no visible focus");
        c.ok(log.switchedOut, "Enter on a sidebar row did not open that conversation");
        await page.keyboard.press("Control+/"); // start from the composer, as for the way out
        await sleep(300);
        log.switchBack = await reachKey(op, (keep) => document.activeElement?.dataset?.conversationId === keep, keys);
        if (log.switchBack.steps > 0) {
          await page.keyboard.press("Enter");
          log.switchedBack = await waitFor(async () => (await convId(op)) === keys, 8000);
        }
        c.ok(log.switchedBack, "could not return to the conversation by keyboard");
      }
      // 5. The "/" palette: opens on "/", the arrows move, Escape closes and keeps the composer.
      await page.keyboard.press("Control+/");
      await op.type("/");
      log.paletteOpen = await waitFor(() => page.evaluate(() => document.getElementById("resource-menu").matches(":popover-open")), 6000);
      await page.keyboard.press("ArrowDown");
      await sleep(300);
      log.paletteFocus = await focusState(op);
      log.paletteShot = await shotOf(op, "s4-05-palette.png");
      await page.keyboard.press("Escape");
      await sleep(400);
      log.paletteClosed = !(await page.evaluate(() => document.getElementById("resource-menu").matches(":popover-open")));
      log.paletteAfter = await focusState(op);
      c.ok(log.paletteOpen, "typing / did not open the palette");
      c.ok(log.paletteFocus.inResourceMenu, `the arrows left the focus outside the palette (${log.paletteFocus.id || log.paletteFocus.tag})`);
      c.ok(log.paletteClosed, "Escape did not close the palette");
      c.ok(log.paletteAfter.id === "prompt", `after Escape the focus is on ${log.paletteAfter.id || log.paletteAfter.tag}, not the composer`);
      await page.keyboard.press("Control+A");
      await page.keyboard.press("Backspace");
      // 6. A long reply, then Stop from the keyboard.
      const runs1 = await page.locator("#messages").getByRole("button", { name: "View run" }).count();
      await page.keyboard.press("Control+/");
      await page.keyboard.insertText("Write the numbers from 1 to 400, one per line, each followed by a different English word. Do not stop early and add nothing else.");
      op.spendPrompt();
      await op.paceSubmission();
      const from2 = Date.now();
      await page.keyboard.press("Enter");
      await op.until(async () => (await page.locator("#messages").getByRole("button", { name: "View run" }).count()) > runs1, "the long run did not start", 30000);
      await op.until(async () => (await streamLen(op)) > 200, "the long answer never started streaming", 90000);
      log.stopReach = await reachKey(op, () => document.activeElement?.id === "cancel");
      log.stopFocus = await focusState(op);
      c.ok(log.stopReach.steps > 0, "the Stop button is not reachable with Tab");
      c.ok(log.stopFocus.ring, "the Stop button shows no visible focus");
      if (log.stopReach.steps > 0) await page.keyboard.press("Enter");
      else await page.locator("#cancel").click(); // mouse fallback only so the run does not keep going
      log.stopped = await waitFor(async () => /Cancel|Stopp/i.test(await page.locator("#conversation-state-pill").innerText()), 15000);
      c.ok(log.stopped, "Stop from the keyboard did not cancel the run");
      await sleep(1500);
      log.stopLen = await streamLen(op);
      recordPhase("s4-05-stop", from2, Date.now(), [], await shotOf(op, "s4-05-stopped.png"), { chars_at_stop: log.stopLen });
      record({ scenario: "s4-05-summary", ...log });
      c.done();
    }, { lint: false });
  },
  "s4-06": async (op) => {
    await op.step("s4-06", "Now: S4-06, a narrow and a wide window with one Markdown and code prompt at each size", async () => {
      const c = soft();
      const page = op.page;
      await newChat(op);
      await sol(op);
      const original = (await setWindow(null)).bounds;
      const ask1 = "Reply in Markdown only: a level-2 heading, a bullet list of 2 items, a table with 4 columns (Island, Harbor, Lanterns, Notes) and 2 rows whose Notes cells are invented sentences of about 60 characters, and one python code block of 6 lines where one line is at least 150 characters long. No other text.";
      const ask2 = "Now the same structure again with different invented data.";
      const sizes = {};
      try {
        for (const [name, want, text] of [["narrow", { width: 480, height: 860 }, ask1], ["wide", null, ask2]]) {
          await op.caption(`Now: the ${name} window`);
          const area = (await setWindow(null)).area;
          const rect = want ? { x: original.x, y: original.y, ...want } : { x: area.x, y: area.y, width: Math.min(area.width, 1900), height: Math.min(area.height, 1000) };
          const got = await setWindow(rect);
          await sleep(1500);
          await ask(op, `s4-06-${name}`, text, /\S/);
          const probe = await layoutProbe(op);
          const shot = await shotOf(op, `s4-06-${name}.png`);
          // The side panel and the conversation list stay usable at this size.
          const panel = {};
          await op.click(page.locator("#panel-toggle"));
          await sleep(600);
          panel.files = await page.evaluate(() => { const p = document.getElementById("activity-panel"), r = p.getBoundingClientRect(); return { open: !p.hidden, left: Math.round(r.left), right: Math.round(r.right), inner: innerWidth, docScroll: document.documentElement.scrollWidth }; });
          panel.shot = await shotOf(op, `s4-06-${name}-panel.png`);
          await page.keyboard.press("Escape");
          await sleep(500);
          panel.closed = await page.evaluate(() => document.getElementById("activity-panel").hidden);
          const side = await page.evaluate(() => { const s = document.getElementById("sidebar"), r = s.getBoundingClientRect(); return { visible: r.width > 0 && r.right > 0 && getComputedStyle(s).visibility !== "hidden", width: Math.round(r.width) }; });
          if (!side.visible || name === "narrow") { await op.click(page.locator("#menu")); await sleep(500); panel.rows = await page.locator("#sidebar button[data-conversation-id]").filter({ visible: true }).count(); await page.keyboard.press("Escape"); await sleep(400); }
          sizes[name] = { requested: rect, bounds: got.bounds, probe, panel, side, shot };
          c.ok(probe.docScroll <= probe.inner[0] + 1 && probe.bodyScroll <= probe.inner[0] + 1, `${name}: the page scrolls horizontally (${probe.docScroll}/${probe.bodyScroll} px in ${probe.inner[0]})`);
          c.ok(probe.spill.length === 0, `${name}: elements spill past the right edge: ${probe.spill.join(", ")}`);
          c.ok(probe.prompt?.visible && probe.send?.visible && probe.prompt.left >= 0 && probe.prompt.right <= probe.inner[0] + 1 && probe.send.right <= probe.inner[0] + 1, `${name}: the composer or the send button is clipped (${JSON.stringify([probe.prompt, probe.send])})`);
          c.ok(probe.code.length >= 1 && probe.code.every((b) => b.scrollW <= b.clientW + 1 || /auto|scroll/.test(b.overflowX)), `${name}: a code block neither fits nor scrolls (${JSON.stringify(probe.code)})`);
          c.ok(probe.tables.every((t) => t.right <= probe.inner[0] + 1 || t.wrapScrolls), `${name}: a table spills and does not scroll (${JSON.stringify(probe.tables)})`);
          c.ok(panel.files.open && panel.files.right <= panel.files.inner + 1 && panel.files.docScroll <= panel.files.inner + 1, `${name}: the files panel is clipped (${JSON.stringify(panel.files)})`);
          c.ok(panel.closed, `${name}: Escape did not close the files panel`);
        }
      } finally {
        await setWindow({ x: original.x, y: original.y, width: original.width, height: original.height });
        await sleep(1200);
      }
      const end = (await setWindow(null)).bounds;
      record({ scenario: "s4-06-summary", original, end, sizes });
      c.ok(JSON.stringify(end) === JSON.stringify(original), `the window was not restored: ${JSON.stringify(original)} -> ${JSON.stringify(end)}`);
      c.done();
    }, { lint: false });
  },
  "s4-07": async (op) => {
    await op.step("s4-07", "Now: S4-07, the light and dark themes with the Markdown and code reply from S4-06", async () => {
      const c = soft();
      const page = op.page;
      const hasReply = (await page.locator("#messages article.assistant .code-block").count()) > 0;
      if (!hasReply) throw new Error("no Markdown and code reply is on screen (run S4-06 first, or open that conversation)");
      const toggle = async () => {
        await op.click(page.locator("#settings"));
        await op.click(page.locator('button[data-settings="appearance"]'));
        await op.click(page.locator("#theme-toggle"));
        await sleep(600);
        await op.click(page.locator("#settings-close"));
        await sleep(600);
      };
      const first = await themeProbe(op);
      const shotA = await shotOf(op, `s4-07-${first.theme}.png`);
      await toggle();
      const second = await themeProbe(op);
      const shotB = await shotOf(op, `s4-07-${second.theme}.png`);
      c.ok(first.theme !== second.theme, `the theme toggle did not change the theme (${first.theme} -> ${second.theme})`);
      const reasons = [];
      for (const [name, p] of [[first.theme, first], [second.theme, second]]) {
        for (const k of ["text", "heading", "code", "th", "td", "sidebar", "composer"]) {
          if (!p[k]) reasons.push(`${name}: ${k} not found`);
          else if (p[k].ratio < 4.5) reasons.push(`${name}: ${k} contrast ${p[k].ratio}`);
        }
      }
      c.ok(reasons.length === 0, `contrast: ${reasons.join("; ")}`);
      c.ok(JSON.stringify(first.codeBg) !== JSON.stringify(second.codeBg) || JSON.stringify(first.code?.fg) !== JSON.stringify(second.code?.fg), "the code block colors are the same in both themes (not themed)");
      c.ok(first.tableBorder !== second.tableBorder || JSON.stringify(first.td?.fg) !== JSON.stringify(second.td?.fg), "the table colors are the same in both themes (not themed)");
      // The choice survives a reload.
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.evaluate(METRICS_JS);
      await page.locator("#startup-gate").waitFor({ state: "hidden", timeout: 40000 });
      await dismissTourSoon(op);
      const reloaded = await page.evaluate(() => ({ theme: document.documentElement.dataset.theme, palette: document.documentElement.dataset.palette, stored: localStorage.getItem("keepharness:theme:harness") }));
      c.ok(reloaded.theme === second.theme && reloaded.palette === second.palette, `the theme after reload is ${reloaded.theme}/${reloaded.palette}, expected ${second.theme}/${second.palette}`);
      await toggle(); // back to the starting theme
      const restored = await page.evaluate(() => document.documentElement.dataset.theme);
      c.ok(restored === first.theme, `the starting theme was not restored (${restored})`);
      record({ scenario: "s4-07-summary", first, second, reloaded, restored, shots: [shotA, shotB] });
      c.done();
    }, { lint: false });
  },
  "s4-08": async (op) => {
    await op.step("s4-08", "Now: S4-08, one conversation streaming in the background while another one is open", async () => {
      const c = soft();
      const page = op.page;
      const tag = Date.now().toString(36).slice(-4).toUpperCase();
      await newChat(op);
      await sol(op);
      const from = Date.now();
      await sendNoWait(op, `Run ${tag}: write ALPHA-${tag}-1 through ALPHA-${tag}-150, one per line, nothing else.`);
      await op.until(async () => (await streamLen(op)) > 20, "ALPHA never started streaming", 90000);
      const idA = await convId(op);
      const own = await visibleState(op);
      c.ok(own.state === "running", `the open streaming chat shows "${own.pill}" (${own.state})`);
      await op.caption("Now: opening another conversation while ALPHA streams");
      await newChat(op);
      await sol(op);
      const dots = [];
      const dot = async (when) => { const s = { when, t: Date.now() - from, alpha: await rowDot(op, idA), bravo: await rowDot(op, await convId(op)) }; dots.push(s); return s; };
      await ask(op, "s4-08-bravo", `Reply with just the word BRAVO-${tag}.`, new RegExp(`BRAVO-${tag}`));
      const idB = await convId(op);
      const afterB = await dot("after BRAVO answered");
      const bText = await page.locator("#messages").innerText();
      c.ok(!bText.includes(`ALPHA-${tag}`), "text of the background chat landed in the open one");
      c.ok(/progress/i.test(afterB.alpha) || afterB.alpha === "Unread response", `the background chat row shows "${afterB.alpha}" while it streams`);
      const shotMid = await shotOf(op, "s4-08-background.png");
      // Stay on BRAVO until ALPHA finishes in the background; watch its row.
      let unread = /Unread/.test(afterB.alpha);
      const end = Date.now() + 180000;
      while (Date.now() < end && !unread) {
        await sleep(2000);
        const s = await dot("waiting");
        if (/Unread/.test(s.alpha)) unread = true;
        else if (s.alpha === "") break;
        c.ok(!(await page.locator("#messages").innerText()).includes(`ALPHA-${tag}`), "text of the background chat landed in the open one");
      }
      const shotDone = await shotOf(op, "s4-08-background-finished.png");
      c.ok(unread, `the finished background chat never showed "Unread response" (last dot "${dots[dots.length - 1].alpha}")`);
      await op.click(rowById(op, idA).locator("button[data-conversation-id]"));
      await op.seeText(page.locator("#messages"), new RegExp(`ALPHA-${tag}-1\\b`), 15000);
      await sleep(2500);
      const opened = await dot("ALPHA opened");
      const aText = await page.locator("#messages").innerText();
      const fin = await runState(op);
      c.ok(opened.alpha === "", `the unread dot stayed on the chat after it was opened ("${opened.alpha}")`);
      c.ok(new RegExp(`ALPHA-${tag}-150\\b`).test(aText) && !aText.includes(`BRAVO-${tag}`), "ALPHA is incomplete or contains BRAVO text");
      c.ok(fin.apiState === "completed" && fin.turns === 1, `ALPHA ended ${fin.apiState} with ${fin.turns} turns`);
      await op.click(rowById(op, idB).locator("button[data-conversation-id]"));
      await sleep(1500);
      const bFinal = await page.locator("#messages").innerText();
      c.ok(bFinal.includes(`BRAVO-${tag}`) && !bFinal.includes(`ALPHA-${tag}`), "BRAVO lost its answer or contains ALPHA text");
      recordPhase("s4-08-alpha", from, Date.now(), [], shotDone, { dots, own_pill: own.pill, alpha_chars: aText.length, shots: [shotMid, shotDone] });
      c.done();
    }, { lint: false });
  },
  "s3-04": async (op) => {
    await op.step("s3-04", "Now: S3-04, reloading the page in the middle of a long Sol reply", async () => {
      const c = soft();
      await newChat(op);
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      // Part A: reload while the text streams.
      await startLong(op, 300);
      const pre = await runState(op);
      await op.caption("Now: reloading the page mid-reply");
      const t0 = Date.now();
      await op.page.reload({ waitUntil: "domcontentloaded" });
      await op.page.evaluate(METRICS_JS);
      await op.page.locator("#startup-gate").waitFor({ state: "hidden", timeout: 40000 });
      const reloadMs = Date.now() - t0;
      await dismissTourSoon(op);
      const post = await runState(op);
      const restored = post.id === pre.id && post.articles > 0;
      const live = post.state;
      await settle(op, 240000);
      const fin = await runState(op);
      const shotA = path.join(ctx.out, "shots", "s3-04-reload-a.png");
      await op.page.screenshot({ path: shotA, timeout: 15000 }).catch(() => {});
      record({ scenario: "s3-04-a", reload_ms: reloadMs, chars_at_reload: pre.chars, pill_after_reload: live, same_conversation: post.id === pre.id, articles_before: pre.articles, articles_after: post.articles, turns_before: pre.turns, turns_after: fin.turns, final_chars_ui: fin.chars, final_chars_api: fin.apiChars, final_state: fin.apiState, tail: fin.tail, shot: shotA });
      c.ok(restored, `the conversation was not restored after the reload (id ${pre.id} -> ${post.id}, ${post.articles} messages)`);
      c.ok(pre.chars > 100 && pre.state === "running", `the reload did not hit a live stream (${pre.chars} chars, ${pre.state})`);
      c.ok(fin.turns === pre.turns && fin.users === pre.users, `a turn was duplicated or lost (${pre.turns}/${pre.users} -> ${fin.turns}/${fin.users})`);
      c.ok(fin.apiState === "completed", `the run ended as ${fin.apiState}`);
      c.ok(/(^|\D)300(\D|$)/.test(fin.tail) && fin.chars >= fin.apiChars - 5, `the final text is incomplete (UI ${fin.chars} chars, server ${fin.apiChars}, tail ${JSON.stringify(fin.tail)})`);
      ctx.convs.s304 = (await snapshot(op.page)).title;
      // Part B: reload right after the send, before the first token, and expect exactly one turn (this also proves send works after a reload).
      const before = await runState(op);
      await op.fill(op.page.locator("#prompt"), "Reply with just the word EARLY-OK.");
      const seen = await submit(op);
      await op.page.reload({ waitUntil: "domcontentloaded" });
      await op.page.evaluate(METRICS_JS);
      await op.page.locator("#startup-gate").waitFor({ state: "hidden", timeout: 40000 });
      await dismissTourSoon(op);
      await settle(op, 120000);
      const early = await runState(op);
      record({ scenario: "s3-04-b", turns_before: before.turns, turns_after: early.turns, state: early.apiState, tail: early.tail, seen });
      c.ok(early.turns === before.turns + 1, `the early reload left ${early.turns} turns, expected ${before.turns + 1}`);
      c.ok(/EARLY-OK/.test(early.tail) && early.apiState === "completed", `the early-reload answer is ${early.apiState}: ${JSON.stringify(early.tail)}`);
      c.done();
    }, { lint: false });
  },
  "s3-05": async (op) => {
    await op.step("s3-05", "Now: S3-05, closing the app window in the middle of a long reply and opening it again", async () => {
      const c = soft();
      await newChat(op);
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      for (const round of (process.env.CHAT_S305_ROUNDS || "a,b").split(",")) { // CHAT_S305_ROUNDS=b reruns only the second round
        await startLong(op, round === "a" ? 200 : 250);
        const pre = await runState(op);
        const title = (await snapshot(op.page)).title;
        const before = await bounds(ctx.handle);
        await op.caption("Now: closing the app mid-reply");
        await quitApp(ctx.handle);
        await sleep(1500);
        const during = await instanceState();
        const started = Date.now();
        await openApp(op.session);
        const ms = Date.now() - started;
        await dismissTourSoon(op);
        const after = await bounds(ctx.handle);
        const snap = await snapshot(op.page);
        const post = await runState(op);
        await settle(op, 240000);
        const fin = await runState(op);
        const shot = path.join(ctx.out, "shots", `s3-05-${round}.png`);
        await op.page.screenshot({ path: shot, timeout: 15000 }).catch(() => {});
        record({ scenario: `s3-05-${round}`, reopen_ms: ms, bounds_before: before, bounds_after: after, harness_while_closed: during, chars_at_close: pre.chars, title_before: title, title_after: snap.title, same_conversation: post.id === pre.id, articles_after: post.articles, pill_after_reopen: post.state, turns_before: pre.turns, turns_after: fin.turns, final_state: fin.apiState, final_chars_ui: fin.chars, final_chars_api: fin.apiChars, tail: fin.tail, shot });
        c.ok(pre.chars > 100 && pre.state === "running", `round ${round}: the close did not hit a live stream (${pre.chars} chars, ${pre.state})`);
        c.ok(during.harness, `round ${round}: the harness stopped when the app closed`);
        c.ok(JSON.stringify(before) === JSON.stringify(after), `round ${round}: window bounds changed ${JSON.stringify(before)} -> ${JSON.stringify(after)}`);
        c.ok(post.id === pre.id && post.articles > 0, `round ${round}: the conversation was not restored (id ${pre.id} -> ${post.id})`);
        c.ok(fin.turns === pre.turns && fin.apiState === "completed", `round ${round}: turns ${pre.turns} -> ${fin.turns}, run ${fin.apiState}`);
        c.ok(new RegExp(`(^|\\D)${round === "a" ? 200 : 250}(\\D|$)`).test(fin.tail) && fin.chars >= fin.apiChars - 5, `round ${round}: the final text is incomplete (UI ${fin.chars}, server ${fin.apiChars}, tail ${JSON.stringify(fin.tail)})`);
        ctx.convs.s305 = snap.title;
        // A short prompt after each reopen: send must work.
        await askOn(op, `s3-05-${round}-send`, "codex", `Reply with just the word REOPEN-${round.toUpperCase()}.`, new RegExp(`REOPEN-${round.toUpperCase()}`));
      }
      c.done();
    }, { lint: false });
  },
  "s3-06": async (op) => {
    await op.step("s3-06", "Now: S3-06, closing and opening the app three times with nothing running, then one prompt per conversation", async () => {
      const c = soft();
      const names = [ctx.convs.s304, ctx.convs.s305].filter(Boolean);
      if (names.length < 2) throw new Error("S3-06 needs the conversations of S3-04 and S3-05 in the same run");
      await op.click(row(op, names[0].slice(0, 30)));
      await op.until(async () => (await snapshot(op.page)).title === names[0], "the first conversation did not open", 15000);
      await op.page.evaluate(() => { const m = document.getElementById("messages"); let e = m; while (e && e.scrollHeight <= e.clientHeight + 2) e = e.parentElement; (e || m).scrollTop = Math.round(((e || m).scrollHeight - (e || m).clientHeight) / 2); });
      await sleep(600);
      const start = await scrollInfo(op.page);
      record({ scenario: "s3-06-start", title: names[0], ...start });
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
        const snap = await snapshot(op.page);
        const sc = await scrollInfo(op.page);
        opens.push(ms);
        record({ scenario: "s3-06-open", n: i, open_ms: ms, bounds_before: before, bounds_after: after, title: snap.title, ...sc });
        c.ok(JSON.stringify(before) === JSON.stringify(after), `window bounds changed on reopen ${i}`);
        c.ok(snap.title === names[0], `reopen ${i}: the selected conversation is "${snap.title}", expected "${names[0]}"`);
        c.ok(Math.abs(sc.top - start.top) <= 40 || (sc.bottom && start.bottom), `reopen ${i}: scroll position ${sc.top} of ${sc.max}, was ${start.top} of ${start.max}`);
        if (snap.title !== names[0]) {
          await op.click(row(op, names[0].slice(0, 30))).catch(() => {});
          await sleep(800);
        }
      }
      const shot = path.join(ctx.out, "shots", "s3-06-last-open.png");
      await op.page.screenshot({ path: shot, timeout: 15000 }).catch(() => {});
      record({ scenario: "s3-06", open_ms: opens, shot });
      await op.click(row(op, names[0].slice(0, 30)));
      await ask(op, "s3-06-a", "Reply with just the word ALPHA-OK.", /ALPHA-OK/);
      await op.click(row(op, names[1].slice(0, 30)));
      await op.until(async () => (await snapshot(op.page)).title === names[1], "the second conversation did not open", 15000);
      await ask(op, "s3-06-b", "Reply with just the word BETA-OK.", /BETA-OK/);
      c.done();
    }, { lint: false });
  },
  "s3-07": async (op) => {
    await op.step("s3-07", "Now: S3-07, restarting the harness from the admin, then continuing the conversation", async () => {
      const c = soft();
      await newChat(op);
      await chooseModel(op, "gpt-5.6-sol");
      await chooseEffort(op, "Medium");
      await askOn(op, "s3-07-plant", "codex", "Remember this code word: KESTREL-6613. Reply with one short sentence confirming it.", /KESTREL-6613/);
      const before = await runState(op);
      const title = (await snapshot(op.page)).title;
      const pidBefore = harnessPid();
      await op.caption("Now: restarting the harness from the admin");
      const t0 = Date.now();
      const stopped = await adminApi("POST", "/api/stop", {});
      await op.until(async () => !(await portOpen(Number(HARNESS_PORT))), "the harness did not stop", 30000);
      const started = await adminApi("POST", "/api/start", {});
      await op.until(() => portOpen(Number(HARNESS_PORT)), "the harness did not come back", 60000);
      const restartMs = Date.now() - t0;
      const pidAfter = harnessPid();
      // The open app should reconnect on its own; if it does not, record that and reload.
      let recovered = false;
      try { await op.until(async () => (await op.page.evaluate(() => !document.getElementById("startup-gate") || document.getElementById("startup-gate").hidden)) && (await runState(op)).turns === before.turns, "the app did not reconnect", 30000); recovered = true; } catch {}
      if (!recovered) {
        await op.page.reload({ waitUntil: "domcontentloaded" }).catch(() => {});
        await op.page.evaluate(METRICS_JS).catch(() => {});
        await op.page.locator("#startup-gate").waitFor({ state: "hidden", timeout: 40000 });
      }
      await dismissTourSoon(op);
      const back = await runState(op);
      const shot = path.join(ctx.out, "shots", "s3-07-restarted.png");
      await op.page.screenshot({ path: shot, timeout: 15000 }).catch(() => {});
      record({ scenario: "s3-07-restart", stop_status: stopped.status, start_status: started.status, restart_ms: restartMs, pid_before: pidBefore, pid_after: pidAfter, app_reconnected_alone: recovered, turns_before: before.turns, turns_after: back.turns, title_before: title, title_after: (await snapshot(op.page)).title, shot });
      c.ok(pidAfter && pidAfter !== pidBefore, `the harness pid did not change (${pidBefore} -> ${pidAfter})`);
      c.ok(back.turns === before.turns && back.articles >= before.articles, `history changed after the restart (${before.turns} -> ${back.turns} turns)`);
      await askOn(op, "s3-07-recall", "codex", "What code word did I give you earlier in this conversation? One short sentence.", /KESTREL-6613/);
      const resumed = ctx.last.evidence;
      record({ scenario: "s3-07-resume", milestones: resumed });
      await askOn(op, "s3-07-more", "codex", "Add a second code word: SWIFT-2047. Then list both code words in one line.", /(?=[\s\S]*KESTREL-6613)(?=[\s\S]*SWIFT-2047)/);
      const end = await runState(op);
      c.ok(end.turns === before.turns + 2 && end.apiState === "completed", `the continued turns are ${end.turns}, last ${end.apiState}`);
      c.done();
    }, { lint: false });
  },
  "s3-09": async (op) => {
    await op.step("s3-09", "Now: S3-09, DeepSeek Markdown, a table, code blocks and copy buttons", async () => {
      const c = soft();
      await newChat(op);
      await chooseModel(op, "deepseek-flash");
      await markdownChecks(op, c, "s3-09");
      c.done();
    }, { lint: false });
  },
  "s3-10": async (op) => {
    await op.step("s3-10", `Now: S3-10, DeepSeek in the project ${PROJECT_NAME}, three fact questions`, async () => {
      const c = soft();
      await newChat(op);
      await chooseProject(op, "Chat facts");
      await chooseModel(op, "deepseek-flash");
      try { await chooseAccessChecked(op, "Read only"); } catch (e) { record({ scenario: "s3-10-access", note: e.message }); }
      const cannot = /can't|cannot|can not|unable|not able|don't have|do not have|no access|not available|couldn't|could not|no file|not able to read|without access/i;
      const qs = [
        ["harbor", FACTS.harbor, "Read FACTS.md in this project and quote exactly the harbor name. If you cannot read files, say so plainly and do not guess."],
        ["lamp", FACTS.lamp, "What does FACTS.md say about the lanterns? Quote the count with its wording. If you cannot read files, say so plainly and do not guess."],
        ["code", FACTS.code, "What is the door code in FACTS.md? If you cannot read files, say so plainly and do not guess."],
      ];
      const outcome = [];
      for (const [key, fact, text] of qs) {
        await askOn(op, `s3-10-${key}`, "deepseek", text, /\S/);
        const reply = (await lastMeta(op)).text;
        const hit = reply.includes(fact);
        const says = cannot.test(reply);
        outcome.push({ key, hit, says });
        c.ok(hit || says, `the ${key} reply neither gives the fact nor says it cannot read it (invented?): ${reply.slice(0, 160)}`);
      }
      record({ scenario: "s3-10", outcome });
      c.done();
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

// Starts a long numbered reply (the tag keeps the conversation title unique for the sidebar lookups) and returns once it has streamed some text (so a reload or close lands mid-reply).
async function startLong(op, n) {
  await op.fill(op.page.locator("#prompt"), `Run ${Date.now().toString(36).slice(-5)}: write the numbers from 1 to ${n}, one per line, each followed by a different English word. Do not stop early and add nothing else.`);
  const before = await submit(op);
  await op.until(async () => (await op.page.locator("#messages").getByRole("button", { name: "View run" }).count()) > before, "the run did not start", 30000);
  await op.until(async () => (await streamLen(op)) > 120, "the long answer never started streaming", 90000);
}

// The open conversation as the page and the server hold it, for the reload and reopen checks.
async function runState(op) {
  return op.page.evaluate(async () => {
    let d = await (await fetch("/v1/conversations/" + encodeURIComponent(conversation))).json();
    if (!d.turns) { await new Promise((r) => setTimeout(r, 2500)); d = await (await fetch("/v1/conversations/" + encodeURIComponent(conversation))).json(); } // a 429 answer has no turns
    const last = d.turns[d.turns.length - 1] || {};
    const arts = [...document.querySelectorAll("#messages article.assistant")];
    const text = arts.length ? arts[arts.length - 1].querySelector(":scope > .text")?.innerText || "" : "";
    const api = last.result?.answer ?? last.result?.partial_answer ?? "";
    return {
      id: conversation, turns: d.turns.length, users: document.querySelectorAll("#messages article.user").length,
      articles: document.querySelectorAll("#messages article").length, state: document.getElementById("conversation-state-pill")?.dataset.state || "",
      apiState: last.state, chars: text.length, apiChars: api.length, tail: text.trim().slice(-40),
    };
  });
}

// Waits until the newest run has left running and queued, as the server and the pill both say.
async function settle(op, timeout) {
  const end = Date.now() + timeout;
  for (;;) {
    const s = await runState(op);
    if (!["running", "queued"].includes(s.apiState || "") && !["running", "queued"].includes(s.state)) break;
    if (Date.now() > end) throw new Error("the run did not settle in time");
    await sleep(2000); // slow poll: the harness answers 429 to a tight loop on /v1/conversations/:id
  }
  await sleep(800);
}

// Scroll state of the messages area (the first scrollable ancestor of #messages).
const scrollInfo = (page) => page.evaluate(() => {
  let e = document.getElementById("messages");
  while (e && e.scrollHeight <= e.clientHeight + 2) e = e.parentElement;
  if (!e) return { top: 0, max: 0, bottom: true };
  const max = e.scrollHeight - e.clientHeight;
  return { top: Math.round(e.scrollTop), max, bottom: max - e.scrollTop < 4 };
});

// Whether the instance is alive while the app is closed (ports only; the harness answers on its port).
async function instanceState() {
  return { admin: await portOpen(Number(ADMIN_PORT)), harness: await portOpen(Number(HARNESS_PORT)) };
}

async function chooseEffort(op, label) {
  await op.click(op.page.locator("#effort-trigger"));
  const option = op.page.locator("#effort-menu").getByRole("option", { name: new RegExp("^\\W*" + label) });
  if (!(await option.count())) throw new Error(`this model offers no ${label} effort`);
  await op.click(option.first());
  await op.seeText(op.page.locator("#effort-label"), new RegExp(label));
}

// Ask on a known provider and record which model the footer and the server say answered.
async function askOn(op, name, provider, text, pattern) {
  const answer = await ask(op, name, text, pattern);
  if (op.area.halted) throw new Error("a provider limit text was seen, stopping");
  const meta = await lastMeta(op);
  const turns = await turnsApi(op).catch(() => []);
  const last = turns[turns.length - 1] || {};
  record({ scenario: `${name}-meta`, provider, footer: meta.meta, request_model: last.model, request_effort: last.effort, state: last.state });
  return answer;
}

// Switches the model picker inside an open conversation and reports whether that is allowed,
// and, when it is not, whether the app says why in plain words.
async function switchModel(op, id) {
  const trigger = op.page.locator("#model-trigger");
  const before = await op.page.evaluate(() => ({ disabled: document.getElementById("model-trigger")?.disabled, title: document.getElementById("model-trigger")?.title || "", model: document.getElementById("model")?.value }));
  let error = null;
  if (!before.disabled) await chooseModel(op, id).catch((e) => (error = e.message));
  const after = await op.page.evaluate(() => ({ model: document.getElementById("model")?.value, notice: [...document.querySelectorAll('[role="alert"], .toast, #composer-notice, .notice')].map((n) => n.innerText).join(" | ").slice(0, 300), disabled: document.getElementById("model-trigger")?.disabled }));
  const shot = path.join(ctx.out, "shots", `switch-to-${id}.png`);
  await op.page.screenshot({ path: shot, timeout: 15000 }).catch(() => {});
  const allowed = after.model === id;
  const words = `${before.title} ${after.notice} ${error || ""}`;
  return { allowed, from: before.model, to: id, disabled: before.disabled, title: before.title, notice: after.notice, error, clear: allowed || /conversation|new chat|start|locked|cannot|can't|switch|same/i.test(words), shot };
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

// Access picker with a loud failure: the label must read the requested access afterwards.
async function chooseAccessChecked(op, label) {
  await chooseAccess(op, label);
  const shown = (await op.page.locator("#access-label").innerText()).trim();
  if (!new RegExp(label, "i").test(shown)) throw new Error(`the access picker reads "${shown}", not ${label}`);
}

// Attaches files from the campaign's attach folder through the file chooser and waits for the chips.
async function attachFiles(op, c, names, expected = names.length) {
  const dir = path.join(paths.projects, "attach");
  const [chooser] = await Promise.all([op.page.waitForEvent("filechooser", { timeout: 10000 }), op.click(op.page.locator("#attach"))]);
  await chooser.setFiles(names.map((n) => path.join(dir, n)));
  await op.until(async () => (await op.page.locator("#attachments .attachment").count()) >= expected, "the attachment chips did not appear", 30000);
  const chips = await op.page.locator("#attachments .attachment .attachment-name").allInnerTexts();
  c.ok(names.every((n) => chips.includes(n)), `chips ${JSON.stringify(chips)} lack one of ${JSON.stringify(names)}`);
}

// Reads Codex rollouts written since a time: only approval_policy, sandbox type and tool-call names (never auth.json).
function rolloutEvidence(since) {
  const root = path.join(paths.state, "providers/home/.codex/sessions");
  const out = { files: 0, approval: [], sandbox: [], tools: [] };
  const walk = (dir) => {
    for (const e of fs.existsSync(dir) ? fs.readdirSync(dir, { withFileTypes: true }) : []) {
      const f = path.join(dir, e.name);
      if (e.isDirectory()) walk(f);
      else if (e.name.endsWith(".jsonl") && fs.statSync(f).mtimeMs >= since) {
        out.files += 1;
        for (const line of fs.readFileSync(f, "utf8").split("\n")) {
          let j;
          try { j = JSON.parse(line); } catch { continue; }
          const p = j.payload || {};
          if (j.type === "turn_context") {
            out.approval.push(p.approval_policy);
            out.sandbox.push(p.sandbox_policy?.type ?? p.sandbox_policy);
          } else if (j.type === "response_item" && /call$/.test(p.type || "")) out.tools.push(p.name || p.type);
        }
      }
    }
  };
  walk(root);
  for (const k of ["approval", "sandbox", "tools"]) out[k] = [...new Set(out[k])];
  return out;
}

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

// ---- slice 4b helpers (S4-02 to S4-08)

const sol = async (op) => {
  if (!/sol/i.test(await op.page.locator("#model-label").innerText())) await chooseModel(op, "gpt-5.6-sol");
  if (!/medium/i.test(await op.page.locator("#effort-label").innerText())) await chooseEffort(op, "Medium");
};
const convId = (op) => op.page.evaluate(() => conversation);
// A bounded wait that answers true or false instead of throwing, so a soft check can report it.
async function waitFor(fn, ms = 8000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (await Promise.resolve(fn()).catch(() => false)) return true;
    await sleep(300);
  }
  return false;
}
const rowById = (op, id) => op.page.locator("#sidebar .conversation-row").filter({ has: op.page.locator(`button[data-conversation-id="${id}"]`) }).first();
async function rowMenu(op, id, label) {
  const r = rowById(op, id);
  await r.hover();
  await op.click(r.locator("summary"));
  await op.click(r.getByRole("button", { name: label }));
}
// Status dot label of a sidebar row ("In progress", "Unread response", ... or "" when there is none).
const rowDot = (op, id) => op.page.evaluate((x) => document.querySelector(`#sidebar button[data-conversation-id="${x}"]`)?.querySelector(".conversation-indicator")?.getAttribute("aria-label") || "", id);
const apiStatus = (op, route) => op.page.evaluate(async (r) => { const x = await fetch(r); return { status: x.status, json: x.ok ? await x.json().catch(() => null) : null }; }, route);
const listApi = (op, query = "") => op.page.evaluate(async (q) => ((await (await fetch("/v1/conversations" + q)).json()).conversations || []).map((x) => ({ id: x.id, title: x.title })), query);
const shotOf = async (op, name) => {
  const file = path.join(ctx.out, "shots", name);
  await op.page.screenshot({ path: file, timeout: 15000 }).catch(() => {});
  return file;
};
// What a person sees after a send: header pill, status line, alerts and the newest message.
const visibleState = (op) => op.page.evaluate(() => ({
  pill: document.getElementById("conversation-state-pill")?.innerText || "",
  state: document.getElementById("conversation-state-pill")?.dataset.state || "",
  status: document.getElementById("status")?.innerText || "",
  alerts: [...document.querySelectorAll('[role="alert"]')].map((e) => e.innerText.trim()).filter(Boolean).join(" | "),
  last: ([...document.querySelectorAll("#messages article")].pop()?.innerText || "").replace(/\s+/g, " ").slice(0, 400),
  users: document.querySelectorAll("#messages article.user").length,
  articles: document.querySelectorAll("#messages article").length,
  resume: !!document.querySelector("#resume-execution:not([hidden])"),
}));
const RAW_TEXT = /Traceback|File ".*", line \d+|\bat [\w.$<>]+ \(|\.py:\d+|\.js:\d+|node_modules|\[object Object\]|\bundefined\b|\bNaN\b|"detail"|^\s*[{[]/m;

// Keyboard helpers: the focused element with its focus ring, and Tab (then Shift+Tab) until a test matches.
const focusState = (op) => op.page.evaluate(() => {
  const e = document.activeElement || document.body;
  let ring = false;
  for (let n = e, i = 0; n && i < 3 && !ring; n = n.parentElement, i++) {
    const s = getComputedStyle(n);
    ring = (s.outlineStyle !== "none" && parseFloat(s.outlineWidth) > 0) || s.boxShadow !== "none";
  }
  return { id: e.id, tag: e.tagName.toLowerCase(), name: (e.getAttribute("aria-label") || e.innerText || e.title || "").trim().replace(/\s+/g, " ").slice(0, 50), ring, inModelMenu: !!e.closest("#model-menu"), inResourceMenu: !!e.closest("#resource-menu") };
});
async function reachKey(op, test, arg, max = 45) {
  for (const key of ["Tab", "Shift+Tab"]) {
    for (let i = 1; i <= max; i++) {
      await op.page.keyboard.press(key);
      await sleep(70);
      if (await op.page.evaluate(test, arg)) return { steps: i, key };
    }
    await op.page.keyboard.press("Control+/"); // back to the composer, then try the other direction
    await sleep(200);
  }
  return { steps: -1, key: "" };
}

// Window size through Electron, with the display work area for the wide size.
const setWindow = (rect) => ctx.handle.app.evaluate(({ BrowserWindow, screen }, [base, r]) => {
  const w = BrowserWindow.getAllWindows().find((x) => x.webContents.getURL().startsWith(base));
  const area = screen.getPrimaryDisplay().workArea;
  if (r) w.setBounds(r);
  return { bounds: w.getBounds(), area };
}, [harnessUrl, rect]);
// Horizontal overflow, the composer, and the code blocks and tables of the newest reply.
const layoutProbe = (op) => op.page.evaluate(() => {
  const de = document.documentElement;
  const clipped = (e) => { for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) { if (/auto|scroll|hidden/.test(getComputedStyle(p).overflowX) && p.getBoundingClientRect().right <= innerWidth + 1) return true; } return false; };
  const spill = [...document.querySelectorAll("body *")].filter((e) => {
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && r.right > innerWidth + 1 && s.visibility !== "hidden" && s.position !== "fixed" && !e.closest("[popover]:not(:popover-open), dialog:not([open]), [hidden], #operator-caption") && !clipped(e);
  }).slice(0, 6).map((e) => `${e.tagName.toLowerCase()}#${e.id}.${String(e.className).slice(0, 30)}`);
  const box = (s) => { const e = document.querySelector(s), r = e?.getBoundingClientRect(); return r ? { left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top), bottom: Math.round(r.bottom), visible: r.width > 0 && r.height > 0 } : null; };
  const a = [...document.querySelectorAll("#messages article.assistant")].pop();
  return {
    inner: [innerWidth, innerHeight], docScroll: de.scrollWidth, bodyScroll: document.body.scrollWidth, spill,
    prompt: box("#prompt"), send: box("#send"),
    code: [...(a?.querySelectorAll(".code-block pre") || [])].map((p) => ({ scrollW: p.scrollWidth, clientW: p.clientWidth, overflowX: getComputedStyle(p).overflowX, right: Math.round(p.getBoundingClientRect().right) })),
    tables: [...(a?.querySelectorAll("table") || [])].map((t) => ({ right: Math.round(t.getBoundingClientRect().right), wrapScrolls: [t.parentElement, t.parentElement?.parentElement].some((p) => p && /auto|scroll/.test(getComputedStyle(p).overflowX)) })),
  };
});
// Colors of the visible reply and its code and table, with WCAG contrast against the first opaque background.
const themeProbe = (op) => op.page.evaluate(() => {
  // color(srgb r g b / a) carries 0..1 channels; rgb() carries 0..255.
  const nums = (s) => { const n = (s.match(/[\d.]+/g) || []).map(Number); return /^color\(srgb/.test(s) ? n.map((v, i) => (i < 3 ? v * 255 : v)) : n; };
  const dark = document.documentElement.dataset.theme === "dark";
  const bgOf = (el) => { for (let e = el; e; e = e.parentElement) { const [r, g, b, a = 1] = nums(getComputedStyle(e).backgroundColor); if (a > 0.9) return [r, g, b]; } return dark ? [0, 0, 0] : [255, 255, 255]; };
  const lum = ([r, g, b]) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return +((x + 0.05) / (y + 0.05)).toFixed(2); };
  const art = [...document.querySelectorAll("#messages article.assistant")].pop();
  const item = (el) => { if (!el) return null; const fg = nums(getComputedStyle(el).color).slice(0, 3), bg = bgOf(el); return { fg, bg, ratio: ratio(fg, bg) }; };
  const pick = (sel) => art?.querySelector(sel) || null;
  return {
    theme: document.documentElement.dataset.theme, palette: document.documentElement.dataset.palette, stored: localStorage.getItem("keepharness:theme:harness"),
    text: item(pick(":scope > .text p") || pick(":scope > .text")), heading: item(pick("h2, h3")), code: item(pick(".code-block pre code") || pick("pre code")),
    codeBg: pick(".code-block pre") ? bgOf(pick(".code-block pre")) : null, th: item(pick("th")), td: item(pick("td")),
    tableBorder: pick("td") ? getComputedStyle(pick("td")).borderBottomColor : null, pageBg: bgOf(document.getElementById("messages") || document.body),
    sidebar: item(document.querySelector("#sidebar button[data-conversation-id]")), composer: item(document.getElementById("prompt")),
  };
});

// Long answer, Stop mid-reply, then a new question (S2-03 on Codex, S3-08 on DeepSeek).
async function stopScenario(op, id, model, effort) {
  await op.step(id, `Now: ${id}, a very long answer stopped mid-reply on ${model}, then a new question`, async () => {
    const c = soft();
    await newChat(op);
    await chooseModel(op, model);
    if (effort) await chooseEffort(op, effort);
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
    const shot = path.join(ctx.out, "shots", `${id}-stopped.png`);
    await page.screenshot({ path: shot, timeout: 15000 }).catch(() => {});
    const win = ctx.samples.filter((s) => s.t >= from);
    record({ scenario: id, model, stop_halt_ms: haltMs, pill_cancelled_ms: tPill, chars_at_stop: len0, chars_final: len1, pill: after.pill, article: after.article, electron_rss_mb: Math.max(...win.map((s) => s.electron_rss), 0), harness_rss_mb: Math.max(...win.map((s) => s.harness_rss), 0), shot });
    c.ok(haltMs < 3000, `the stream took ${haltMs} ms to halt after Stop`);
    c.ok(len1 >= len0 && len1 > 100, `the partial text was not kept (${len0} chars at Stop, ${len1} after)`);
    c.ok(/Cancel|Stopp/i.test(after.pill) || /cancel|stopp/i.test(after.article), `the reply is not marked stopped: pill "${after.pill}"`);
    c.ok(!["running", "queued"].includes(after.state), `the pill is still ${after.state} after Stop (ghost stream?)`);
    c.ok(after.cancelHidden === true, "the Stop button is still shown after the stop");
    await ask(op, `${id}-next`, "Reply with just the word NEXT-OK.", /NEXT-OK/);
    c.ok(/NEXT-OK/.test((await lastMeta(op)).text), "the next send did not answer");
    c.done();
  }, { lint: false });
}

// Markdown, a table, two code blocks and their copy buttons (S1-02 on Codex, S3-09 on DeepSeek).
async function markdownChecks(op, c, id) {
  await ask(op, `${id}-md`, "Reply in Markdown with exactly: a level-2 heading, a level-3 heading, a bullet list of 3 items, a numbered list of 3 items and a table of 3 columns (Island, Harbor, Lanterns) with 3 rows of invented data. No code blocks, no other text.", /\S/);
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
  record({ scenario: `${id}-md`, md });
  await ask(op, `${id}-two-langs`, "Give two short code blocks, one Python and one JavaScript, each a function with a loop and an if inside it, indented with 4 spaces. Only the two code blocks, no other text.", /\S/);
  const two = await codeBlocks(op);
  c.ok(two.length >= 2 && new Set(two.map((b) => b.lang)).size >= 2, `expected 2 languages, got ${JSON.stringify(two.map((b) => b.lang))}`);
  c.ok(two.every((b) => /\n {4}\S/.test(b.text)), "a code block lost its indentation");
  await copyCheck(op, c, 0, "first code block");
  await copyCheck(op, c, 1, "second code block");
}


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
  ctx = { home: paths.home, tmp: fs.mkdtempSync(path.join(os.tmpdir(), "claude-kh-")), out, samples: [], lines: [], turns: 0, electronPid: 0, convs: {} };
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
