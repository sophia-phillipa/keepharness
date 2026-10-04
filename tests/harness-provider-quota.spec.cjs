// L55: the quota panel names the provider it explains (PRD-R1-10), a rail meter opens its own
// provider whichever model is selected (CDX-R2-2) and DeepSeek shows its balance (PRD-R2-11).
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
    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.15.0"));
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
    const codexMeter = page.locator('.provider-quota-meter[data-backend="codex"]');
    const claudeMeter = page.locator('.provider-quota-meter[data-backend="claude"]');
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
    assert.match(await page.locator("#quota-short").innerText(), /Quota unavailable|Checking quota|remaining/, "the header still describes the selected model");
    assert.equal(await page.locator("#quota-toggle").getAttribute("aria-label").then((text) => /Claude/.test(text)), false, "the header summary stays on the selected Codex model");
    await page.keyboard.press("Escape");
    await page.locator("#quota-panel").waitFor({ state: "hidden" });
    assert(await claudeMeter.evaluate(el => el === document.activeElement), "Escape restores focus to the provider meter");

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
    await page.click("#settings");
    await page.click("#settings-quota");
    await page.locator("#quota-panel").waitFor({ state: "visible" });
    assert.equal(await heading(), "DeepSeek balance");
    await page.locator("#quota-current .quota-window").waitFor();
    assert.match(await page.locator("#quota-current").innerText(), /USD 12\.34 available[\s\S]*Granted 2\.00 · topped up 10\.34/);
    assert.match(await page.locator("#quota-note").innerText(), /Prepaid balance/);
    assert.doesNotMatch(await page.locator("#quota-panel").innerText(), /ChatGPT/);
    assert(usageCalls.includes("deepseek"));
    assert.deepEqual(errors, []);
    console.log("PASS: quota panel per provider, meter opens its own provider, DeepSeek balance");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
