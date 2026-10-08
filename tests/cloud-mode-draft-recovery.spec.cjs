// D-044 corrections: incomplete capabilities fail closed; only explicit New
// may replace a retired draft mode, preserving its text and attachments.
const assert = require("node:assert/strict");
const { mockHarness, runPersona } = require("./personas/_harness.cjs");

async function openDraft(page, backend, modes, historical = false, project = "sem-projeto") {
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
  await page.addInitScript(({ modelId, historical, project }) => {
    const saved = {
      project, conversation: historical ? "old-session" : "",
      composer_selection: { model: modelId, effort: "low" },
      execution_mode: "scoped", execution_mode_chosen: true,
      draft: "Preserved draft", files: [{ id: "kept-file", name: "notes.txt", project, type: "text/plain" }],
    };
    sessionStorage.setItem("remote-view", JSON.stringify(saved));
    sessionStorage.setItem("conversation-draft:" + (saved.conversation || "new:" + project), JSON.stringify(saved));
  }, { modelId: model.id, historical, project });
  await page.goto("http://harness.test");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  await page.waitForFunction(() => !initializing && !loading && !policyPending);
  return { state, model, projects };
}

const scenarios = [];
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
