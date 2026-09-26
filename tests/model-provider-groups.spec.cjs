const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 390, height: 700 },
    });
    await page.setContent(
      '<select id="model"></select><div id="model-menu" class="composer-menu"><div class="picker-options" role="listbox"></div></div>',
    );
    await page.addStyleTag({
      content: fs.readFileSync("agent_service/ui.css", "utf8"),
    });
    await page.addScriptTag({ path: "tail_ui/assets/components.js" });
    const source = fs.readFileSync("agent_service/ui.js", "utf8");
    await page.evaluate((source) => {
      window.busy = false;
      window.$ = (id) => document.getElementById(id);
      window.modelIcon = () => "◈";
      window.models = [
        "codex",
        "local",
        "claude",
        "codex",
        "deepseek",
        "gemini",
        "qwen",
      ].map((backend, i) => ({ id: `model-${i}`, backend }));
      models.forEach((m) => $("model").add(new Option(m.id, m.id)));
      eval(
        source.slice(
          source.indexOf("function renderPicker("),
          source.indexOf("function openComposerPicker("),
        ),
      );
      renderPicker("model");
    }, source);
    const groups = page.locator("#model-menu [role=group]");
    assert.deepEqual(
      await groups.evaluateAll((els) =>
        els.map((el) => el.getAttribute("aria-label")),
      ),
      ["Codex", "Local model", "Claude", "DeepSeek", "Google"],
    );
    assert.equal(await groups.first().locator("[role=option]").count(), 2);
    assert.equal(await groups.nth(1).locator("[role=option]").count(), 2);
    assert.equal(await page.locator(".model-logo-icon").count(), 7);
    for (const button of await page.locator("[role=option]").all())
      assert.match(await button.getAttribute("title"), /Select/);
    assert.equal(await page.locator("[aria-selected=true]").count(), 1);
    assert.equal(
      await page
        .locator("#model-menu strong")
        .first()
        .evaluate((el) => getComputedStyle(el).fontSize),
      "13px",
    );
    assert.equal(await page.locator("details[open]").count(), 1);
    assert.equal(await page.locator("summary .th-icon").count(), 10);
    await groups.nth(2).locator("summary").click();
    assert.equal(await groups.nth(2).getAttribute("open"), "");
    assert.equal(await groups.first().getAttribute("open"), null);
    console.log(
      "PASS: provider grouping, local aliases, icons, selection and compact typography",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
