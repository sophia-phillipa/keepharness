const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict"),
  fs = require("node:fs/promises"),
  path = require("node:path");

// WP5 UI: "Continue in another app..." row action. The endpoint and the desktop bridge are
// mocked; nothing here opens another app or calls a provider.
const origin = "http://127.0.0.1:8094";
const TITLE = 'Plan: v2/draft? "final"*';
const conversations = [
  { id: "c1", title: TITLE, project: "sem-projeto", state: "completed", execution: { backend: "local", model: "fixture" } },
  { id: "old", title: "Legacy chat", project: "sem-projeto", state: "completed", legacy: true, execution: { backend: "local", model: "fixture" } },
];
const textFor = (target, paths) => `You are ${target === "claude" ? "Claude" : "ChatGPT"}. Handoff for ${target} paths=${paths}\nline two & more`;
const luminance = (rgb) =>
  rgb.match(/[\d.]+/g).slice(0, 3).map(Number).map((v) => {
    v /= 255;
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  }).reduce((a, v, i) => a + v * [0.2126, 0.7152, 0.0722][i], 0);
const ratio = (a, b) => (Math.max(luminance(a), luminance(b)) + 0.05) / (Math.min(luminance(a), luminance(b)) + 0.05);

async function scenario(browser, { fullAccess = true, localOwner = fullAccess, bridge = null, clipboard = null } = {}) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, acceptDownloads: true });
  await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin });
  const page = await context.newPage();
  await page.emulateMedia({ reducedMotion: "reduce" });
  const state = { requests: [], gates: {}, truncated: false, failWith: null, errors: [] };
  page.on("pageerror", (e) => state.errors.push(e.message));
  await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
  // clipboard: "none" = insecure origin (no API), "slow" = writes wait for __clipRelease(),
  // "second-fails" = the first write works, later ones reject.
  if (clipboard)
    await page.addInitScript((mode) => {
      if (mode === "none") return delete Navigator.prototype.clipboard;
      let writes = 0;
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: {
          writeText: (text) => {
            writes++;
            if (mode === "second-fails" && writes > 1) return Promise.reject(new Error("denied"));
            if (mode !== "slow") return Promise.resolve();
            return new Promise((resolve) => (window.__clipRelease = () => resolve()));
          },
        },
      });
    }, clipboard);
  if (bridge)
    await page.addInitScript((b) => {
      window.__calls = { apps: 0, open: [] };
      window.keepharnessDesktop = {
        handoffApps: async () => (window.__calls.apps++, { apps: b.apps }),
        openHandoff: async (target, text) => {
          window.__calls.open.push([target, text]);
          if (b.slow) await new Promise((resolve) => (window.__openRelease = resolve));
          if (b.reject) throw new Error("ipc down");
          return b.result;
        },
      };
    }, bridge);
  await page.route(origin + "/**", async (route) => {
    const url = new URL(route.request().url()),
      p = url.pathname;
    if (p.startsWith("/v1/")) {
      let data = {};
      if (/\/continuation$/.test(p)) {
        const target = url.searchParams.get("target"),
          paths = url.searchParams.get("include_paths");
        state.requests.push({ id: p.split("/")[3], target, paths });
        if (state.failWith) return route.fulfill({ status: state.failWith.status, json: { code: state.failWith.code } });
        await state.gates[target];
        return route.fulfill({ json: { target, text: textFor(target, paths), turns_included: 3, truncated: state.truncated } }).catch(() => {});
      }
      if (p === "/v1/projects") data = { projects: ["sem-projeto"], details: {} };
      if (p === "/v1/models")
        data = { models: [{ id: "fixture", backend: "local", efforts: ["configured"] }], full_access: fullAccess, local_owner: localOwner, admin_url: origin + "/admin/" };
      if (p === "/v1/conversations") data = { conversations };
      if (p === "/v1/version") data = { version: "test", build: "continuation-test" };
      if (p === "/v1/catalog") data = { agents: [], skills: [], warnings: [] };
      return route.fulfill({ json: data });
    }
    const file = p === "/" ? "index.html" : p.slice(1);
    return route.fulfill({
      body: await fs.readFile(path.join(__dirname, file.startsWith("assets/") ? "../harness_ui" : "../agent_service", file)),
      contentType: file.endsWith(".svg") ? "image/svg+xml" : file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : "text/html",
    });
  });
  await page.goto(origin);
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  return { context, page, state };
}
const rowTrigger = (page, id = "c1") =>
  page.locator(`#history [data-conversation-id="${id}"]`).locator("xpath=..").locator(".conversation-actions > summary");
async function openDialog(page) {
  await rowTrigger(page).click();
  await page.getByRole("button", { name: "Continue in another app…" }).click();
  await page.locator("#continuation-dialog").waitFor({ state: "visible" });
  await page.waitForFunction(() => document.getElementById("continuation-text").value !== "");
}

(async () => {
  const browser = await chromium.launch();
  const opened = [];
  try {
    // 1. Item placement, legacy rows, endpoint query, local-owner default.
    {
      const { context, page, state } = await scenario(browser);
      opened.push(context);
      await rowTrigger(page).click();
      const labels = await page.locator('#history [data-conversation-id="c1"]').locator("xpath=..").locator(".conversation-actions-menu button").allInnerTexts();
      assert.deepEqual(labels.map((s) => s.trim()), ["Rename conversation", "Continue in another app…", "Archive conversation", "Delete permanently"]);
      await page.keyboard.press("Escape");
      assert(
        await page.locator('#history [data-conversation-id="old"]').locator("xpath=..").locator(".conversation-actions").evaluate((e) => e.hidden),
        "legacy rows keep their whole actions menu hidden",
      );
      assert.equal(state.requests.length, 0, "nothing is fetched before the click");
      await openDialog(page);
      assert.deepEqual(state.requests, [{ id: "c1", target: "chatgpt", paths: "1" }]);
      assert(await page.locator("#continuation-paths").isChecked(), "local owner includes project folders by default");
      assert(await page.locator('input[name="continuation-target"][value="chatgpt"]').isChecked());
      assert.equal(await page.locator("#continuation-text").inputValue(), textFor("chatgpt", "1"));
      assert.equal(await page.locator("#continuation-text").getAttribute("readonly"), "");
      assert.match(await page.locator("#continuation-summary").innerText(), /3 turns included/);
      assert.equal(await page.locator("#continuation-open").isHidden(), true, "no bridge: no open question");
      // 2. Target and checkbox changes refetch with the right query.
      await page.locator('input[name="continuation-target"][value="claude"]').check();
      await page.waitForFunction(() => document.getElementById("continuation-text").value.includes("for claude"));
      await page.locator("#continuation-paths").uncheck();
      await page.waitForFunction(() => document.getElementById("continuation-text").value.includes("paths=0"));
      assert.deepEqual(state.requests.at(-1), { id: "c1", target: "claude", paths: "0" });
      // 3. Copy, "Copied" label, no open question without a bridge.
      await page.locator("#continuation-copy").click();
      await page.waitForFunction(() => document.getElementById("continuation-copy").textContent.trim() === "Copied");
      assert.equal(await page.evaluate(() => navigator.clipboard.readText()), textFor("claude", "0"));
      // 4. Save as .md: exact filename and body.
      const [download] = await Promise.all([page.waitForEvent("download"), page.locator("#continuation-save").click()]);
      assert.equal(download.suggestedFilename(), "Plan- v2-draft- -final-continue-in-claude.md");
      assert.equal(await fs.readFile(await download.path(), "utf8"), textFor("claude", "0"));
      assert.equal(await page.locator("#continuation-open").isHidden(), true, "browser mode never offers to open an app");
      // 5. Contrast on both themes, measured from computed colors.
      await page.mouse.move(0, 0); // resting state, not the hover tint
      const numbers = {};
      for (const theme of ["paper", "graphite"]) {
        await page.evaluate((t) => HarnessTheme.apply(t, false), theme);
        for (const sel of ["#continuation-title", "#continuation-summary", "#continuation-text", "#continuation-copy", "#continuation-save", "#continuation-close", ".continuation-targets legend", ".continuation-targets label", "#continuation-paths-label"]) {
          const c = await page.locator(sel).first().evaluate((e) => {
            let b = e;
            while (b && /^rgba\(.*, 0(\.\d+)?\)$|\//.test(getComputedStyle(b).backgroundColor)) b = b.parentElement;
            return { fg: getComputedStyle(e).color, bg: getComputedStyle(b || document.body).backgroundColor };
          });
          const r = ratio(c.fg, c.bg);
          numbers[theme + " " + sel] = r.toFixed(2);
          assert(r >= 4.5, `${theme} ${sel} contrast ${r.toFixed(2)} < 4.5`);
        }
      }
      console.log("CONTRAST", JSON.stringify(numbers));
      // 6. Escape closes the dialog and returns focus to the row trigger.
      await page.keyboard.press("Escape");
      await page.locator("#continuation-dialog").waitFor({ state: "hidden" });
      assert(await rowTrigger(page).evaluate((e) => e === document.activeElement), "focus returns to the row trigger");
      assert.deepEqual(state.errors, []);
    }

    // 7. Stale answers are dropped; truncated notice; failure copy.
    {
      const { context, page, state } = await scenario(browser, { fullAccess: false });
      opened.push(context);
      state.truncated = true;
      await rowTrigger(page).click();
      let release;
      state.gates.claude = new Promise((r) => (release = r));
      await page.getByRole("button", { name: "Continue in another app…" }).click();
      await page.waitForFunction(() => document.getElementById("continuation-text").value !== "");
      assert.equal(await page.locator("#continuation-paths").isChecked(), false, "remote clients start without project folders");
      assert.deepEqual(state.requests[0], { id: "c1", target: "chatgpt", paths: "0" });
      assert.match(await page.locator("#continuation-summary").innerText(), /Older turns were left out to fit the size limit\./);
      await page.locator('input[name="continuation-target"][value="claude"]').check();
      await page.waitForFunction(() => document.getElementById("continuation-text").value === "");
      await page.locator('input[name="continuation-target"][value="chatgpt"]').check();
      await page.waitForFunction(() => document.getElementById("continuation-text").value.includes("for chatgpt"));
      release();
      await page.waitForTimeout(300);
      assert.equal(await page.locator("#continuation-text").inputValue(), textFor("chatgpt", "0"), "the slower claude answer was dropped");
      state.failWith = { status: 400, code: "invalid_continuation_target" };
      await page.locator('input[name="continuation-target"][value="claude"]').check();
      await page.locator("#continuation-error").filter({ hasText: "Choose Claude or ChatGPT" }).waitFor();
      assert.equal(await page.locator("#continuation-copy").isDisabled(), true);
      assert.equal(await page.locator("#continuation-save").isDisabled(), true);
      assert.deepEqual(state.errors, []);
    }

    // 8. Desktop bridge: question only for installed apps, nothing before a click.
    const bridgeCase = async (result, target, expectStatus, apps = ["chatgpt", "claude"]) => {
      const { context, page, state } = await scenario(browser, { bridge: { apps, result } });
      opened.push(context);
      await openDialog(page);
      if (target === "claude") {
        await page.locator('input[name="continuation-target"][value="claude"]').check();
        await page.waitForFunction(() => document.getElementById("continuation-text").value.includes("for claude"));
      }
      const text = await page.locator("#continuation-text").inputValue();
      assert.equal(await page.evaluate(() => window.__calls.open.length), 0, "no open before any click");
      assert.equal(await page.locator("#continuation-open").isHidden(), true, "no question before Copy or Save");
      await page.locator("#continuation-copy").click();
      const name = target === "claude" ? "Claude" : "ChatGPT";
      await page.locator("#continuation-open-question").filter({ hasText: `Open ${name} to continue there?` }).waitFor();
      assert.equal(await page.evaluate(() => window.__calls.open.length), 0, "the question alone opens nothing");
      await page.evaluate(() => navigator.clipboard.writeText("overwritten"));
      await page.locator("#continuation-open-yes").click();
      await page.locator("#continuation-open-status").filter({ hasText: expectStatus }).waitFor();
      assert.deepEqual(await page.evaluate(() => window.__calls.open), [[target, text]]);
      assert.equal(await page.evaluate(() => navigator.clipboard.readText()), text, "the full prompt is on the clipboard before opening");
      assert.deepEqual(state.errors, []);
      return page;
    };
    await bridgeCase({ opened: true, mode: "full" }, "claude", "Opened Claude with the handoff in a new chat. Review it and send it there.");
    await bridgeCase({ opened: true, mode: "short" }, "chatgpt", "paste it from your clipboard (Ctrl+V)");
    await bridgeCase({ opened: false, error: "handoff_forbidden" }, "chatgpt", "KeepHarness can only open other apps from its own window");
    await bridgeCase({ opened: false, error: "handoff_invalid" }, "chatgpt", "couldn't build a link from this handoff");
    await bridgeCase({ opened: false, error: "handoff_app_missing" }, "chatgpt", "isn't set up to open links");
    await bridgeCase({ opened: false, error: "handoff_open_failed" }, "chatgpt", "Couldn't open ChatGPT. The handoff is on your clipboard.");

    // 9. Only installed apps get the question; Save also offers it; Not now hides it.
    {
      const { context, page } = await scenario(browser, { bridge: { apps: ["claude"], result: { opened: true, mode: "full" } } });
      opened.push(context);
      await openDialog(page);
      await page.locator("#continuation-copy").click();
      await page.waitForTimeout(200);
      assert.equal(await page.locator("#continuation-open").isHidden(), true, "ChatGPT is not installed: no question");
      await page.locator('input[name="continuation-target"][value="claude"]').check();
      await page.waitForFunction(() => document.getElementById("continuation-text").value.includes("for claude"));
      assert.equal(await page.locator("#continuation-open").isHidden(), true, "a changed target needs a new Copy or Save");
      await Promise.all([page.waitForEvent("download"), page.locator("#continuation-save").click()]);
      await page.locator("#continuation-open-question").filter({ hasText: "Open Claude to continue there?" }).waitFor();
      await page.locator("#continuation-open-no").click();
      assert.equal(await page.locator("#continuation-open").isHidden(), true);
      assert.equal(await page.evaluate(() => window.__calls.open.length), 0, "Not now never opens the app");
    }
    // 10. A bridge that lists no apps (forbidden) shows no question.
    {
      const { context, page } = await scenario(browser, { bridge: { apps: [], result: { opened: false, error: "handoff_forbidden" } } });
      opened.push(context);
      await openDialog(page);
      await page.locator("#continuation-copy").click();
      await page.waitForTimeout(200);
      assert.equal(await page.locator("#continuation-open").isHidden(), true);
    }
    // 11. The owner's default does not depend on Full mode; guests never get it.
    for (const [fullAccess, localOwner, checked] of [[false, true, true], [false, false, false]]) {
      const { context, page, state } = await scenario(browser, { fullAccess, localOwner });
      opened.push(context);
      await openDialog(page);
      assert.equal(await page.locator("#continuation-paths").isChecked(), checked, `local_owner=${localOwner}`);
      assert.equal(state.requests[0].paths, checked ? "1" : "0");
    }
    // 12. Endpoint 404: the message is shown and nothing can be copied or saved.
    {
      const { context, page, state } = await scenario(browser);
      opened.push(context);
      state.failWith = { status: 404, code: "conversation_not_found" };
      await rowTrigger(page).click();
      await page.getByRole("button", { name: "Continue in another app…" }).click();
      await page.locator("#continuation-error").filter({ hasText: /Couldn't prepare the handoff: \S/ }).waitFor();
      assert.equal(await page.locator("#continuation-copy").isDisabled(), true);
    }
    // 13. B1: no navigator.clipboard (plain-http client). The fallback must copy from inside the modal.
    {
      const { context, page } = await scenario(browser, { clipboard: "none", bridge: { apps: ["chatgpt"], result: { opened: true, mode: "full" } } });
      opened.push(context);
      await openDialog(page);
      assert.equal(await page.evaluate(() => navigator.clipboard), undefined);
      const text = await page.locator("#continuation-text").inputValue();
      const reader = await context.newPage();
      await reader.goto(origin + "/v1/version");
      await reader.bringToFront();
      await reader.evaluate(() => navigator.clipboard.writeText("sentinel"));
      await page.bringToFront();
      await page.locator("#continuation-copy").click();
      await page.waitForFunction(() => document.getElementById("continuation-copy").textContent.trim() === "Copied");
      await reader.bringToFront();
      assert.equal(await reader.evaluate(() => navigator.clipboard.readText()), text, "the page clipboard really holds the prompt");
      await reader.close();
    }
    // 14. W2/W4: an IPC rejection while opening is not a copy failure; focus returns to Copy after Open.
    {
      const { context, page } = await scenario(browser, { bridge: { apps: ["chatgpt"], reject: true } });
      opened.push(context);
      await openDialog(page);
      await page.locator("#continuation-copy").click();
      await page.locator("#continuation-open-question").waitFor();
      assert.equal(await page.locator("#continuation-open-question").getAttribute("role"), "status");
      await page.locator("#continuation-open-yes").click();
      await page.locator("#continuation-open-status").filter({ hasText: "Couldn't open ChatGPT. The handoff is on your clipboard." }).waitFor();
      assert.doesNotMatch(await page.locator("#continuation-open-status").innerText(), /Couldn't copy/);
      assert(await page.locator("#continuation-copy").evaluate((e) => e === document.activeElement), "focus moves to Copy after Open");
    }
    {
      const { context, page } = await scenario(browser, { clipboard: "second-fails", bridge: { apps: ["chatgpt"], result: { opened: true, mode: "full" } } });
      opened.push(context);
      await openDialog(page);
      await page.locator("#continuation-copy").click();
      await page.locator("#continuation-open-question").waitFor();
      await page.locator("#continuation-open-yes").click();
      await page.locator("#continuation-open-status").filter({ hasText: "Couldn't copy the handoff, so ChatGPT was not opened." }).waitFor();
      assert.equal(await page.evaluate(() => window.__calls.open.length), 0);
    }
    // 15. W3: switching the target while Copy is pending never asks about the uncopied target.
    {
      const { context, page } = await scenario(browser, { clipboard: "slow", bridge: { apps: ["chatgpt", "claude"], result: { opened: true, mode: "full" } } });
      opened.push(context);
      await openDialog(page);
      await page.locator("#continuation-copy").click();
      await page.locator('input[name="continuation-target"][value="claude"]').check();
      await page.waitForFunction(() => document.getElementById("continuation-text").value.includes("for claude"));
      await page.evaluate(() => window.__clipRelease());
      await page.waitForTimeout(300);
      assert.equal(await page.locator("#continuation-open").isHidden(), true, "no question for the target that was not copied");
    }
    // 15b. W3: switching the target while the app opens still names the app that was opened.
    {
      const { context, page } = await scenario(browser, { bridge: { apps: ["chatgpt", "claude"], result: { opened: true, mode: "full" }, slow: true } });
      opened.push(context);
      await openDialog(page);
      await page.locator("#continuation-copy").click();
      await page.locator("#continuation-open-yes").click();
      await page.waitForFunction(() => window.__calls.open.length === 1);
      await page.locator('input[name="continuation-target"][value="claude"]').check();
      await page.waitForFunction(() => document.getElementById("continuation-text").value.includes("for claude"));
      await page.evaluate(() => window.__openRelease());
      await page.locator("#continuation-open-status").filter({ hasText: "Opened ChatGPT with the handoff" }).waitFor();
    }
    // 16. Filename: whole code points, no control or bidi characters.
    {
      const { context, page } = await scenario(browser);
      opened.push(context);
      const names = await page.evaluate(() => [
        continuationFilename("a".repeat(79) + "\u{1F600}tail", "claude"),
        continuationFilename("ab\u007fcd\u0085e\u202ef\u2066g\u2069h", "chatgpt"),
      ]);
      assert.equal(names[0], "a".repeat(79) + "\u{1F600}-continue-in-claude.md");
      assert.equal(names[1], "abcdefgh-continue-in-chatgpt.md");
    }
    // 17. A double click never leaves "Copied" stuck on the button.
    {
      const { context, page } = await scenario(browser);
      opened.push(context);
      await openDialog(page);
      await page.locator("#continuation-copy").dblclick();
      await page.waitForFunction(() => document.getElementById("continuation-copy").textContent.trim() === "Copied");
      await page.waitForFunction(() => document.getElementById("continuation-copy").textContent.trim() === "Copy", null, { timeout: 4000 });
    }
    console.log("PASS: continuation dialog, refetch, stale drop, copy, save, bridge question and statuses, legacy rows, focus and contrast");
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
