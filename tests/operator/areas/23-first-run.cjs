// First-run wizard (#69, D-052): theme preview, the provider scan, finish, and "Run setup again".
// Fixture mode resets the marker and walks the wizard. Real mode first reads GET /api/first-run:
// on a completed install it only checks the "Run setup again" control and the scan rows (never
// Finish, never reset); on a fresh one the walkthrough needs --allow-writes.
"use strict";
const { adminOpenUrl } = require("../lib/app.cjs");

const IDS = ["codex", "claude", "deepseek", "gemini", "local"];
const DETAILS = [
  "signed_in",
  "signed_out",
  "not_installed",
  "timeout",
  "error",
  "key_saved_unverified",
  "credential_present_unverified",
];

module.exports = {
  id: "first-run",
  title: "First-run wizard",
  async run(op) {
    let page = op.page;
    let own = null;
    let fresh = false;
    const api = (route, body) =>
      page.evaluate(
        ([r, b]) =>
          fetch("/api/" + r, {
            method: b === undefined ? "GET" : "POST",
            headers: { "X-Harness-Admin": "1", "Content-Type": "application/json" },
            body: b === undefined ? undefined : JSON.stringify(b),
          }).then((res) => res.json()),
        [route, body],
      );
    const dialog = () => page.locator("dialog#first-run");
    const title = () => page.getByTestId("first-run-step-title");
    const step = (name) => op.seeText(title(), new RegExp("^" + name + "$"));
    const walkable = () => {
      if (!op.fixtureMode && !fresh)
        op.skip("this install already finished the setup; real mode never resets it");
    };

    try {
      await op.step(
        "open",
        "Open the admin providers page and read the first-run status",
        async () => {
          if (op.session.target === "browser") {
            own = await op.session.newPage();
            page = own;
            op.session.page = own;
          }
          await page.goto(op.fixtureMode ? adminOpenUrl(op) : op.options.adminUrl + "/");
          await page.goto(op.options.adminUrl + "/#providers");
          await op.see(page.locator("#first-run-again"));
          if (op.fixtureMode) {
            // Start from an install that never ran the wizard, then reload so it opens by itself.
            await api("first-run:reset", {});
            await page.reload();
          }
          const status = await api("first-run");
          fresh = status.completed === false;
          op.check(typeof status.completed === "boolean", "GET /api/first-run has no completed flag");
          op.check(typeof status.version === "string", "GET /api/first-run has no version");
        },
        { critical: true },
      );

      await op.step(
        "scan-rows",
        "The scan lists every provider with a known status and no account data",
        async () => {
          const scan = await api("first-run/scan", {});
          op.check(
            scan.providers.map((p) => p.id).sort().join() === [...IDS].sort().join(),
            "scan rows are " + scan.providers.map((p) => p.id),
          );
          for (const row of scan.providers) {
            op.check(
              Object.keys(row).sort().join() === "detail,found,id,signed_in",
              `${row.id} row carries ${Object.keys(row)}`,
            );
            op.check(DETAILS.includes(row.detail), `${row.id} detail "${row.detail}" is outside the vocabulary`);
            op.check(
              row.signed_in === null || typeof row.signed_in === "boolean",
              `${row.id} signed_in is ${row.signed_in}`,
            );
          }
          op.check(!/@|\/home\/|\/Users\//.test(JSON.stringify(scan)), "the scan carries an email or a path");
        },
      );

      await op.step(
        "run-again-control",
        "The providers page offers Run setup again",
        async () => {
          const button = page.locator("#first-run-again");
          await op.see(button);
          op.check(await button.isEnabled(), "Run setup again is disabled");
          op.check(
            (await button.getAttribute("title")) || (await button.innerText()),
            "Run setup again has no name",
          );
        },
      );

      await op.step(
        "wizard-opens",
        "A fresh install opens the wizard on the Appearance step",
        async () => {
          walkable();
          await op.see(dialog());
          await step("Appearance");
        },
        { writes: true },
      );

      await op.step(
        "theme-preview",
        "Picking a theme previews it live; Next shows the provider rows",
        async () => {
          walkable();
          const before = await page.evaluate(() => document.documentElement.dataset.palette);
          const ids = await page.evaluate(() => HarnessTheme.themes.map((t) => t.id));
          const other = ids.find((id) => id !== before);
          await op.click(page.getByTestId("first-run-theme-" + other));
          await op.until(
            async () => (await page.evaluate(() => document.documentElement.dataset.palette)) === other,
            "the theme was not previewed",
          );
          // Return to the starting theme so a finished run leaves the install as it found it.
          await op.click(page.getByTestId("first-run-theme-" + before));
          await op.click(page.getByTestId("first-run-next"));
          await step("Providers");
          await op.see(page.getByTestId("first-run-provider-codex"));
          for (const id of IDS) await op.see(page.getByTestId("first-run-provider-" + id));
        },
        { writes: true },
      );

      await op.step(
        "signed-out-login",
        "A signed-out Claude shows the #36 login button (not clicked)",
        async () => {
          walkable();
          const claude = page.getByTestId("first-run-provider-claude");
          const state = await claude.getAttribute("data-signed-in");
          if (state !== "false") op.skip(`Claude is ${state === "true" ? "signed in" : "not checkable"} here`);
          await op.see(page.getByTestId("first-run-login-claude"));
        },
        { writes: true },
      );

      await op.step(
        "escape",
        "Esc closes the wizard without completing the setup",
        async () => {
          walkable();
          await op.press("Escape");
          await op.gone(dialog());
          op.check((await api("first-run")).completed === false, "Esc completed the setup");
        },
        { writes: true },
      );

      await op.step(
        "finish",
        "Run setup again reopens it; Finish completes it and it stays closed after a reload",
        async () => {
          walkable();
          await op.click(page.locator("#first-run-again"));
          await op.see(dialog());
          await op.click(page.getByTestId("first-run-next"));
          await op.see(page.getByTestId("first-run-provider-codex"));
          await op.click(page.getByTestId("first-run-next"));
          await step("Defaults");
          // Finish must not turn a provider on (that would save settings and may start a harness).
          for (const id of ["codex", "claude"]) {
            const box = page.getByTestId("first-run-enable-" + id);
            if ((await box.count()) && (await box.isChecked())) await op.click(box);
          }
          await op.click(page.getByTestId("first-run-finish"));
          await op.gone(dialog());
          const status = await api("first-run");
          op.check(status.completed === true, "Finish did not complete the setup");
          op.check(/^\d{4}-\d\d-\d\dT/.test(status.completed_at || ""), "no completion time: " + status.completed_at);
          await page.reload();
          await op.see(page.locator("#first-run-again"));
          await op.gone(dialog());
        },
        { writes: true },
      );
    } finally {
      if (own) {
        await own.close().catch(() => {});
        op.session.page = op.session.context.pages()[0];
      }
    }
  },
};
