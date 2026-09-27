// H20 CSRF attacker (P6): a page on another origin tries to drive the REAL
// admin (18094) and harness (18095) with forged Origin / Sec-Fetch metadata.
// The browser already blocks cross-origin fetch to loopback, so this exercises
// the server-side guard directly (page.request bypasses CORS and can set the
// headers a browser forbids). Re-confirms F-06; it does not re-report it.
"use strict";
const assert = require("node:assert/strict");
const { runPersona } = require("./_harness.cjs");

const HARNESS = process.env.HARNESS_URL || "http://127.0.0.1:18095";
const ADMIN = process.env.ADMIN_URL || "http://127.0.0.1:18094";
const EVIL = "http://evil.test";

runPersona("H20", [
  {
    title: "H20-S1 forged cross-origin writes to admin and harness are refused",
    async run(page) {
      const req = page.request;

      // Admin: a forged Origin is rejected before the cookie is even checked.
      const adminForged = await req.post(ADMIN + "/api/settings", {
        headers: {
          origin: EVIL,
          "x-harness-admin": "1",
          "content-type": "application/json",
        },
        data: { services: {} },
      });
      assert.equal(adminForged.status(), 403, "admin forged Origin");
      assert.equal((await adminForged.json()).error, "Unauthorized origin.");

      // Admin: cross-site fetch metadata (no gesture) is refused too.
      const adminFetch = await req.post(ADMIN + "/api/settings", {
        headers: {
          "sec-fetch-site": "cross-site",
          "sec-fetch-mode": "cors",
          "x-harness-admin": "1",
          "content-type": "application/json",
        },
        data: { services: {} },
      });
      assert.equal(adminFetch.status(), 403, "admin cross-site fetch");
      assert.equal((await adminFetch.json()).error, "Unauthorized origin.");

      // Harness: a forged Origin on a job submission is refused, so no run is
      // ever queued from another site.
      const jobForged = await req.post(HARNESS + "/v1/jobs", {
        headers: { origin: EVIL, "content-type": "application/json" },
        data: {
          project_id: "sem-projeto",
          prompt: "x",
          backend: "local",
          model: "qwen",
        },
      });
      assert.equal(jobForged.status(), 403, "harness forged Origin");
      assert.equal((await jobForged.json()).code, "origin_denied");

      // Harness: cross-site GET fetch (the shape a hostile page would use) is
      // refused; the local_access identity is never granted cross-site.
      const readFetch = await req.get(HARNESS + "/v1/projects", {
        headers: { "sec-fetch-site": "cross-site", "sec-fetch-mode": "cors" },
      });
      assert.equal(readFetch.status(), 403, "harness cross-site read fetch");
      assert.equal((await readFetch.json()).code, "origin_denied");

      // A cross-site top-level navigation (a clicked link) does NOT get the
      // ambient local identity: it stops at authentication (F-06 fix).
      const nav = await req.get(HARNESS + "/v1/projects", {
        headers: {
          "sec-fetch-site": "cross-site",
          "sec-fetch-mode": "navigate",
          "sec-fetch-dest": "document",
          "sec-fetch-user": "?1",
        },
      });
      assert.equal(
        nav.status(),
        401,
        "cross-site navigation is not local_access",
      );
      assert.equal((await nav.json()).code, "authentication_required");

      // The guard blocks only cross-origin traffic: same-origin still works, so
      // the harness records legitimate requests (control assertion for F-06).
      const same = await req.get(HARNESS + "/v1/projects", {
        headers: { origin: HARNESS, "sec-fetch-site": "same-origin" },
      });
      assert.equal(same.status(), 200, "same-origin read still works");
      assert(
        Array.isArray((await same.json()).projects),
        "same-origin request returns the project list",
      );
    },
  },
  {
    title: "H20-S2 forged navigation to the admin needs a real user gesture",
    async run(page) {
      const req = page.request;

      // A script-driven cross-site navigation (no Sec-Fetch-User) is refused:
      // an evil page cannot silently open the admin panel.
      const noGesture = await req.get(ADMIN + "/", {
        headers: {
          "sec-fetch-site": "cross-site",
          "sec-fetch-mode": "navigate",
          "sec-fetch-dest": "document",
        },
      });
      assert.equal(noGesture.status(), 403, "no-gesture navigation refused");
      assert.equal((await noGesture.json()).error, "Unauthorized origin.");

      // A real click (Sec-Fetch-User: ?1) is allowed to load the document only.
      const withGesture = await req.get(ADMIN + "/", {
        headers: {
          "sec-fetch-site": "cross-site",
          "sec-fetch-mode": "navigate",
          "sec-fetch-dest": "document",
          "sec-fetch-user": "?1",
        },
      });
      assert.equal(
        withGesture.status(),
        200,
        "user-activated navigation loads",
      );
      assert.match(
        await withGesture.text(),
        /<!doctype html>|<html/i,
        "the gesture only yields the panel document",
      );

      // Even after that document loads, a cross-site API write is still refused:
      // the gesture opens the page, never the API.
      const apiAfter = await req.post(ADMIN + "/api/settings", {
        headers: {
          "sec-fetch-site": "cross-site",
          "sec-fetch-mode": "cors",
          "x-harness-admin": "1",
          "content-type": "application/json",
        },
        data: { services: {} },
      });
      assert.equal(apiAfter.status(), 403, "API stays cross-site protected");
    },
  },
]);
