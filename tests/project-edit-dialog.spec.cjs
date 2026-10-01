const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict"),
  path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
        viewport: { width: 1280, height: 900 },
      }),
      errors = [];
    page.setDefaultTimeout(5000);
    page.on("pageerror", (e) => errors.push(e.message));
    let detail = {
        label: "Alpha",
        root: "/home/user/first",
        additional_roots: ["/home/user/second"],
      },
      writes = [],
      failure = null,
      hold = null;
    await page.route("http://panel.test/**", (route) => {
      const p = new URL(route.request().url()).pathname;
      return route.fulfill({
        path: path.join(
          __dirname,
          "..",
          p.startsWith("/assets/") ? "tail_ui" : "agent_service",
          p === "/" ? "index.html" : p,
        ),
      });
    });
    await page.route("**/v1/**", async (route) => {
      const u = new URL(route.request().url());
      let data = {};
      if (u.pathname === "/v1/projects") {
        if (route.request().method() === "PATCH") {
          const body = route.request().postDataJSON();
          writes.push(body);
          if (hold) await hold;
          if (failure)
            return route.fulfill({ status: 409, json: { code: failure } });
          detail = {
            label: body.name,
            root: body.paths[0],
            additional_roots: body.paths.slice(1),
          };
          return route.fulfill({ json: { project_id: "alpha" } });
        }
        data = {
          projects: ["sem-projeto", "alpha"],
          details: { alpha: detail },
        };
      } else if (u.pathname === "/v1/models")
        data = {
          models: [
            {
              id: "fixture",
              backend: "local",
              efforts: ["low"],
              permissions: { upload: true },
            },
          ],
        };
      else if (u.pathname === "/v1/conversations") data = { conversations: [] };
      else if (u.pathname === "/v1/version")
        data = { version: "test", build: "edit" };
      else if (u.pathname === "/v1/project-directories")
        data = {
          roots: [{ id: "home", label: "Personal folder" }],
          root_id: "home",
          absolute_path: "/home/user",
          entries: [
            {
              name: "third",
              path: "third",
              absolute_path: "/home/user/third",
              type: "directory",
            },
          ],
        };
      return route.fulfill({ json: data });
    });
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.10"));
    await page.goto("http://panel.test");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.locator("#project-tree > summary").click();
    await page.evaluate(() => {
      $("project").value = "alpha";
      conversation = "fixture-conversation";
      $("messages").textContent = "Conversation preserved";
    });
    await page.locator("#prompt").fill("Preserved draft");
    const open = async () => {
      await page
        .getByRole("button", {
          name: "Actions for project " + detail.label,
          exact: true,
        })
        .click();
      await page
        .getByRole("button", { name: "Edit project", exact: true })
        .click();
    };
    // P1: visible controls, primary choice and correction by cancel.
    await open();
    assert.equal(await page.locator("#project-name").inputValue(), "Alpha");
    await page
      .getByRole("button", { name: "Make main: second", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Main folder: second", exact: true })
      .waitFor();
    await page.locator("#project-dialog-cancel").click();
    assert.equal(writes.length, 0);
    // P4: focus and Escape; explicit primary state.
    await open();
    assert(
      await page
        .locator("#project-name")
        .evaluate((e) => e === document.activeElement),
    );
    assert.equal(
      await page
        .getByRole("button", { name: "Main folder: first", exact: true })
        .getAttribute("aria-pressed"),
      "true",
    );
    await page.keyboard.press("Escape");
    assert.equal(await page.locator("#project-dialog").isVisible(), false);
    // P3: add a folder, rename and retain the active conversation and draft.
    await open();
    await page.locator("#project-name").fill("Renamed");
    await page
      .locator("#project-directory-list .project-file-row")
      .filter({ hasText: "third" })
      .click();
    await page.locator("#project-directory-add-current").click();
    await page
      .getByRole("button", { name: "Make main: second", exact: true })
      .click();
    // P2: double submission and cancel during pending save.
    let release;
    hold = new Promise((resolve) => (release = resolve));
    await page.locator("#project-create").click();
    await page.waitForFunction(() => projectDirectory.saving);
    await page.evaluate(() => {
      $("project-form").dispatchEvent(
        new Event("submit", { cancelable: true }),
      );
    });
    await page.keyboard.press("Escape");
    assert.equal(await page.locator("#project-dialog").isVisible(), true);
    assert.equal(
      await page.locator("#project-dialog-cancel").isDisabled(),
      true,
    );
    release();
    hold = null;
    await page.locator("#project-dialog").waitFor({ state: "hidden" });
    assert.equal(writes.length, 1);
    assert.deepEqual(writes[0], {
      name: "Renamed",
      project_id: "alpha",
      paths: ["/home/user/second", "/home/user/first", "/home/user/third"],
    });
    assert.equal(await page.locator("#prompt").inputValue(), "Preserved draft");
    assert.equal(
      await page.evaluate(() => conversation),
      "fixture-conversation",
    );
    assert.equal(
      await page.locator("#messages").innerText(),
      "Conversation preserved",
    );
    // P6: server rejects busy project, preserves edits, then succeeds.
    await open();
    failure = "project_busy";
    await page.locator("#project-name").fill("Final name");
    await page.locator("#project-create").click();
    await page.getByText(/Wait for this project's tasks to finish/).waitFor();
    assert.equal(
      await page.locator("#project-name").inputValue(),
      "Final name",
    );
    failure = null;
    await page.locator("#project-create").click();
    await page.locator("#project-dialog").waitFor({ state: "hidden" });
    // P5: mobile layout, failure recovery and retained folder selection.
    if (await page.locator("#activity-panel").isVisible())
      await page.locator("#panel-toggle").click();
    await page.setViewportSize({ width: 390, height: 844 });
    if (
      !(await page
        .locator("#sidebar")
        .evaluate((e) => e.classList.contains("open")))
    )
      await page.locator("#menu").click();
    await open();
    await page.screenshot({ path: "/tmp/project-edit-mobile-before.png" });
    assert(
      await page.locator("#project-dialog").evaluate((e) => {
        const r = e.getBoundingClientRect();
        return (
          r.left >= 0 && r.right <= innerWidth && e.scrollWidth <= e.clientWidth
        );
      }),
    );
    failure = "project_name_exists";
    await page.locator("#project-create").click();
    await page.getByText(/A project with that name already exists/).waitFor();
    assert.equal(await page.locator("#project-selected-paths li").count(), 3);
    assert(
      await page
        .locator("#project-form button")
        .evaluateAll((nodes) =>
          nodes.every((node) => node.title && node.querySelector("svg")),
        ),
    );
    await page.screenshot({ path: "/tmp/project-edit-mobile.png" });
    failure = null;
    await page.locator("#project-dialog-cancel").click();
    // P7: edit is discoverable with an icon; create remains an independent empty form.
    if (!(await page.locator("#project-tree").evaluate((el) => el.open)))
      await page.locator("#project-tree > summary").click();
    await page.locator("#add-project").click();
    assert.equal(
      await page.locator("#project-dialog-title").innerText(),
      "Create project",
    );
    assert.equal(await page.locator("#project-name").inputValue(), "");
    assert.equal(await page.locator("#project-selected-paths li").count(), 0);
    await page.locator("#project-name").fill("New");
    await page.locator("#project-create").click();
    await page.getByText("Add at least one folder.", { exact: true }).waitFor();
    assert.deepEqual(errors, []);
    console.log(
      "PASS P1-P7: project edit, primary/add, cancel, keyboard, duplicate submit, continuity, errors and mobile.",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
