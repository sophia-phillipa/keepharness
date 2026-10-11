// Issue #16 P8 — static markup, the tour and the terminal pane keep their English text and show pt-BR when the language is pt-BR.
//
// (a) English: the skip link text, the Back button title, the settings search placeholder, the first tour
//     step title and the terminal notice read exactly as before.
// (b) pt-BR: a stored language and a complete pt-BR catalog switch the same strings and <html lang>.
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

// The pt-BR expectations come from the shipped catalog, so this spec holds no Portuguese text.
const PT_BR = JSON.parse(
  fs.readFileSync(
    path.join(__dirname, "..", "harness_ui", "assets", "locales", "pt-br.json"),
    "utf8",
  ),
);

// The same local model fixture as language-sidebar-composer.spec.cjs: one model, no internet or terminal.
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

// One conversation, so the history list is ready before the strings are read.
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

async function openHarness(browser, extraRoute) {
  const context = await browser.newContext({
    viewport: { width: 1280, height: 860 },
  });
  const page = await context.newPage();
  await page.addInitScript(() =>
    localStorage.setItem("keepharness-tour-seen", "0.16.0"),
  );
  // The page asks for /v1/models with a project_id query, so match on the path alone.
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

// Static markup: a text node, a title attribute and a placeholder.
async function staticText(page) {
  return {
    skip: await page.locator(".skip-link").textContent(),
    backTitle: await page.locator("#nav-back").getAttribute("title"),
    searchPlaceholder: await page
      .locator("#settings-search")
      .getAttribute("placeholder"),
  };
}

// Tour step 1 title, read while the tour is open.
async function tourTitle(page) {
  await page.evaluate(() => window.keepHarnessTour.start());
  const title = await page.locator("#tour-title").textContent();
  await page.evaluate(() => window.keepHarnessTour.stop());
  return title;
}

// Terminal pane notice: Enter with no session shows the start hint.
async function terminalNotice(page) {
  await page.evaluate(() =>
    document
      .getElementById("terminal-input")
      .dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
      ),
  );
  return page.locator("#terminal-notice").textContent();
}

(async () => {
  const browser = await chromium.launch();
  try {
    await setLanguage(null);
    const english = await openHarness(browser);
    try {
      assert.equal(
        await english.page.evaluate(() => document.documentElement.lang),
        "en",
      );
      assert.deepEqual(await staticText(english.page), {
        skip: "Skip to message",
        backTitle: "Back · Ctrl+[",
        searchPlaceholder: "Find a preference…",
      });
      assert.equal(await tourTitle(english.page), "Write and route work");
      assert.equal(
        await terminalNotice(english.page),
        "Open the terminal to start a session.",
      );
    } finally {
      await english.context.close();
    }

    await setLanguage("pt-BR");
    const pt = await openHarness(browser, completeIndex);
    try {
      assert.equal(
        await pt.page.evaluate(() => document.documentElement.lang),
        "pt-BR",
      );
      assert.deepEqual(await staticText(pt.page), {
        skip: PT_BR["static.topbar.skip_link"],
        backTitle: PT_BR["static.topbar.nav_back.title"],
        searchPlaceholder: PT_BR["static.settings.settings_search.placeholder"],
      });
      assert.equal(await tourTitle(pt.page), PT_BR["tour.write.title"]);
      assert.equal(
        await terminalNotice(pt.page),
        PT_BR["terminal.open_to_start"],
      );
    } finally {
      await setLanguage(null);
      await pt.context.close();
    }
    console.log(
      "PASS language-static: English static, tour and terminal text, pt-BR static, tour and terminal text",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
