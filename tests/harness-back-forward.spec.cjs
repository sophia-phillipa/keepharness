// Back / forward navigation at the top, like the Codex desktop app: a short in-memory history of
// views (conversations, Settings pages, Space, Scheduled, Customize, Home), Ctrl+[ / Ctrl+], the
// mouse back and forward buttons, and a per-conversation scroll position restored on return.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");

const turn = (id, prompt) => ({
  id,
  project: "sem-projeto",
  state: "completed",
  request: { backend: "codex", model: "gpt-6-astra", prompt, access_mode: "ask", effort: "medium" },
  result: { answer: "Answer to " + prompt + "\n\n" + "More detail.\n\n".repeat(12) },
});
const alphaTurns = Array.from({ length: 12 }, (_, i) => turn("a" + i, "Alpha question " + i));
const bravoTurns = [turn("b0", "Bravo question")];
// Gamma ends with a running turn: opening it makes load() watch the stream.
const gammaTurns = [...Array.from({ length: 11 }, (_, i) => turn("g" + i, "Gamma question " + i)), turn("g11", "Gamma question 11")];
const ORIGIN = "http://localhost:18990/";

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    page.on("pageerror", (e) => console.error("PAGEERROR", e.message));
    // Mutable fixture state for the failure-mode cases below.
    const deleted = new Set();
    let gammaDone = false, gammaHold = Promise.resolve(), alphaHold = Promise.resolve();
    const gamma = () => gammaTurns.map((t) => (t.id === "g11" && !gammaDone ? { ...t, state: "running", result: undefined } : t));
    await page.route("http://localhost:18990/**", async (route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname.startsWith("/admin")) return route.fulfill({ contentType: "text/html", body: "<!doctype html><title>admin</title>" });
      if (pathname === "/v1/jobs/g11/events") {
        await gammaHold;
        return route.fulfill({ contentType: "text/event-stream", body: "" }).catch(() => {});
      }
      const id = pathname.match(/^\/v1\/conversations\/(\w+)$/)?.[1];
      if (id === "alpha") await alphaHold;
      if (deleted.has(id)) return route.fulfill({ status: 404, json: { code: "conversation_not_found" } });
      if (!pathname.startsWith("/v1/"))
        return route.fulfill({
          path: path.join(__dirname, "..", pathname.startsWith("/assets/") ? "harness_ui" : "agent_service", pathname === "/" ? "index.html" : pathname),
        });
      let data = {};
      if (pathname === "/v1/projects") data = { projects: ["sem-projeto"] };
      else if (pathname === "/v1/models")
        data = { models: [{ id: "gpt-6-astra", name: "GPT-6 Astra", backend: "codex", efforts: ["medium"] }], providers: { codex: true }, uploads_enabled: false, admin_url: ORIGIN + "admin/" };
      else if (pathname === "/v1/conversations")
        data = {
          conversations: [
            { id: "alpha", title: "Alpha chat", project: "sem-projeto", state: "completed", last_job_id: "a11", updated_at: 2 },
            { id: "bravo", title: "Bravo chat", project: "sem-projeto", state: "completed", last_job_id: "b0", updated_at: 1 },
            { id: "gamma", title: "Gamma chat", project: "sem-projeto", state: "running", last_job_id: "g11", updated_at: 0 },
          ].filter((c) => !deleted.has(c.id)),
        };
      else if (pathname === "/v1/conversations/alpha") data = { title: "Alpha chat", turns: alphaTurns };
      else if (pathname === "/v1/conversations/bravo") data = { title: "Bravo chat", turns: bravoTurns };
      else if (pathname === "/v1/conversations/gamma") data = { title: "Gamma chat", turns: gamma() };
      else if (pathname === "/v1/version") data = { version: "fixture", build: "back-forward" };
      else if (pathname === "/v1/schedules") data = { schedules: [] };
      else if (pathname === "/v1/pages") data = { pages: [] };
      else if (/^\/v1\/jobs\/[abg]\d+$/.test(pathname)) data = [...alphaTurns, ...bravoTurns, ...gamma()].find((t) => "/v1/jobs/" + t.id === pathname);
      return route.fulfill({ json: data });
    });
    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
    await page.goto(ORIGIN);
    await page.locator("#startup-gate").waitFor({ state: "hidden" });

    const back = page.getByTestId("nav-back"), forward = page.getByTestId("nav-forward");
    const open = async (title) => {
      await page.locator("#history .conversation-row > button", { hasText: title }).click();
      await page.waitForFunction((t) => document.querySelector("#messages article.user")?.innerText.includes(t), title.split(" ")[0]);
    };
    const shown = () => page.locator("#messages article.user").first().innerText();

    // Fresh start: nothing to go back or forward to; labelled, and the title carries the shortcut.
    assert.equal(await back.isDisabled(), true);
    assert.equal(await forward.isDisabled(), true);
    assert.equal(await back.getAttribute("aria-label"), "Back");
    assert.equal(await forward.getAttribute("aria-label"), "Forward");
    assert.match(await back.getAttribute("title"), /Ctrl\+\[/);
    assert.match(await forward.getAttribute("title"), /Ctrl\+\]/);
    // Order in the top bar: Back, Forward, then the sidebar toggle.
    const order = await page.locator("#app-topbar").evaluate((bar) =>
      ["nav-back", "nav-forward", "menu"].map((id) => [...bar.querySelectorAll("button")].findIndex((b) => b.id === id || b.dataset.testid === id)));
    assert.deepEqual(order, [...order].sort((a, b) => a - b));
    assert.ok(order.every((i) => i >= 0));

    // Alpha, scrolled; then Bravo: Back is on, Back returns to Alpha at the same scroll position.
    await open("Alpha chat");
    await page.waitForFunction(() => document.querySelector("#messages").scrollHeight > document.querySelector("#messages").clientHeight + 400);
    await page.evaluate(() => { document.querySelector("#messages").scrollTo({ top: 300, behavior: "instant" }); });
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => r())));
    await open("Bravo chat");
    assert.equal(await back.isEnabled(), true);
    assert.equal(await forward.isDisabled(), true);
    await back.click();
    await page.waitForFunction(() => document.querySelector("#messages article.user")?.innerText.includes("Alpha"));
    await page.waitForFunction(() => Math.abs(document.querySelector("#messages").scrollTop - 300) <= 1);
    assert.equal(await forward.isEnabled(), true);
    await forward.click();
    await page.waitForFunction(() => document.querySelector("#messages article.user")?.innerText.includes("Bravo"));
    await back.click();
    await page.waitForFunction(() => document.querySelector("#messages article.user")?.innerText.includes("Alpha"));

    // A new navigation after Back drops the Forward entries (Home / new chat here).
    await page.click("#new");
    await page.waitForFunction(() => document.querySelectorAll("#messages article.user").length === 0);
    assert.equal(await forward.isDisabled(), true);
    assert.equal(await back.isEnabled(), true);
    await back.click();
    assert.match(await shown(), /Alpha/);

    // Settings and a Settings page are entries; the shortcuts and mouse button 3 go back.
    await page.click("#settings");
    await page.locator('[data-settings="models"]').click();
    await page.waitForFunction(() => !document.querySelector("#settings-models").hidden);
    await page.keyboard.press("Control+[");
    await page.waitForFunction(() => document.querySelector('[data-settings][aria-pressed="true"]')?.dataset.settings !== "models");
    assert.equal(await page.locator("#settings-dialog").evaluate((d) => d.open), true);
    await page.keyboard.press("Control+]");
    await page.waitForFunction(() => !document.querySelector("#settings-models").hidden);
    await page.keyboard.press("Control+[");
    await page.keyboard.press("Control+[");
    await page.waitForFunction(() => !document.querySelector("#settings-dialog").open);
    assert.match(await shown(), /Alpha/);
    assert.equal(await forward.isEnabled(), true);
    await page.keyboard.press("Control+]");
    await page.waitForFunction(() => document.querySelector("#settings-dialog").open);
    await page.evaluate(() => document.dispatchEvent(new MouseEvent("mouseup", { button: 3, bubbles: true, cancelable: true })));
    await page.waitForFunction(() => !document.querySelector("#settings-dialog").open);
    await page.evaluate(() => document.dispatchEvent(new MouseEvent("mouseup", { button: 4, bubbles: true, cancelable: true })));
    await page.waitForFunction(() => document.querySelector("#settings-dialog").open);

    // Closing a dialog by its own button is a recorded navigation to the conversation underneath.
    await page.click("#settings-close");
    await page.waitForFunction(() => !document.querySelector("#settings-dialog").open);
    assert.equal(await forward.isDisabled(), true);
    await back.click();
    await page.waitForFunction(() => document.querySelector("#settings-dialog").open);
    await page.click("#settings-close");

    // Space, Scheduled and Customize are entries too.
    await page.click("#rail-space");
    await page.waitForFunction(() => document.querySelector("#space-dialog").open);
    await page.click("#space-close");
    await page.click("#rail-scheduled");
    await page.waitForFunction(() => document.querySelector("#scheduled-dialog").open);
    // The dialog is modal, so the top-bar button is out of reach; the shortcut works.
    await page.keyboard.press("Control+[");
    await page.waitForFunction(() => !document.querySelector("#scheduled-dialog").open);
    await page.click("#rail-agents");
    await page.waitForFunction(() => !document.querySelector("#settings-customize").hidden);
    // The dialog is modal, so the top-bar button is out of reach; the shortcut works.
    await page.keyboard.press("Control+[");
    await page.waitForFunction(() => !document.querySelector("#settings-dialog").open);
    assert.match(await shown(), /Alpha/);
    console.log("PASS back and forward navigate views, shortcuts and scroll position");

    // Review findings. Each case starts from a fresh page, so the in-memory history is empty.
    const fresh = async () => {
      // The saved view would reopen the last conversation; start from an empty history instead.
      await page.evaluate(() => { localStorage.clear(); sessionStorage.clear(); });
      await page.goto(ORIGIN);
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
    };
    const raf = () => page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r()))));
    const scrollNear = (top) => page.waitForFunction((t) => Math.abs(document.querySelector("#messages").scrollTop - t) <= 1, top, { timeout: 5000 });

    // A conversation with a running turn: the scroll position is restored once its turns render,
    // not when the stream ends, and it does not jump afterwards.
    await fresh();
    let release;
    gammaHold = new Promise((resolve) => (release = resolve));
    await open("Gamma chat");
    await page.waitForFunction(() => document.querySelector("#messages").scrollHeight > document.querySelector("#messages").clientHeight + 400);
    await page.evaluate(() => { document.querySelector("#messages").scrollTo({ top: 300, behavior: "instant" }); });
    await raf();
    await open("Bravo chat");
    await back.click();
    await page.waitForFunction(() => document.querySelector("#messages article.user")?.innerText.includes("Gamma"));
    await scrollNear(300); // the stream is still held open here
    gammaDone = true;
    const finished = page.waitForResponse((r) => r.url().endsWith("/v1/jobs/g11"));
    release();
    await finished;
    await raf();
    await scrollNear(300);
    console.log("PASS Back restores the scroll before a running turn's stream ends");

    // System has five buttons that differ only by admin section: Back returns to the one left.
    await fresh();
    await page.click("#settings");
    await page.locator('[data-settings="system"][data-admin-section="runs"]').click();
    await page.locator('[data-settings="models"]').click();
    await page.keyboard.press("Control+[");
    await page.waitForFunction(() => !document.querySelector("#settings-system").hidden);
    assert.equal(await page.locator('[data-settings][aria-pressed="true"]').getAttribute("data-admin-section"), "runs");
    await page.keyboard.press("Control+]");
    await page.waitForFunction(() => !document.querySelector("#settings-models").hidden);
    console.log("PASS System admin sections are separate history entries");

    // Back to a conversation deleted meanwhile: the dead entry leaves the history and the view stays.
    await fresh();
    await open("Alpha chat");
    await open("Bravo chat");
    deleted.add("alpha");
    await back.click();
    await page.waitForFunction(() => /Couldn't open the conversation/.test(document.body.innerText));
    assert.match(await shown(), /Bravo/);
    // Home is still behind Bravo; nothing is ahead, because the index went back to Bravo.
    assert.equal(await back.isEnabled(), true);
    assert.equal(await forward.isDisabled(), true);
    await back.click();
    await page.waitForFunction(() => document.querySelectorAll("#messages article.user").length === 0);
    deleted.clear();
    console.log("PASS Back to a deleted conversation leaves the history coherent");

    // A recorded navigation that fails (sidebar click on a conversation deleted meanwhile) leaves no dead
    // entry: the first Back goes to the previous view instead of appearing to do nothing.
    await fresh();
    await open("Alpha chat");
    deleted.add("bravo");
    await page.locator("#history .conversation-row > button", { hasText: "Bravo chat" }).click();
    await page.waitForFunction(() => /Couldn't open the conversation/.test(document.body.innerText));
    assert.match(await shown(), /Alpha/);
    await back.click();
    await page.waitForFunction(() => document.querySelectorAll("#messages article.user").length === 0);
    deleted.clear();
    console.log("PASS A failed navigation leaves no dead history entry");

    // Reopening the app restores the open conversation's scroll position, not the top.
    await fresh();
    await open("Alpha chat");
    await page.waitForFunction(() => document.querySelector("#messages").scrollHeight > document.querySelector("#messages").clientHeight + 600);
    await page.evaluate(() => { document.querySelector("#messages").scrollTo({ top: 500, behavior: "instant" }); });
    await raf();
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.waitForFunction(() => document.querySelector("#messages article.user")?.innerText.includes("Alpha"));
    await page.waitForFunction(() => Math.abs(document.querySelector("#messages").scrollTop - 500) <= 40, null, { timeout: 5000 });
    console.log("PASS Reload restores the conversation scroll position");

    // At the bottom, the record is "the end" (-1), so a longer reply or a shorter window still opens at the bottom.
    await page.evaluate(() => { const box = document.querySelector("#messages"); box.scrollTo({ top: box.scrollHeight, behavior: "instant" }); });
    await raf();
    await page.evaluate(() => dispatchEvent(new Event("pagehide")));
    assert.ok((await page.evaluate(() => JSON.parse(localStorage.getItem("conversation-scroll")))).some(([, top]) => top === -1));
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.waitForFunction(() => document.querySelector("#messages article.user")?.innerText.includes("Alpha"));
    await page.waitForFunction(() => { const box = document.querySelector("#messages"); return box.scrollHeight - box.scrollTop - box.clientHeight < 48; }, null, { timeout: 5000 });
    console.log("PASS A conversation left at the bottom reopens at the bottom");

    // A corrupted stored value is ignored.
    await page.evaluate(() => localStorage.setItem("conversation-scroll", "{"));
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.waitForFunction(() => document.querySelector("#messages article.user")?.innerText.includes("Alpha"));
    console.log("PASS A corrupted scroll record is ignored");

    // Back, then Settings while the conversation is still loading: Back returns to that conversation.
    await fresh();
    await open("Alpha chat");
    await open("Bravo chat");
    let releaseAlpha;
    alphaHold = new Promise((resolve) => (releaseAlpha = resolve));
    await back.click();
    // Navigation toward a conversation is blocked while it loads, and the buttons say so.
    await page.waitForFunction(() => document.querySelector("#nav-forward").disabled);
    await page.click("#settings");
    releaseAlpha();
    alphaHold = Promise.resolve();
    await page.waitForFunction(() => document.querySelector("#messages article.user")?.innerText.includes("Alpha"));
    await page.keyboard.press("Control+[");
    await page.waitForFunction(() => !document.querySelector("#settings-dialog").open);
    await raf();
    assert.match(await shown(), /Alpha/);
    console.log("PASS Settings during a pending Back does not rewrite the entry");

    // The nav buttons follow the blocked state once the load ends.
    await fresh();
    await open("Alpha chat");
    await open("Bravo chat");
    await back.click();
    await page.waitForFunction(() => document.querySelector("#messages article.user")?.innerText.includes("Alpha"));
    await page.waitForFunction(() => !document.querySelector("#nav-forward").disabled);
    console.log("PASS Navigation buttons re-enable after the load");

    // A modal dialog that is not a view (About) keeps Ctrl+[ and Ctrl+] to itself.
    await fresh();
    await open("Alpha chat");
    await open("Bravo chat");
    await page.evaluate(() => document.getElementById("about-dialog").showModal());
    await page.keyboard.press("Control+[");
    await raf();
    assert.match(await shown(), /Bravo/);
    assert.equal(await forward.isDisabled(), true);
    await page.evaluate(() => document.getElementById("about-dialog").close());
    console.log("PASS Shortcuts are ignored under a foreign modal dialog");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
