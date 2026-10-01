const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
(async () => {
  const browser = await chromium.launch();
  for (const scenario of [
    "empty",
    "catalog-error",
    "submit-error",
    "uploads-disabled",
    "stream-resume",
    "stream-cancel",
    "pending-submit",
    "duplicate-cancel",
    "approval-retry",
    "submission-retry",
    "submission-reload",
    "submission-change",
  ]) {
    const page = await browser.newPage();
    let mode = scenario,
      jobState = "running",
      eventRequests = 0,
      fileRequests = 0,
      cancelRequests = 0,
      decisionRequests = 0;
    const submissionKeys = [],
      decisions = [];
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/v1/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      let data = {};
      if (path === "/v1/projects")
        data = { projects: ["sem-projeto"], details: {} };
      if (path === "/v1/models") {
        if (mode === "catalog-error")
          return route.fulfill({
            status: 503,
            json: { code: "backend_unavailable" },
          });
        data = {
          models:
            mode === "empty"
              ? []
              : [
                  {
                    id: "fixture-local",
                    name: "Local test",
                    backend: "local",
                    efforts: ["low"],
                  },
                ],
          providers: { local: true },
          uploads_enabled: false,
        };
      }
      if (path === "/v1/conversations") data = { conversations: [] };
      if (path === "/v1/files") {
        fileRequests++;
        return route.fulfill({ status: 403, json: { code: "uploads_denied" } });
      }
      if (path.startsWith("/v1/approvals/")) {
        decisionRequests++;
        decisions.push(route.request().postDataJSON());
        await new Promise((resolve) => setTimeout(resolve, 250));
        if (decisionRequests === 1)
          return route.fulfill({
            status: 503,
            json: { code: "temporarily_unavailable" },
          });
      }
      if (path === "/v1/jobs" && route.request().method() === "POST") {
        submissionKeys.push(route.request().headers()["idempotency-key"]);
        if (scenario.startsWith("submission-")) return route.abort("failed");
        if (mode === "pending-submit")
          await new Promise((resolve) => setTimeout(resolve, 800));
        if (mode === "submit-error")
          return route.fulfill({
            status: 422,
            json: { code: "model_not_allowed" },
          });
        data = { job_id: "fixture-job" };
      }
      if (path.endsWith("/events")) {
        eventRequests++;
        return route.fulfill({
          contentType: "text/event-stream",
          body:
            scenario === "approval-retry"
              ? "data: " +
                JSON.stringify({
                  id: 1,
                  type: "approval_required",
                  data: {
                    approval_id: "fixture-approval",
                    request: {
                      questions: [{ id: "choice", question: "Test question" }],
                    },
                  },
                }) +
                "\n\n"
              : "",
        });
      }
      if (path.endsWith("/cancel")) {
        cancelRequests++;
        assert.equal(path, "/v1/jobs/fixture-job/cancel");
        await new Promise((resolve) => setTimeout(resolve, 500));
        jobState = "cancelled";
      }
      if (path === "/v1/jobs/fixture-job")
        data = {
          state: jobState,
          request: {},
          result:
            jobState === "running"
              ? null
              : {
                  answer: jobState === "completed" ? "Simulated answer." : "",
                  model: "fixture-local",
                },
        };
      return route.fulfill({ json: data });
    });
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.12.1"));
    await page.goto(process.env.HARNESS_URL || "http://127.0.0.1:18196/");
    await page.waitForFunction(
      () =>
        document.querySelector("#models-retry").textContent === "Check again",
    );
    if (scenario === "empty") {
      assert(await page.locator("#model-availability").isVisible());
      assert.match(
        await page.locator("#model-availability").innerText(),
        /No model available/,
      );
      assert(await page.locator("#send").isDisabled());
      assert(await page.locator("#attach").isDisabled());
    } else if (scenario === "catalog-error") {
      assert(await page.locator("#model-availability").isVisible());
      assert.match(
        await page.locator("#model-availability").innerText(),
        /Couldn't check/,
      );
      assert.doesNotMatch(
        await page.locator("#status").innerText(),
        /Connect Tailscale/,
      );
      assert(await page.locator("#startup-gate").isVisible());
      assert(await page.locator("main").evaluate((el) => el.inert));
      mode = "recovered";
      await page
        .locator("#startup-gate")
        .waitFor({ state: "hidden", timeout: 20000 });
      await page.waitForFunction(
        () => document.querySelector("#model").value === "fixture-local",
      );
      assert.equal(await page.locator("#prompt").inputValue(), "");
      assert(!(await page.locator("#model-availability").isVisible()));
      await page.fill("#prompt", "Connection recovered");
      assert(await page.locator("#send").isEnabled());
    } else if (scenario === "uploads-disabled") {
      assert(await page.locator("#attach").isDisabled());
      assert.match(
        await page.locator("#attachment-help").innerText(),
        /disabled/,
      );
      await page.locator("#file").setInputFiles({
        name: "fixture.txt",
        mimeType: "text/plain",
        buffer: Buffer.from("Fixture without personal data"),
      });
      await page.waitForFunction(() =>
        document
          .querySelector("#status")
          .textContent.includes("attachments are disabled"),
      );
      assert.equal(fileRequests, 0);
    } else if (scenario.startsWith("submission-")) {
      await page.fill("#prompt", "Preserved request");
      await page.click("#send");
      await page.waitForFunction(() =>
        document.querySelector("#status").textContent.includes("Couldn't run"),
      );
      const first = submissionKeys[0];
      if (scenario === "submission-reload") {
        await page.reload();
        await page.waitForFunction(
          () =>
            document.querySelector("#models-retry").textContent ===
            "Check again",
        );
        await page.fill("#prompt", "Preserved request");
      }
      if (scenario === "submission-change")
        await page.fill("#prompt", "Changed request");
      await page.click("#send");
      await page.waitForFunction(() =>
        document.querySelector("#status").textContent.includes("Couldn't run"),
      );
      assert.equal(submissionKeys.length, 2);
      if (scenario === "submission-change")
        assert.notEqual(submissionKeys[1], first);
      else assert.equal(submissionKeys[1], first);
    } else if (scenario === "approval-retry") {
      await page.fill("#prompt", "Approval request");
      await page.click("#send");
      const box = page.locator("#approval-fixture-approval");
      await box.waitFor();
      await box.locator("input").fill("Answer kept");
      await box.evaluate((el) => {
        el.querySelectorAll("button")[0].click();
        el.querySelectorAll("button")[1].click();
      });
      assert(await box.locator("button").first().isDisabled());
      assert(await box.locator("input").isDisabled());
      await page.waitForFunction(() =>
        document
          .querySelector("#approval-fixture-approval [role=status]")
          .textContent.includes("Couldn't confirm"),
      );
      assert.equal(decisionRequests, 1);
      assert.equal(decisions[0].approved, true);
      assert.equal(await box.locator("input").inputValue(), "Answer kept");
      assert(await box.locator("button").last().isEnabled());
      await box.locator("button").last().click();
      await box.waitFor({ state: "detached" });
      assert.equal(decisionRequests, 2);
      assert.equal(decisions[1].approved, false);
    } else if (
      scenario === "pending-submit" ||
      scenario === "duplicate-cancel"
    ) {
      await page.fill("#prompt", "Concurrency test");
      await page.click("#send");
      if (scenario === "pending-submit") {
        assert(await page.locator("#cancel").isDisabled());
        await page.locator("#cancel").evaluate((button) => button.click());
        assert.equal(cancelRequests, 0);
      }
      await page.waitForFunction(
        () => !document.querySelector("#cancel").disabled,
      );
      await page.locator("#cancel").evaluate((button) => {
        button.click();
        button.click();
      });
      assert(await page.locator("#cancel").isDisabled());
      await page.waitForTimeout(650);
      assert.equal(cancelRequests, 1);
    } else {
      await page.fill("#prompt", "Simulated question");
      await page.click("#send");
      if (scenario === "submit-error") {
        await page.waitForFunction(() =>
          document
            .querySelector("#status")
            .textContent.includes("This model is not enabled"),
        );
        assert.equal(
          await page.locator("#prompt").inputValue(),
          "Simulated question",
        );
        assert(await page.locator("#send").isEnabled());
      } else {
        await page
          .locator("#resume-execution")
          .waitFor({ state: "visible", timeout: 10000 });
        assert.equal(eventRequests, 4);
        assert(await page.locator("#cancel").isVisible());
        assert(
          await page.locator("#new").isEnabled(),
          "another conversation may start while this job remains on the server",
        );
        if (scenario === "stream-resume") {
          jobState = "completed";
          await page.click("#resume-execution");
        } else await page.click("#cancel");
        await page.locator("#cancel").waitFor({ state: "hidden" });
        assert(await page.locator("#new").isEnabled());
        assert(!(await page.locator("#resume-execution").isVisible()));
        if (scenario === "stream-resume")
          assert.match(
            await page.locator("#messages").innerText(),
            /Simulated answer/,
          );
      }
    }
    assert.deepEqual(errors, []);
    await page.close();
    console.log("PASS harness UX:", scenario);
  }
  await browser.close();
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
