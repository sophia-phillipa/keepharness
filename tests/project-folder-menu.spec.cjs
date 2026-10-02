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
    const errors = [];
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
      if (url.pathname === "/v1/projects")
        data = {
          projects: ["sem-projeto", "alpha", "empty"],
          details: {
            alpha: {
              label: "Alpha",
              root: "/home/user/first",
              additional_roots: ["/home/user/second"],
            },
            empty: { label: "No folder" },
          },
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
        data = { version: "fixture", build: "folder-menu" };
      else if (url.pathname === "/v1/project-files") {
        const navigating = url.searchParams.get("navigate_project") === "1";
        const root = navigating
            ? "system"
            : url.searchParams.get("root_id") || "home",
          dir = navigating
            ? "home/user/first"
            : url.searchParams.get("path") || "";
        data = {
          state: "ready",
          roots: [{ id: "home", label: "Personal folder" }],
          root_id: root,
          path: dir,
          entries:
            root === "system"
              ? [
                  { path: dir + "/file.txt", name: "file.txt", type: "file" },
                  { path: dir + "/src", name: "src", type: "directory" },
                ]
              : [{ path: "home.txt", name: "home.txt", type: "file" }],
        };
      }
      return route.fulfill({ json: data });
    });
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.15"));
    await page.goto("http://panel.test");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.locator("#project-tree > summary").click();
    await page.locator("#prompt").fill("My draft");
    const currentProject = await page.locator("#project").inputValue();
    const trigger = page.getByRole("button", {
      name: "Actions for project Alpha",
      exact: true,
    });
    await trigger.click();
    const icons = page.locator(
      ".project-actions-menu:popover-open button > svg",
    );
    assert.equal(await icons.count(), 6);
    await page.waitForFunction(() =>
      [
        ...document.querySelectorAll(
          ".project-actions-menu:popover-open button > svg",
        ),
      ].every((svg) => svg.getBBox().width > 0 && svg.getBBox().height > 0),
    );
    await page.screenshot({ path: "/tmp/project-menu-icons.png" });
    await page
      .getByRole("button", { name: "Go to project folder", exact: true })
      .click();
    await page
      .locator("#files-tree")
      .getByText("file.txt", { exact: true })
      .waitFor();
    assert.equal(
      await page.locator("#project-folder-location").innerText(),
      "/home/user/first",
    );
    assert.equal(await page.locator("#project").inputValue(), currentProject);
    assert.equal(await page.locator("#prompt").inputValue(), "My draft");
    assert.equal(await page.locator("#activity-panel").isVisible(), true);
    await page.getByRole("button", { name: "Expand src", exact: true }).click();
    await page.waitForFunction(() =>
      fileTree.cache.has("system\0home/user/first/src"),
    );
    await page.locator('.file-root[data-root-id="home"]').click();
    await page
      .locator("#files-tree")
      .getByText("home.txt", { exact: true })
      .waitFor();
    assert.equal(await page.locator("#project-folder-location").count(), 0);
    await trigger.click();
    await page.keyboard.press("Escape");
    await page.locator(".project-actions-menu:popover-open").waitFor({ state: "hidden" });
    // Native popover closure queues the toggle event that updates the ARIA state.
    await page.waitForFunction(
      (button) => button.getAttribute("aria-expanded") === "false",
      await trigger.elementHandle(),
    );
    assert.equal(await trigger.getAttribute("aria-expanded"), "false");
    await page
      .getByRole("button", {
        name: "Actions for project No folder",
        exact: true,
      })
      .click();
    await page
      .getByRole("button", { name: "Go to project folder", exact: true })
      .click();
    await page.getByText(/This project has no associated folder/).waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator("#menu").click();
    await trigger.click();
    const menu = page.locator(".project-actions-menu:popover-open");
    const rect = await menu.boundingBox();
    assert(rect.x >= 0 && rect.x + rect.width <= 390);
    await page
      .getByRole("button", { name: "Go to project folder", exact: true })
      .click();
    await page.locator("#project-folder-location").waitFor();
    assert.deepEqual(errors, []);
    await page.screenshot({ path: "/tmp/project-folder-menu.png" });
    console.log(
      "PASS: project folder menu, primary root, nested files, draft preservation, root reset, missing folder, Escape and mobile.",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
