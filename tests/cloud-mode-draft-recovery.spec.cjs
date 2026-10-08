// D-044 corrections: incomplete capabilities fail closed; only explicit New
// may replace a retired draft mode, preserving its text and attachments.
const assert = require("node:assert/strict");
const { mockHarness, runPersona } = require("./personas/_harness.cjs");

async function openDraft(page, backend, modes, historical = false, project = "sem-projeto", modeChosen = true) {
  const model = { id: backend === "claude" ? "claude-sonnet-4-6" : backend + "-fixture", backend, efforts: ["low"], permissions: { upload: true } };
  if (modes !== undefined) model.execution_modes = modes;
  const catalog = { models: [model], providers: { [backend]: true }, uploads_enabled: true };
  const projects = { projects: [...new Set([project, "sem-projeto"])], details: {} };
  const turn = { id: "old-session", project, state: "completed", request: { backend, model: model.id, prompt: "Old turn", execution_mode: "scoped" }, result: { answer: "Historic answer" } };
  const state = await mockHarness(page, {
    "GET /v1/projects": route => route.fulfill({ json: projects }),
    "GET /v1/models": route => route.fulfill({ json: catalog }),
    "GET /v1/conversations/old-session": { json: { execution_mode: "scoped", turns: [turn] } },
  });
  // Mutable response lets the test refresh capabilities without changing the draft.
  await page.addInitScript(({ modelId, historical, project, modeChosen }) => {
    if (sessionStorage.getItem("round3-reload")) return;
    const saved = {
      project, conversation: historical ? "old-session" : "",
      composer_selection: { model: modelId, effort: "low" },
      execution_mode: "scoped", execution_mode_chosen: modeChosen,
      draft: "Preserved draft", files: [{ id: "kept-file", name: "notes.txt", project, type: "text/plain" }],
    };
    sessionStorage.setItem("remote-view", JSON.stringify(saved));
    sessionStorage.setItem("conversation-draft:" + (saved.conversation || "new:" + project), JSON.stringify(saved));
  }, { modelId: model.id, historical, project, modeChosen });
  await page.goto("http://harness.test");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  await page.waitForFunction(() => !initializing && !loading && !policyPending);
  return { state, model, projects, catalog };
}

const scenarios = [];
const settled = page => page.waitForFunction(() => !initializing && !loading && !policyPending);
const historicalRoutes = [
  ...["codex", "claude"].flatMap(backend => [
    { name: `Local to ${backend}`, backends: ["local", backend], mode: "scoped", locked: true },
    { name: `${backend} to Local`, backends: [backend, "local"], mode: "scoped", locked: true },
    { name: `Local through ${backend} to Local`, backends: ["local", backend, "local"], mode: "scoped", locked: true },
  ]),
  { name: "Local-only", backends: ["local", "local"], mode: "scoped", locked: false },
  { name: "native cloud handoff", backends: ["codex", "claude"], mode: "native", locked: false },
];
for (const history of historicalRoutes) {
  scenarios.push({
    title: `historical ${history.name} derives retirement from the complete conversation`,
    async run(page) {
      const { state, catalog } = await openDraft(page, "local", ["scoped"]);
      catalog.models.push({ id: "codex-fixture", backend: "codex", execution_modes: ["native"], efforts: ["low"] });
      await page.evaluate(() => initialize());
      const turns = history.backends.map((backend, index) => ({
        id: `historical-turn-${index}`, project: "sem-projeto", state: "completed",
        request: { backend, model: backend + "-fixture", execution_mode: history.mode,
          prompt: `Historical request ${index}`, ...(index ? { parent_job_id: `historical-turn-${index - 1}` } : {}) },
        result: { answer: `Historical answer ${index}` },
      }));
      await page.route("**/v1/conversations/mixed-history", route => route.fulfill({ json: {
        execution_mode: history.mode, turns,
      } }));
      await page.evaluate(() => navigate({ kind: "conversation", id: "mixed-history" }));
      await settled(page);
      await page.selectOption("#model", history.mode === "native" ? "codex-fixture" : "local-fixture", { force: true });
      await page.fill("#prompt", "Continue the historical conversation");
      await settled(page);
      assert.equal(await page.getByText("Historical answer 0", { exact: true }).isVisible(), true);
      assert.equal(await page.evaluate(() => draftMode.retiredLock), history.locked);
      assert.equal(await page.evaluate(() => executionMode), history.mode);
      assert.equal(await page.locator("#send").isDisabled(), history.locked);
      if (history.locked) {
        assert.match(await page.locator("#execution-mode-unavailable").innerText(), /no longer supported/);
        await page.locator("#prompt").press("Enter");
        assert.equal(state.posts.length, 0);
        assert.equal(await page.evaluate(() => JSON.parse(sessionStorage.getItem("conversation-draft:mixed-history")).draft_mode.retiredLock), true);
      } else {
        await page.locator("#prompt").press("Enter");
        await page.waitForFunction(() => job === "job-1");
        assert.equal(state.posts.length, 1);
        assert.equal(state.posts[0].parent_job_id, turns.at(-1).id);
      }
    },
  });
}
for (const backend of ["codex", "claude"]) {
  for (const modeChosen of [false, true]) for (const transition of ["back", "forward", "home", "project", "probe", "refresh", "reload"]) {
    scenarios.push({
      title: `${backend} locked draft retains its complete state through ${transition} (chosen=${modeChosen})`,
      async run(page) {
        const { state, projects } = await openDraft(page, backend, ["native"], false, "sem-projeto", modeChosen);
        await page.route("**/v1/conversations/native-session", route => route.fulfill({ json: {
          execution_mode: "native", turns: [{ id: "native-turn", project: "sem-projeto", state: "completed",
            request: { backend, model: backend === "claude" ? "claude-sonnet-4-6" : backend + "-fixture", prompt: "Native history" }, result: { answer: "Answer" } }],
        } }));
        if (transition === "back") {
          await page.evaluate(() => navigate({ kind: "conversation", id: "native-session" }));
          await page.getByTestId("nav-back").click();
        } else if (transition === "forward") {
          await page.evaluate(() => navigate({ kind: "conversation", id: "native-session" }));
          await page.getByTestId("nav-back").click();
          await page.getByTestId("nav-forward").click();
          await page.getByTestId("nav-back").click();
        } else if (transition === "home") {
          await page.evaluate(() => navigate({ kind: "conversation", id: "native-session" }));
          await page.evaluate(() => applyView({ kind: "home" }, true));
        } else if (transition === "project") {
          projects.projects.push("another-project");
          await page.evaluate(() => initialize());
          await page.selectOption("#project", "another-project", { force: true });
        } else if (transition === "probe") {
          projects.projects = ["replacement-project"];
          await page.evaluate(async () => { readinessRetryAt = 0; await probeReadiness(); });
        } else if (transition === "refresh") {
          await page.evaluate(() => initialize());
        } else {
          // The seed script must not replace the actual persisted snapshot on reload.
          await page.evaluate(() => sessionStorage.setItem("round3-reload", "1"));
          await page.reload();
        }
        await settled(page);
        assert.equal(await page.evaluate(() => executionMode), "scoped");
        assert.equal(await page.locator("#prompt").inputValue(), "Preserved draft");
        assert.equal(await page.locator("#send").isDisabled(), true);
        await page.locator("#prompt").press("Enter");
        assert.equal(state.posts.length, 0);
        for (const key of ["remote-view", "conversation-draft:new:" + await page.locator("#project").inputValue()]) {
          assert.deepEqual(await page.evaluate(key => JSON.parse(sessionStorage.getItem(key)).draft_mode, key),
            { mode: "scoped", modeChosen, retiredLock: true });
        }
        await page.click("#new");
        await settled(page);
        assert.equal(await page.evaluate(() => executionMode), "native");
        assert.equal(await page.locator("#prompt").inputValue(), "Preserved draft");
      },
    });
  }
}
for (const backend of ["codex", "claude"]) {
  scenarios.push({
    title: `${backend} retired lock survives Local selection and its disabled isolation indicator`,
    async run(page) {
      const { state, catalog } = await openDraft(page, backend, ["native"]);
      catalog.models.push({ id: "local-fixture", backend: "local", execution_modes: ["scoped"], efforts: ["low"] });
      await page.evaluate(() => initialize());
      await page.selectOption("#model", "local-fixture", { force: true });
      await settled(page);
      assert.equal(await page.locator("#send").isDisabled(), true);
      assert.equal(await page.locator("#isolation-toggle").isDisabled(), true);
      // A programmatic click on the required Local indicator cannot bypass the lock.
      await page.evaluate(() => $("isolation-toggle").click());
      assert.equal(await page.evaluate(() => executionMode), "scoped");
      assert.match(await page.locator("#execution-mode-unavailable").innerText(), /no longer supported/);
      await page.locator("#prompt").press("Enter");
      assert.equal(state.posts.length, 0);
      await page.click("#new");
      await settled(page);
      assert.equal(await page.locator("#model").inputValue(), "local-fixture");
      assert.equal(await page.locator("#send").isDisabled(), false);
    },
  });
}
scenarios.push({
  title: "legacy saved mode does not invent an explicit choice",
  async run(page) {
    const { state } = await openDraft(page, "codex", ["native"]);
    await page.evaluate(() => {
      const saved = JSON.parse(sessionStorage.getItem("remote-view"));
      delete saved.draft_mode;
      delete saved.execution_mode_chosen;
      sessionStorage.setItem("remote-view", JSON.stringify(saved));
      sessionStorage.setItem("round3-reload", "1");
    });
    await page.reload();
    await settled(page);
    assert.deepEqual(await page.evaluate(() => JSON.parse(sessionStorage.getItem("remote-view")).draft_mode),
      { mode: "scoped", modeChosen: false, retiredLock: true });
    await page.locator("#prompt").press("Enter");
    assert.equal(state.posts.length, 0);
  },
});
scenarios.push({
  title: "historical Local backend remains scoped when its old model leaves the catalog",
  async run(page) {
    const { state } = await openDraft(page, "local", ["scoped"]);
    await page.route("**/v1/conversations/local-history", route => route.fulfill({ json: {
      execution_mode: "scoped", turns: [{ id: "local-turn", project: "sem-projeto", state: "completed",
        request: { backend: "local", model: "removed-local-model", prompt: "Local history" }, result: { answer: "Answer" } }],
    } }));
    await page.evaluate(() => {
      sessionStorage.setItem("conversation-draft:local-history", JSON.stringify({ project: "sem-projeto",
        draft: "Local followup", draft_mode: { mode: "scoped", modeChosen: true, retiredLock: false } }));
    });
    await page.evaluate(() => navigate({ kind: "conversation", id: "local-history" }));
    await settled(page);
    assert.deepEqual(await page.evaluate(() => JSON.parse(sessionStorage.getItem("remote-view")).draft_mode),
      { mode: "scoped", modeChosen: true, retiredLock: false });
    assert.equal(await page.locator("#send").isDisabled(), false);
    await page.locator("#prompt").press("Enter");
    await page.waitForFunction(() => job === "job-1");
    assert.equal(state.posts[0].parent_job_id, "local-turn");
  },
});
scenarios.push({
  title: "Back restores a native Home draft independently of the retired conversation being left",
  async run(page) {
    const { state } = await openDraft(page, "codex", ["native"]);
    await page.click("#new");
    await settled(page);
    await page.evaluate(() => navigate({ kind: "conversation", id: "old-session" }));
    await settled(page);
    assert.equal(await page.locator("#send").isDisabled(), true);
    await page.getByTestId("nav-back").click();
    await settled(page);
    assert.equal(await page.locator("#prompt").inputValue(), "Preserved draft");
    assert.deepEqual(await page.evaluate(() => JSON.parse(sessionStorage.getItem("remote-view")).draft_mode),
      { mode: "native", modeChosen: false, retiredLock: false });
    assert.equal(await page.locator("#send").isDisabled(), false);
    assert.equal(state.posts.length, 0);
  },
});
scenarios.push({
  title: "fresh project selection leaves mode unchosen so Local can send",
  async run(page) {
    const { state, projects, catalog } = await openDraft(page, "codex", ["native"]);
    catalog.models.push({ id: "local-fixture", backend: "local", execution_modes: ["scoped"], efforts: ["low"], permissions: { upload: true } });
    projects.projects.push("another-project");
    await page.evaluate(() => initialize());
    await page.click("#new");
    await settled(page);
    await page.selectOption("#project", "another-project", { force: true });
    await settled(page);
    assert.equal(await page.evaluate(() => JSON.parse(sessionStorage.getItem("remote-view")).execution_mode_chosen), false);
    await page.selectOption("#model", "local-fixture", { force: true });
    await settled(page);
    assert.equal(await page.evaluate(() => executionMode), "scoped");
    await page.selectOption("#project", "sem-projeto", { force: true });
    await settled(page);
    assert.equal(await page.locator("#model").inputValue(), "local-fixture");
    assert.equal(await page.evaluate(() => executionMode), "scoped");
    assert.equal(await page.locator("#send").isDisabled(), false);
    await page.selectOption("#model", "codex-fixture", { force: true });
    assert.deepEqual(await page.evaluate(() => JSON.parse(sessionStorage.getItem("remote-view")).draft_mode),
      { mode: "native", modeChosen: false, retiredLock: false });
    await page.selectOption("#model", "local-fixture", { force: true });
    await page.locator("#prompt").press("Enter");
    await page.waitForFunction(() => !!job);
    assert.equal(state.posts[0].execution_mode, "scoped");
    assert.equal(state.posts[0].model, "local-fixture");
  },
});

for (const backend of ["codex", "claude"]) {
  for (const destinationDraft of [false, true]) {
    scenarios.push({
      title: `${backend} removed project preserves scoped mode${destinationDraft ? " over a saved native destination draft" : ""} until explicit New`,
      async run(page) {
        const { state, projects } = await openDraft(page, backend, ["native"], false, "old-project");
        assert.equal(await page.locator("#project").inputValue(), "old-project");
        assert.equal(await page.locator("#send").isDisabled(), true);
        if (destinationDraft) await page.evaluate(() => {
          sessionStorage.setItem("conversation-draft:new:sem-projeto", JSON.stringify({
            project: "sem-projeto", draft: "Preserved draft", execution_mode: "native", execution_mode_chosen: true,
          }));
        });
        projects.projects = ["sem-projeto"];
        await page.evaluate(async () => { readinessRetryAt = 0; await probeReadiness(); });
        await page.waitForFunction(() => !policyPending && !loading);
        assert.equal(await page.locator("#project").inputValue(), "sem-projeto");
        assert.equal(await page.evaluate(() => executionMode), "scoped");
        assert.equal(await page.locator("#prompt").inputValue(), "Preserved draft");
        assert.equal(await page.locator("#send").isDisabled(), true);
        assert.match(await page.locator("#execution-mode-unavailable").innerText(), /no longer supported.*[Nn]ew conversation/);
        await page.locator("#prompt").press("Enter");
        assert.equal(state.posts.length, 0);
        await page.evaluate(() => initialize());
        assert.equal(await page.evaluate(() => executionMode), "scoped");
        assert.equal(await page.locator("#send").isDisabled(), true);
        await page.click("#new");
        await page.waitForFunction(() => !policyPending && !loading);
        assert.equal(await page.evaluate(() => executionMode), "native");
        assert.equal(state.posts.length, 0);
        await page.click("#send");
        await page.waitForFunction(() => !!job);
        assert.equal(state.posts.length, 1);
        assert.equal(state.posts[0].execution_mode, "native");
        assert.equal(state.posts[0].parent_job_id, undefined);
      },
    });
  }
  scenarios.push({
    title: `${backend} replaying home preserves the retired mode until explicit New`,
    async run(page) {
      const { state } = await openDraft(page, backend, ["native"], true);
      await page.evaluate(() => applyView({ kind: "home" }, true));
      await page.waitForFunction(() => !policyPending && !loading);
      assert.equal(await page.evaluate(() => executionMode), "scoped");
      assert.equal(await page.locator("#send").isDisabled(), true);
      await page.locator("#prompt").press("Enter");
      assert.equal(state.posts.length, 0);
      await page.click("#new");
      await page.waitForFunction(() => !policyPending && !loading);
      assert.equal(await page.evaluate(() => executionMode), "native");
      assert.equal(state.posts.length, 0);
    },
  });
  for (const [name, modes] of [["missing", undefined], ["null", null], ["string", "scoped"], ["object", {}], ["empty", []], ["invalid entry", ["scoped", null]]]) {
    scenarios.push({
      title: `${backend} ${name} capabilities block a restored scoped draft`,
      async run(page) {
        const { state, model } = await openDraft(page, backend, modes);
        assert.equal(await page.locator("#prompt").inputValue(), "Preserved draft");
        assert.equal(await page.evaluate(() => executionMode), "scoped");
        assert.deepEqual(await page.evaluate(() => supportedExecutionModes()), []);
        assert.equal(await page.locator("#send").isDisabled(), true);
        await page.locator("#prompt").press("Enter");
        assert.equal(state.posts.length, 0);
        model.execution_modes = ["native"];
        await page.evaluate(() => initialize());
        assert.equal(await page.evaluate(() => executionMode), "scoped");
        assert.equal(await page.locator("#send").isDisabled(), true);
        await page.click("#new");
        await page.waitForFunction(() => !policyPending && !loading);
        assert.equal(await page.evaluate(() => executionMode), "native");
        assert.equal(state.posts.length, 0);
        await page.click("#send");
        await page.waitForFunction(() => !!job);
        assert.equal(state.posts.length, 1);
        assert.equal(state.posts[0].execution_mode, "native");
        assert.equal(state.posts[0].parent_job_id, undefined);
      },
    });
  }
  for (const historical of [false, true]) {
    scenarios.push({
      title: `${backend} explicit New recovers ${historical ? "historical session" : "scoped draft"} without losing text or files`,
      async run(page) {
        const { state } = await openDraft(page, backend, ["native"], historical);
        assert.equal(await page.locator("#send").isDisabled(), true);
        await page.click("#new");
        await page.waitForFunction(() => !policyPending && !loading);
        assert.equal(await page.evaluate(() => executionMode), "native");
        assert.equal(await page.locator("#prompt").inputValue(), "Preserved draft");
        assert.equal(await page.locator("#attachments .attachment").count(), 1);
        assert.deepEqual(await page.evaluate(() => [conversation, parent, job]), ["", null, ""]);
        assert.equal(state.posts.length, 0);
        await page.click("#send");
        await page.waitForFunction(() => !!job);
        assert.equal(state.posts[0].execution_mode, "native");
        assert.deepEqual(state.posts[0].file_ids, ["kept-file"]);
        assert.equal(state.posts[0].parent_job_id, undefined);
      },
    });
  }
}
scenarios.push({
  title: "explicit New invalidates resource selections from a retired mode",
  async run(page) {
    await openDraft(page, "claude", ["native"]);
    await page.evaluate(() => {
      $("prompt").value = "Preserve @old-agent";
      resourceSelections = [{ id: "old-agent", revision: "r1", token: "@old-agent" }];
      saveView();
    });
    await page.click("#new");
    assert.equal(await page.evaluate(() => executionMode), "native");
    assert.deepEqual(await page.evaluate(() => resourceSelections), []);
    assert.equal(await page.evaluate(() => invalidResourceTokens.has("@old-agent")), true);
    assert.equal(await page.locator("#prompt").inputValue(), "Preserve @old-agent");
  },
});
for (const [backend, mode] of [["local", "scoped"], ["gemini", "native"], ["deepseek", "native"]]) {
  scenarios.push({
    title: `explicit New preserves ${backend} supported ${mode} mode`,
    async run(page) {
      const { state } = await openDraft(page, backend, [mode]);
      await page.click("#new");
      await page.waitForFunction(() => !policyPending && !loading);
      assert.equal(await page.evaluate(() => executionMode), mode);
      assert.equal(await page.locator("#prompt").inputValue(), "Preserved draft");
      assert.equal(state.posts.length, 0);
      await page.click("#send");
      await page.waitForFunction(() => !!job);
      assert.equal(state.posts[0].execution_mode, mode);
      assert.equal(state.posts[0].parent_job_id, undefined);
    },
  });
}
runPersona("cloud-mode-draft-recovery", scenarios);
