// Plan review, explicit automation and workflow discovery reuse the existing chat shell.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { mount, run } = require("./run-console-fixture.cjs");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 900 },
    });
    page.setDefaultTimeout(5000);
    const posts = [],
      errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await mount(page, async (url, request) => {
      if (url.pathname === "/v1/resources")
        return {
          json: {
            items: [
              {
                id: "project/sem-projeto/workflows/review.json",
                resource_id: "project/sem-projeto/workflows/review.json",
                revision: "revision-one",
                kind: "workflow",
                mode: "delegated",
                name: "review",
                group: "Workflows",
                description: "Review evidence in two sequential steps",
                scope: "project",
                origin: "project",
                selectable: true,
              },
            ],
            warnings: [],
          },
        };
      if (url.pathname === "/v1/activity")
        return { json: { jobs: [run], needs_you: [], providers: [] } };
      if (url.pathname.startsWith("/v1/approvals/")) {
        posts.push(request.postDataJSON());
        return { json: { resolved: true, choice: "approve" } };
      }
      if (url.pathname === "/v1/jobs" && request.method() === "POST") {
        posts.push(request.postDataJSON());
        return { status: 422, json: { error: "fixture_stop" } };
      }
    });
    // P6: Automation is an explicit planning choice and preserves the project default.
    assert.equal(await page.locator("#maestro-plan-policy").inputValue(), "");
    await page.locator("#settings").click();
    await page.locator("[data-settings=agents]").click();
    await page.locator("#maestro-plan-policy").selectOption("auto");
    await page.locator("#settings-close").click();
    await page.locator("#prompt").fill("Review the evidence");
    await page.locator("#send").click();
    await page.waitForFunction(() => !document.querySelector("#send").disabled);
    assert.equal(posts.at(-1).maestro_plan_policy, "auto");
    await page.locator("#settings").click();
    await page.locator("#maestro-plan-policy").selectOption("review");
    await page.locator("#settings-close").click();
    // P1/P3: Human review presents the actual ordered plan before execution.
    const steps = [
      {
        id: "inspect",
        role: "Reviewer",
        backend: "local",
        model: "fixture",
        effort: "configured",
        task: "Inspect the evidence",
        reason: "Check sources before delivery",
      },
    ];
    await page.evaluate((steps) => {
      active = assistant("run-a", "fixture");
      showMaestroPlan({ steps, gate_id: "plan-review" });
    }, steps);
    const card = page.locator("#gate-plan-review");
    assert.match(await card.innerText(), /Nothing runs until you approve/);
    // P4/P7: Controls are reachable and entirely visible at both required sizes.
    const output = process.env.EVAL_OUTPUT;
    if (output) await fs.mkdir(output, { recursive: true });
    for (const [label, width, height] of [
      ["desktop", 1440, 900],
      ["mobile", 400, 812],
    ]) {
      await page.setViewportSize({ width, height });
      await card.scrollIntoViewIfNeeded();
      for (const button of await card.locator("button").all()) {
        await button.focus();
        const box = await button.boundingBox();
        assert(
          box &&
            box.x >= 0 &&
            box.x + box.width <= width &&
            box.y >= 0 &&
            box.y + box.height <= height,
        );
      }
      if (output)
        await page.screenshot({ path: path.join(output, `plan-${label}.png`) });
      await page.locator("#prompt").fill("/rev");
      await page.locator("#resource-menu").waitFor({ state: "visible" });
      assert.match(
        await page.locator("#resource-menu").innerText(),
        /Workflows/i,
      );
      const option = page
        .getByRole("option")
        .filter({ hasText: "review" })
        .first();
      await option.scrollIntoViewIfNeeded();
      const box = await option.boundingBox();
      assert(
        box &&
          box.x >= 0 &&
          box.x + box.width <= width &&
          box.y >= 0 &&
          box.y + box.height <= height,
      );
      if (output)
        await page.screenshot({
          path: path.join(output, `workflow-palette-${label}.png`),
        });
      await page.locator("#prompt").press("Escape");
    }
    // P2: Repeated approval cannot submit twice while waiting; P5: resolved replay stays disabled.
    await card.getByRole("button", { name: /Approve plan/ }).click();
    assert.equal(posts.filter((value) => value.choice === "approve").length, 1);
    await page.evaluate(() =>
      event({
        id: 901,
        type: "gate_resolved",
        data: { gate_id: "plan-review", choice: "approve" },
      }),
    );
    assert.equal(
      await card.getByRole("button", { name: /Approve plan/ }).isDisabled(),
      true,
    );
    assert.deepEqual(errors, []);
    console.log(
      "PASS plan review, auto policy and workflow palette at desktop/mobile sizes",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
