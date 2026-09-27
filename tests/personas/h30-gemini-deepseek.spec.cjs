// H30 Gemini/DeepSeek user (P7/P6): non-default providers in the model menu,
// their quota wording, native-only isolation and the request they send.
"use strict";
const assert = require("node:assert/strict");
const { mockHarness, runPersona, visible } = require("./_harness.cjs");

const MODELS = [
  {
    id: "gpt-5.6-luna",
    backend: "codex",
    efforts: ["low"],
    permissions: { upload: true },
    execution_modes: ["native", "scoped"],
  },
  {
    id: "gemini-3-pro",
    backend: "gemini",
    efforts: ["configured"],
    permissions: { upload: true },
    execution_modes: ["native"],
  },
  {
    id: "deepseek-flash",
    backend: "deepseek",
    efforts: ["configured"],
    permissions: { upload: false },
    execution_modes: ["native"],
  },
];

async function open(page, over = {}) {
  const s = await mockHarness(page, {
    "GET /v1/models": {
      json: {
        models: MODELS,
        providers: { codex: true, gemini: true, deepseek: true },
        uploads_enabled: true,
      },
    },
    ...over,
  });
  await page.goto("http://harness.test");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  return s;
}

async function chooseModel(page, id) {
  if (!(await page.locator("#model-menu").isVisible()))
    await page.click("#model-trigger");
  const option = page.locator(`#model-menu [role=option][data-value="${id}"]`);
  if (!(await option.isVisible()))
    await option.locator("xpath=ancestor::details/summary").click();
  await option.click();
}

const text = (page, selector) => page.locator(selector).innerText();

runPersona("H30", [
  {
    title: "H30-S1 model menu groups Gemini and DeepSeek with quota wording",
    async run(page) {
      await open(page);
      await page.click("#model-trigger");
      await visible(page, "#model-menu");
      const groups = await page
        .locator("#model-menu [role=group]")
        .evaluateAll((els) => els.map((e) => e.getAttribute("aria-label")));
      assert.deepEqual(groups.slice(1), [
        // F-91 fixed: the Gemini group is now headed "Gemini CLI", matching
        // the option titles, #model-note and the quota identity.
        "Gemini CLI",
        "DeepSeek",
      ]);
      assert.equal(groups[0], "Codex");
      const geminiTitle = await page
        .locator('#model-menu [role=option][data-value="gemini-3-pro"]')
        .getAttribute("title");
      assert.match(geminiTitle, /Gemini CLI/);

      await chooseModel(page, "gemini-3-pro");
      assert.match(
        await text(page, "#model-note"),
        /Gemini CLI · Google account · subscription quota/,
      );
      assert.equal(await text(page, "#model-trigger-icon"), "✦");
      assert.match(
        await page.locator("#quota-model-identity").getAttribute("title"),
        /Gemini CLI/,
      );
      // F-92 fixed: renderQuotaIdentity() has a "gemini" entry, so the header
      // shows Gemini-specific wording and the panel names Gemini.
      assert.equal(await text(page, "#quota-short"), "Checking Gemini quota…");
      await page.click("#quota-toggle");
      await visible(page, "#quota-panel");
      assert.match(
        await text(page, "#quota-current"),
        /Gemini CLI reports its own subscription usage/,
      );
      // F-92 fixed (same finding): the quota panel heading now names Gemini.
      assert.equal(
        await text(page, "#quota-panel .quota-heading strong"),
        "Gemini subscription quota",
      );
    },
  },
  {
    title: "H30-S2 DeepSeek shows credits, native-only mode and sends natively",
    async run(page) {
      let usage = 0;
      const s = await open(page, {
        "GET /v1/usage": (route) => {
          usage++;
          return route.fulfill({ json: { available: false } });
        },
      });
      await chooseModel(page, "deepseek-flash");
      assert.equal(await text(page, "#quota-short"), "DeepSeek credits");
      assert.match(
        await text(page, "#model-note"),
        /DeepSeek API · uses your DeepSeek credits/,
      );
      assert.equal(await text(page, "#model-trigger-icon"), "🐋");
      await visible(page, "#execution-mode-unavailable");
      assert.equal(
        await text(page, "#execution-mode-unavailable"),
        "This model only offers native mode.",
      );
      assert.equal(await page.locator("#isolation-toggle").isDisabled(), true);
      // DeepSeek is text-only and this fixture denies uploads.
      assert.equal(await page.locator("#attach").isDisabled(), true);
      assert.match(
        await text(page, "#attachment-help"),
        /Attachments disabled for this model/,
      );
      await page.click("#quota-toggle");
      assert.match(
        await text(page, "#quota-current"),
        /your own DeepSeek account credits/,
      );
      // F-92 scope decision (JEV, WP-C2): only Gemini (and Claude) get their
      // own quota heading; DeepSeek keeps the generic "ChatGPT account quota"
      // heading, matching the triage row's fix scope.
      assert.equal(
        await text(page, "#quota-panel .quota-heading strong"),
        "ChatGPT account quota",
      );
      await page.click("#quota-toggle");

      const before = usage;
      await page.fill("#prompt", "Summarize this in one line.");
      await page.click("#send");
      await page.waitForFunction(
        () => !document.getElementById("prompt").value,
      );
      assert.equal(s.posts.length, 1);
      assert.equal(s.posts[0].backend, "deepseek");
      assert.equal(s.posts[0].model, "deepseek-flash");
      assert.equal(s.posts[0].effort, "configured");
      assert.equal(s.posts[0].execution_mode, "native");
      // Only Codex sends pre-flight a quota snapshot.
      assert.equal(usage, before);
      await page
        .locator("#execution-mode-indicator")
        .waitFor({ state: "visible" });
      assert.equal(
        await page
          .locator("#execution-mode-indicator")
          .getAttribute("aria-label"),
        "Native conversation · isolation off",
      );
    },
  },
  {
    title: "H30-S2b isolation chosen for Codex blocks a DeepSeek send",
    async run(page) {
      const s = await open(page);
      assert.equal(await page.locator("#model").inputValue(), "gpt-5.6-luna");
      await page.click("#isolation-toggle");
      assert.equal(
        await page.locator("#isolation-toggle").getAttribute("aria-checked"),
        "true",
      );
      await chooseModel(page, "deepseek-flash");
      await visible(page, "#execution-mode-unavailable");
      assert.match(
        await text(page, "#execution-mode-unavailable"),
        /does not offer isolated mode\. Choose a different model or change the mode before sending\./,
      );
      await page.fill("#prompt", "Should not leave the browser");
      await page.keyboard.press("Enter");
      await page.waitForFunction(() =>
        /doesn't offer the selected mode/.test(
          document.getElementById("status").textContent,
        ),
      );
      assert.equal(s.posts.length, 0);
      assert.equal(
        await page.locator("#prompt").inputValue(),
        "Should not leave the browser",
      );
      // F-94: the switch stays usable while it names a mode this model lacks, so
      // the user turns isolation off without leaving DeepSeek.
      assert.equal(await page.locator("#isolation-toggle").isDisabled(), false);
      assert.equal(
        await page.locator("#isolation-toggle").getAttribute("aria-checked"),
        "true",
      );
      await page.click("#isolation-toggle");
      assert.equal(
        await page.locator("#isolation-toggle").getAttribute("aria-checked"),
        "false",
      );
      assert.equal(await page.locator("#isolation-toggle").isDisabled(), true);
      assert.equal(await page.locator("#model").inputValue(), "deepseek-flash");
      assert.equal(
        await text(page, "#execution-mode-unavailable"),
        "This model only offers native mode.",
      );
      assert.equal(
        await page.locator("#prompt").inputValue(),
        "Should not leave the browser",
      );
      await page.click("#send");
      await page.waitForFunction(
        () => !document.getElementById("prompt").value,
      );
      assert.equal(s.posts.length, 1);
      assert.equal(s.posts[0].execution_mode, "native");
    },
  },
]);
