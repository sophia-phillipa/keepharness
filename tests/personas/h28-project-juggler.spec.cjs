// H28 project juggler (P3/P6): 25 projects, names that collide with the
// "No project" scope (persisted id `sem-projeto`), and switching project with a
// draft in the composer.
"use strict";
const assert = require("node:assert/strict");
const { mockHarness, runPersona, visible } = require("./_harness.cjs");

const IDS = Array.from(
  { length: 25 },
  (_, i) => "p" + String(i + 1).padStart(2, "0"),
);
const WORDS = ["Atlas", "Borealis", "Cedar", "Delta", "Ember"];
const LABEL = Object.fromEntries(
  IDS.map((id, i) => [id, `Client ${i + 1} ${WORDS[i % 5]}`]),
);
const MODELS = [
  {
    id: "gpt-5.6-luna",
    backend: "codex",
    efforts: ["low"],
    permissions: { upload: true },
    execution_modes: ["native", "scoped"],
  },
  {
    id: "claude-sonnet-5",
    backend: "claude",
    efforts: ["low"],
    permissions: { upload: true },
    execution_modes: ["native", "scoped"],
  },
];
const fold = (s) =>
  s.normalize("NFKC").split(/\s+/).filter(Boolean).join(" ").toLowerCase();

// Mirrors ProjectService.add_project (agent_service/services/project_service.py
// ~L80-96) with the runtime label {"sem-projeto": {"label": "No project"}}
// (control/runtime_config.py:26): names are compared NFKC + whitespace-collapsed
// + casefolded against every label, the reserved one included.
async function open(page, { slow = {} } = {}) {
  const state = {
    projects: ["sem-projeto", ...IDS],
    details: {
      "sem-projeto": { label: "No project" },
      ...Object.fromEntries(IDS.map((id) => [id, { label: LABEL[id] }])),
    },
    created: [],
    modelQueries: [],
  };
  const s = await mockHarness(page, {
    "/^(GET|POST) \\/v1\\/projects$/": async (route) => {
      const req = route.request();
      if (req.method() === "GET")
        return route.fulfill({
          json: { projects: state.projects, details: state.details },
        });
      const body = req.postDataJSON();
      state.created.push(body);
      const label = body.name
        .normalize("NFKC")
        .split(/\s+/)
        .filter(Boolean)
        .join(" ");
      if (
        Object.values(state.details).some((d) => fold(d.label) === fold(label))
      )
        return route.fulfill({
          status: 409,
          json: { code: "project_name_exists", retryable: false },
        });
      const id = "project-" + state.created.length;
      state.projects.push(id);
      state.details[id] = { label };
      return route.fulfill({ status: 201, json: { project_id: id } });
    },
    "GET /v1/models": async (route) => {
      const project = new URL(route.request().url()).searchParams.get(
        "project_id",
      );
      state.modelQueries.push(project);
      if (slow[project]) await new Promise((r) => setTimeout(r, slow[project]));
      // p07 only grants Codex; every other scope grants both models.
      const models = project === "p07" ? MODELS.slice(0, 1) : MODELS;
      return route.fulfill({
        json: {
          models,
          providers: { codex: true, claude: true },
          uploads_enabled: true,
        },
      });
    },
    "GET /v1/project-directories": {
      json: {
        roots: [{ id: "home", label: "Local folders" }],
        root_id: "home",
        path: "",
        absolute_path: "/home/test-user",
        entries: [
          {
            name: "Work A",
            path: "Work A",
            absolute_path: "/home/test-user/Work A",
            type: "directory",
          },
        ],
        limited: false,
      },
    },
  });
  await page.goto("http://harness.test");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  // Projects are listed open by default in the Codex-style sidebar.
  if (!(await page.locator("#project-tree").evaluate((el) => el.open)))
    await page.locator("#project-tree > summary").click();
  return { s, state };
}

async function createProject(page, name) {
  await page.fill("#project-name", name);
  await page.click("#project-create");
}

// Chromium logs every 4xx fetch as a console error; the helper fails on any
// console error, so this scenario swaps in a listener ignoring only that line.
function allowHttpErrors(page) {
  const unexpected = [];
  page.removeAllListeners("console");
  page.on("console", (m) => {
    if (m.type() === "error" && !/^Failed to load resource/.test(m.text()))
      unexpected.push(m.text());
  });
  return unexpected;
}

runPersona("H28", [
  {
    title: "H28-S1 25 projects and names that collide with No project",
    async run(page) {
      const unexpected = allowHttpErrors(page);
      const { state } = await open(page);
      // `sem-projeto` is the loose-conversation scope, never a project group.
      assert.equal(await page.locator("#projects .project-group").count(), 25);
      assert.equal(
        await page.locator('#projects [data-project-id="sem-projeto"]').count(),
        0,
      );
      assert(
        await page.evaluate(() => {
          // The resize handle deliberately overhangs #sidebar; check the list.
          const s = document.querySelector("#sidebar .sidebar-tree");
          return (
            s.scrollWidth <= s.clientWidth + 1 &&
            document.documentElement.scrollWidth <= innerWidth
          );
        }),
        "25 projects must not overflow sideways",
      );
      await page
        .locator('#projects [data-project-id="p25"] summary')
        .scrollIntoViewIfNeeded();
      await visible(page, '#projects [data-project-id="p25"] summary');

      if (!(await page.locator("#project-tree").evaluate((el) => el.open)))
        await page.locator("#project-tree > summary").click();
      await page.click("#add-project");
      await visible(page, "#project-dialog");
      await page
        .locator("#project-directory-list .project-file-row")
        .filter({ hasText: "Work A" })
        .click();
      await page.click("#project-directory-add-current");

      for (const name of ["No project", "  NO   project "]) {
        await createProject(page, name);
        await page.waitForFunction(() =>
          /already exists/.test(
            document.getElementById("project-create-note").textContent,
          ),
        );
        assert.equal(
          await page.locator("#project-create-note").innerText(),
          "Couldn't save: A project with that name already exists.",
        );
        assert(await page.locator("#project-dialog").evaluate((d) => d.open));
      }
      // A client-side letter count refuses two-letter names before any request.
      await createProject(page, "No");
      assert.match(
        await page.locator("#project-create-note").innerText(),
        /at least 3 letters/,
      );
      assert.equal(state.created.length, 2);

      // The persisted id is not a reserved *label*: the server accepts it and gives
      // the new project its own id, so the scopes stay distinct.
      await createProject(page, "sem-projeto");
      await page.locator("#project-dialog").waitFor({ state: "hidden" });
      assert.equal(state.created.length, 3);
      assert.equal(await page.locator("#project").inputValue(), "project-3");
      assert.equal(await page.locator("#projects .project-group").count(), 26);
      assert.equal(
        await page
          .locator('#projects [data-project-id="project-3"] summary > button')
          .innerText(),
        "sem-projeto",
      );
      await page.fill("#prompt", "First message in the look-alike project");
      await page.click("#send");
      await page.waitForFunction(
        () => !document.getElementById("prompt").value,
      );
      assert.deepEqual(unexpected, []);
    },
  },
  {
    title: "H28-S2 switching project mid-draft",
    async run(page) {
      const { s, state } = await open(page);
      const draft = "Draft about the Q3 invoices for Client 7";
      await page.fill("#prompt", draft);
      const group = page.locator('#projects [data-project-id="p07"]');
      await group.locator("summary > button").first().click();
      const create = group.locator(".project-new");
      await visible(page, '#projects [data-project-id="p07"] .project-new');
      assert.equal(
        await create.getAttribute("aria-label"),
        "New Conversation in " + LABEL.p07,
      );

      // F-95: moving an unsent draft to another project keeps it, with no
      // discard prompt.
      const prompts = [];
      page.on("dialog", (d) => {
        prompts.push(d.message());
        return d.dismiss();
      });
      await create.click();
      await page.waitForFunction(
        () => document.getElementById("project").value === "p07",
      );
      assert.deepEqual(prompts, []);
      assert.equal(await page.locator("#prompt").inputValue(), draft);
      await page.waitForFunction(
        () => document.getElementById("model").options.length === 1,
      );
      assert(
        state.modelQueries.includes("p07"),
        "/v1/models?project_id=p07 refreshed",
      );
      assert.equal(await page.locator("#model").inputValue(), "gpt-5.6-luna");
      assert.equal(
        await page.locator("#conversation-title").innerText(),
        "New Conversation in project " + LABEL.p07,
      );

      // The legacy select path keeps the draft and refreshes the scoped models.
      await page.fill("#prompt", draft);
      const before = state.modelQueries.length;
      await page.selectOption("#project", "p12", { force: true });
      await page.waitForFunction(
        () => document.getElementById("model").options.length === 2,
      );
      assert.equal(await page.locator("#prompt").inputValue(), draft);
      assert(state.modelQueries.slice(before).includes("p12"));
      await page.click("#send");
      await page.waitForFunction(
        () => !document.getElementById("prompt").value,
      );
      assert.equal(s.posts.at(-1).project_id, "p12");
    },
  },
  {
    title: "H28-S2b fast switch: the slower first project's models do not win",
    async run(page) {
      const { state } = await open(page, { slow: { p07: 800 } });
      await page.selectOption("#project", "p07", { force: true });
      await page.selectOption("#project", "p12", { force: true });
      await page.waitForTimeout(1200);
      assert.deepEqual(state.modelQueries.slice(-2), ["p07", "p12"]);
      assert.equal(await page.locator("#project").inputValue(), "p12");
      // p07 would have left only Codex; p12 grants both.
      assert.equal(await page.locator("#model option").count(), 2);
    },
  },
]);
