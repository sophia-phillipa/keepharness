// The operator: runs narrated steps against one page, the way a person would.
// Each step shows a caption, highlights what it touches, asserts an outcome, takes a
// screenshot and records the layout-lint findings for that screen.
"use strict";
const fs = require("node:fs");
const path = require("node:path");

// The shared lint expression from the layout-lint gate (tests/support/layout-lint.js).
const LINT = fs.readFileSync(path.join(__dirname, "../../support/layout-lint.js"), "utf8");
const LINT_KINDS = ["overlaps", "spills", "beyond", "fields"];

// The harness accepts 12 submitted messages a minute per identity; stay under it.
const SUBMISSIONS_PER_MINUTE = 10;

class SkipStep extends Error {}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

class Operator {
  constructor({ session, options, known, report, fixture }) {
    this.session = session;
    this.options = options;
    this.known = known;
    this.report = report;
    this.fixture = fixture;
    this.budget = options.budget;
    this.area = null;
    this.shot = 0;
    this.submissions = [];
    this.sent = 0;
    this.rateLimited = 0;
    this.limitedPaths = {};
    this.retryAt = 0;
    this.watched = new WeakSet();
  }

  // Record 429 answers so a step can say it was throttled, and the next one waits it out.
  watch(page) {
    if (!page || this.watched.has(page)) return;
    this.watched.add(page);
    page.on("response", (response) => {
      if (response.status() !== 429) return;
      this.rateLimited++;
      const where = new URL(response.url()).pathname.replace(/[0-9a-f]{32}/g, ":id");
      this.limitedPaths[where] = (this.limitedPaths[where] || 0) + 1;
      const seconds = Number(response.headers()["retry-after"]) || 5;
      this.retryAt = Math.max(this.retryAt, Date.now() + Math.min(seconds, 60) * 1000);
    });
  }

  async paceSubmission() {
    const now = Date.now();
    this.submissions = this.submissions.filter((t) => now - t < 60000);
    if (this.submissions.length >= SUBMISSIONS_PER_MINUTE) {
      const wait = 60500 - (now - this.submissions[0]);
      await this.caption(`Waiting ${Math.ceil(wait / 1000)} s: the server takes ${SUBMISSIONS_PER_MINUTE + 2} messages a minute`);
      await sleep(wait);
    }
    this.submissions.push(Date.now());
    this.sent++;
  }

  get page() {
    return this.session.page;
  }
  get visible() {
    return this.options.visible;
  }
  get fixtureMode() {
    return this.options.mode === "fixture";
  }

  // ------------------------------------------------------------------ narration

  async caption(text) {
    if (!this.visible) return;
    await this.page
      .evaluate((value) => {
        let c = document.getElementById("operator-caption");
        if (!c) {
          c = document.createElement("div");
          c.id = "operator-caption";
          c.setAttribute("aria-hidden", "true");
          Object.assign(c.style, {
            position: "fixed", left: "50%", bottom: "40px", transform: "translateX(-50%)",
            zIndex: 2147483647, pointerEvents: "none", background: "rgba(255,59,48,.92)",
            color: "#fff", font: "600 14px/1.35 system-ui, sans-serif", padding: "7px 14px",
            borderRadius: "999px", boxShadow: "0 2px 10px rgba(0,0,0,.3)", maxWidth: "80vw",
            whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
          });
          document.documentElement.appendChild(c);
        }
        c.textContent = "Claude: " + value;
      }, text)
      .catch(() => {});
  }

  async pause(ms = this.options.pace) {
    // Display pacing for a human watcher only; synchronization never relies on it.
    if (ms > 0) await sleep(ms);
  }

  async highlight(locator) {
    if (!this.visible) return;
    await locator
      .evaluate((el) => {
        el.scrollIntoView({ block: "nearest", inline: "nearest" });
        const previous = [el.style.outline, el.style.outlineOffset];
        el.style.outline = "3px solid #ff3b30";
        el.style.outlineOffset = "2px";
        setTimeout(() => ([el.style.outline, el.style.outlineOffset] = previous), 1100);
      })
      .catch(() => {});
    await this.pause();
  }

  // ------------------------------------------------------------------ actions

  async click(locator, options = {}) {
    await locator.waitFor({ state: "visible", timeout: options.timeout || 10000 });
    await this.highlight(locator);
    await locator.click({ timeout: options.timeout || 10000, ...options.click });
    await this.pause(this.options.pace / 2);
  }

  async fill(locator, text) {
    await locator.waitFor({ state: "visible", timeout: 10000 });
    await this.highlight(locator);
    // Typing "09:00" into a 12-hour time field leaves its AM/PM segment empty (no value at all),
    // so segmented date and time inputs take the value in one step even when visible.
    const segmented = await locator.evaluate((el) => /^(time|date|datetime-local|month|week)$/.test(el.type || ""));
    if (this.visible && !segmented) {
      await locator.click();
      await locator.fill("");
      await locator.pressSequentially(text, { delay: 28 });
    } else await locator.fill(text);
    await this.pause(this.options.pace / 2);
  }

  // Visible: key by key, for the watcher. Headless: one input event, so a palette that
  // refetches on every keystroke does not exhaust the server's request budget.
  async type(text) {
    if (this.visible || /[\n\t]/.test(text)) await this.page.keyboard.type(text, { delay: this.visible ? 40 : 0 });
    else await this.page.keyboard.insertText(text);
  }

  async press(key) {
    await this.page.keyboard.press(key);
    await this.pause(this.options.pace / 2);
  }

  async select(locator, value) {
    await this.highlight(locator);
    await locator.selectOption(value);
    await this.pause(this.options.pace / 2);
  }

  // ------------------------------------------------------------------ assertions

  async see(locator, timeout = 10000) {
    await locator.first().waitFor({ state: "visible", timeout });
  }

  async gone(locator, timeout = 10000) {
    await locator.first().waitFor({ state: "hidden", timeout });
  }

  async seeText(locator, pattern, timeout = 15000) {
    const regex = pattern instanceof RegExp ? pattern : new RegExp(escape(pattern));
    const deadline = Date.now() + timeout;
    let last = "";
    while (Date.now() < deadline) {
      last = await locator.first().innerText({ timeout: 2000 }).catch(() => "");
      if (regex.test(last)) return last;
      await this.resumeTracking();
      await sleep(150);
    }
    throw new Error(`expected ${regex} in text, got: ${JSON.stringify(last.slice(0, 200))}`);
  }

  async until(predicate, message, timeout = 15000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      if (await predicate().catch(() => false)) return;
      await this.resumeTracking();
      await sleep(150);
    }
    throw new Error(message);
  }

  // After a lost connection the app offers "Resume tracking"; a person would press it.
  async resumeTracking() {
    const button = this.page?.locator("#resume-execution");
    if (!button || !(await button.isVisible().catch(() => false))) return;
    await button.click().catch(() => {});
    this.resumed = (this.resumed || 0) + 1;
  }

  check(condition, message) {
    if (!condition) throw new Error(message);
  }

  skip(reason) {
    throw new SkipStep(reason);
  }

  // Real providers cost money: every prompt in real mode draws from --budget.
  spendPrompt() {
    if (this.fixtureMode) return;
    if (this.budget <= 0) this.skip("real-mode prompt budget exhausted");
    this.budget -= 1;
  }

  // ------------------------------------------------------------------ steps

  async runArea(area) {
    // A throttled area leaves the server's one-minute window spent; let it recover first.
    if (this.rateLimited > (this.limitedAtAreaStart || 0)) {
      await this.caption("Letting the server's request window recover");
      await sleep(30000);
    }
    this.limitedAtAreaStart = this.rateLimited;
    this.area = this.report.area(area);
    try {
      await area.run(this);
    } catch (error) {
      if (!(error instanceof SkipStep)) this.area.error = String(error.message || error).split("\n")[0];
    }
  }

  // id: stable within the area (used by the known-defects allow-list).
  // options: fixtureOnly, realOnly, writes (mutates state; real mode needs --allow-writes),
  //          target ("browser"/"desktop" only), lint (false to skip), critical (skip the rest
  //          of the area on failure), always (run even after a critical failure: cleanup),
  //          recover (false: keep the screen after a failure).
  async step(id, caption, action, options = {}) {
    const key = this.area.id + "." + id;
    const record = this.area.step(key, caption);
    const started = Date.now();
    if (this.area.halted && !options.always) return record.finish("skipped", "an earlier critical step failed");
    if (options.fixtureOnly && !this.fixtureMode) return record.finish("skipped", "fixture mode only");
    if (options.realOnly && this.fixtureMode) return record.finish("skipped", "real mode only");
    if (options.writes && !this.fixtureMode && !this.options.allowWrites)
      return record.finish("skipped", "changes state; real mode needs --allow-writes");
    if (options.target && options.target !== this.session.target)
      return record.finish("skipped", `${options.target} target only`);
    this.watch(this.page);
    if (Date.now() < this.retryAt) {
      await this.caption("Waiting for the server's rate limit to reset");
      await sleep(this.retryAt - Date.now());
    }
    const limitedBefore = this.rateLimited;
    const sentBefore = this.sent;
    this.resumed = 0;
    this.limitedPaths = {};
    await this.caption(caption);
    let { status, detail } = await this.attempt(action);
    // The server throttled the page during a failed step: wait it out and try once more,
    // unless the step already sent a message (a retry would send it twice).
    if (status === "fail" && this.rateLimited > limitedBefore && this.sent === sentBefore) {
      await this.caption("The server asked to slow down; trying this step again");
      await sleep(Math.max(0, this.retryAt - Date.now()) + 500);
      const second = await this.attempt(action);
      status = second.status;
      detail = (second.detail ? second.detail + " · " : "") + "retried after a 429";
    }
    const known = this.known.steps[key];
    if (known && status === "fail") status = "known";
    else if (known && status === "pass") record.stale = known.id;
    record.known = known;
    record.rateLimited = this.rateLimited - limitedBefore;
    if (this.resumed) detail = `${detail ? detail + " · " : ""}pressed Resume tracking ${this.resumed}x after a lost connection`;
    if (record.rateLimited) {
      const paths = Object.entries(this.limitedPaths).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([p, n]) => `${p} ${n}x`).join(", ");
      detail = `${detail ? detail + " · " : ""}server answered 429 ${record.rateLimited}x during this step (${paths})`;
    }
    record.ms = Date.now() - started;
    await this.capture(record, options);
    record.finish(status, detail);
    // A known defect is an expected assertion failure: leave the screen as it is.
    if (status === "fail") {
      if (options.critical) this.area.halted = true;
      if (options.recover !== false) await this.recover();
    }
    this.log(record);
    await this.pause();
    return record;
  }

  async attempt(action) {
    try {
      await action(this.page);
      return { status: "pass", detail: "" };
    } catch (error) {
      if (error instanceof SkipStep) return { status: "skipped", detail: error.message };
      return { status: "fail", detail: String(error.message || error).split("\n").slice(0, 3).join(" ") };
    }
  }

  async capture(record, options) {
    const page = this.page;
    if (!page || page.isClosed()) return;
    const name = `${String(++this.shot).padStart(3, "0")}-${record.id.replace(/[^a-z0-9.-]+/gi, "-")}.jpg`;
    await page
      .screenshot({ path: path.join(this.report.dir, "shots", name), type: "jpeg", quality: 70, timeout: 10000 })
      .then(() => (record.shot = "shots/" + name))
      .catch(() => {});
    if (options.lint === false) return;
    const lint = await page.evaluate(LINT).catch((error) => ({ error: String(error.message).split("\n")[0] }));
    record.lint = this.classifyLint(lint);
  }

  classifyLint(lint) {
    if (lint.error) return { error: lint.error, counts: {}, items: [] };
    const items = [];
    for (const kind of LINT_KINDS)
      for (const item of lint[kind] || []) {
        const text = JSON.stringify(item);
        const known = this.known.lint.find((rule) => rule.kinds.includes(kind) && new RegExp(rule.match).test(text));
        items.push({ kind, known: known?.id || null, summary: lintSummary(kind, item) });
      }
    const counts = {};
    for (const item of items) counts[item.kind] = (counts[item.kind] || 0) + 1;
    return { meta: lint.meta, counts, items: items.slice(0, 40), unexpected: items.filter((i) => !i.known).length };
  }

  // After a failure: close whatever is open so the next step starts from a calm screen.
  async recover() {
    const page = this.page;
    if (!page || page.isClosed()) return;
    for (let i = 0; i < 3; i++) await page.keyboard.press("Escape").catch(() => {});
    await page
      .evaluate(() => {
        for (const dialog of document.querySelectorAll("dialog[open]")) dialog.close();
        for (const popover of document.querySelectorAll("[popover]")) {
          try {
            popover.hidePopover();
          } catch {}
        }
      })
      .catch(() => {});
  }

  log(record) {
    const mark = { pass: "PASS", fail: "FAIL", known: "KNOWN", skipped: "SKIP" }[record.status];
    const extra = record.status === "known" ? ` (${record.known.id})` : record.detail ? ` — ${record.detail}` : "";
    const lint = record.lint?.unexpected ? ` [lint ${record.lint.unexpected}]` : "";
    console.log(`${mark.padEnd(5)} ${record.id}: ${record.caption}${extra}${lint}`);
  }
}

function lintSummary(kind, item) {
  if (kind === "overlaps") return `${item.a} "${item.aLabel}" overlaps ${item.b} "${item.bLabel}" by ${item.overlap.w}x${item.overlap.h}px`;
  if (kind === "spills") return `${item.sel} ${item.kind} ${item.excess.x}x${item.excess.y}px (${item.culprit})`;
  if (kind === "beyond") return `${item.sel} ${item.kind} ${item.container} by ${item.excess}px`;
  return `${item.sel} text ${item.textWidth}px in ${item.availWidth}px, deficit ${item.deficit}px`;
}

function escape(text) {
  return String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

module.exports = { Operator };
