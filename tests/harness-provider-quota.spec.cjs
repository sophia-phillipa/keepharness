// L55: the quota panel names the provider it explains (PRD-R1-10), a rail meter opens its own
// provider whichever model is selected (CDX-R2-2) and DeepSeek shows its balance (PRD-R2-11).
// WP8 (D-032): every provider the project allows has a meter, in a fixed order, with an n/a reason or a balance.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");

const window = (used, minutes) => ({ usedPercent: used, windowDurationMins: minutes, resetsAt: Math.floor(Date.now() / 1000) + 3600 });
const codexQuota = { available: true, checked_at: Date.now() / 1000, rateLimits: { primary: window(40, 300) } };
const claudeQuota = { available: true, checked_at: Date.now() / 1000, rateLimitsByLimitId: { five_hour: { primary: window(25, 300) } } };
const balance = {
  provider: "deepseek",
  available: true,
  account_active: true,
  checked_at: Date.now() / 1000,
  balances: [{ currency: "USD", total: "12.34", granted: "2.00", topped_up: "10.34" }],
};

// WP8 fixtures: a passive /v1/activity that lists the given providers, and a counted /v1/usage.
const reading = (used, minutes = 300) => ({ available: true, reason: null, checked_at: Date.now() / 1000, rateLimitsByLimitId: { five_hour: { primary: window(used, minutes) } } });
const missing = (reason) => ({ available: false, reason });
const deepseekBalance = { available: true, reason: null, kind: "balance", checked_at: Date.now() / 1000, balance: { amount: "12.40", currency: "USD" } };
const allProviders = [
  { backend: "codex", model: "codex-model", quota: missing("quota_not_read") },
  { backend: "claude", model: "claude-sonnet-4-5", quota: reading(30) },
  { backend: "gemini", model: "gemini-model", quota: missing("quota_not_reported") },
  { backend: "deepseek", model: "deepseek-flash", quota: deepseekBalance },
  { backend: "local", model: "local-model", quota: missing("local_no_quota") },
];
// Only Gemini and local models are selectable, so the panel never reads a quota on its own and
// every /v1/usage call counted here comes from the rail.
const railModels = [
  { id: "gemini-model", name: "Gemini model", backend: "gemini", efforts: ["low"] },
  { id: "local-model", name: "Local model", backend: "local", efforts: ["low"] },
];
async function openRail(browser, { providers, width = 1280, height = 860, visibility = "visible" }) {
  const fixture = { providers };
  const context = await browser.newContext({ locale: "en-US", viewport: { width, height } });
  const page = await context.newPage();
  const usageCalls = [];
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript((state) => {
    localStorage.setItem("keepharness-tour-seen", "0.16.0");
    window.__visibility = state;
    Object.defineProperty(Document.prototype, "visibilityState", { configurable: true, get: () => window.__visibility });
  }, visibility);
  if (!process.env.HARNESS_URL)
    await page.route("http://panel.test/**", (route) => {
      const pathname = new URL(route.request().url()).pathname;
      return route.fulfill({
        path: path.join(__dirname, "..", pathname.startsWith("/assets/") ? "harness_ui" : "agent_service", pathname === "/" ? "index.html" : pathname),
      });
    });
  await page.route("**/v1/**", (route) => {
    const url = new URL(route.request().url());
    const data =
      url.pathname === "/v1/usage"
        ? (usageCalls.push(url.searchParams.get("backend") || "codex"), { available: false, reason: "quota_not_read" })
        : url.pathname === "/v1/models"
          ? { models: railModels, providers: { gemini: true, local: true }, uploads_enabled: false }
          : url.pathname === "/v1/projects"
            ? { projects: ["sem-projeto"], details: {} }
            : url.pathname === "/v1/conversations"
              ? { conversations: [] }
              : url.pathname === "/v1/activity"
                ? { jobs: [], needs_you: [], counts: {}, providers: fixture.providers }
                : url.pathname === "/v1/version"
                  ? { version: "fixture", build: "fixture" }
                  : {};
    return route.fulfill({ json: data });
  });
  await page.goto(process.env.HARNESS_URL || "http://panel.test/");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  return { page, context, usageCalls, errors, fixture };
}
// Later polls of the fixture activity return the same providers, so a feed cannot be overwritten.
const feed = (rail, providers) => {
  rail.fixture.providers = providers;
  return rail.page.evaluate((items) => window.updateProviderQuotas(items), providers);
};
const meterRows = (page) =>
  page.locator("#provider-quotas .provider-quota-meter").evaluateAll((nodes) =>
    nodes.map((node) => ({
      provider: node.dataset.provider,
      state: node.dataset.state,
      text: node.textContent,
      label: node.getAttribute("aria-label"),
      title: node.title,
      testid: node.dataset.testid,
      role: node.getAttribute("role"),
      tag: node.tagName,
      width: node.getBoundingClientRect().width,
      overflow: node.scrollWidth - node.clientWidth,
      icons: [...node.querySelectorAll("svg use")].map((use) => use.getAttribute("href")),
      childTags: [...node.children].map((child) => child.tagName.toLowerCase()),
      iconHidden: node.querySelector("svg")?.getAttribute("aria-hidden"),
      iconWidth: node.querySelector("svg")?.getBoundingClientRect().width,
    })),
  );

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const usageCalls = [];
    if (!process.env.HARNESS_URL)
      await page.route("http://panel.test/**", (route) => {
        const pathname = new URL(route.request().url()).pathname;
        return route.fulfill({
          path: path.join(__dirname, "..", pathname.startsWith("/assets/") ? "harness_ui" : "agent_service", pathname === "/" ? "index.html" : pathname),
        });
      });
    await page.route("**/v1/**", (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/v1/usage") {
        const backend = url.searchParams.get("backend") || "codex";
        usageCalls.push(backend);
        return route.fulfill({ json: backend === "claude" ? claudeQuota : backend === "deepseek" ? balance : codexQuota });
      }
      const data =
        url.pathname === "/v1/models"
          ? {
              models: [
                { id: "codex-model", name: "Codex model", backend: "codex", efforts: ["low"] },
                { id: "claude-sonnet-4-5", name: "Sonnet", backend: "claude", efforts: ["low"] },
                { id: "deepseek-flash", name: "DeepSeek Flash", backend: "deepseek", efforts: ["low"] },
              ],
              providers: { codex: true, claude: true, deepseek: true },
              uploads_enabled: false,
            }
          : url.pathname === "/v1/projects"
            ? { projects: ["sem-projeto"], details: {} }
            : url.pathname === "/v1/conversations"
              ? { conversations: [] }
              : url.pathname === "/v1/activity"
                ? {
                    jobs: [],
                    needs_you: [],
                    counts: {},
                    providers: [
                      { backend: "codex", model: "codex-model", quota: codexQuota },
                      { backend: "claude", model: "claude-sonnet-4-5", quota: claudeQuota },
                    ],
                  }
                : url.pathname === "/v1/version"
                  ? { version: "fixture", build: "fixture" }
                  : {};
      return route.fulfill({ json: data });
    });
    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
    await page.goto(process.env.HARNESS_URL || "http://panel.test/");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    const select = async (id) =>
      page.evaluate((value) => {
        const model = document.getElementById("model");
        model.value = value;
        model.dispatchEvent(new Event("change", { bubbles: true }));
      }, id);
    const heading = () => page.locator("#quota-panel .quota-heading strong").innerText();

    // Rail meters are buttons that name their provider.
    await page.keyboard.press("Control+j");
    const codexMeter = page.locator('.provider-quota-meter[data-provider="codex"]');
    const claudeMeter = page.locator('.provider-quota-meter[data-provider="claude"]');
    await codexMeter.waitFor();
    await claudeMeter.waitFor();
    assert.equal(await codexMeter.evaluate((el) => el.tagName), "BUTTON");
    assert.match(await claudeMeter.getAttribute("aria-label"), /^Claude Code quota, 75% remaining/);

    // With Codex selected, the Claude meter explains Claude, not Codex.
    await select("codex-model");
    await claudeMeter.click();
    await page.locator("#quota-panel").waitFor({ state: "visible" });
    assert.equal(await heading(), "Claude Code subscription quota");
    await page.locator("#quota-current .quota-window").first().waitFor();
    assert.match(await page.locator("#quota-current").innerText(), /75% remaining/);
    assert.match(await page.locator("#quota-note").innerText(), /Claude Code last reported/);
    assert.doesNotMatch(await page.locator("#quota-panel").innerText(), /Qwen and Claude don't use this quota|isn't available in this panel/);
    const selectedSummary = await page.locator("#quota-short").innerText();
    assert.equal(await page.locator("#quota-toggle").getAttribute("aria-label").then((text) => /Claude/.test(text)), false, "the header summary stays on the selected Codex model");
    await page.keyboard.press("Escape");
    await page.locator("#quota-panel").waitFor({ state: "hidden" });
    assert(await claudeMeter.evaluate(el => el === document.activeElement), "Escape restores focus to the provider meter");

    // Returning through Settings restores the selected Codex model's quota.
    await page.keyboard.press("Control+,");
    await page.click("#settings-quota");
    await page.locator("#quota-panel").waitFor({ state: "visible" });
    assert.equal(await heading(), "ChatGPT account quota");
    await page.waitForFunction(() => /60% remaining/.test(document.getElementById("quota-current").textContent), null, { timeout: 3000 });
    assert.match(await page.locator("#quota-current").innerText(), /60% remaining/);
    assert.match(selectedSummary, /remaining|Quota unavailable/, "the header still describes the selected model while Claude has focus");
    assert.match(await page.locator("#quota-short").innerText(), /60% remaining/);
    await page.keyboard.press("Escape");
    await page.click("#settings-close");

    // The Codex meter opens by keyboard and says it is the ChatGPT account.
    await codexMeter.focus();
    await page.keyboard.press("Enter");
    await page.locator("#quota-panel").waitFor({ state: "visible" });
    assert.equal(await heading(), "ChatGPT account quota");
    assert.match(await page.locator("#quota-note").innerText(), /Shared with the account's other usage/);
    await page.keyboard.press("Escape");

    assert(await codexMeter.evaluate(el => el === document.activeElement), "keyboard opener regains focus");

    // A DeepSeek model shows its balance and never a ChatGPT heading.
    await select("deepseek-flash");
    await page.keyboard.press("Control+,");
    await page.click("#settings-quota");
    await page.locator("#quota-panel").waitFor({ state: "visible" });
    assert.equal(await heading(), "DeepSeek balance");
    await page.locator("#quota-current .quota-window").waitFor();
    assert.match(await page.locator("#quota-current").innerText(), /USD 12\.34 available[\s\S]*Granted 2\.00 · topped up 10\.34/);
    assert.match(await page.locator("#quota-note").innerText(), /Prepaid balance/);
    assert.doesNotMatch(await page.locator("#quota-panel").innerText(), /ChatGPT/);
    assert(usageCalls.includes("deepseek"));

    // WP8 / D-032: every provider has a meter, in a fixed order.
    const rail = await openRail(browser, { providers: allProviders });
    await rail.page.locator('[data-testid="quota-meter"]').first().waitFor();
    let rows = await meterRows(rail.page);
    assert.deepEqual(rows.map((row) => row.provider), ["codex", "claude", "gemini", "deepseek", "local"]);
    assert.deepEqual(rows.map((row) => row.state), ["na", "ok", "na", "balance", "na"]);
    for (const row of rows) {
      assert.equal(row.tag, "BUTTON");
      assert.equal(row.testid, "quota-meter");
      assert.equal(row.role, null, "a meter button carries no role=meter");
      assert(row.width <= 34.5, `${row.provider} keeps the 34 px column`);
      assert(row.label && row.title === row.label, `${row.provider}: title equals aria-label`);
    }
    const byProvider = Object.fromEntries(rows.map((row) => [row.provider, row]));
    assert.equal(byProvider.gemini.label, "Gemini CLI quota not available: provider reports no quota. Open details.");
    assert.match(byProvider.gemini.text, /n\/a/);
    assert.match(byProvider.claude.label, /^Claude Code quota, 70% remaining\. Open details\.$/);
    assert.match(byProvider.deepseek.text, /\$12/);
    assert.doesNotMatch(byProvider.deepseek.text, /%|n\/a/);
    assert.equal(byProvider.deepseek.label, "DeepSeek balance $12.40. Open details.");
    assert.equal(await rail.page.locator('[data-provider="deepseek"] > i').count(), 0, "a balance replaces the bar");

    // D-035: the provider's logo replaces its short name; the full name stays in the accessible name and tooltip.
    const logos = { codex: "brand-openai", claude: "brand-claude", gemini: "brand-gemini", deepseek: "brand-deepseek", local: "stack-2" };
    const fullNames = { codex: "Codex", claude: "Claude Code", gemini: "Gemini CLI", deepseek: "DeepSeek", local: "Local models" };
    for (const row of rows) {
      assert.equal(row.icons.length, 1, `${row.provider}: exactly one logo`);
      assert(row.icons[0].endsWith("#" + logos[row.provider]), `${row.provider} logo: ${row.icons[0]}`);
      assert.equal(row.iconHidden, "true", `${row.provider}: the logo is decorative`);
      assert(row.iconWidth >= 14 && row.iconWidth <= 18, `${row.provider}: logo is about 16 px (${row.iconWidth})`);
      assert.equal(row.childTags[0], "svg", `${row.provider}: the logo leads the meter`);
      assert(!row.childTags.includes("span"), `${row.provider}: no visible provider name`);
      assert(!row.text.includes(fullNames[row.provider]) && !row.text.includes(row.provider), `${row.provider}: no provider text in the meter`);
      assert(row.label.startsWith(fullNames[row.provider]), `${row.provider}: accessible name starts with the full name: ${row.label}`);
      assert(row.overflow <= 0, `${row.provider}: nothing overflows the 34 px column (${row.overflow})`);
    }
    assert(await rail.page.evaluate(() => railQuotaSummaries.get("codex")?.startsWith("Codex")), "the n/a summary keeps the full name");

    // Each reason code gets a short human sentence.
    const reasons = {
      codex: ["quota_not_read", "not read yet"],
      claude: ["quota_stale", "last reading is older than 5 minutes"],
      gemini: ["usage_unavailable", "could not be read"],
      deepseek: ["balance_not_read", "balance not read yet"],
      local: ["local_no_quota", "local models have no quota"],
    };
    await feed(rail, Object.entries(reasons).map(([backend, [reason]]) => ({ backend, model: backend, quota: missing(reason) })));
    rows = await meterRows(rail.page);
    for (const row of rows) {
      assert(row.label.endsWith(`: ${reasons[row.provider][1]}. Open details.`), `${row.provider} reason text: ${row.label}`);
      assert.equal(row.state, "na");
    }

    // The rail follows the providers it is given: five, one, none; two Claude models make one meter.
    await feed(rail, [allProviders[2]]);
    assert.deepEqual((await meterRows(rail.page)).map((row) => row.provider), ["gemini"]);
    await feed(rail, [
      { backend: "claude", model: "claude-sonnet-4-5", quota: reading(30) },
      { backend: "claude", model: "claude-opus-4-1", quota: reading(60) },
    ]);
    rows = await meterRows(rail.page);
    assert.equal(rows.length, 1);
    assert.match(rows[0].label, /40% remaining/, "the meter shows the lowest remaining quota");
    await feed(rail, []);
    assert.equal(await rail.page.locator("#provider-quotas").isHidden(), true, "no providers hides the group");
    assert.equal(await rail.page.locator("#provider-quotas .provider-quota-meter").count(), 0);

    // The meter logo and text are readable on all eight palettes.
    await feed(rail, allProviders);
    const palettes = await rail.page.evaluate(() => HarnessTheme.themes.map((theme) => theme.id));
    assert.equal(palettes.length, 8);
    const weak = [];
    for (const palette of palettes) {
      await rail.page.evaluate((id) => HarnessTheme.apply(id), palette);
      const ratios = await rail.page.evaluate(() => {
        const parse = (c) => {
          const m = c.match(/rgba?\(([^)]+)\)|color\(srgb ([^)]+)\)/);
          let v = (m[1] || m[2]).split(/[ ,/]+/).map(Number);
          if (m[2]) v = [v[0] * 255, v[1] * 255, v[2] * 255, v[3] ?? 1];
          return [v[0], v[1], v[2], v[3] ?? 1];
        };
        const over = (top, bottom) => [0, 1, 2].map((i) => top[i] * top[3] + bottom[i] * (1 - top[3])).concat(1);
        const lum = (c) => {
          const f = (x) => ((x /= 255) <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4);
          return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
        };
        return [...document.querySelectorAll("#provider-quotas .provider-quota-meter > :is(svg, b)")].map((el) => {
          let bg = over(parse(getComputedStyle(document.documentElement).backgroundColor), [255, 255, 255, 1]);
          const chain = [];
          for (let e = el; e; e = e.parentElement) chain.unshift(e);
          for (const e of chain) {
            const c = parse(getComputedStyle(e).backgroundColor);
            if (c[3] > 0) bg = over(c, bg);
          }
          let fg = parse(getComputedStyle(el).color);
          if (fg[3] < 1) fg = over(fg, bg);
          const [hi, lo] = [lum(fg), lum(bg)].sort((a, b) => b - a);
          return { text: el.textContent, state: el.parentElement.dataset.state, ratio: (hi + 0.05) / (lo + 0.05) };
        });
      });
      assert(ratios.length >= 10, "label and value text of every meter was measured");
      for (const item of ratios) if (item.ratio < 4.5) weak.push(`${palette}/${item.state} "${item.text}" ${item.ratio.toFixed(2)}`);
    }
    assert.deepEqual(weak, [], "meter text is at least 4.5:1 on every palette");
    await rail.page.evaluate((id) => HarnessTheme.apply(id), palettes[0]);

    // A meter with no reading opens the panel with the reason instead of closing it.
    await rail.page.locator('[data-provider="gemini"]').click();
    await rail.page.locator("#quota-panel").waitFor({ state: "visible" });
    assert.equal(await rail.page.locator("#quota-panel .quota-heading strong").innerText(), "Gemini subscription quota");
    assert.match(await rail.page.locator("#quota-current").innerText(), /Gemini CLI quota not available: provider reports no quota\./);
    await rail.page.keyboard.press("Escape");
    await rail.page.locator("#quota-panel").waitFor({ state: "hidden" });
    assert(await rail.page.locator('[data-provider="gemini"]').evaluate((el) => el === document.activeElement), "Escape restores focus to the n/a meter");
    await rail.page.locator('[data-provider="local"]').click();
    await rail.page.locator("#quota-panel").waitFor({ state: "visible" });
    assert.match(await rail.page.locator("#quota-current").innerText(), /Local models quota not available: local models have no quota\./);
    await rail.page.keyboard.press("Escape");

    // Narrow and short windows. The rail is a vertical column down to 621 px wide and needs 670 px of
    // height without the meters plus about 200 px with them: the meters show only where all of that fits
    // (inside the rail, below its last icon, above Settings) and are dropped at 680 px of height or less
    // and at 620 px of width or less, where the rail is a 52 px top bar.
    const railFit = () =>
      rail.page.evaluate(() => {
        const rect = (selector) => document.querySelector(selector).getBoundingClientRect();
        const group = document.getElementById("provider-quotas");
        const bar = document.getElementById("app-topbar");
        return {
          shown: getComputedStyle(group).display !== "none",
          overflow: bar.scrollHeight - bar.clientHeight,
          top: rect("#provider-quotas").top,
          bottom: rect("#provider-quotas").bottom,
          above: rect("#rail-agents").bottom,
          settings: rect("#settings").top,
          width: innerWidth,
          height: innerHeight,
        };
      });
    for (const [width, height, shown] of [
      [1280, 860, true],
      [1000, 860, true],
      [800, 700, true],
      [621, 700, true],
      [900, 681, true],
      [900, 680, false],
      [900, 600, false],
      [720, 500, false],
      [620, 860, false],
      [390, 844, false],
    ]) {
      await rail.page.setViewportSize({ width, height });
      const fit = await railFit();
      assert.equal(fit.shown, shown, `meters ${shown ? "show" : "are dropped"} at ${width}x${height}`);
      assert.equal(fit.overflow, 0, `the rail does not overflow at ${width}x${height}`);
      if (shown) {
        assert(fit.top >= fit.above && fit.bottom <= fit.settings, `meters sit between the rail icons and Settings at ${width}x${height}: ${JSON.stringify(fit)}`);
        assert.equal(await rail.page.locator("#provider-quotas .provider-quota-meter").count(), 5);
      }
    }
    await rail.page.setViewportSize({ width: 1280, height: 860 });
    assert.deepEqual(rail.errors, []);

    // The rail primes the usage caches once per backend, only while the tab is visible, never for
    // providers that report no quota.
    const stale = [
      { backend: "codex", model: "m", quota: missing("quota_not_read") },
      { backend: "claude", model: "m", quota: missing("quota_stale") },
      { backend: "gemini", model: "m", quota: missing("quota_not_reported") },
      { backend: "deepseek", model: "m", quota: missing("balance_not_read") },
      { backend: "local", model: "m", quota: missing("local_no_quota") },
    ];
    const primed = await openRail(browser, { providers: stale, visibility: "hidden" });
    await primed.page.locator('[data-testid="quota-meter"]').first().waitFor();
    await feed(primed, stale);
    await primed.page.waitForTimeout(300);
    assert.deepEqual(primed.usageCalls, [], "nothing is primed while the tab is hidden");
    await primed.page.evaluate(() => { window.__visibility = "visible"; });
    for (let poll = 0; poll < 4; poll += 1) await feed(primed, stale);
    await primed.page.waitForTimeout(300);
    assert.deepEqual([...primed.usageCalls].sort(), ["claude", "codex", "deepseek"], "one call per backend across several polls, none for Gemini or local");
    assert.deepEqual(primed.errors, []);
    await primed.context.close();
    await rail.context.close();
    assert.deepEqual(errors, []);
    console.log("PASS: quota panel per provider, meter opens its own provider, DeepSeek balance");
    console.log("PASS: WP8 rail meters: order, n/a reasons, balance, one per backend, priming, palettes, narrow");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
