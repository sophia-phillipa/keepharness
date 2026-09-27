// H19 traversal tester (P6): attacks the REAL harness (18095) directory
// browsers and the REAL admin (18094) folder creation with `..`, `%2e%2e`,
// absolute paths and system roots. Nothing outside an authorized root may be
// listed or created. Re-confirms F-05/F-22 (system files hidden) hold at the
// live endpoints; it does not re-report them.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { runPersona } = require("./_harness.cjs");

const HARNESS = process.env.HARNESS_URL || "http://127.0.0.1:18095";
const ADMIN = process.env.ADMIN_URL || "http://127.0.0.1:18094";

// System directories the picker must never expose (agent_service/workspaces.py
// SYSTEM_DIRECTORIES). Listing any of these under root `/` would leak host state.
const SYSTEM_DIRS = [
  "proc",
  "sys",
  "dev",
  "etc",
  "usr",
  "boot",
  "ostree",
  "sysroot",
  "var",
  "run",
];

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

const AX = {
  settings: {
    services: {
      local: {
        added: true,
        enabled: true,
        models: ["qwen"],
        projects: ["sem-projeto"],
        permissions: { read: true, upload: true },
        mode: "native",
        integrations: [],
      },
    },
    projects: [],
    logins: [],
    port: 8095,
    tailnet_port: 8095,
    uploads_enabled: true,
  },
  inventory: {
    platform: "Linux",
    services: [
      {
        id: "local",
        name: "Local model",
        found: true,
        models: ["qwen"],
        runtimes: [],
      },
    ],
    projects: [],
    network: { online: true },
  },
  authentication: { local: true },
  models: { local: { qwen: ["low"] } },
  integrations: { local: [] },
  local_profiles: {},
  operations: [],
  credentials: {},
  status: { running: false, local_url: "http://127.0.0.1:8095/" },
};

runPersona("H19", [
  {
    title:
      "H19-S1 harness directory browsers refuse traversal and hide system files",
    async run(page) {
      const done = tolerateFailedLoads(page);
      const req = page.request;

      // /v1/project-directories is gated (shared_projects off on the test
      // server): every traversal payload gets a controlled error, never a
      // listing of host paths.
      for (const p of [
        "",
        "..",
        "%2e%2e",
        "../../etc",
        "/etc",
        "/proc/self",
        "....//",
      ]) {
        const r = await req.get(
          HARNESS + "/v1/project-directories?root_id=home&path=" + p,
        );
        assert(r.status() >= 400, "project-directories must refuse: " + p);
        const j = await r.json();
        assert(!Array.isArray(j.entries), "no entries leaked for " + p);
        assert.equal(
          j.absolute_path,
          undefined,
          "no host path leaked for " + p,
        );
      }

      // /v1/project-files?view=tree is the reachable system browser.
      const home = await (
        await req.get(
          HARNESS + "/v1/project-files?view=tree&root_id=home&limit=5",
        )
      ).json();
      assert.equal(home.state, "ready");
      for (const e of home.entries)
        assert(
          !e.path.includes("..") && !e.path.startsWith("/"),
          "home entry stays relative: " + e.path,
        );

      // Traversal out of a root is refused, never silently resolved.
      for (const p of ["..", "%2e%2e%2f%2e%2e", "/etc", "/", "../../.."]) {
        const r = await req.get(
          HARNESS + "/v1/project-files?view=tree&root_id=home&path=" + p,
        );
        assert(r.status() >= 400, "tree must refuse path " + p);
        const j = await r.json();
        assert.equal(j.code, "path_not_authorized", "code for " + p);
        assert(!Array.isArray(j.entries), "no listing for " + p);
      }

      // Root `/` never exposes the system directories (F-05 / F-22 hold).
      const system = await (
        await req.get(
          HARNESS + "/v1/project-files?view=tree&root_id=system&limit=200",
        )
      ).json();
      assert.equal(system.state, "ready");
      const names = system.entries.map((e) => e.name);
      for (const dir of SYSTEM_DIRS)
        assert(!names.includes(dir), "system root must hide /" + dir);

      // Descending directly into a system directory yields nothing.
      for (const dir of ["proc", "etc", "run", "sys", "var"]) {
        const r = await req.get(
          HARNESS + "/v1/project-files?view=tree&root_id=system&path=" + dir,
        );
        assert(r.status() < 400, "system/" + dir + " is a controlled response");
        const j = await r.json();
        assert.deepEqual(j.entries, [], "/" + dir + " must list nothing");
      }

      // An unknown root cannot be forged.
      const bad = await req.get(
        HARNESS + "/v1/project-files?view=tree&root_id=%2e%2e",
      );
      assert(bad.status() >= 400);
      assert.equal((await bad.json()).code, "system_root_denied");

      // The Add project dialog surfaces a readable error, never a host listing.
      await page.goto(HARNESS + "/");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
      await page.locator("#add-project").click();
      await page.locator("#project-dialog").waitFor();
      await page
        .locator("#project-directory-error", {
          hasText: /Couldn't list folders/,
        })
        .waitFor();
      const listText = (
        await page.locator("#project-directory-list").textContent()
      ).trim();
      assert.equal(listText, "", "no host directories are listed");
      assert.equal(
        await page.locator("#project-directory-roots button").count(),
        0,
      );
      // F-100: the gating code becomes a guided message, never the raw code.
      const refusal = await page
        .locator("#project-directory-error")
        .textContent();
      assert.match(refusal, /Adding projects is turned off/);
      assert.doesNotMatch(refusal, /project_registration_disabled/);

      done();
    },
  },
  {
    title:
      "H19-S2 admin folder creation rejects `..`, `../x`, `a/b` and never escapes",
    async run(page) {
      const done = tolerateFailedLoads(page);
      // Real /api/folders and /api/folders/create; only /api/state and the
      // dashboard are mocked so the local-model panel renders on this test
      // server (which has no providers configured).
      await page.route(ADMIN + "/api/state", (r) => r.fulfill({ json: AX }));
      await page.route(ADMIN + "/api/dashboard", (r) =>
        r.fulfill({
          json: {
            available: true,
            checked_at: 1,
            requests_per_second: 0,
            active: 0,
            queued: 0,
            input_tokens: null,
            output_tokens: null,
            measured_jobs: 0,
            latest_output_tokens_per_second: null,
            hardware: {
              cpu_percent: 0,
              memory_used: 100,
              memory_total: 1000,
              gpus: [],
            },
            recent: [],
          },
        }),
      );
      const parent = fs.mkdtempSync(path.join(os.tmpdir(), "h19s2-"));
      try {
        await page.goto(ADMIN + "/");
        await page.waitForLoadState("networkidle");
        await page.locator("[data-panel=providers]").click();
        await page
          .locator("#configured-providers button", { hasText: /^Edit/ })
          .first()
          .click();
        await page.locator("#local-models").waitFor();
        await page.locator("#add-local-model").click();
        await page.locator("#source-file").click();
        await page.locator("#choose-model-folder").waitFor();
        await page.fill("#model-folder", parent);
        await page.locator("#choose-model-folder").click();
        await page.locator("#folder-picker").waitFor();
        await page
          .locator("#folder-picker-status", { hasText: /subfolder/ })
          .waitFor();
        await page.locator(".folder-picker-create summary").click();

        for (const name of ["..", "../x", "a/b", ".", "  ", "x\\y"]) {
          await page.fill("#folder-picker-new-name", name);
          await page.locator("#folder-picker-create").click();
          await page.locator("#folder-picker-error:not([hidden])").waitFor();
          assert.match(
            await page.locator("#folder-picker-error").textContent(),
            /Use a simple folder name, with no slashes/,
            "rejected traversal name: " + JSON.stringify(name),
          );
        }

        // A plain name still works: proves the guard rejects only traversal.
        await page.fill("#folder-picker-new-name", "workspace");
        await page.locator("#folder-picker-create").click();
        await page.waitForFunction(
          () => document.querySelector("#folder-picker-error").hidden,
        );

        // Filesystem check: only the legitimate folder exists, and nothing was
        // created outside the parent.
        assert.deepEqual(fs.readdirSync(parent), ["workspace"]);
        const grandparent = path.dirname(parent);
        assert(!fs.existsSync(path.join(grandparent, "x")), "no stray ../x");
        assert(
          !fs.existsSync(path.join(grandparent, "b")),
          "no stray a/b leaf",
        );
        assert(!fs.existsSync(path.join(parent, "a")), "no nested a/ created");
      } finally {
        fs.rmSync(parent, { recursive: true, force: true });
      }
      done();
    },
  },
]);
