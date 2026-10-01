const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 1280, height: 860 },
    });
    page.setDefaultTimeout(5000);
    const errors = [],
      writes = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("http://panel.test/**", (route) => {
      const pathname = new URL(route.request().url()).pathname;
      return route.fulfill({
        path: path.join(
          __dirname,
          "..",
          pathname.startsWith("/assets/") ? "tail_ui" : "agent_service",
          pathname === "/" ? "index.html" : pathname,
        ),
      });
    });
    await page.route("**/v1/**", (route) => {
      const url = new URL(route.request().url());
      let data = {};
      if (route.request().method() !== "GET") writes.push(url.pathname);
      if (url.pathname === "/v1/projects")
        data = {
          projects: ["sem-projeto", "alpha", "beta"],
          details: { alpha: { label: "Alpha" }, beta: { label: "Beta" } },
        };
      else if (url.pathname === "/v1/resources")
        data = { items: [], warnings: [] };
      else if (url.pathname === "/v1/models")
        data = {
          models: [
            {
              id: "fixture",
              name: "Fixture",
              backend: "local",
              efforts: ["low"],
              permissions: { upload: true },
            },
          ],
          providers: { local: true },
          uploads_enabled: true,
        };
      else if (url.pathname === "/v1/conversations")
        data = {
          conversations: [
            {
              id: "existing",
              project: "alpha",
              title: "Preserved conversation",
              state: "completed",
            },
          ],
        };
      else if (url.pathname === "/v1/version")
        data = { version: "fixture", build: "project-preferences" };
      return route.fulfill({ json: data });
    });
    const ready = async () => {
      await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.12"));
      await page.goto("http://panel.test");
      await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.locator("#project-tree > summary").click();
    };
    const action = async (name, label) => {
      await page
        .getByRole("button", {
          name: "Actions for project " + name,
          exact: true,
        })
        .click();
      await page.getByRole("button", { name: label, exact: true }).click();
    };
    const restore = async (name) => {
      assert.equal(
        await page
          .locator("#removed-projects > summary use")
          .getAttribute("href"),
        "/assets/icons.svg#archive",
      );
      if (!(await page.locator("#removed-projects").evaluate((el) => el.open)))
        await page.locator("#removed-projects > summary").click();
      await page
        .getByRole("button", { name: "Restore " + name, exact: true })
        .click();
    };
    const order = () =>
      page
        .locator(".project-group")
        .evaluateAll((nodes) => nodes.map((node) => node.dataset.projectId));
    await ready();
    // P1: visible removal wording and recovery without CLI or project recreation.
    await action("Alpha", "Remove from list");
    assert.equal(await page.locator('[data-project-id="alpha"]').count(), 0);
    await restore("Alpha");
    assert.equal(await page.locator('[data-project-id="alpha"]').count(), 1);
    console.log("PASS P1: remove and restore using visible controls");
    // P2: changing organization during work preserves the active project, draft and attachments.
    await page.evaluate(() => {
      document.getElementById("project").value = "alpha";
      files = [{ id: "attachment", name: "notes.txt" }];
      conversation = "existing";
      renderProjects();
    });
    await page.locator("#prompt").fill("Important draft");
    await action("Alpha", "Remove from list");
    assert.equal(await page.locator("#project").inputValue(), "alpha");
    assert.equal(await page.locator("#prompt").inputValue(), "Important draft");
    assert.deepEqual(await page.evaluate(() => ({ files, conversation })), {
      files: [{ id: "attachment", name: "notes.txt" }],
      conversation: "existing",
    });
    await restore("Alpha");
    await action("Beta", "Add to favorites");
    await action("Beta", "Remove from favorites");
    assert.deepEqual(await order(), ["alpha", "beta"]);
    console.log(
      "PASS P2: draft, attachments, conversation and rapid favorite toggle",
    );
    // P3: preferences persist and server history refresh does not resurrect hidden projects.
    await page.evaluate(() => {
      files = [];
      conversation = "";
      saveView();
    });
    await action("Beta", "Add to favorites");
    await action("Alpha", "Remove from list");
    await ready();
    assert.deepEqual(await order(), ["beta"]);
    assert.equal(
      await page
        .getByRole("button", { name: "Beta · Favorite", exact: true })
        .count(),
      1,
    );
    await page.locator("#removed-projects > summary").click();
    await page.evaluate(async () => {
      await probeReadiness();
      await history();
    });
    assert.deepEqual(await order(), ["beta"]);
    assert(
      await page.locator("#removed-projects").evaluate((el) => el.open),
      "History refresh must preserve the restoration list being browsed",
    );
    await restore("Alpha");
    if (
      !(await page
        .locator('[data-project-id="alpha"]')
        .evaluate((el) => el.open))
    )
      await page
        .locator('[data-project-id="alpha"] > summary > button')
        .click();
    await page
      .locator(".conversation-title").filter({ hasText: /^Preserved conversation$/ }).first()
      .waitFor();
    console.log("PASS P3: reload, polling and restored conversation history");
    // P4: keyboard activation and focus after replacing menu DOM; Escape remains supported.
    const trigger = page.getByRole("button", {
      name: "Actions for project Beta",
      exact: true,
    });
    await trigger.focus();
    await page.keyboard.press("Enter");
    await page
      .getByRole("button", { name: "Remove from favorites", exact: true })
      .focus();
    await page.keyboard.press("Enter");
    assert(await trigger.evaluate((el) => el === document.activeElement));
    await page.keyboard.press("Enter");
    await page.keyboard.press("Escape");
    await page.waitForFunction((el) => el.getAttribute("aria-expanded") === "false", await trigger.elementHandle());
    assert.equal(await trigger.getAttribute("aria-expanded"), "false");
    console.log(
      "PASS P4: keyboard, accessible favorite label, focus and Escape",
    );
    // P5: mobile menu stays within viewport; local preferences work without API connectivity.
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator("#menu").click();
    await trigger.click();
    const box = await page
      .locator(".project-actions-menu:popover-open")
      .boundingBox();
    assert(box.x >= 0 && box.x + box.width <= 390 && box.y + box.height <= 844);
    const offline = route => route.abort();
    await page.route("**/v1/**", offline);
    await page
      .getByRole("button", { name: "Add to favorites", exact: true })
      .click();
    await action("Alpha", "Remove from list");
    await restore("Alpha");
    await page.screenshot({ path: "/tmp/project-list-preferences-mobile.png" });
    await page.unroute("**/v1/**", offline); // Restore the original synthetic API routes.
    console.log("PASS P5: mobile bounds and offline remove/restore/favorite");
    // P6: removal is strictly a UI preference; failed storage writes do not pretend success.
    assert.deepEqual(writes, []);
    assert.equal(await page.locator("#project option").count(), 3);
    await page.evaluate(() => {
      window.originalSetItem = Storage.prototype.setItem;
      Storage.prototype.setItem = function () {
        throw new Error("Storage blocked");
      };
    });
    await action("Alpha", "Remove from list");
    assert.equal(await page.locator('[data-project-id="alpha"]').count(), 1);
    await page
      .getByText("Couldn't save the project list in this browser. Try again.", {
        exact: true,
      })
      .waitFor();
    await page.evaluate(() => {
      Storage.prototype.setItem = window.originalSetItem;
    });
    await action("Alpha", "Remove from list");
    await restore("Alpha");
    console.log(
      "PASS P6: no API writes, catalog preserved, storage failure and retry",
    );
    // P7: favorites lead the list, removal has feedback and the last restoration clears the section.
    assert.deepEqual(await order(), ["beta", "alpha"]);
    await action("Beta", "Remove from list");
    await action("Alpha", "Remove from list");
    assert.deepEqual(await order(), []);
    await page
      .getByText(
        "Project removed from the list. Folders and conversations preserved.",
        { exact: true },
      )
      .waitFor();
    await restore("Beta");
    await restore("Alpha");
    assert.equal(await page.locator("#removed-projects").count(), 0);
    assert.deepEqual(await order(), ["beta", "alpha"]);
    await page.setViewportSize({ width: 1280, height: 860 });
    await page.screenshot({
      path: "/tmp/project-list-preferences-desktop.png",
    });
    assert.deepEqual(errors, []);
    console.log(
      "PASS P7: favorite hierarchy, empty list, feedback and full restoration",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
