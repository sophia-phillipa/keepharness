// Approval expiry remains visible, accessible and stable across event replay.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    let submissions = 0;
    let finishDecision;
    await page.route("http://approval.test/**", async (route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname.startsWith("/v1/")) {
        if (pathname.startsWith("/v1/approvals/")) {
          submissions++;
          if (pathname.endsWith("/not-enrolled"))
            return route.fulfill({ status: 403, json: { code: "approval_session_required", owner: "local" } });
          if (pathname.endsWith("/not-enrolled-hostile"))
            return route.fulfill({ status: 403, json: { code: "approval_session_required", owner: "x; touch pwned" } });
          if (pathname.endsWith("/keyboard-pending")) {
            await new Promise(resolve => { finishDecision = resolve; });
            return route.fulfill({ status: 500, json: { code: "internal_error" } });
          }
          return route.fulfill({ status: 404, json: { code: "approval_expired" } });
        }
        const data = pathname === "/v1/projects" ? { projects: ["sem-projeto"] }
          : pathname === "/v1/models" ? { models: [{ id: "fixture", backend: "local", efforts: ["configured"] }], providers: { local: true } }
          : pathname === "/v1/conversations" ? { conversations: [] }
          : pathname === "/v1/version" ? { version: "fixture", build: "fixture" } : {};
        return route.fulfill({ json: data });
      }
      const file = pathname === "/" ? "index.html" : pathname.slice(1);
      return route.fulfill({ body: await fs.readFile(path.join(__dirname, file.startsWith("assets/") ? "../harness_ui" : "../agent_service", file)),
        contentType: file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : "text/html" });
    });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    async function open() {
      await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
      await page.goto("http://approval.test");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
    }
    async function required(id, sequence) {
      await page.evaluate(({ id, sequence }) => event({ id: sequence, type: "approval_required", data: {
        approval_id: id, kind: "command", request: { command: "fixture command" }, expires_at: Date.now() / 1000 + 1800,
      } }), { id, sequence });
    }
    async function expired(id, sequence) {
      await page.evaluate(({ id, sequence }) => event({ id: sequence, type: "approval_expired", data: { approval_id: id } }), { id, sequence });
    }
    await open();
    await required("beginner", 1);
    await expired("beginner", 2);
    assert.match(await page.locator("#approval-beginner").innerText(), /Approval expired/);
    assert.match(await page.locator("#approval-beginner").innerText(), /Send your message again/);
    console.log("PASS P1: visible expiry and recovery instruction");

    await required("rushed", 3);
    await page.getByRole("button", { name: "Allow once", exact: true }).last().dblclick();
    await page.waitForFunction(() => document.getElementById("approval-rushed").dataset.state === "expired");
    assert.equal(submissions, 1);
    assert.equal(await page.locator("#approval-rushed button:enabled").count(), 0);
    console.log("PASS P2: repeated decision submits once, HTTP expiry disables retry");

    await required("keyboard", 4);
    await page.locator("#approval-keyboard button").first().focus();
    await expired("keyboard", 5);
    assert.equal(await page.locator("#prompt").evaluate(node => node === document.activeElement), true);
    assert.match(await page.locator("#approval-keyboard [role=status]").innerText(), /expired/i);
    console.log("PASS P4: expiry announces state and restores keyboard focus");

    await required("keyboard-pending", 6);
    await page.locator("#approval-keyboard-pending button").first().focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => document.querySelector("#approval-keyboard-pending button").disabled);
    await expired("keyboard-pending", 7);
    assert.equal(await page.locator("#prompt").evaluate(node => node === document.activeElement), true);
    finishDecision();
    await page.waitForTimeout(100);
    assert.match(await page.locator("#approval-keyboard-pending [role=status]").innerText(), /This approval request expired/);
    assert.equal(await page.locator("#approval-keyboard-pending button:enabled").count(), 0);
    console.log("PASS P4 recovery: expiry during keyboard submission survives a late HTTP error");

    await open();
    await required("history", 1);
    await expired("history", 2);
    await expired("history", 2);
    assert.equal(await page.locator("#approval-history").count(), 1);
    assert.equal(await page.locator("#approval-history button:enabled").count(), 0);
    console.log("PASS P3: replay after reopening preserves expired state");

    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await required("mobile", 1);
    await expired("mobile", 2);
    const bounds = await page.locator("#approval-mobile").boundingBox();
    assert(bounds.x >= 0 && bounds.x + bounds.width <= 391);
    assert.equal(await page.locator("#approval-mobile button:enabled").count(), 0);
    console.log("PASS P5: narrow-screen reload and replay remain usable");

    const guidance = await page.evaluate(() => userErrors.approval_session_required);
    assert.match(guidance, /enroll/i);
    assert.match(guidance, /approve-device/);
    console.log("PASS P6: denied worker credentials have human enrollment guidance");
    // A browser that is not enrolled sees how to enroll, in the card and the status line,
    // and the decision buttons stay usable for a retry after enrolling.
    await page.setViewportSize({ width: 1280, height: 860 });
    await required("not-enrolled", 3);
    await page.locator("#approval-not-enrolled").getByRole("button", { name: "Allow once" }).click();
    await page.locator("#approval-not-enrolled").getByText(/not enrolled/).waitFor();
    // The remote or local browser is told to run the command on the computer where KeepHarness runs.
    const command = /On the computer where KeepHarness runs, run keepharness approve-device --owner local\b/;
    assert.match(await page.locator("#approval-not-enrolled").innerText(), command);
    assert.doesNotMatch(await page.locator("#approval-not-enrolled").innerText(), /guest|ask the owner|alice/i);
    assert.match(await page.locator("#status").innerText(), /not enrolled/);
    assert.match(await page.locator("#status").innerText(), command);
    assert.equal(await page.locator("#approval-not-enrolled button:enabled").count() > 0, true);
    console.log("PASS P6b: an approval from a browser that is not enrolled explains the enrollment");
    // A 403 body naming any owner never reaches the command: the text is fixed.
    {
      await required("not-enrolled-hostile", 4);
      await page.locator("#approval-not-enrolled-hostile").getByRole("button", { name: "Allow once" }).click();
      await page.locator("#approval-not-enrolled-hostile").getByText(/not enrolled/).waitFor();
      const text = await page.locator("#approval-not-enrolled-hostile").innerText();
      assert.match(text, command);
      assert.doesNotMatch(text, /touch pwned/);
    }
    console.log("PASS P6c: enrollment guidance is fixed and never echoes an owner from the response");
    await page.evaluate(async () => {
      job = "expired-waits";
      active = assistant(job, "fixture");
      await result(job, controller, {
        state: "cancelled",
        result: { error: "approval_expiration_limit" },
        request: { model: "fixture" },
      });
    });
    assert.match(await page.locator(".run-notice").last().innerText(), /cancelled after repeated approval requests expired/);
    assert.match(await page.locator(".run-notice").last().innerText(), /Send your message again/);
    console.log("PASS P6 recovery: cancellation without streamed output preserves its recovery guidance");
    assert.match(await page.locator("#approval-mobile h3").innerText(), /^Approval expired$/);
    assert.equal(errors.length, 0, errors.join("\n"));
    console.log("PASS P7: consistent expiry label, no raw error or browser exceptions");
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
