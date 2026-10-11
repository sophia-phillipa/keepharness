// Issue #16 P14 — Settings › Appearance switches the interface language in place, without a reload.
//
// (a) The language select lists English and the complete locales of index.json (pt-BR served as complete here).
// (b) Choosing pt-BR re-translates a static data-i18n element, the sidebar's untitled row and a composer
//     label, with no navigation and no load event: window.__noReload survives the switch.
// (c) The choice is saved: PATCH /v1/ui-state carries language.
// (d) A toast shown before the switch keeps its English text.
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { executionModes } = require("./model-fixture.cjs");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

const REAL_HARNESS = (process.env.HARNESS_URL || "").replace(/\/$/, "");
assert(
  REAL_HARNESS,
  "HARNESS_URL is required (run through scripts/test-ui.sh)",
);

// The pt-BR expectations come from the shipped catalogs, so this spec holds no Portuguese text.
const LOCALES = path.join(__dirname, "..", "harness_ui", "assets", "locales");
const PT_BR = JSON.parse(
  fs.readFileSync(path.join(LOCALES, "pt-br.json"), "utf8"),
);
const INDEX = JSON.parse(
  fs.readFileSync(path.join(LOCALES, "index.json"), "utf8"),
);
const NAME = Object.fromEntries(
  INDEX.locales.map((locale) => [locale.tag, locale.name]),
);

// The same local model fixture as language-sidebar-composer.spec.cjs.
const MODELS = {
  models: [
    {
      id: "fixture",
      name: "Fixture",
      backend: "local",
      execution_modes: executionModes("local"),
      efforts: ["low"],
      permissions: { upload: true },
    },
  ],
  providers: { local: true },
  uploads_enabled: true,
};

// A conversation with no title; the sidebar row falls back to the untitled label.
const UNTITLED = {
  id: "c-untitled",
  title: "",
  project: "sem-projeto",
  state: "completed",
  last_job_id: "t-untitled",
};

async function setLanguage(language) {
  const response = await fetch(REAL_HARNESS + "/v1/ui-state", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ values: { language } }),
  });
  assert(response.ok, "PATCH /v1/ui-state failed: " + response.status);
}

// The shipped index lists pt-BR as incomplete; serve it as complete for the pt-BR run only.
async function completeIndex(route) {
  const response = await route.fetch();
  const index = await response.json();
  index.locales = index.locales.map((locale) =>
    locale.tag === "pt-BR" ? { ...locale, complete: true } : locale,
  );
  await route.fulfill({ response, json: index });
}

async function openChat(browser, extraRoute) {
  const context = await browser.newContext({
    viewport: { width: 1280, height: 860 },
  });
  const page = await context.newPage();
  await page.addInitScript(() =>
    localStorage.setItem("keepharness-tour-seen", "0.16.0"),
  );
  await page.route(
    (url) => url.pathname === "/v1/models",
    (route) => route.fulfill({ json: MODELS }),
  );
  await page.route(
    (url) => url.pathname === "/v1/conversations",
    (route) => route.fulfill({ json: { conversations: [UNTITLED] } }),
  );
  if (extraRoute) await page.route("**/assets/locales/index.json", extraRoute);
  await page.goto(REAL_HARNESS + "/");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  await page.waitForFunction(
    () =>
      document.getElementById("model-label")?.textContent !== "Loading models…",
  );
  await page
    .locator("#history .conversation-row")
    .first()
    .waitFor({ state: "attached" });
  return { context, page };
}

const optionValues = (page) =>
  page
    .locator("#language-select option")
    .evaluateAll((options) => options.map((option) => option.value));

(async () => {
  const browser = await chromium.launch();
  try {
    await setLanguage(null);

    // (a) with the shipped index only English is offered; the routed index adds pt-BR.
    const shipped = await openChat(browser);
    try {
      assert.equal(
        await shipped.page.locator("#language-select").inputValue(),
        "en",
      );
      assert(
        (await optionValues(shipped.page)).includes("en"),
        "English must be an option",
      );
    } finally {
      await shipped.context.close();
    }

    const { context, page } = await openChat(browser, completeIndex);
    try {
      await page.waitForFunction(
        () => document.querySelectorAll("#language-select option").length === 2,
      );
      assert.deepEqual(
        await page
          .locator("#language-select option")
          .evaluateAll((options) =>
            options.map((option) => [option.value, option.textContent]),
          ),
        [
          ["en", NAME.en],
          ["pt-BR", NAME["pt-BR"]],
        ],
      );
      assert.equal(await page.evaluate(() => HarnessPrefs.server), true);
      assert.equal(
        await page.locator("label[for='language-select']").textContent(),
        "Language",
      );

      // (d) a persistent toast raised in English before the switch.
      await page.evaluate(() =>
        window.HarnessUI.toast("English toast kept", { persistent: true }),
      );
      await page.evaluate(() => {
        window.__noReload = true;
      });
      let loads = 0;
      page.on("load", () => {
        loads += 1;
      });

      // (c) the saved value reaches the store through the debounced PATCH. The select sits in Settings ›
      // Appearance, so open that section first; the request wait and the selection run together, so a
      // failed selection is reported as itself rather than as a later wait timeout.
      await page.evaluate(() => openSettings("appearance"));
      await page.locator("#language-select").waitFor({ state: "visible" });
      const [saved] = await Promise.all([
        page.waitForRequest(
          (request) =>
            request.method() === "PATCH" &&
            request.url().endsWith("/v1/ui-state") &&
            "language" in (request.postDataJSON()?.values ?? {}),
        ),
        page.selectOption("#language-select", "pt-BR"),
      ]);
      assert.equal(saved.postDataJSON().values.language, "pt-BR");

      // (b) the page re-translates in place: no reload, no load event, same document.
      assert.equal(
        await page.evaluate(() => document.documentElement.lang),
        "pt-BR",
      );
      assert.equal(await page.evaluate(() => window.__noReload), true);
      assert.equal(loads, 0);
      assert.equal(
        await page.locator("#new").textContent(),
        PT_BR["static.sidebar.new"],
      );
      assert.equal(
        await page.locator("#settings-title").textContent(),
        PT_BR["static.settings.settings_title"],
      );
      assert.equal(
        await page.locator("label[for='language-select']").textContent(),
        PT_BR["settings.appearance.language_label"],
      );
      assert.equal(
        await page
          .locator("#history .conversation-row")
          .first()
          .locator(".conversation-title")
          .textContent(),
        PT_BR["sidebar.conversation.untitled"],
      );
      assert.equal(
        await page.locator("#execution-mode-label").textContent(),
        PT_BR["composer.execution_mode.isolated"],
      );

      // (d) the English toast keeps its text.
      assert(
        (await page.locator("#th-toast").textContent()).includes(
          "English toast kept",
        ),
        "the toast raised before the switch keeps its English text",
      );
    } finally {
      await setLanguage(null);
      await context.close();
    }
    console.log(
      "PASS language-switch: select lists English and pt-BR, live switch without reload, saved, toast kept",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
