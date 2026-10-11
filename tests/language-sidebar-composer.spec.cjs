// Issue #16 P5 — composer and sidebar strings keep their English text and show pt-BR when the language is pt-BR.
//
// (a) English: the attachment help, permission summary, model note, effort label and the untitled
//     conversation row (title, actions label, rename item) read exactly as before.
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

// The same local model fixture as composer-limits.spec.cjs: one model, uploads on, no internet or terminal.
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

// A conversation with no title; the row falls back to the untitled label.
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

async function openComposer(browser, extraRoute) {
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
  // One conversation without a title, so the sidebar row shows the untitled label.
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

// Reads the sidebar strings of the untitled conversation row: title, actions label and rename item.
async function sidebarText(page) {
  const row = page.locator("#history .conversation-row").first();
  return {
    title: await row.locator(".conversation-title").textContent(),
    actions: await row.locator("summary").getAttribute("aria-label"),
    rename: await row
      .locator(".conversation-actions-menu button")
      .first()
      .textContent(),
  };
}

// Reads the composer strings this spec covers; the same reads run for both languages.
async function composerText(page) {
  return {
    help: await page.locator("#attachment-help").textContent(),
    attachTitle: await page.locator("#attach").getAttribute("title"),
    permissions: await page.locator("#model-permissions").textContent(),
    note: await page.locator("#model-note").textContent(),
    effort: await page.locator("#effort option").first().textContent(),
    mode: await page.locator("#execution-mode-label").textContent(),
  };
}

(async () => {
  const browser = await chromium.launch();
  try {
    await setLanguage(null);
    const english = await openComposer(browser);
    try {
      assert.equal(
        await english.page.evaluate(() => document.documentElement.lang),
        "en",
      );
      const help =
        "Up to 100 MiB per file · audio: up to 4h, transcribed locally · Images depending on model support · " +
        "MP4 unavailable for this model or mode";
      const text = await composerText(english.page);
      assert.equal(text.help, help);
      assert.equal(text.attachTitle, "Attach file. " + help);
      assert.equal(
        text.permissions,
        "Attachments allowed · Internet disabled · Terminal disabled",
      );
      assert.equal(
        text.note,
        "Local model runs on the server through the Codex CLI · no OpenAI quota · check the sources",
      );
      assert.equal(text.effort, "Low");
      assert.equal(text.mode, "Isolated conversation");
      assert.deepEqual(await sidebarText(english.page), {
        title: "Conversation",
        actions: "Actions for Conversation",
        rename: "Rename conversation",
      });
    } finally {
      await english.context.close();
    }

    await setLanguage("pt-BR");
    const pt = await openComposer(browser, completeIndex);
    try {
      assert.equal(
        await pt.page.evaluate(() => document.documentElement.lang),
        "pt-BR",
      );
      const help = PT_BR["composer.attachments.help"]
        .replace("{images}", PT_BR["composer.attachments.images_depend"])
        .replace("{video}", PT_BR["composer.attachments.video_unavailable"]);
      const text = await composerText(pt.page);
      assert.equal(text.help, help);
      assert.equal(
        text.attachTitle,
        PT_BR["composer.attachments.title"].replace("{help}", help),
      );
      assert.equal(
        text.permissions,
        [
          PT_BR["composer.permissions.upload_allowed"],
          PT_BR["composer.permissions.internet_disabled"],
          PT_BR["composer.permissions.shell_disabled"],
        ].join(" · "),
      );
      assert.equal(text.note, PT_BR["composer.model_note.local"]);
      assert.equal(text.effort, PT_BR["composer.effort.low"]);
      assert.equal(text.mode, PT_BR["composer.execution_mode.isolated"]);
      const untitled = PT_BR["sidebar.conversation.untitled"];
      assert.deepEqual(await sidebarText(pt.page), {
        title: untitled,
        actions: PT_BR["sidebar.conversation.actions_for"].replace(
          "{title}",
          untitled,
        ),
        rename: PT_BR["sidebar.menu.rename"],
      });
    } finally {
      await setLanguage(null);
      await pt.context.close();
    }
    console.log(
      "PASS language-sidebar-composer: English composer and sidebar text, pt-BR composer and sidebar text",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
