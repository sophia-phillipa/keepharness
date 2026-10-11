// Issue #16 P3 — settings search keeps its English text and shows pt-BR when the language is pt-BR.
//
// (a) English: the status lines read exactly as before the catalog move.
// (b) pt-BR: a stored language and a complete pt-BR catalog switch the same strings and <html lang>.
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

const REAL_HARNESS = (process.env.HARNESS_URL || "").replace(/\/$/, "");
assert(
  REAL_HARNESS,
  "HARNESS_URL is required (run through scripts/test-ui.sh)",
);

const NO_MATCH = "zz-no-such-preference";
const MATCH = "Message text size";
// The pt-BR expectations come from the shipped catalog, so this spec holds no Portuguese text.
const PT_BR = JSON.parse(
  fs.readFileSync(
    path.join(__dirname, "..", "harness_ui", "assets", "locales", "pt-br.json"),
    "utf8",
  ),
);

async function setLanguage(language) {
  const response = await fetch(REAL_HARNESS + "/v1/ui-state", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ values: { language } }),
  });
  assert(response.ok, "PATCH /v1/ui-state failed: " + response.status);
}

async function openHarness(browser, route) {
  const context = await browser.newContext({
    viewport: { width: 1280, height: 860 },
  });
  const page = await context.newPage();
  await page.addInitScript(() =>
    localStorage.setItem("keepharness-tour-seen", "0.16.0"),
  );
  if (route) await page.route("**/assets/locales/index.json", route);
  await page.goto(REAL_HARNESS + "/");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  return { context, page };
}

async function openSearch(page) {
  await page.keyboard.press("Control+,");
  await page.locator("#settings-dialog").waitFor({ state: "visible" });
  const search = page.locator("#settings-search");
  await search.waitFor({ state: "visible" });
  await search.focus();
  return search;
}

// The status also gets " Checking local admin…" while the embedded admin answers; wait for that to end.
async function settledStatus(page, query) {
  await page.locator("#settings-search").fill(query);
  await page.waitForFunction(
    () =>
      !document
        .querySelector("#settings-search-status")
        .textContent.includes("Checking local admin"),
  );
  return page.locator("#settings-search-status").textContent();
}

(async () => {
  const browser = await chromium.launch();
  try {
    const english = await openHarness(browser);
    try {
      assert.equal(
        await english.page.evaluate(() => document.documentElement.lang),
        "en",
      );
      await openSearch(english.page);
      assert.equal(
        await settledStatus(english.page, NO_MATCH),
        "No matching preferences.",
      );
      assert.match(
        await settledStatus(english.page, MATCH),
        /^\d+ preferences found\.$/,
      );
    } finally {
      await english.context.close();
    }

    await setLanguage("pt-BR");
    // The shipped index lists pt-BR as incomplete; serve it as complete for this run only.
    const completeIndex = async (route) => {
      const response = await route.fetch();
      const index = await response.json();
      index.locales = index.locales.map((locale) =>
        locale.tag === "pt-BR" ? { ...locale, complete: true } : locale,
      );
      await route.fulfill({ response, json: index });
    };
    const pt = await openHarness(browser, completeIndex);
    try {
      assert.equal(
        await pt.page.evaluate(() => document.documentElement.lang),
        "pt-BR",
      );
      await openSearch(pt.page);
      const none = PT_BR["settings_search.status.none"];
      assert.notEqual(none, "No matching preferences.");
      assert.equal(await settledStatus(pt.page, NO_MATCH), none);
      const [head, tail] =
        PT_BR["settings_search.status.found"].split("{count}");
      const found = await settledStatus(
        pt.page,
        PT_BR["static.settings.reading_size_label"],
      );
      assert(found.startsWith(head) && found.endsWith(tail), found);
      assert.match(
        found.slice(head.length, found.length - tail.length),
        /^\d+$/,
      );
    } finally {
      await setLanguage(null);
      await pt.context.close();
    }
    console.log(
      "PASS language-settings-search: English and pt-BR status lines",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
