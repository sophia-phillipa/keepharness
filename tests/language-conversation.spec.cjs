// Issue #16 P6 — conversation header and run-status strings keep their English text and show pt-BR when the language is pt-BR.
//
// (a) English: the header state pill of the home view reads "Draft", and the sidebar summaries of a failed and of a
//     queued conversation read "Failed" and "Waiting in the queue".
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

// The same local model fixture as language-sidebar-composer.spec.cjs: one model, uploads on, no internet or terminal.
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

// Two titled conversations: their sidebar rows show the summary for a failed and for a queued run.
const CONVERSATIONS = [
  {
    id: "c-failed",
    title: "Broken run",
    project: "sem-projeto",
    state: "failed",
    last_job_id: "t-failed",
  },
  {
    id: "c-queued",
    title: "Waiting run",
    project: "sem-projeto",
    state: "queued",
    last_job_id: "t-queued",
  },
];

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

async function openHome(browser, extraRoute) {
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
    (route) => route.fulfill({ json: { conversations: CONVERSATIONS } }),
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

// The sidebar summary of the row whose title is given; the titles are literal data, so they match in every language.
async function rowSummary(page, title) {
  return page
    .locator("#history .conversation-row")
    .filter({ hasText: title })
    .locator(".conversation-summary")
    .textContent();
}

// Fails the next job submission and returns how the status line shows the failure (role and error class).
async function sendFailureTone(page) {
  await page.route(
    (url) => url.pathname === "/v1/jobs",
    (route) =>
      route.request().method() === "POST"
        ? route.fulfill({ status: 500, json: { error: "send refused" } })
        : route.fallback(),
  );
  await page.locator("#prompt").fill("Tone probe");
  const rejected = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/v1/jobs" &&
      response.request().method() === "POST",
  );
  await page.locator("#send").click();
  await rejected;
  await page.waitForFunction(() => {
    const text = document.getElementById("status")?.textContent || "";
    return text !== "" && !/^(Sending request|Checking quota)/.test(text);
  });
  return page.locator("#status").evaluate((el) => ({
    role: el.getAttribute("role"),
    error: el.classList.contains("error"),
  }));
}

// Reads the strings this spec covers; the same reads run for both languages.
async function conversationText(page) {
  await page.waitForFunction(
    () => document.querySelector("#history .conversation-summary") !== null,
  );
  return {
    pill: await page.locator("#conversation-state-pill").textContent(),
    failed: await rowSummary(page, "Broken run"),
    queued: await rowSummary(page, "Waiting run"),
  };
}

(async () => {
  const browser = await chromium.launch();
  try {
    await setLanguage(null);
    const english = await openHome(browser);
    try {
      assert.equal(
        await english.page.evaluate(() => document.documentElement.lang),
        "en",
      );
      assert.deepEqual(await conversationText(english.page), {
        pill: "Draft",
        failed: "Failed",
        queued: "Waiting in the queue",
      });
      assert.deepEqual(await sendFailureTone(english.page), {
        role: "alert",
        error: true,
      });
      assert.deepEqual(
        await english.page.evaluate(() => [
          runLabel("queue_wait"),
          runLabelTone("interrupted"),
        ]),
        ["Waiting in the queue", "error"],
      );
    } finally {
      await english.context.close();
    }

    await setLanguage("pt-BR");
    const pt = await openHome(browser, completeIndex);
    try {
      assert.equal(
        await pt.page.evaluate(() => document.documentElement.lang),
        "pt-BR",
      );
      // The header pill is rewritten by the runtime after the catalog loads, so wait for the pt-BR text.
      await pt.page.waitForFunction(
        (expected) =>
          document.getElementById("conversation-state-pill")?.textContent ===
          expected,
        PT_BR["conversation.header.draft"],
      );
      assert.deepEqual(await conversationText(pt.page), {
        pill: PT_BR["conversation.header.draft"],
        failed: PT_BR["conversation.summary.failed"],
        queued: PT_BR["run_status.activity.queued"],
      });
      // A translated send failure is an error notice too: same role and class as the English one.
      assert.deepEqual(await sendFailureTone(pt.page), {
        role: "alert",
        error: true,
      });
      // Run labels follow the active catalog; the tone comes from the run state, not the text.
      assert.deepEqual(
        await pt.page.evaluate(() => [
          runLabel("running"),
          runLabelTone("running"),
        ]),
        [PT_BR["run_status.label.running"], "progress"],
      );
    } finally {
      await setLanguage(null);
      await pt.context.close();
    }
    console.log(
      "PASS language-conversation: English header and run-status summaries, pt-BR header and run-status summaries, send-failure tone in both",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
