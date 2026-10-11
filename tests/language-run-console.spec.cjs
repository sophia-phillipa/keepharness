// Issue #16 P9 — the run console keeps its English text and shows pt-BR when the language is pt-BR.
//
// (a) English: the tabs, the Pipeline empty state and the "Needs you (N)" button read exactly as before.
// (b) pt-BR: a stored language and a complete pt-BR catalog switch the same strings.
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

// The pt-BR expectations come from the shipped catalog, so this spec holds no Portuguese text.
const PT_BR = JSON.parse(
  fs.readFileSync(
    path.join(__dirname, "..", "harness_ui", "assets", "locales", "pt-br.json"),
    "utf8",
  ),
);
const TABS = ["Pipeline", "Timeline", "Logs", "Runs", "Agents"];
const EN_EMPTY = "Select a run to inspect its recorded steps.";
const EN_NEEDS_YOU = /^Needs you \(\d+\)$/;

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

async function openConsole(page) {
  await page.locator("#run-status-toggle").click();
  await page.locator("#run-console").waitFor({ state: "visible" });
}

// Checks the tabs, the empty Pipeline view and the inbox button against the given strings.
async function assertConsole(page, tabLabels, emptyText, needsYouPattern) {
  await openConsole(page);
  for (const label of tabLabels) {
    await page
      .getByRole("tab", { name: label, exact: true })
      .waitFor({ state: "visible" });
  }
  await page.getByRole("tab", { name: tabLabels[0], exact: true }).click();
  await page
    .getByText(emptyText, { exact: true })
    .waitFor({ state: "visible" });
  assert.match(
    await page.locator("#needs-you-toggle").textContent(),
    needsYouPattern,
  );
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
      await assertConsole(english.page, TABS, EN_EMPTY, EN_NEEDS_YOU);
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
      const tabLabels = TABS.map(
        (name) => PT_BR["run_console.tab." + name.toLowerCase()],
      );
      const emptyText = PT_BR["run_console.pipeline.select_run"];
      const [head, tail] = PT_BR["run_console.inbox.button"]
        .split("{count}")
        .map(escapeRegExp);
      await assertConsole(
        pt.page,
        tabLabels,
        emptyText,
        new RegExp("^" + head + "\\d+" + tail + "$"),
      );
    } finally {
      await setLanguage(null);
      await pt.context.close();
    }
    console.log(
      "PASS language-run-console: English and pt-BR run console strings",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
