// D-031: Retry on the latest failed or interrupted turn, and the "Retried" history marker.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");

const MODELS = [
  { id: "gpt-6-astra", name: "Astra", backend: "codex", efforts: ["medium"], permissions: { upload: true }, capabilities: { images: true } },
  { id: "deepseek-flash", name: "DeepSeek", backend: "deepseek", efforts: ["configured"], permissions: { upload: true }, capabilities: { images: false } },
];
const turn = (id, state, extra = {}) => ({
  id, project: "sem-projeto", state, attachments: [],
  request: { prompt: "Prompt " + id, backend: "codex", model: "gpt-6-astra", effort: "medium", execution_mode: "native", ...extra.request },
  result: state === "completed" ? { answer: "Done" } : { error: "fixture_failure", partial_answer: "partial" },
  ...(extra.turn || {}),
});

async function fixture(browser, turns) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  page.setDefaultTimeout(5000);
  const state = { turns, retries: 0, loads: 0, gets: [], retryReply: { status: 202, json: { job_id: "t2", reused: false } } };
  await page.route("http://panel.test/**", (route) => {
    const pathname = new URL(route.request().url()).pathname;
    return route.fulfill({
      path: path.join(__dirname, "..", pathname.startsWith("/assets/") ? "harness_ui" : "agent_service", pathname === "/" ? "index.html" : pathname),
    });
  });
  await page.route("**/v1/**", (route) => {
    const url = new URL(route.request().url()), p = url.pathname;
    if (p.endsWith("/retry")) {
      state.retries++;
      return new Promise((resolve) => setTimeout(resolve, 150)).then(() => route.fulfill(state.retryReply));
    }
    if (p.startsWith("/v1/conversations/")) state.gets.push(p);
    if (p === "/v1/conversations/c1") { state.loads++; return route.fulfill({ json: { id: "c1", title: "Chat", turns: state.turns } }); }
    const data = p === "/v1/projects" ? { projects: ["sem-projeto"], details: {} }
      : p === "/v1/models" ? { models: MODELS, uploads_enabled: true }
      : p === "/v1/conversations" ? { conversations: [{ id: "c1", title: "Chat", project: "sem-projeto", state: "failed", last_job_id: "t1" }] }
      : {};
    return route.fulfill({ json: data });
  });
  await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
  await page.goto("http://panel.test/");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  const open = () => page.evaluate(() => load("c1"));
  return { page, state, open };
}

(async () => {
  const browser = await chromium.launch();
  try {
    const retry = (page) => page.getByTestId("turn-retry");

    // Failed latest turn: one Retry, one run for a double click, history shows the marker.
    let f = await fixture(browser, [turn("t0", "completed"), turn("t1", "failed")]);
    await f.open();
    assert.equal(await retry(f.page).count(), 1, "Retry on the latest failed turn");
    assert.equal(await retry(f.page).innerText(), "Retry");
    assert.equal(await f.page.locator(".turn-retry-actions [role=status]").count(), 1);
    await f.page.evaluate(() => { $("prompt").value = "Prompt t1"; });
    f.state.turns = [turn("t0", "completed"), turn("t1", "failed"), turn("t2", "completed", { request: { retry_of: "t1" } })];
    await retry(f.page).dblclick();
    await f.page.waitForFunction(() => document.querySelectorAll('[data-testid="turn-retried"]').length === 1);
    assert.equal(f.state.retries, 1, "double click starts one run");
    assert.equal(await retry(f.page).count(), 0, "no Retry once the retry completed");
    assert.match(await f.page.getByTestId("turn-retried").innerText(), /Retried/);
    assert.equal(await f.page.locator("#messages .message.user").count(), 2, "retry turn repeats no prompt bubble");
    assert.equal(await f.page.evaluate(() => $("prompt").value), "", "restored prompt cleared");
    await f.page.close();

    // Interrupted latest turn also offers Retry.
    f = await fixture(browser, [turn("t1", "interrupted")]);
    await f.open();
    assert.equal(await retry(f.page).count(), 1);
    await f.page.close();

    // Cancelled, workflow and non-latest turns do not.
    f = await fixture(browser, [turn("t1", "cancelled")]);
    await f.open();
    assert.equal(await retry(f.page).count(), 0, "no Retry on cancelled");
    await f.page.close();
    f = await fixture(browser, [turn("t1", "failed", { turn: { workflow_checkpoint: true, workflow_completed_steps: 1 } })]);
    await f.open();
    assert.equal(await retry(f.page).count(), 0, "no Retry on workflow turns");
    assert.equal(await f.page.getByRole("button", { name: "Resume workflow" }).count(), 1);
    await f.page.close();
    f = await fixture(browser, [turn("t1", "failed"), turn("t2", "completed")]);
    await f.open();
    assert.equal(await retry(f.page).count(), 0, "no Retry on an earlier turn");
    await f.page.close();

    // 409: the conversation reloads and the note explains.
    f = await fixture(browser, [turn("t1", "failed")]);
    f.state.retryReply = { status: 409, json: { code: "retry_source_superseded" } };
    await f.open();
    const loads = f.state.loads;
    await retry(f.page).click();
    await f.page.waitForFunction(() => /newer turn/i.test(document.querySelector(".turn-retry-actions [role=status]")?.textContent || ""));
    assert.equal(await f.page.getByTestId("turn-retry").getAttribute("aria-busy"), null);
    assert.notEqual(await f.page.evaluate(() => document.activeElement.tagName), "BODY", "focus survives the reload");
    assert(f.state.loads > loads, "conversation refreshed after 409");
    await f.page.close();

    // The user switched conversations during the Retry POST: the old one is not reloaded over the new view.
    f = await fixture(browser, [turn("t1", "failed")]);
    await f.open();
    const before = f.state.gets.length;
    await retry(f.page).click();
    await f.page.evaluate(() => { conversation = "other"; });
    await f.page.waitForTimeout(500);
    assert.equal(f.state.retries, 1);
    assert.equal(f.state.gets.length, before, "no conversation reload after switching away");
    await f.page.close();

    // Failed turn with an image on a model that cannot read images: choose another model, no Retry.
    f = await fixture(browser, [turn("t1", "failed", {
      request: { backend: "deepseek", model: "deepseek-flash" },
      turn: { attachments: [{ file_id: "i1", name: "a.png", preview_url: "data:image/gif;base64,R0lGODlhAQABAAAAACw=" }] },
    })]);
    await f.open();
    assert.equal(await retry(f.page).count(), 0);
    await f.page.getByTestId("turn-choose-model").click();
    await f.page.waitForFunction(() => document.querySelector("#model-menu").matches(":popover-open"));
    await f.page.close();
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exit(1); });
