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

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    page.on("pageerror", (e) => console.error("PAGEERROR", e.message));
    await page.route("http://nav.test/**", async (route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (!pathname.startsWith("/v1/"))
        return route.fulfill({
          path: path.join(__dirname, "..", pathname.startsWith("/assets/") ? "harness_ui" : "agent_service", pathname === "/" ? "index.html" : pathname),
        });
      let data = {};
      if (pathname === "/v1/projects") data = { projects: ["sem-projeto"] };
      else if (pathname === "/v1/models")
        data = { models: [{ id: "gpt-6-astra", name: "GPT-6 Astra", backend: "codex", efforts: ["medium"] }], providers: { codex: true }, uploads_enabled: false };
      else if (pathname === "/v1/conversations")
        data = {
          conversations: [
            { id: "alpha", title: "Alpha chat", project: "sem-projeto", state: "completed", last_job_id: "a11", updated_at: 2 },
            { id: "bravo", title: "Bravo chat", project: "sem-projeto", state: "completed", last_job_id: "b0", updated_at: 1 },
          ],
        };
      else if (pathname === "/v1/conversations/alpha") data = { title: "Alpha chat", turns: alphaTurns };
      else if (pathname === "/v1/conversations/bravo") data = { title: "Bravo chat", turns: bravoTurns };
      else if (pathname === "/v1/version") data = { version: "fixture", build: "back-forward" };
      else if (pathname === "/v1/schedules") data = { schedules: [] };
      else if (pathname === "/v1/pages") data = { pages: [] };
      else if (/^\/v1\/jobs\/[ab]\d+$/.test(pathname)) data = [...alphaTurns, ...bravoTurns].find((t) => "/v1/jobs/" + t.id === pathname);
      return route.fulfill({ json: data });
    });
    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
    await page.goto("http://nav.test/");
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
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
