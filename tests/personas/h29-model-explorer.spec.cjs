// H29 model explorer (P6): a stale model saved in this browser, a model removed
// while the page is open, and a send refused with 422 model_not_allowed.
"use strict";
const assert = require("node:assert/strict");
const { mockHarness, runPersona, visible } = require("./_harness.cjs");

const MODELS = [
  {
    id: "gpt-5.6-luna",
    backend: "codex",
    efforts: ["low", "medium"],
    permissions: { upload: true },
    execution_modes: ["native", "scoped"],
  },
  {
    id: "claude-sonnet-5",
    backend: "claude",
    efforts: ["low", "high"],
    permissions: { upload: true },
    execution_modes: ["native", "scoped"],
  },
];
const catalog = (models) => ({
  json: {
    models,
    providers: { codex: true, claude: true },
    uploads_enabled: true,
  },
});

async function open(page, selection, over = {}) {
  if (selection !== undefined)
    await page.addInitScript((value) => {
      if (!sessionStorage.getItem("h29-seeded")) {
        localStorage.setItem("chat-selection", value);
        sessionStorage.setItem("h29-seeded", "1");
      }
    }, selection);
  const s = await mockHarness(page, {
    "GET /v1/models": catalog(MODELS),
    ...over,
  });
  await page.goto("http://harness.test");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  return s;
}

async function chooseModel(page, id) {
  await page.click("#model-trigger");
  const option = page.locator(`#model-menu [role=option][data-value="${id}"]`);
  if (!(await option.isVisible()))
    await option.locator("xpath=ancestor::details/summary").click();
  await option.click();
}

// Chromium logs every 4xx fetch as a console error ("Failed to load resource");
// the helper treats all console errors as failures, so scenarios that force an
// HTTP error swap its listener for one that only ignores that network line.
function allowHttpErrors(page) {
  const unexpected = [];
  page.removeAllListeners("console");
  page.on("console", (m) => {
    if (m.type() === "error" && !/^Failed to load resource/.test(m.text()))
      unexpected.push(m.text());
  });
  return unexpected;
}

// Text a user could read as "your saved model is gone, we picked another one".
const fallbackNotice = (page, gone) =>
  page.evaluate(
    (gone) =>
      document.body.innerText
        .split("\n")
        .some(
          (line) =>
            line.includes(gone) ||
            /no longer (available|offered)|was removed|switched to/i.test(line),
        ),
    gone,
  );

runPersona("H29", [
  {
    title: "H29-S1 stale saved model falls back to an available model",
    async run(page) {
      await open(
        page,
        JSON.stringify({ model: "claude-opus-4-1", effort: "max" }),
      );
      // Falls back to the first available model and one of its efforts.
      assert.equal(await page.locator("#model").inputValue(), "gpt-5.6-luna");
      assert.match(await page.locator("#model-label").innerText(), /luna/i);
      assert(
        ["low", "medium"].includes(await page.locator("#effort").inputValue()),
      );
      assert.equal(await page.locator("#send").isDisabled(), true);
      await page.fill("#prompt", "Hello");
      assert.equal(await page.locator("#send").isDisabled(), false);
      // F-90: the fallback is announced, naming the model that is gone.
      assert.match(
        await page.locator("#status").textContent(),
        /claude-opus-4-1 is no longer available; switched to .*Luna/i,
      );
    },
  },
  {
    title: "H29-S1b saved effort no longer offered keeps the saved model",
    async run(page) {
      await open(
        page,
        JSON.stringify({ model: "claude-sonnet-5", effort: "ultra" }),
      );
      assert.equal(
        await page.locator("#model").inputValue(),
        "claude-sonnet-5",
      );
      assert(
        ["low", "high"].includes(await page.locator("#effort").inputValue()),
      );
    },
  },
  {
    title: "H29-S1c corrupt saved selection does not break startup",
    async run(page) {
      await open(page, "{not json");
      await visible(page, "#prompt");
      assert.equal(await page.locator("#model").inputValue(), "gpt-5.6-luna");
    },
  },
  {
    title: "H29-S1d model removed by the admin while the page is open",
    async run(page) {
      let models = MODELS;
      await open(
        page,
        JSON.stringify({ model: "claude-sonnet-5", effort: "high" }),
        {
          "GET /v1/models": (route) => route.fulfill(catalog(models)),
        },
      );
      assert.equal(
        await page.locator("#model").inputValue(),
        "claude-sonnet-5",
      );
      await page.fill("#prompt", "Draft written for Claude");
      assert.equal(
        await fallbackNotice(page, "claude-sonnet-5"),
        true,
        "label names the model",
      );
      models = MODELS.filter((m) => m.backend !== "claude");
      // The periodic readiness probe also runs on visibilitychange.
      await page.evaluate(() =>
        document.dispatchEvent(new Event("visibilitychange")),
      );
      await page.waitForFunction(
        () => document.getElementById("model").value === "gpt-5.6-luna",
      );
      assert.equal(
        await page.locator("#prompt").inputValue(),
        "Draft written for Claude",
      );
      assert.match(await page.locator("#model-label").innerText(), /luna/i);
      // F-90: the switch from Claude to Codex is announced before the next send.
      assert.match(
        await page.locator("#status").textContent(),
        /is no longer available; switched to .*Luna/i,
      );
    },
  },
  {
    title: "H29-S2 send refused with model_not_allowed keeps the draft",
    async run(page) {
      let refused = 0;
      const unexpected = allowHttpErrors(page);
      const s = await open(page, undefined, {
        "POST /v1/jobs": (route) => {
          refused++;
          return route.fulfill({
            status: 422,
            json: {
              code: "model_not_allowed",
              message: "model_not_allowed",
              retryable: false,
            },
          });
        },
      });
      await chooseModel(page, "claude-sonnet-5");
      const draft = "Explain the retry policy — com acentuação";
      await page.fill("#prompt", draft);
      await page.click("#send");
      await page.waitForFunction(() =>
        /Couldn't run/.test(document.getElementById("status").textContent),
      );
      const text = await page.locator("#status").innerText();
      assert.match(
        text,
        /This model is not enabled\. Check the available models\./,
      );
      assert.doesNotMatch(text, /model_not_allowed/);
      assert.equal(refused, 1);
      assert.equal(s.posts.length, 0);
      assert.equal(await page.locator("#prompt").inputValue(), draft);
      assert.equal(
        await page.locator(".chat-bubbles .user, [data-role=user]").count(),
        0,
      );
      // The model menu stays reachable so the user can pick another model.
      assert.equal(await page.locator("#model-trigger").isDisabled(), false);
      await page.click("#model-trigger");
      await page.locator("#model-menu").waitFor({ state: "visible" });
      assert(
        (await page.locator("#model-menu [role=option]").count()) >= 2,
        "both models listed",
      );
      await page.keyboard.press("Escape");
      assert.equal(await page.locator("#send").isDisabled(), false);
      assert.deepEqual(unexpected, []);
    },
  },
  {
    title: "H29-S2b recovers by picking another model after the refusal",
    async run(page) {
      const accepted = [];
      const unexpected = allowHttpErrors(page);
      await open(page, undefined, {
        "POST /v1/jobs": (route) => {
          const body = route.request().postDataJSON();
          if (body.model === "claude-sonnet-5")
            return route.fulfill({
              status: 422,
              json: { code: "model_not_allowed", retryable: false },
            });
          accepted.push(body);
          return route.fulfill({ json: { job_id: "job-ok" } });
        },
        "GET /v1/jobs/job-ok": {
          json: {
            id: "job-ok",
            project: "sem-projeto",
            state: "completed",
            result: { answer: "Fixture response." },
          },
        },
      });
      await chooseModel(page, "claude-sonnet-5");
      await page.fill("#prompt", "Second attempt");
      await page.click("#send");
      await page.waitForFunction(() =>
        /not enabled/.test(document.getElementById("status").textContent),
      );
      await chooseModel(page, "gpt-5.6-luna");
      assert.equal(
        await page.locator("#prompt").inputValue(),
        "Second attempt",
      );
      await page.click("#send");
      await page.waitForFunction(
        () => !document.getElementById("prompt").value,
      );
      assert.equal(accepted.length, 1);
      assert.equal(accepted[0].model, "gpt-5.6-luna");
      assert.equal(accepted[0].backend, "codex");
      assert.equal(accepted[0].prompt, "Second attempt");
      assert.deepEqual(unexpected, []);
    },
  },
]);
