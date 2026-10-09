const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const path = require("node:path");

// First-run wizard (#69, D-052). scripts/test-ui.sh resets first-run before every
// tests/first-run*.spec.cjs, so this spec starts from a truly fresh install. The provider scan and the
// model list are page.route mocks (no real CLI is probed, nothing logs in); first-run status, the
// finish call and the backend UI-state store are the real control server.
const ADMIN = process.env.ADMIN_URL || "http://127.0.0.1:8094";
const STATE = process.env.KEEPHARNESS_TEST_STATE;
const ROWS = [
  { id: "codex", found: true, signed_in: true, detail: "signed_in" },
  { id: "claude", found: true, signed_in: false, detail: "signed_out" },
  {
    id: "deepseek",
    found: true,
    signed_in: null,
    detail: "key_saved_unverified",
  },
  {
    id: "gemini",
    found: true,
    signed_in: null,
    detail: "credential_present_unverified",
  },
  { id: "local", found: false, signed_in: false, detail: "not_installed" },
];
const MODEL = "gpt-5.5";

// What the control process stored for the owner, read through the product's own store code.
const stored = () =>
  JSON.parse(
    execFileSync(
      process.env.PYTHON || "python3",
      [
        "-c",
        "import json,sys;from agent_service import ui_state;" +
          "print(json.dumps(ui_state.read({'state_dir': sys.argv[1] + '/runs'}, 'local')['values']))",
        STATE,
      ],
      { cwd: path.join(__dirname, ".."), encoding: "utf8" },
    ),
  );

(async () => {
  assert.ok(STATE, "KEEPHARNESS_TEST_STATE is exported by scripts/test-ui.sh");
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({
      viewport: { width: 1280, height: 900 },
    });
    const [cookieName, ...cookieValue] = (
      process.env.ADMIN_LOCAL_COOKIE || ""
    ).split("=");
    if (cookieName && cookieValue.length)
      await context.addCookies([
        {
          name: cookieName,
          value: cookieValue.join("="),
          domain: "127.0.0.1",
          path: "/",
          httpOnly: true,
          sameSite: "Strict",
        },
      ]);
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const logins = [];
    page.on("request", (r) => {
      if (/\/api\/(provider-login|provider-logout)/.test(r.url()))
        logins.push(r.url());
    });
    let scans = 0;
    let savedSettings = null;
    await page.route("**/api/first-run/scan", (route) => {
      scans++;
      return route.fulfill({ json: { providers: ROWS } });
    });
    await page.route("**/api/check", (route) =>
      route.fulfill({
        json: { models: { [MODEL]: ["configured", "low", "high"] } },
      }),
    );
    // Turning a provider on would save real settings (and start a harness); keep the payload instead.
    await page.route("**/api/settings", (route) => {
      savedSettings = route.request().postDataJSON();
      return route.fulfill({ json: {} });
    });
    const status = () =>
      page.evaluate(() => fetch("/api/first-run").then((r) => r.json()));
    const settle = () =>
      page.evaluate(
        () => new Promise((done) => requestAnimationFrame(() => done())),
      );
    const wizard = page.locator("dialog#first-run");
    const title = page.getByTestId("first-run-step-title");
    const palette = () =>
      page.evaluate(() => document.documentElement.dataset.palette);

    // The admin shown inside the harness (?embedded=1) never auto-opens it, even when it is not done.
    const inner = await context.newPage();
    const innerStatus = inner.waitForResponse(
      (r) =>
        r.url().endsWith("/api/first-run") && r.request().method() === "GET",
    );
    await inner.goto(ADMIN + "/?embedded=1#providers");
    assert.equal((await (await innerStatus).json()).completed, false);
    await inner.evaluate(
      () => new Promise((done) => requestAnimationFrame(() => done())),
    );
    assert.equal(
      await inner.evaluate(() => document.documentElement.dataset.embedded),
      "1",
    );
    assert.equal(await inner.locator("dialog#first-run").isVisible(), false);
    await inner.close();

    // A rejected settings save stops Finish: no completion call, still not done, the error is shown.
    const failing = await context.newPage();
    let completions = 0;
    failing.on("request", (r) => {
      if (r.url().endsWith("/api/first-run") && r.method() === "POST")
        completions++;
    });
    await failing.route("**/api/first-run/scan", (route) =>
      route.fulfill({ json: { providers: ROWS } }),
    );
    await failing.route("**/api/check", (route) =>
      route.fulfill({ json: { models: { [MODEL]: ["configured", "high"] } } }),
    );
    await failing.route("**/api/settings", (route) =>
      route.fulfill({ status: 500, json: { error: "disk full" } }),
    );
    await failing.goto(ADMIN + "/#providers");
    await failing.locator("dialog#first-run").waitFor({ state: "visible" });
    await failing.getByTestId("first-run-next").click();
    await failing.getByTestId("first-run-provider-codex").waitFor();
    await failing.getByTestId("first-run-next").click();
    await failing.getByTestId("first-run-enable-codex").waitFor();
    await failing.getByTestId("first-run-finish").click();
    await failing.locator("#first-run-error").waitFor({ state: "visible" });
    assert.match(await failing.locator("#first-run-error").innerText(), /\S/);
    assert.equal(completions, 0, "a failed settings save never completes");
    assert.equal(await failing.locator("dialog#first-run").isVisible(), true);
    assert.equal(
      (
        await failing.evaluate(() =>
          fetch("/api/first-run").then((r) => r.json()),
        )
      ).completed,
      false,
    );
    await failing.close();

    // A fresh install shows the wizard on the Appearance step.
    const first = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/first-run") && r.request().method() === "GET",
    );
    await page.goto(ADMIN + "/#providers");
    assert.equal((await (await first).json()).completed, false);
    await wizard.waitFor({ state: "visible" });
    await title.waitFor();
    assert.equal(await title.innerText(), "Appearance");
    assert.equal(
      await page.locator("#first-run-progress").innerText(),
      "Step 1 of 3",
    );
    const keysBefore = await page.evaluate(() =>
      Object.keys(localStorage).sort(),
    );
    const original = await palette();
    const ids = await page.evaluate(() => HarnessTheme.themes.map((t) => t.id));
    const chosen = ids.find((id) => id !== original);
    assert.ok(chosen, "the wizard offers a second theme");

    // Appearance: a click previews the theme and saves nothing yet.
    const choice = page.getByTestId("first-run-theme-" + chosen);
    await choice.click();
    assert.equal(await palette(), chosen);
    assert.equal(await choice.getAttribute("aria-pressed"), "true");
    assert.notEqual(stored().theme, chosen, "the preview must not be saved");

    // Providers: rows come from the (mocked) scan; signed-out Claude offers the #36 login, nothing else does.
    await page.getByTestId("first-run-next").click();
    await page.getByTestId("first-run-provider-codex").waitFor();
    assert.equal(await title.innerText(), "Providers");
    assert.equal(scans, 1);
    const row = (id) => page.getByTestId("first-run-provider-" + id);
    assert.equal(await row("codex").getAttribute("data-signed-in"), "true");
    assert.match(
      await row("codex").innerText(),
      /Ready, using your existing login/,
    );
    assert.equal(await row("claude").getAttribute("data-signed-in"), "false");
    assert.match(await row("claude").innerText(), /Not signed in/);
    assert.match(await row("deepseek").innerText(), /Key saved, not verified/);
    assert.match(
      await row("gemini").innerText(),
      /Credentials found, not verified/,
    );
    assert.match(await row("local").innerText(), /Not installed/);
    assert.equal(await page.getByTestId("first-run-login-claude").count(), 1);
    assert.equal(await page.getByTestId("first-run-login-codex").count(), 0);
    assert.equal(await page.getByTestId("first-run-login-deepseek").count(), 0);
    assert.equal(await page.getByTestId("first-run-login-gemini").count(), 0);
    await page.getByTestId("first-run-rescan").click();
    await page.getByTestId("first-run-provider-codex").waitFor();
    assert.equal(scans, 2, "Scan again probes once more");

    // Defaults: the signed-in provider can be turned on and given a default model.
    await page.getByTestId("first-run-next").click();
    await page.getByTestId("first-run-enable-codex").waitFor();
    assert.equal(await title.innerText(), "Defaults");
    assert.equal(
      await page.getByTestId("first-run-enable-codex").isChecked(),
      true,
    );
    assert.equal(await page.getByTestId("first-run-enable-claude").count(), 0);
    await page.getByTestId("first-run-model").selectOption(MODEL);
    await page.getByTestId("first-run-effort").selectOption("high");
    assert.equal(
      await page.getByTestId("first-run-finish").innerText(),
      "Finish",
    );
    assert.equal(
      (await status()).completed,
      false,
      "nothing is completed before Finish",
    );

    // Finish: dialog closes, status says completed, the choices are in the backend store.
    await page.getByTestId("first-run-finish").click();
    await wizard.waitFor({ state: "hidden" });
    const done = await status();
    assert.equal(done.completed, true);
    assert.match(done.completed_at, /^\d{4}-\d\d-\d\dT/);
    assert.equal(savedSettings.services.codex.enabled, true);
    assert.deepEqual(savedSettings.services.codex.models, [MODEL]);
    assert.equal(savedSettings.services.claude.enabled, false);
    const values = stored();
    assert.equal(
      values.theme,
      chosen,
      "the theme is read back from the backend store",
    );
    assert.deepEqual(values.chat_selection, { model: MODEL, effort: "high" });
    assert.deepEqual(
      await page.evaluate(() => Object.keys(localStorage).sort()),
      keysBefore,
      "the wizard adds no browser storage key",
    );

    // A reload does not show it again.
    const again = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/first-run") && r.request().method() === "GET",
    );
    await page.reload();
    assert.equal((await (await again).json()).completed, true);
    await page.locator("#first-run-again").waitFor();
    await settle();
    assert.equal(await wizard.isVisible(), false);

    // Run setup again reopens it; Esc closes without completing and drops the theme preview.
    await page.locator("#first-run-again").click();
    await wizard.waitFor({ state: "visible" });
    assert.equal(
      (await status()).completed,
      false,
      "Run setup again resets the marker",
    );
    const before = await palette();
    const other = ids.find((id) => id !== before);
    await page.getByTestId("first-run-theme-" + other).click();
    assert.equal(await palette(), other);
    await page.keyboard.press("Escape");
    await wizard.waitFor({ state: "hidden" });
    assert.equal(await palette(), before, "Esc reverts the preview");
    assert.equal(
      (await status()).completed,
      false,
      "Esc does not complete the setup",
    );
    assert.equal(stored().theme, chosen, "Esc leaves the saved theme alone");

    assert.deepEqual(logins, [], "the wizard never starts or ends a login");
    assert.deepEqual(errors, []);
    console.log("first-run-wizard: ok");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
