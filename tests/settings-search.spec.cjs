// Issue #55 — Settings search acceptance matrix.
//
// P1 beginner: visible names/descriptions find individual preferences; no-match recovers.
// P2 rushed user: typing, clearing and Escape never write a preference.
// P3 domain professional: a reached preference still uses the durable UI-state backend.
// P4 keyboard/accessibility user: results announce state, support keys and meet AA contrast.
// P5 mobile/unstable network user: unavailable embedded admin explains and retries.
// P6 harness engineer: admin descriptors are authenticated, allowlisted and preserve iframe state.
// P7 UI/UX specialist: indexing follows displayed labels and remote capability boundaries.
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

const ROOT = path.resolve(__dirname, "..");
const ADMIN = (process.env.ADMIN_URL || "").replace(/\/$/, "");
const REAL_HARNESS = (process.env.HARNESS_URL || "").replace(/\/$/, "");
const RESULTS = "#settings-search-results";

assert(ADMIN, "ADMIN_URL is required (run through scripts/test-ui.sh)");
assert(REAL_HARNESS, "HARNESS_URL is required (run through scripts/test-ui.sh)");

function contentType(file) {
  if (file.endsWith(".js")) return "text/javascript";
  if (file.endsWith(".css")) return "text/css";
  if (file.endsWith(".svg")) return "image/svg+xml";
  return "text/html";
}

async function staticResponse(route, pathname) {
  const file = pathname === "/" ? "index.html" : pathname.slice(1);
  const directory = file.startsWith("assets/") ? "harness_ui" : "agent_service";
  try {
    return await route.fulfill({
      body: await fs.readFile(path.join(ROOT, directory, file)),
      contentType: contentType(file),
    });
  } catch {
    return route.fulfill({ status: 404, body: "" });
  }
}

const MODELS = {
  models: [{ id: "fixture", name: "Fixture", backend: "codex", efforts: ["low"] }],
  providers: { codex: true },
  uploads_enabled: false,
  admin_url: ADMIN + "/",
};

const API = {
  "/v1/projects": { projects: ["sem-projeto"], details: {} },
  "/v1/conversations": { conversations: [] },
  "/v1/usage": { available: false },
  "/v1/version": { version: "fixture", build: "settings-search" },
  "/v1/catalog": { agents: [], skills: [], warnings: [] },
  "/v1/resources": { items: [], warnings: [] },
};

async function addOwnerCookie(context) {
  const raw = process.env.ADMIN_LOCAL_COOKIE || "";
  const split = raw.indexOf("=");
  if (split < 1) return;
  await context.addCookies([{
    name: raw.slice(0, split),
    value: raw.slice(split + 1),
    domain: "127.0.0.1",
    path: "/",
    httpOnly: true,
    sameSite: "Strict",
  }]);
}

async function installHarness(page, origin, uiState, patches) {
  await page.route(origin + "/**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (!pathname.startsWith("/v1/")) return staticResponse(route, pathname);
    if (pathname === "/v1/models") return route.fulfill({ json: MODELS });
    if (pathname === "/v1/ui-state") {
      if (request.method() === "PATCH") {
        const body = request.postDataJSON();
        patches.push(body);
        Object.assign(uiState.values, body.values);
      }
      return route.fulfill({ json: uiState });
    }
    return route.fulfill({ json: API[pathname] || {} });
  });
}

async function openFixture(browser, origin, uiState, options = {}) {
  const context = await browser.newContext({
    viewport: options.viewport || { width: 1280, height: 860 },
    colorScheme: options.colorScheme || "light",
  });
  await addOwnerCookie(context);
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const patches = [];
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
  await installHarness(page, origin, uiState, patches);
  await page.goto(origin + "/");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  return { context, page, patches, errors };
}

async function openSearch(page) {
  await page.keyboard.press("Control+,");
  await page.locator("#settings-dialog").waitFor({ state: "visible" });
  const search = page.locator("#settings-search");
  await search.waitFor({ state: "visible" });
  assert.equal(await search.getAttribute("type"), "search");
  await search.focus();
  return search;
}

const buttons = (page) => page.locator(RESULTS + " button");
const resultNamed = (page, pattern) => buttons(page).filter({ hasText: pattern });

async function query(page, text) {
  await page.locator("#settings-search").fill(text);
  await page.waitForFunction(
    ({ selector, value }) => document.querySelector(selector)?.value === value,
    { selector: "#settings-search", value: text },
  );
}

async function preferenceWrites(page) {
  await page.evaluate(() => window.HarnessPrefs?.flush?.());
  return page.waitForTimeout(50);
}

async function waitForFocused(locator) {
  await locator.evaluate((element) => new Promise((resolve, reject) => {
    let frames = 0;
    const check = () => {
      if (document.activeElement === element) resolve();
      else if (++frames < 120) requestAnimationFrame(check);
      else reject(new Error("expected control did not receive focus"));
    };
    check();
  }));
}

function contrast(a, b) {
  const lum = (rgb) => rgb.slice(0, 3).map((v) => {
    const n = v / 255;
    return n <= 0.03928 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4;
  }).reduce((sum, value, i) => sum + value * [0.2126, 0.7152, 0.0722][i], 0);
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

async function searchContrast(page) {
  return page.locator("#settings-search, #settings-search-clear, #settings-search-status, #settings-search-results button").evaluateAll((elements) => {
    const rgba = (value) => {
      const match = value.match(/[\d.]+/g)?.map(Number) || [];
      return [match[0] || 0, match[1] || 0, match[2] || 0, match[3] ?? 1];
    };
    const over = (front, back) => [0, 1, 2].map((i) => front[i] * front[3] + back[i] * (1 - front[3])).concat(1);
    const background = (element) => {
      const layers = [];
      for (let node = element; node; node = node.parentElement) {
        const color = rgba(getComputedStyle(node).backgroundColor);
        if (color[3]) layers.push(color);
        if (color[3] === 1) break;
      }
      return layers.reverse().reduce((value, layer) => over(layer, value), [255, 255, 255, 1]);
    };
    return elements.filter((element) => {
      const box = element.getBoundingClientRect();
      return box.width && box.height && !element.disabled;
    }).map((element) => {
      const style = getComputedStyle(element);
      const bg = background(element);
      return {
        name: element.id || element.textContent.trim().slice(0, 50),
        foreground: over(rgba(style.color), bg),
        background: bg,
      };
    });
  });
}

async function runScenario(id, name, work) {
  await work();
  console.log("PASS " + id + " " + name);
}

(async () => {
  const ownerCookie = process.env.ADMIN_LOCAL_COOKIE || "";
  const adminPageResponse = await fetch(ADMIN + "/", { headers: { Cookie: ownerCookie } });
  const policy = adminPageResponse.headers.get("content-security-policy") || "";
  const fixtureOrigin = policy.match(/frame-ancestors (http:\/\/127\.0\.0\.1:\d+)/)?.[1];
  assert(fixtureOrigin, "admin CSP must name the harness origin: " + policy);
  const stateResponse = await fetch(REAL_HARNESS + "/v1/ui-state");
  assert(stateResponse.ok, "the fixture harness exposes the real UI-state backend");
  const uiStateTemplate = await stateResponse.json();
  const adminCookie = (adminPageResponse.headers.get("set-cookie") || "").split(";", 1)[0];
  assert.match(adminCookie, /^admin=/, "owner handshake must issue the admin cookie");
  const adminStateResponse = await fetch(ADMIN + "/api/state", { headers: { Cookie: ownerCookie + "; " + adminCookie } });
  assert(adminStateResponse.ok, "the fixture admin exposes synthetic state");
  const adminStateTemplate = await adminStateResponse.json();
  const freshState = () => ({ ...uiStateTemplate, values: {} });
  const browser = await chromium.launch({ args: ["--disable-features=LocalNetworkAccessChecks"] });
  try {
    await runScenario("P1-S1", "individual labels, descriptions, destinations and empty recovery", async () => {
      const { context, page, errors } = await openFixture(browser, fixtureOrigin, freshState());
      const search = await openSearch(page);
      assert.equal(await page.locator(RESULTS).getAttribute("role"), "list");
      assert.equal(await page.locator("#settings-search-status").getAttribute("role"), "status");
      await query(page, "side conversations");
      const panel = resultNamed(page, /Panel position/i);
      await panel.waitFor();
      assert.match(await panel.innerText(), /Appearance/i, "the result identifies its destination");
      assert.match(await panel.innerText(), /side conversations/i, "the displayed description is searchable and visible");
      await query(page, "preference-that-does-not-exist");
      assert.equal(await buttons(page).count(), 0);
      assert.match(await page.locator("#settings-search-status").innerText(), /no (matching )?(settings|preferences|results)/i);
      await page.locator("#settings-search-clear").click();
      assert.equal(await search.inputValue(), "");
      assert.equal(await search.evaluate((element) => element === document.activeElement), true);
      assert.deepEqual(errors, []);
      await context.close();
    });

    await runScenario("P2-S1", "query, clear and Escape are side-effect free", async () => {
      const { context, page, patches } = await openFixture(browser, fixtureOrigin, freshState());
      const search = await openSearch(page);
      await preferenceWrites(page);
      patches.length = 0;
      await search.pressSequentially("theme", { delay: 5 });
      await page.locator("#settings-search-clear").click();
      await query(page, "markers");
      await page.keyboard.press("Escape");
      assert.equal(await search.inputValue(), "");
      assert.equal(await search.evaluate((element) => element === document.activeElement), true);
      assert(await page.locator("#settings-dialog").isVisible(), "first Escape clears search without closing Settings");
      await preferenceWrites(page);
      assert.deepEqual(patches, [], "search interactions never PATCH preferences");
      await context.close();
    });

    await runScenario("P3-S1", "reached control keeps navigation and durable preference behavior", async () => {
      const context = await browser.newContext({ viewport: { width: 1280, height: 860 } });
      const page = await context.newPage();
      const writes = [];
      page.on("request", (request) => {
        if (request.method() === "PATCH" && new URL(request.url()).pathname === "/v1/ui-state") writes.push(request.postDataJSON());
      });
      await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
      await page.goto(REAL_HARNESS + "/");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
      await openSearch(page);
      writes.length = 0;
      await query(page, "Message text size");
      await resultNamed(page, /Message text size/i).click();
      assert.equal(await page.locator("#reading-size").evaluate((element) => element === document.activeElement), true);
      await page.locator("#reading-size").selectOption("19");
      await page.evaluate(() => window.HarnessPrefs.flush());
      await page.waitForFunction(() => getComputedStyle(document.documentElement).getPropertyValue("--th-reading-size").trim() === "19px");
      assert(writes.some((write) => write.values?.reading_size === "19"), JSON.stringify(writes));
      await page.keyboard.press("Control+[");
      await page.locator("#settings-dialog").waitFor({ state: "hidden" });
      await page.keyboard.press("Control+]");
      await page.locator("#settings-dialog").waitFor({ state: "visible" });
      assert.equal(await page.locator("#reading-size").inputValue(), "19");
      await context.close();
    });

    await runScenario("P4-S1", "keyboard selection, focus, announcement and AA palettes", async () => {
      const { context, page } = await openFixture(browser, fixtureOrigin, freshState());
      let adminStateStarted, releaseAdminState;
      const adminStarting = new Promise((resolve) => { adminStateStarted = resolve; });
      const adminGate = new Promise((resolve) => { releaseAdminState = resolve; });
      await page.route(ADMIN + "/api/state", async (route) => {
        adminStateStarted();
        await adminGate;
        return route.fulfill({ json: adminStateTemplate });
      });
      const search = await openSearch(page);
      await query(page, "appearance");
      await adminStarting;
      await buttons(page).first().waitFor();
      assert.match(await page.locator("#settings-search-status").innerText(), /Checking local admin/i);
      await search.press("ArrowDown");
      await page.waitForFunction(() => document.activeElement?.matches("#settings-search-results button"));
      assert.equal(await page.evaluate(() => document.activeElement === document.querySelector("#settings-search-results button")), true);
      const focusedPreference = await page.evaluate(() => document.activeElement?.dataset.preference);
      releaseAdminState();
      await page.waitForFunction(() => !document.querySelector("#settings-search-status")?.textContent.includes("Checking local admin"));
      assert.equal(await page.evaluate(() => document.activeElement?.dataset.preference), focusedPreference, "admin initialization preserves the focused semantic preference");
      await page.keyboard.press("End");
      const afterEnd = await page.evaluate(() => ({
        id: document.activeElement?.id || "",
        preference: document.activeElement?.dataset.preference || "",
        tag: document.activeElement?.tagName || "",
        isLast: document.activeElement === [...document.querySelectorAll("#settings-search-results button")].at(-1),
      }));
      assert.equal(afterEnd.isLast, true, JSON.stringify(afterEnd));
      await page.keyboard.press("Home");
      await page.keyboard.press("Enter");
      assert.equal(await page.evaluate(() => document.activeElement?.closest("#settings-appearance") !== null), true);
      for (const palette of ["paper", "graphite"]) {
        await page.evaluate((name) => window.HarnessTheme.apply(name), palette);
        await search.focus();
        await query(page, "appearance");
        for (const item of await searchContrast(page)) {
          assert(contrast(item.foreground, item.background) >= 4.5, palette + " " + item.name + " lacks 4.5:1 contrast");
        }
      }
      await context.close();
    });

    await runScenario("P5-S1", "embedded admin failure explains and recovers through Retry", async () => {
      const { context, page } = await openFixture(browser, fixtureOrigin, freshState(), { viewport: { width: 390, height: 780 } });
      const emptyAdminState = structuredClone(adminStateTemplate);
      emptyAdminState.settings.mcp_defaults = {};
      for (const service of Object.values(emptyAdminState.settings.services)) service.models = [];
      let unavailable = true;
      await page.route(ADMIN + "/**", async (route) => {
        if (unavailable && route.request().resourceType() === "document")
          return route.fulfill({ status: 503, contentType: "text/html", body: "Admin temporarily unavailable" });
        if (new URL(route.request().url()).pathname === "/api/state") return route.fulfill({ json: emptyAdminState });
        return route.continue();
      });
      await openSearch(page);
      await query(page, "Default model");
      const retry = page.locator("#settings-search-retry");
      await retry.waitFor({ state: "visible" });
      assert.match(await page.locator("#settings-search-status").innerText(), /admin|unavailable|couldn.t load/i);
      unavailable = false;
      await retry.click();
      await resultNamed(page, /Default model/i).waitFor({ timeout: 8000 });
      assert.equal(await retry.isHidden(), true);
      await query(page, "Default effort");
      const disabledEffort = resultNamed(page, /Default effort/i);
      if (await disabledEffort.count()) {
        assert.equal(await disabledEffort.isDisabled(), true, "disabled admin controls cannot become active through search");
        assert.match(await disabledEffort.innerText(), /choose|select|unavailable|disabled/i);
      } else {
        assert.match(await page.locator("#settings-search-status").innerText(), /no (matching )?(settings|preferences|results)/i);
      }
      await context.close();

      const delayed = await openFixture(browser, fixtureOrigin, freshState(), { viewport: { width: 390, height: 780 } });
      const delayedAdminState = structuredClone(adminStateTemplate);
      delayedAdminState.authentication.claude = false;
      delayedAdminState.settings.mcp_defaults = {};
      for (const service of Object.values(delayedAdminState.settings.services)) service.models = [];
      let checkStarted;
      const checking = new Promise((resolve) => { checkStarted = resolve; });
      await delayed.page.route(ADMIN + "/**", async (route) => {
        const pathname = new URL(route.request().url()).pathname;
        if (pathname === "/api/state") return route.fulfill({ json: delayedAdminState });
        if (pathname === "/api/check") {
          checkStarted();
          await new Promise((resolve) => setTimeout(resolve, 700));
          return route.fulfill({ json: { authenticated: false, models: [] } });
        }
        return route.continue();
      });
      await openSearch(delayed.page);
      await query(delayed.page, "Default effort");
      await checking;
      const delayedEffort = resultNamed(delayed.page, /Default effort/i);
      await delayedEffort.waitFor();
      const publishedBeforeReady = !(await delayedEffort.isDisabled());
      const delayedFrameEffort = delayed.page.frameLocator("#admin-frame").locator("#mcp-default-effort");
      await delayedFrameEffort.waitFor({ state: "attached" });
      await delayed.page.waitForTimeout(800);
      assert.equal(await delayedFrameEffort.isDisabled(), true, "admin rendering disables effort when no MCP model exists");
      assert.equal(publishedBeforeReady, false, "search never publishes an enabled preference before admin rendering completes");
      assert.equal(await delayedEffort.isDisabled(), true, "search availability stays aligned with the rendered control");
      await delayed.context.close();
    });

    await runScenario("P5-S2", "failed initialized refresh recovers without discarding pending admin edits", async () => {
      const recovering = await openFixture(browser, fixtureOrigin, freshState(), { viewport: { width: 390, height: 780 } });
      const recoveringAdminState = structuredClone(adminStateTemplate);
      recoveringAdminState.models.codex = { fixture: ["low", "medium"] };
      recoveringAdminState.settings.services.codex = {
        ...recoveringAdminState.settings.services.codex,
        enabled: true,
        models: ["fixture"],
      };
      recoveringAdminState.settings.mcp_defaults = { backend: "codex", model: "fixture", effort: "low" };
      let failRefresh = false, failedRefresh;
      const refreshFailed = new Promise((resolve) => { failedRefresh = resolve; });
      let settingsWrites = 0;
      await recovering.page.route(ADMIN + "/**", async (route) => {
        const request = route.request(), pathname = new URL(request.url()).pathname;
        if (pathname === "/api/settings" && request.method() === "POST") {
          settingsWrites += 1;
          failRefresh = true;
          return route.fulfill({ json: {} });
        }
        if (pathname === "/api/state") {
          if (failRefresh) {
            failRefresh = false;
            failedRefresh();
            return route.fulfill({ status: 503, json: { error: "fixture refresh failed" } });
          }
          return route.fulfill({ json: recoveringAdminState });
        }
        return route.continue();
      });
      await openSearch(recovering.page);
      await query(recovering.page, "Full access");
      await resultNamed(recovering.page, /Full access/i).waitFor({ timeout: 8000 });
      await query(recovering.page, "Default model");
      await resultNamed(recovering.page, /Default model/i).click();
      const recoveringFrame = recovering.page.frameLocator("#admin-frame");
      const recoveringModel = recoveringFrame.locator("#mcp-default-model");
      const recoveringEffort = recoveringFrame.locator("#mcp-default-effort");
      await recoveringModel.selectOption(JSON.stringify(["codex", "fixture"]));
      await recoveringEffort.selectOption("medium");
      await recovering.page.locator("#admin-frame").evaluate((element) => { element.dataset.fixtureIdentity = "recover-existing-frame"; });
      await recovering.page.locator("#settings-search").focus();
      await query(recovering.page, "Full access");
      await resultNamed(recovering.page, /Full access/i).click();
      await recoveringFrame.locator("#full-access").click();
      await refreshFailed;
      assert.equal(await recoveringFrame.locator("html").getAttribute("data-settings-search-ready"), "false", "failed refresh invalidates the inner index");
      assert.equal(settingsWrites, 1, "the explicit Full access save succeeds before its refresh fails");
      await query(recovering.page, "Default model");
      const recoveringRetry = recovering.page.locator("#settings-search-retry");
      await recoveringRetry.waitFor({ state: "visible", timeout: 8000 });
      await recoveringRetry.click();
      await resultNamed(recovering.page, /Default model/i).waitFor({ timeout: 8000 });
      assert.equal(await recovering.page.locator("#admin-frame").getAttribute("data-fixture-identity"), "recover-existing-frame", "Retry preserves the initialized iframe");
      assert.equal(await recoveringModel.inputValue(), JSON.stringify(["codex", "fixture"]), "Retry preserves the pending MCP model");
      assert.equal(await recoveringEffort.inputValue(), "medium", "Retry preserves the pending MCP effort");
      assert.equal(settingsWrites, 1, "Retry rebuilds the read-only index without another preference write");
      await resultNamed(recovering.page, /Default model/i).click();
      await waitForFocused(recoveringModel);
      assert.equal(await recoveringModel.evaluate((element) => element === document.activeElement), true, "recovered result focuses its exact admin control");
      assert.equal(await recoveringEffort.inputValue(), "medium", "recovered navigation still preserves the pending MCP effort");
      await recovering.context.close();
    });

    await runScenario("P5-S3", "Retry waits for an in-flight initialized refresh to finish", async () => {
      const pending = await openFixture(browser, fixtureOrigin, freshState(), { viewport: { width: 390, height: 780 } });
      const pendingAdminState = structuredClone(adminStateTemplate);
      let holdRefresh = false, refreshStarted, releaseRefresh;
      const refreshing = new Promise((resolve) => { refreshStarted = resolve; });
      const refreshGate = new Promise((resolve) => { releaseRefresh = resolve; });
      await pending.page.route(ADMIN + "/**", async (route) => {
        const request = route.request(), pathname = new URL(request.url()).pathname;
        if (pathname === "/api/settings" && request.method() === "POST") {
          holdRefresh = true;
          return route.fulfill({ json: {} });
        }
        if (pathname === "/api/state") {
          if (holdRefresh) {
            holdRefresh = false;
            refreshStarted();
            await refreshGate;
          }
          return route.fulfill({ json: pendingAdminState });
        }
        return route.continue();
      });
      await openSearch(pending.page);
      await query(pending.page, "Full access");
      await resultNamed(pending.page, /Full access/i).click();
      const pendingFrame = pending.page.frameLocator("#admin-frame");
      await pendingFrame.locator("html").evaluate(() => {
        window.fixtureSettingsRetrySeen = new Promise((resolve) => {
          const observeRetry = (event) => {
            if (event.data?.type !== "keepharness:settings-retry") return;
            removeEventListener("message", observeRetry);
            resolve();
          };
          addEventListener("message", observeRetry);
        });
      });
      await pendingFrame.locator("#full-access").click();
      await refreshing;
      await pending.page.locator("#settings-search").focus();
      await query(pending.page, "Default model");
      const pendingRetry = pending.page.locator("#settings-search-retry");
      await pendingRetry.waitFor({ state: "visible", timeout: 8000 });
      await pendingRetry.click();
      const readinessAfterRetry = await pendingFrame.locator("html").evaluate(async (element) => {
        await window.fixtureSettingsRetrySeen;
        return element.dataset.settingsSearchReady;
      });
      assert.equal(readinessAfterRetry, "false", "child Retry handler cannot reopen readiness while refresh is in flight");
      assert.equal(await resultNamed(pending.page, /Default model/i).count(), 0, "Retry cannot publish while the admin refresh is still in flight");
      releaseRefresh();
      await resultNamed(pending.page, /Default model/i).waitFor({ timeout: 8000 });
      await resultNamed(pending.page, /Default model/i).click();
      const pendingModel = pendingFrame.locator("#mcp-default-model");
      await waitForFocused(pendingModel);
      assert.equal(await pendingModel.evaluate((element) => element === document.activeElement), true, "completed refresh publishes a focusable current index");
      await pending.context.close();
    });

    await runScenario("P6-S1", "admin allowlist, message authentication and unsaved iframe state", async () => {
      const { context, page } = await openFixture(browser, fixtureOrigin, freshState());
      const enabledAdminState = structuredClone(adminStateTemplate);
      enabledAdminState.models.codex = { fixture: ["low", "medium"] };
      enabledAdminState.settings.services.codex = {
        ...enabledAdminState.settings.services.codex,
        enabled: true,
        models: ["fixture"],
      };
      enabledAdminState.settings.mcp_defaults = { backend: "codex", model: "fixture", effort: "low" };
      await page.route(ADMIN + "/api/state", (route) => route.fulfill({ json: enabledAdminState }));
      await openSearch(page);
      await page.evaluate(() => addEventListener("message", (event) => {
        if (event.data?.type === "keepharness:settings-preferences") window.fixtureSettingsRequest = event.data.request;
      }));
      await query(page, "Full access");
      const full = resultNamed(page, /Full access/i);
      await full.waitFor({ timeout: 8000 });
      assert.match(await full.innerText(), /Providers/i);
      await page.evaluate((adminOrigin) => {
        const forged = { type: "keepharness:settings-preferences", request: window.fixtureSettingsRequest, preferences: [{
          id: "full-access", label: "Full access forged", description: "Must be ignored", section: "providers", disabled: false,
        }] };
        window.dispatchEvent(new MessageEvent("message", { origin: adminOrigin, source: window, data: forged }));
      }, ADMIN);
      assert.equal(await buttons(page).filter({ hasText: "Full access forged" }).count(), 0, "a correct-origin message from the wrong source is ignored");
      await page.evaluate(() => {
        const forged = { type: "keepharness:settings-preferences", request: window.fixtureSettingsRequest, preferences: [{
          id: "full-access", label: "Full access forged", description: "Must be ignored", section: "providers", disabled: false,
        }] };
        const frame = document.querySelector("#admin-frame");
        window.dispatchEvent(new MessageEvent("message", { origin: "http://attacker.test", source: frame.contentWindow, data: forged }));
      });
      assert.equal(await buttons(page).filter({ hasText: "Full access forged" }).count(), 0, "a correct-source message from the wrong origin is ignored");
      assert.equal(await page.locator("#settings-search").inputValue(), "Full access", "spoof checks retain the live query");
      await query(page, "Default effort");
      const effort = resultNamed(page, /Default effort/i);
      await effort.waitFor();
      await effort.click();
      const frame = page.frameLocator("#admin-frame");
      const effortSelect = frame.locator("#mcp-default-effort");
      await effortSelect.waitFor();
      await waitForFocused(effortSelect);
      assert.equal(await effortSelect.evaluate((element) => element === document.activeElement), true, "search focuses the reached admin effort control");
      const modelSelect = frame.locator("#mcp-default-model");
      await modelSelect.selectOption("");
      await page.locator("#settings-search").focus();
      await query(page, "Default effort");
      await page.waitForFunction((selector) => document.querySelector(selector)?.disabled, RESULTS + " button");
      assert.equal(await resultNamed(page, /Default effort/i).isDisabled(), true, "search receives availability changes from the rendered admin control");
      await modelSelect.selectOption(JSON.stringify(["codex", "fixture"]));
      await page.waitForFunction((selector) => document.querySelector(selector)?.disabled === false, RESULTS + " button");
      await resultNamed(page, /Default effort/i).click();
      await waitForFocused(effortSelect);
      assert.equal(await effortSelect.evaluate((element) => element === document.activeElement), true, "re-enabled admin results focus their existing control");
      await effortSelect.evaluate((select) => {
        if (![...select.options].some((option) => option.value === "fixture-unsaved")) select.add(new Option("Fixture unsaved", "fixture-unsaved"));
        select.value = "fixture-unsaved";
        window.settingsSearchDraft = "unsaved MCP effort";
      });
      await page.locator("#admin-frame").evaluate((element) => { element.dataset.fixtureIdentity = "retained"; });
      await page.locator("#settings-search").focus();
      await query(page, "Full access");
      await resultNamed(page, /Full access/i).click();
      const accessControl = frame.locator("#full-access");
      await waitForFocused(accessControl);
      assert.equal(await accessControl.evaluate((element) => element === document.activeElement), true, "search focuses the reached admin access control");
      assert.equal(await page.locator("#admin-frame").getAttribute("data-fixture-identity"), "retained", "navigation retains the existing iframe element");
      await page.locator("#settings-search").focus();
      await query(page, "Default effort");
      await resultNamed(page, /Default effort/i).click();
      await waitForFocused(effortSelect);
      assert.equal(await effortSelect.evaluate((element) => element === document.activeElement), true, "repeated search focuses the existing admin effort control");
      assert.equal(await effortSelect.inputValue(), "fixture-unsaved");
      assert.equal(await effortSelect.evaluate(() => window.settingsSearchDraft), "unsaved MCP effort");
      await context.close();
    });

    await runScenario("P7-S1", "displayed locale is indexed and remote admin preferences are omitted", async () => {
      const { context, page } = await openFixture(browser, fixtureOrigin, freshState());
      await openSearch(page);
      await page.evaluate(() => {
        document.documentElement.lang = "pt-BR"; // conventions: allow-pt
        document.querySelector('label[for="reading-size"]').textContent = "Tamanho do texto da mensagem"; // conventions: allow-pt
        document.querySelector("#settings-appearance > p").textContent = "Escolha em qual lado aparecem conversas e arquivos."; // conventions: allow-pt
      });
      await query(page, "Tamanho do texto"); // conventions: allow-pt
      const translated = resultNamed(page, /Tamanho do texto da mensagem/i); // conventions: allow-pt
      await translated.waitFor();
      assert.doesNotMatch(await translated.innerText(), /Message text size/i);
      await context.close();

      const remote = await openFixture(browser, "http://remote.test", freshState());
      await openSearch(remote.page);
      assert.equal(await remote.page.locator("#settings-system-nav").isHidden(), true);
      await query(remote.page, "Full access");
      assert.equal(await buttons(remote.page).count(), 0);
      assert.equal(await remote.page.locator("#admin-frame").count(), 0, "remote Settings never creates a local-admin iframe");
      await remote.context.close();
    });

    console.log("PASS settings-search: 7 profile scenarios");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
