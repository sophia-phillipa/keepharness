// D42 / D47: one display name per provider in the app and the admin, "through the Codex CLI"
// said in the model note, and no Gemini setup card or admin-panel pointer in this release.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");

const models = [
  ["codex-model", "codex"],
  ["local-model", "local"],
  ["claude-sonnet-4-5", "claude"],
  ["deepseek-flash", "deepseek"],
].map(([id, backend]) => ({ id, name: id, backend, efforts: ["low"] }));

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 1280, height: 860 },
    });
    if (!process.env.HARNESS_URL)
      await page.route("http://panel.test/**", (route) => {
        const pathname = new URL(route.request().url()).pathname;
        return route.fulfill({
          path: path.join(
            __dirname,
            "..",
            pathname.startsWith("/assets/") ? "harness_ui" : "agent_service",
            pathname === "/" ? "index.html" : pathname,
          ),
        });
      });
    await page.route("**/v1/**", (route) => {
      const pathname = new URL(route.request().url()).pathname;
      const data =
        pathname === "/v1/models"
          ? {
              models,
              providers: { codex: true, local: true, deepseek: true },
              uploads_enabled: false,
            }
          : pathname === "/v1/projects"
            ? { projects: ["sem-projeto"], details: {} }
            : pathname === "/v1/conversations"
              ? { conversations: [] }
              : pathname === "/v1/catalog"
                ? { agents: [], skills: [] }
                : pathname === "/v1/version"
                  ? { version: "fixture", build: "fixture" }
                  : {};
      return route.fulfill({ json: data });
    });
    await page.addInitScript(() =>
      localStorage.setItem("keepharness-tour-seen", "0.16.0"),
    );
    await page.goto(process.env.HARNESS_URL || "http://panel.test/");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });

    // The shared table feeds both surfaces.
    const names = await page.evaluate(() => HarnessUI.providerNames);
    assert.deepEqual(
      ["codex", "claude", "deepseek", "local", "maestro"].map(
        (id) => names[id],
      ),
      ["Codex", "Claude Code", "DeepSeek", "Local models", undefined],
    );

    // Settings › Models: cards say what runs the model once, and offer no Gemini setup.
    await page.keyboard.press("Control+,");
    await page.click('[data-settings="models"]');
    const cards = page.locator("#catalog-models");
    await cards
      .getByText("DeepSeek model; runs through the Codex CLI.")
      .waitFor();
    const text = await cards.innerText();
    assert.match(text, /Local model; runs through the Codex CLI\./);
    assert.match(text, /Claude Code/);
    assert.doesNotMatch(
      text,
      /Gemini/i,
      "no Gemini card in this release (D47)",
    );
    await page.click("#settings-close");

    // The model note names the engine for DeepSeek and local models.
    await page.evaluate(() => {
      const select = document.getElementById("model");
      select.value = "deepseek-flash";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    assert.match(
      await page.locator("#model-note").innerText(),
      /DeepSeek API · uses your DeepSeek credits · runs through the Codex CLI/,
    );

    // A Gemini condition never sends anyone to an admin card that does not exist.
    const copy = await page.evaluate(
      () => executionCondition("provider_unavailable", "gemini")?.message ?? "",
    );
    assert.equal(
      copy,
      "Gemini is not available in this KeepHarness release. Select another provider to continue this conversation.",
    );
    assert.doesNotMatch(copy, /admin panel/i);
    console.log(
      "PASS: canonical provider names, Codex CLI note and release-independent Gemini copy",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
