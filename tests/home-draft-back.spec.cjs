// #58: opening history must not discard the Home project's unsent composer.
const assert = require("node:assert/strict");
const { mockHarness, runPersona } = require("./personas/_harness.cjs");

const settled = page => page.waitForFunction(() => !initializing && !loading && !policyPending && !uploads);
const scenarios = [];
for (const project of ["project-a", "sem-projeto"]) {
  for (const entry of ["sidebar", "search"]) {
    for (const method of ["button", "Alt+Left", "browser"]) {
      for (const retiredHistory of project === "project-a" && entry === "sidebar" && method === "button" ? [false, true] : [false]) {
      scenarios.push({
        title: `${project} draft survives ${entry}, ${method}, Forward and Back${retiredHistory ? " from retired history" : ""}`,
        async run(page, phone) {
          const state = await mockHarness(page, {
            "GET /v1/projects": { json: { projects: ["sem-projeto", "project-a"], details: { "project-a": { label: "Project Alpha" } } } },
            "GET /v1/models": { json: { models: [{ id: "fixture", backend: "codex", execution_modes: ["native"], efforts: ["low"], permissions: { upload: true } }], providers: { codex: true }, uploads_enabled: true } },
            "GET /v1/conversations": { json: { conversations: [{ id: "existing", title: "Existing conversation", project: "sem-projeto", state: "completed", execution: { backend: "codex", model: "fixture" } }] } },
            "GET /v1/conversations/existing": { json: { execution_mode: retiredHistory ? "scoped" : "native", turns: [{ id: "old-turn", project: "sem-projeto", state: "completed", request: { backend: "codex", model: "fixture", execution_mode: retiredHistory ? "scoped" : "native", prompt: "Previous question" }, result: { answer: "Previous answer" } }] } },
          });
          if (phone) {
            await page.addInitScript(() => localStorage.setItem("keepharness:theme:harness", "graphite"));
            await page.route("**/v1/activity", route => route.fulfill({ json: {
              jobs: [{ job_id: "old-turn", conversation_id: "existing", project_id: "sem-projeto", state: "completed", backend: "codex", model: "fixture" }],
              providers: [], needs_you: [], counts: {},
            } }));
          }
          await page.goto("http://harness.test");
          await page.locator("#startup-gate").waitFor({ state: "hidden" });
          await settled(page);
          await page.selectOption("#project", project, { force: true });
          await settled(page);
          const text = `Unsent ${project} draft`;
          await page.fill("#prompt", text);
          await page.locator("#file").setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("Draft attachment") });
          await settled(page);
          if (phone) {
            assert.equal(await page.locator("html").getAttribute("data-palette"), "graphite");
            await page.waitForFunction(() => observedActivityJobs.some(item => item.job_id === "old-turn"));
            await page.keyboard.press("Control+j");
            await page.locator("#run-console").waitFor({ state: "visible" });
            if (phone === "collapsed") {
              await page.keyboard.press("Control+j");
              await page.locator("#run-console").waitFor({ state: "hidden" });
            }
          }
          const beforeMode = await page.evaluate(() => ({ ...draftMode }));
          const beforeFiles = await page.evaluate(() => JSON.parse(JSON.stringify(files)));
          if (entry === "sidebar") {
            await page.locator("#history .conversation-row > button", { hasText: "Existing conversation" }).click();
          } else {
            await page.keyboard.press("Control+k");
            await page.fill("#conversation-search", "Existing conversation");
            await page.locator(".conversation-search-result", { hasText: "Existing conversation" }).click();
          }
          await page.waitForFunction(() => conversation === "existing" && !loading);
          if (phone) await page.locator("#run-console[aria-modal='true']").waitFor({ state: "visible" });
          const move = async delta => {
            if (method === "button") await page.getByTestId(delta < 0 ? "nav-back" : "nav-forward").click();
            else if (method === "Alt+Left") await page.keyboard.press(delta < 0 ? "Alt+ArrowLeft" : "Alt+ArrowRight");
            else await page.evaluate(delta => window.history.go(delta), delta);
          };
          for (let cycle = 0; cycle < 2; cycle++) {
            await move(-1);
            await page.waitForFunction(() => !conversation && !loading);
            if (phone) {
              assert.equal(await page.locator("#prompt").evaluate(node => node === document.activeElement), true, "Home immediately focuses its editor after closing the responsive console");
              assert.equal(await page.locator("#prompt").evaluate(node => node.checkVisibility() && !node.closest("[inert]")), true);
            }
            await settled(page);
            assert.equal(await page.locator("#project").inputValue(), project);
            assert.equal(await page.locator("#prompt").inputValue(), text);
            assert.match(await page.locator("#attachments").innerText(), /notes\.txt/);
            assert.deepEqual(await page.evaluate(() => files), beforeFiles);
            assert.deepEqual(await page.evaluate(() => ({ ...draftMode })), beforeMode);
            assert.equal(await page.locator("#prompt").evaluate(node => node === document.activeElement), true);
            assert.equal(state.posts.length, 0);
            if (!cycle) {
              await move(1);
              await page.waitForFunction(() => conversation === "existing" && !loading);
              assert.match(await page.locator("#messages").innerText(), /Previous answer/);
            }
          }
        },
      });
      }
    }
  }
}
const phoneScenario = scenarios.find(item => item.title === "project-a draft survives search, Alt+Left, Forward and Back");
for (const consoleState of ["expanded", "collapsed"]) scenarios.push({
  title: `Graphite 390px command search restores editor focus with console initially ${consoleState}`,
  viewport: { width: 390, height: 844 },
  run(page) { return phoneScenario.run(page, consoleState); },
});
scenarios.push({
  title: "Home leaves outer browser history available when no app Back target exists",
  async run(page) {
    await mockHarness(page, {
      "GET /outside": { body: "<!doctype html><title>Outside KeepHarness</title>" },
    });
    await page.goto("http://harness.test/outside");
    await page.goto("http://harness.test");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await settled(page);
    assert.equal(await page.getByTestId("nav-back").isDisabled(), true);
    assert.equal(await page.evaluate(() => document.dispatchEvent(new KeyboardEvent("keydown", {
      key: "ArrowLeft", altKey: true, bubbles: true, cancelable: true,
    }))), true, "Alt+Left retains its native default without an app history target");
    await page.goBack();
    assert.equal(new URL(page.url()).pathname, "/outside");
  },
});
runPersona("home-draft-back", scenarios);
