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
      deletes = [];
    let fail = false,
      missingRoute = false,
      release,
      deleted = false;
    const folder = "/home/user/Projects/My project with a long name/main files";
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
    await page.route("**/v1/**", async (route) => {
      const url = new URL(route.request().url());
      let data = {};
      if (url.pathname === "/v1/projects")
        data = {
          projects: deleted ? ["sem-projeto"] : ["sem-projeto", "alpha"],
          details: { alpha: { label: "Alpha", root: folder } },
        };
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
        data = { conversations: [] };
      else if (url.pathname === "/v1/version")
        data = { version: "fixture", build: "delete-folder" };
      else if (url.pathname === "/v1/project-folder") {
        assert.equal(url.searchParams.get("project_id"), "alpha");
        if (missingRoute)
          return route.fulfill({ status: 404, body: "Not Found" });
        if (route.request().method() === "DELETE") {
          deletes.push(route.request().postDataJSON());
          if (fail)
            return route.fulfill({
              status: 409,
              json: {
                code: "project_folder_changed",
                message: "project_folder_changed",
              },
            });
          await new Promise((resolve) => {
            release = resolve;
          });
          deleted = true;
          data = { deleted_paths: [folder], deleted_projects: ["alpha"] };
        } else data = { paths: [folder], revision: "fixture-revision" };
      }
      return route.fulfill({ json: data });
    });
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.1"));
    await page.goto("http://panel.test");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.locator("#project-tree > summary").click();
    const dialog = page.locator("#delete-project-folder-dialog"),
      check = page.locator("#delete-project-folder-check"),
      confirm = page.locator("#delete-project-folder-confirm");
    const open = async () => {
      await page
        .getByRole("button", { name: "Actions for project Alpha", exact: true })
        .click();
      await page
        .getByRole("button", { name: "Delete folder", exact: true })
        .click();
      await page.waitForFunction(
        () => !document.getElementById("delete-project-folder-check").disabled,
      );
    };
    missingRoute = true;
    await page
      .getByRole("button", { name: "Actions for project Alpha", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Delete folder", exact: true })
      .click();
    await page
      .getByText(
        "The running service does not offer the folder deletion lookup. Restart the service from the admin panel and try again. This does not mean the project has no folder.",
        { exact: true },
      )
      .waitFor();
    assert(await check.isDisabled());
    assert(await confirm.isDisabled());
    await page.locator("#delete-project-folder-cancel").click();
    missingRoute = false;
    await open();
    assert.equal(
      await page.locator("#delete-project-folder-paths").innerText(),
      folder,
    );
    assert(await confirm.isDisabled());
    await confirm.evaluate((button) => button.click());
    assert.equal(deletes.length, 0);
    await check.check();
    assert.equal(await confirm.isDisabled(), false);
    assert.equal(deletes.length, 0);
    await check.uncheck();
    assert(await confirm.isDisabled());
    await page.keyboard.press("Escape");
    assert.equal(await dialog.isVisible(), false);
    assert.equal(deletes.length, 0);
    await open();
    assert.equal(await check.isChecked(), false);
    await check.check();
    await page.locator("#delete-project-folder-cancel").click();
    assert.equal(deletes.length, 0);
    await open();
    await check.check();
    fail = true;
    await confirm.click();
    await page
      .getByText(
        "The folder changed since the confirmation. Close and reopen this window.",
        { exact: true },
      )
      .waitFor();
    assert(await confirm.isDisabled());
    assert.equal(await check.isChecked(), false);
    await page.locator("#delete-project-folder-cancel").click();
    await page.locator("#panel-toggle").click();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator("#menu").click();
    await open();
    const bounds = await dialog.boundingBox();
    assert(bounds.x >= 0 && bounds.x + bounds.width <= 390);
    assert(await dialog.evaluate((el) => el.scrollWidth <= el.clientWidth));
    await page.screenshot({ path: "/tmp/project-folder-delete-mobile.png" });
    await check.check();
    fail = false;
    await confirm.click();
    await page.waitForFunction(
      () =>
        document.getElementById("delete-project-folder-status").textContent ===
        "Deleting folder…",
    );
    assert(await confirm.isDisabled());
    assert(await check.isDisabled());
    assert(await page.locator("#delete-project-folder-cancel").isDisabled());
    await page.keyboard.press("Escape");
    assert(await dialog.isVisible());
    while (!release) await new Promise((resolve) => setTimeout(resolve, 10));
    release();
    await dialog.waitFor({ state: "hidden" });
    await page.waitForFunction(
      () => !document.querySelector('[data-project-id="alpha"]'),
    );
    assert.equal(await page.locator("#removed-projects").count(), 0);
    await page
      .getByRole("dialog", { name: "Folder deleted", exact: true })
      .getByRole("button", { name: "Close", exact: true })
      .click();
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    assert.equal(await page.locator('[data-project-id="alpha"]').count(), 0);
    assert.equal(await page.locator("#removed-projects").count(), 0);
    assert.equal(deletes.length, 2);
    assert.deepEqual(deletes[1], {
      paths: [folder],
      revision: "fixture-revision",
      confirmed: true,
    });
    assert.deepEqual(errors, []);
    console.log(
      "PASS: folder deletion path preview, unchecked/rechecked gating, Escape/cancel, reset, stale confirmation, in-flight controls, mobile and success.",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
