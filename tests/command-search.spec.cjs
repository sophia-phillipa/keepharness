const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");

const origin = process.env.HARNESS_URL;
if (!origin) throw new Error("HARNESS_URL is required (run through scripts/test-ui.sh)");

function contrast(rgb1, rgb2) {
  const values = (rgb) => {
    const channels = rgb.match(/[\d.]+/g).slice(0, 3).map(Number);
    return rgb.startsWith("color(srgb") ? channels.map((value) => value * 255) : channels;
  };
  const luminance = (rgb) => {
    const [r, g, b] = values(rgb).map((value) => {
      value /= 255;
      return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const [lighter, darker] = [luminance(rgb1), luminance(rgb2)].sort((a, b) => b - a);
  return (lighter + 0.05) / (darker + 0.05);
}

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    let jobPosts = 0;
    let delayedProjectSearch = null;
    await page.route("**/v1/**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/v1/project-files" && delayedProjectSearch) {
        const delayed = delayedProjectSearch;
        delayedProjectSearch = null;
        delayed.started();
        await delayed.response;
      }
      if (route.request().method() === "POST" && url.pathname === "/v1/jobs") jobPosts += 1;
      const data =
        url.pathname === "/v1/projects"
          ? { projects: ["project-a", "sem-projeto"], details: { "project-a": { label: "Project Alpha", root: "/fixture" } } }
          : url.pathname === "/v1/models"
            ? {
                admin_url: origin.replace(/:\d+$/, ":1") + "/",
                models: [
                  { id: "fixture", name: "Fixture", backend: "local", efforts: ["low"] },
                  { id: "cloud", name: "Cloud", backend: "codex", efforts: ["medium"], permissions: { upload: true } },
                ],
                providers: { local: true, codex: true },
                uploads_enabled: true,
              }
            : url.pathname === "/v1/conversations"
              ? { conversations: [{ id: "c-known", title: "Known migration chat", project: "project-a", state: "completed", execution: { backend: "local", model: "fixture" } }] }
              : url.pathname === "/v1/conversations/c-known"
                ? { title: "Known migration chat", turns: [{ id: "job-known", project: "project-a", state: "completed", request: { prompt: "Fixture", backend: "local", model: "fixture", effort: "low" }, result: { answer: "Fixture answer" } }] }
                : url.pathname === "/v1/files"
                  ? { file_id: "attached-fixture" }
                : url.pathname === "/v1/project-files"
                  ? { entries: [{ name: "known-plan.md", path: "docs/known-plan.md", type: "file" }] }
                  : url.pathname === "/v1/version"
                    ? { version: "fixture", build: "fixture" }
                    : {};
      return route.fulfill({ json: data });
    });
    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
    await page.goto(origin);
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.selectOption("#project", "project-a", { force: true });
    await page.selectOption("#model", "cloud", { force: true });
    await page.fill("#prompt", "Preserve this draft");
    await page.locator("#file").setInputFiles({ name: "attached.txt", mimeType: "text/plain", buffer: Buffer.from("fixture") });
    await page.waitForFunction(() => files.length === 1 && uploads === 0);

    const snapshot = async () => ({
      draft: await page.locator("#prompt").inputValue(),
      project: await page.locator("#project").inputValue(),
      provider: await page.locator("#model").inputValue(),
      access: await page.locator("#access-mode").inputValue(),
      attachments: await page.locator("#attachments > *").count(),
    });
    const before = await snapshot();
    const dialog = page.locator("#conversation-search-dialog");
    const input = page.locator("#conversation-search");

    for (const open of [
      async () => page.click("#search-conversations"),
      async () => page.keyboard.press("Control+k"),
      async () => page.keyboard.press("Control+Shift+p"),
    ]) {
      await open();
      await dialog.waitFor({ state: "visible" });
      assert.equal(await input.evaluate((node) => node === document.activeElement), true);
      await page.keyboard.press("Escape");
      await dialog.waitFor({ state: "hidden" });
      assert.deepEqual(await snapshot(), before, "open/cancel preserves composer route state");
    }
    await page.keyboard.press("Control+Shift+k");
    assert.equal(await dialog.isVisible(), false, "Ctrl+Shift+K is not an advertised alias");
    console.log("PASS A1 sidebar, Ctrl+K, and Ctrl+Shift+P share command search and preserve state");

    await page.keyboard.press("Control+k");
    await input.fill("a");
    assert.match(await dialog.getByRole("button", { name: /Focus composer/ }).innerText(), /Command/);
    assert.match(await dialog.getByRole("button", { name: /Appearance/ }).innerText(), /Settings/);
    assert.match(await dialog.getByRole("button", { name: /Known migration chat/ }).innerText(), /Project Alpha/);
    console.log("PASS A2b one fixture query returns distinguishable command, Settings and conversation results");
    await input.fill("focus composer");
    assert.deepEqual(await dialog.locator(".search-result-group h3").allTextContents(), ["Commands · 1"]);
    const focusCommand = dialog.getByRole("button", { name: /Focus composer/ });
    assert.match(await focusCommand.innerText(), /Command/);
    await page.keyboard.press("ArrowDown");
    assert.equal(await focusCommand.evaluate((node) => node === document.activeElement), true);
    await page.keyboard.press("Enter");
    assert.equal(await page.locator("#prompt").evaluate((node) => node === document.activeElement), true);
    assert.deepEqual(await snapshot(), before);
    console.log("PASS A2 command filtering, arrow navigation, Enter, and focus composer activation");

    const correctionFailures = [];
    let releaseDelayedSearch;
    const delayedSearchStarted = new Promise((resolve) => {
      delayedProjectSearch = {
        started: resolve,
        response: new Promise((release) => { releaseDelayedSearch = release; }),
      };
    });
    await page.keyboard.press("Control+k");
    const contentResponse = page.waitForResponse((response) =>
      new URL(response.url()).pathname === "/v1/conversations" && response.url().includes("focus+composer"));
    await input.fill("focus composer");
    await delayedSearchStarted;
    await contentResponse;
    await page.keyboard.press("ArrowDown");
    const focusedResultId = await page.evaluate(() => document.activeElement?.dataset.searchResultId || null);
    const delayedResponse = page.waitForResponse((response) =>
      new URL(response.url()).pathname === "/v1/project-files" && response.url().includes("focus+composer"));
    releaseDelayedSearch();
    await delayedResponse;
    const focusedAfterRefresh = await page.evaluate(() => document.activeElement?.dataset.searchResultId || null);
    if (focusedResultId !== "command:focus-composer" || focusedAfterRefresh !== focusedResultId)
      correctionFailures.push("C1 delayed search refresh preserves the focused semantic result");
    await page.keyboard.press("Enter");
    if (!(await page.locator("#prompt").evaluate((node) => node === document.activeElement)))
      correctionFailures.push("C1 Enter activates the result after delayed search refresh");
    if (await dialog.isVisible()) await page.keyboard.press("Escape");

    const savedConversations = await page.evaluate(() => structuredClone(conversations));
    const disappearingSearchStarted = new Promise((resolve) => {
      delayedProjectSearch = {
        started: resolve,
        response: new Promise((release) => { releaseDelayedSearch = release; }),
      };
    });
    await page.keyboard.press("Control+k");
    const disappearingContentResponse = page.waitForResponse((response) =>
      new URL(response.url()).pathname === "/v1/conversations" && response.url().includes("known+migration"));
    await input.fill("known migration");
    await disappearingSearchStarted;
    await disappearingContentResponse;
    await page.keyboard.press("ArrowDown");
    if (await page.evaluate(() => document.activeElement?.dataset.searchResultId) !== "run:c-known:")
      correctionFailures.push("C1 the disappearing run starts with its semantic result focused");
    await page.evaluate(() => { conversations = []; });
    const disappearingResponse = page.waitForResponse((response) =>
      new URL(response.url()).pathname === "/v1/project-files" && response.url().includes("known+migration"));
    releaseDelayedSearch();
    await disappearingResponse;
    if (!(await input.evaluate((node) => node === document.activeElement)))
      correctionFailures.push("C1 a vanished focused result returns focus to search input without selecting another result");
    await page.evaluate((saved) => { conversations = saved; renderConversationSearch(); }, savedConversations);
    await page.keyboard.press("Escape");

    const expectCoveredComposerOmitted = async (name) => {
      await page.keyboard.press("Control+k");
      await input.fill("focus composer");
      if (await dialog.getByRole("button", { name: /Focus composer/ }).count())
        correctionFailures.push("C2 " + name + " omits Focus composer while its target is covered");
      await page.keyboard.press("Escape");
      await dialog.waitFor({ state: "hidden" });
    };
    const expectComposerAvailable = async (name) => {
      await page.keyboard.press("Control+k");
      await input.fill("focus composer");
      assert.equal(await dialog.getByRole("button", { name: /Focus composer/ }).count(), 1, name + " restores Focus composer after closing");
      await page.keyboard.press("Escape");
    };

    await page.setViewportSize({ width: 400, height: 860 });
    await page.click("#menu");
    assert.equal(await page.locator("#sidebar").getAttribute("class").then((value) => value.includes("open")), true);
    assert.equal(await page.locator("#prompt").evaluate((node) => !!node.closest("[inert]")), true);
    await expectCoveredComposerOmitted("mobile sidebar");
    assert.equal(await page.locator("#sidebar").getAttribute("class").then((value) => value.includes("open")), true, "search does not close the covering sidebar");
    await page.keyboard.press("Escape");
    await expectComposerAvailable("mobile sidebar");

    await page.setViewportSize({ width: 800, height: 860 });
    await page.evaluate(() => setPanelOpen(true, false));
    assert.equal(await page.locator("#prompt").evaluate((node) => !!node.closest("[inert]")), true);
    await expectCoveredComposerOmitted("responsive activity panel");
    assert.equal(await page.locator("#activity-panel").isVisible(), true, "search does not close the covering activity panel");
    await page.evaluate(() => setPanelOpen(false, false));
    await expectComposerAvailable("responsive activity panel");

    await page.setViewportSize({ width: 650, height: 860 });
    await page.keyboard.press("Control+j");
    await page.locator("#run-console").waitFor({ state: "visible" });
    assert.equal(await page.locator("#prompt").evaluate((node) => !!node.closest("[inert]")), true);
    await expectCoveredComposerOmitted("responsive run console");
    assert.equal(await page.locator("#run-console").isVisible(), true, "search does not close the covering run console");
    await page.keyboard.press("Control+j");
    await page.locator("#run-console").waitFor({ state: "hidden" });
    await expectComposerAvailable("responsive run console");

    await page.setViewportSize({ width: 1280, height: 860 });
    assert.deepEqual(await snapshot(), before, "async and responsive command checks preserve composer route state");
    assert.deepEqual(correctionFailures, [], correctionFailures.join("; "));
    console.log("PASS C1a delayed results preserve semantic focus and Enter activation");
    console.log("PASS C1b a vanished result returns focus to the search input");
    console.log("PASS C2a mobile sidebar omits Focus composer while covered");
    console.log("PASS C2b responsive activity panel omits Focus composer while covered");
    console.log("PASS C2c responsive run console omits Focus composer while covered");

    await page.keyboard.press("Control+Shift+p");
    await input.fill("appearance");
    const appearance = dialog.getByRole("button", { name: /Appearance/ });
    assert.match(await appearance.innerText(), /Settings/);
    await appearance.click();
    await page.locator("#settings-appearance:not([hidden])").waitFor();
    assert.deepEqual(await snapshot(), before);
    await page.keyboard.press("Control+[");
    await page.locator("#settings-dialog").waitFor({ state: "hidden" });
    await page.keyboard.press("Control+]");
    await page.locator("#settings-dialog").waitFor({ state: "visible" });
    await page.click("#settings-close");
    console.log("PASS A3 Settings result is distinguishable and opens its destination");

    await page.keyboard.press("Control+k");
    await input.fill("known migration");
    const conversation = dialog.getByRole("button", { name: /Known migration chat/ });
    assert.match(await conversation.innerText(), /Project Alpha/);
    await conversation.click();
    await page.waitForFunction(() => document.querySelector("#conversation-title").textContent === "Known migration chat");

    await page.keyboard.press("Control+k");
    await input.fill("known-plan");
    await page.waitForFunction(() => document.querySelector("#conversation-search-list")?.textContent.includes("known-plan.md"));
    await dialog.getByRole("button", { name: /known-plan\.md/ }).click();
    assert.equal(await page.locator("#activity-panel").isVisible(), true, "existing file result remains reachable");
    await page.keyboard.press("Escape");
    console.log("PASS A4 conversation and project-file fixture results remain reachable");

    await page.keyboard.press("Control+k");
    await input.fill("no such action");
    assert.match(await page.locator("#search-results").innerText(), /No command, setting, run, or loaded file matched/);
    await input.fill("run history");
    assert.equal(await dialog.getByRole("button", { name: /Run history/ }).count(), 1, "local owner sees System Settings commands");
    await page.keyboard.press("Escape");

    await page.evaluate(() => modelAvailability({ admin_url: "http://localhost:1/" }));
    await page.keyboard.press("Control+k");
    await input.fill("run history");
    assert.equal(await dialog.getByRole("button", { name: /Run history/ }).count(), 0, "remote fallback hides admin-only Settings commands");
    await page.keyboard.press("Escape");
    console.log("PASS A5 local admin visibility and remote fallback reuse existing access guards");

    await page.evaluate(() => { uploads = 1; updateComposer(); });
    await page.keyboard.press("Control+k");
    await input.fill("new conversation");
    assert.equal(await dialog.getByRole("button", { name: /New conversation/ }).count(), 0, "busy actions are absent instead of dead");
    await page.keyboard.press("Escape");
    await page.evaluate(() => { uploads = 0; updateComposer(); });
    assert.equal(jobPosts, 0, "search never submits a turn or changes access");
    console.log("PASS A6 unavailable actions are absent; attachment, access mode, and no-POST contracts hold");

    for (const palette of ["paper", "amethyst"]) {
      await page.evaluate((name) => HarnessTheme.apply(name, false), palette);
      await page.keyboard.press("Control+k");
      await input.fill("focus composer");
      const colors = await focusCommand.evaluate((node) => {
        const foreground = getComputedStyle(node).color;
        let background = getComputedStyle(node).backgroundColor;
        if (background === "rgba(0, 0, 0, 0)") background = getComputedStyle(node.closest("dialog")).backgroundColor;
        return { foreground, background };
      });
      assert(contrast(colors.foreground, colors.background) >= 4.5, palette + " command text contrast: " + JSON.stringify(colors));
      const supporting = await dialog.evaluate(() => {
        const background = getComputedStyle(document.querySelector("#conversation-search-dialog")).backgroundColor;
        return [
          document.querySelector(".search-result-group h3"),
          document.querySelector(".conversation-search-result small"),
        ].filter(Boolean).map((node) => ({ color: getComputedStyle(node).color, background }));
      });
      for (const sample of supporting)
        assert(contrast(sample.color, sample.background) >= 4.5, palette + " supporting text contrast: " + JSON.stringify(sample));
      await input.fill("no matching item anywhere");
      const empty = await page.locator("#search-results").evaluate((node) => ({
        color: getComputedStyle(node).color,
        background: getComputedStyle(node.closest("dialog")).backgroundColor,
      }));
      assert(contrast(empty.color, empty.background) >= 4.5, palette + " empty-state contrast: " + JSON.stringify(empty));
      await page.keyboard.press("Escape");
    }
    console.log("PASS A7 paper and amethyst command text contrast is at least 4.5:1");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
