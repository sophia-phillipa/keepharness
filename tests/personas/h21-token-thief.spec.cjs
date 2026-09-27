// H21 token thief (P6): plants a sentinel secret through the REAL admin
// (18094), then hunts for it (and for any admin cookie / client credential
// digest) across the admin DOM, /api/state, browser storage, the settings
// export, and the REAL harness (18095) DOM, setup code and /v1 bodies. The
// sentinel is always cleaned up. Re-confirms F-09 secret hygiene; not a
// re-report.
"use strict";
const assert = require("node:assert/strict");
const { runPersona } = require("./_harness.cjs");

const HARNESS = process.env.HARNESS_URL || "http://127.0.0.1:18095";
const ADMIN = process.env.ADMIN_URL || "http://127.0.0.1:18094";
const SENTINEL = "SENTINELdeadbeef0123456789cafef00d";
// The test server seeds clients.local with an all-zero sha256; the harness must
// never echo a client credential digest.
const CLIENT_DIGEST = "0".repeat(64);

// The probes provoke 4xx answers on purpose; Chromium logs each one as
// "Failed to load resource". Tolerate only that line (F-102 fixed the CSP noise).
function tolerateFailedLoads(page) {
  page.removeAllListeners("console");
  const errors = [];
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const t = m.text();
    if (/Failed to load resource/.test(t)) return;
    errors.push(t);
  });
  return () => assert.deepEqual(errors, []);
}

runPersona("H21", [
  {
    title: "H21-S1 a planted DeepSeek key never reaches the admin surface",
    async run(page) {
      const done = tolerateFailedLoads(page);
      await page.goto(ADMIN + "/"); // sets the admin cookie in this context
      const admin = {
        headers: { "x-harness-admin": "1", "content-type": "application/json" },
      };
      try {
        const saved = await page.request.post(ADMIN + "/api/provider-token", {
          ...admin,
          data: { provider: "deepseek", token: SENTINEL },
        });
        assert.equal(saved.status(), 200, "sentinel stored");
        assert.equal((await saved.json()).saved, true);

        // Reload so the panel renders with the credential present.
        await page.goto(ADMIN + "/");
        await page.waitForLoadState("networkidle");

        const dom = await page.content();
        assert(!dom.includes(SENTINEL), "sentinel absent from the admin DOM");

        const state = await (
          await page.request.get(ADMIN + "/api/state")
        ).text();
        assert(!state.includes(SENTINEL), "sentinel absent from /api/state");
        // /api/state reveals only that a credential exists, not its value.
        const parsed = JSON.parse(state);
        assert.equal(parsed.credentials.deepseek, true, "existence flag only");

        const exported = await (
          await page.request.post(ADMIN + "/api/settings-export", {
            ...admin,
            data: {},
          })
        ).text();
        assert(!exported.includes(SENTINEL), "sentinel absent from the export");

        const storage = await page.evaluate(() => {
          const dump = (s) => {
            let out = "";
            try {
              for (let i = 0; i < s.length; i++)
                out += s.key(i) + "=" + s.getItem(s.key(i)) + ";";
            } catch {}
            return out;
          };
          return dump(localStorage) + dump(sessionStorage) + document.cookie;
        });
        assert(
          !storage.includes(SENTINEL),
          "sentinel absent from browser storage",
        );
        // The admin cookie is httponly, so it never appears in document.cookie.
        assert(
          !/(^|;\s*)admin=/.test(await page.evaluate(() => document.cookie)),
        );
      } finally {
        const removed = await page.request.post(
          ADMIN + "/api/provider-delete",
          {
            ...admin,
            data: { provider: "deepseek" },
          },
        );
        assert.equal(removed.status(), 200, "sentinel cleaned up");
        const after = await (
          await page.request.get(ADMIN + "/api/state")
        ).json();
        assert.equal(after.credentials.deepseek, false, "credential removed");
      }
      done();
    },
  },
  {
    title: "H21-S2 the harness leaks no cookie, token or credential digest",
    async run(page) {
      const done = tolerateFailedLoads(page);
      await page.goto(HARNESS + "/");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });

      // The setup instructions (populated on load) carry only the public
      // origin, never a secret.
      const setupCode = await page.locator("#setup-code").textContent();
      assert(setupCode.length > 0, "setup code is populated");
      assert(
        !/token|secret|sha256|Bearer\s/i.test(setupCode),
        "setup code has no secret",
      );
      assert(
        !setupCode.includes(CLIENT_DIGEST),
        "setup code has no credential digest",
      );

      // Nothing in the harness document exposes an admin cookie or a digest.
      const dom = await page.content();
      assert(
        !dom.includes(CLIENT_DIGEST),
        "no client credential digest in the DOM",
      );
      assert(
        !/harness_token=|admin=/.test(dom),
        "no session cookie printed in the DOM",
      );
      const cookie = await page.evaluate(() => document.cookie);
      assert(!/harness_token=/.test(cookie), "harness_token stays httponly");
      assert(!/admin=/.test(cookie), "no admin cookie on this origin");

      // The public /v1 bodies carry no credential material.
      for (const p of [
        "/v1/projects",
        "/v1/models",
        "/v1/version",
        "/v1/catalog",
        "/v1/usage",
      ]) {
        const text = await (await page.request.get(HARNESS + p)).text();
        assert(
          !text.includes(CLIENT_DIGEST),
          p + " leaks no credential digest",
        );
        assert(
          !/"sha256"|"token"|"cookie"|vpn/i.test(text),
          p + " exposes no secret field",
        );
      }
      done();
    },
  },
]);
