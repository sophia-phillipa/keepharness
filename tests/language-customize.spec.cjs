// Issue #16 P12 — Plugins page strings (chip, empty state, search label) keep their English text and show pt-BR
// when the browser language is pt-BR.
//
// (a) English: the Apps chip, the Apps empty state and the search label read exactly as before the catalog move.
// (b) pt-BR: a pt-BR browser context and a complete pt-BR catalog switch the same strings and <html lang>.
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

const LOCALES = path.join(__dirname, "..", "harness_ui", "assets", "locales");
// The pt-BR expectations come from the shipped catalog, so this spec holds no Portuguese text.
const PT_BR = JSON.parse(
  fs.readFileSync(path.join(LOCALES, "pt-br.json"), "utf8"),
);
const SHIPPED_INDEX = JSON.parse(
  fs.readFileSync(path.join(LOCALES, "index.json"), "utf8"),
);

const service = () => ({
  added: false,
  enabled: false,
  models: [],
  projects: ["sem-projeto"],
  permissions: {},
  mode: "scoped",
  integrations: [],
});
// The admin state the Plugins page reads; no plugins or apps are installed, so the Apps chip is empty.
const state = {
  settings: {
    services: { codex: service(), claude: service() },
    projects: [],
    logins: [],
    port: 8095,
    tailnet_port: 8095,
    uploads_enabled: false,
  },
  inventory: {
    platform: "Linux",
    services: [
      { id: "codex", name: "Codex CLI", found: true },
      { id: "claude", name: "Claude Code", found: true },
    ],
    projects: [],
    network: { online: true },
  },
  authentication: {},
  models: {},
  integrations: { codex: [], claude: [] },
  operations: [],
  credentials: {},
  status: { running: false },
};
const EMPTY_CATALOG = { items: [], warnings: [], actions: [] };

async function serveAdmin(route) {
  const url = new URL(route.request().url());
  const file = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
  const contentType = file.endsWith(".js")
    ? "text/javascript"
    : file.endsWith(".css")
      ? "text/css"
      : file.endsWith(".svg")
        ? "image/svg+xml"
        : file.endsWith(".json")
          ? "application/json"
          : "text/html";
  return route.fulfill({
    body: await fs.promises.readFile(
      path.join(
        __dirname,
        file.startsWith("assets/") ? "../harness_ui" : "../control",
        file,
      ),
    ),
    contentType,
  });
}

async function serveApi(route) {
  const url = new URL(route.request().url());
  const name = url.pathname.replace(/^.*\/api\//, "");
  if (name === "provider-state") {
    return route.fulfill({
      json: {
        snapshot: {
          provider: url.searchParams.get("provider"),
          fingerprint: "fp",
          items: [],
          warnings: [],
        },
        external_changes: [],
      },
    });
  }
  if (name === "integration-catalog")
    return route.fulfill({ json: EMPTY_CATALOG });
  return route.fulfill({ json: name === "state" ? state : {} });
}

// Opens the Plugins page; a pt-BR run also serves the catalog index with pt-BR marked complete.
async function openPlugins(browser, { locale, completePtBr }) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    ...(locale ? { locale } : {}),
  });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("http://admin.test/**", serveAdmin);
  await page.route("**/api/**", serveApi);
  if (completePtBr) {
    // The shipped index lists pt-BR as incomplete; serve it as complete for this run only.
    const index = {
      ...SHIPPED_INDEX,
      locales: SHIPPED_INDEX.locales.map((item) =>
        item.tag === "pt-BR" ? { ...item, complete: true } : item,
      ),
    };
    await page.route("**/assets/locales/index.json", (route) =>
      route.fulfill({ json: index }),
    );
  }
  await page.goto("http://admin.test/#plugins");
  await page.locator('[data-testid="plugins-panel"]').waitFor();
  await page.waitForFunction(
    () =>
      document
        .querySelector('[data-testid="plugins-list"]')
        ?.getAttribute("aria-busy") === "false",
  );
  return { context, page, errors };
}

// Reads the three strings: the Apps chip (label and count), the Apps empty state and the search label.
async function readStrings(page) {
  const panel = page.locator('[data-testid="plugins-panel"]');
  const chip = panel.locator('[data-testid="plugins-chip-apps"]');
  const chipText = await chip.innerText();
  await chip.click();
  const emptyText = await panel
    .locator('[data-testid="plugins-empty"]')
    .innerText();
  const searchLabel = await panel
    .locator('[data-testid="plugins-search"]')
    .getAttribute("aria-label");
  return { chipText, emptyText, searchLabel };
}

(async () => {
  const browser = await chromium.launch();
  try {
    const english = await openPlugins(browser, {});
    try {
      assert.equal(
        await english.page.evaluate(() => document.documentElement.lang),
        "en",
      );
      assert.deepEqual(await readStrings(english.page), {
        chipText: "Apps 0",
        emptyText: "No apps are listed.",
        searchLabel: "Search plugins",
      });
      assert.deepEqual(english.errors, []);
    } finally {
      await english.context.close();
    }

    const pt = await openPlugins(browser, {
      locale: "pt-BR",
      completePtBr: true,
    });
    try {
      assert.equal(
        await pt.page.evaluate(() => document.documentElement.lang),
        "pt-BR",
      );
      const expected = {
        chipText: PT_BR["customize.chip.apps"] + " 0",
        emptyText: PT_BR["customize.empty.apps"],
        searchLabel: PT_BR["customize.search.plugins"],
      };
      assert.notEqual(expected.emptyText, "No apps are listed.");
      assert.deepEqual(await readStrings(pt.page), expected);
      assert.deepEqual(pt.errors, []);
    } finally {
      await pt.context.close();
    }
    console.log(
      "PASS language-customize: English and pt-BR Plugins page strings",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
