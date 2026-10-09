const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict"),
  fs = require("node:fs/promises"),
  path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    let createdProject = null;
    const origin = "http://127.0.0.1:8094";
    const page = await browser.newPage({
        viewport: { width: 1280, height: 900 },
      }),
      errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.route(origin + "/**", async (route) => {
      const url = new URL(route.request().url()),
        p = url.pathname;
      if (p.startsWith("/v1/")) {
        let data = {};
        if (p === "/v1/projects" && route.request().method() === "POST") {
          createdProject = route.request().postDataJSON();
          return route.fulfill({ json: { project_id: "new-project" } });
        }
        if (p === "/v1/projects")
          data = {
            projects: createdProject
              ? ["sem-projeto", "new-project"]
              : ["sem-projeto"],
            details: createdProject
              ? { "new-project": { label: createdProject.name } }
              : {},
          };
        if (p === "/v1/project-directories")
          data = {
            roots: [{ id: "home", label: "Local folders" }],
            root_id: "home",
            path: "",
            absolute_path: "/home/test-user",
            entries: url.searchParams.get("path")
              ? [
                  {
                    name: "Subfolder",
                    path: "Work A/Subfolder",
                    absolute_path: "/home/test-user/Work A/Subfolder",
                    type: "directory",
                  },
                  { name: "hidden.txt", path: "hidden.txt", type: "file" },
                ]
              : [
                  "Work A",
                  "Work B",
                  ...Array.from({ length: 12 }, (_, i) => "Folder " + i),
                ].map((name) => ({
                  name,
                  path: name,
                  absolute_path: "/home/test-user/" + name,
                  type: "directory",
                })),
            limited: false,
          };
        if (p === "/v1/models")
          data = {
            models: [
              { id: "qwen-local", backend: "local", efforts: ["configured"] },
            ],
          };
        if (p === "/v1/conversations") data = { conversations: [] };
        if (p === "/v1/version")
          data = { version: "test", build: "project-dialog-test" };
        if (p === "/v1/catalog")
          data = { agents: [], skills: [], warnings: [] };
        return route.fulfill({ json: data });
      }
      const file = p === "/" ? "index.html" : p.slice(1);
      return route.fulfill({
        body: await fs.readFile(
          path.join(
            __dirname,
            file.startsWith("assets/") ? "../harness_ui" : "../agent_service",
            file,
          ),
        ),
        contentType: file.endsWith(".svg")
          ? "image/svg+xml"
          : file.endsWith(".js")
            ? "text/javascript"
            : file.endsWith(".css")
              ? "text/css"
              : "text/html",
      });
    });
    await page.addInitScript(() =>
      localStorage.setItem("keepharness-tour-seen", "0.16.0"),
    );
    await page.goto(origin);
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    if (!(await page.locator("#project-tree").evaluate((el) => el.open)))
      await page.locator("#project-tree > summary").click();
    await page.click("#add-project");
    assert.equal(
      await page.locator("#project-dialog-title").innerText(),
      "Create project",
    );
    assert.equal(
      await page.locator("#project-folder-browser").isVisible(),
      true,
    );
    assert.equal(
      await page.locator("#project-selected-paths").isVisible(),
      false,
    );
    assert(
      await page
        .locator("#project-name")
        .evaluate((e) => e === document.activeElement),
    );
    await page.evaluate(() => HarnessTheme.apply("amethyst", false));
    await page.screenshot({ path: "/tmp/project-create-desktop.png" });
    // D39: a folder is optional; the dialog says what a project without one keeps.
    assert.match(
      await page.locator("#project-folders-empty").innerText(),
      /without a folder/,
    );
    // UX-R4-2: the folder tree shows at least eight rows (32 px each) when it has them.
    const tree = await page
      .locator("#project-directory-list")
      .evaluate((list) => ({
        height: list.clientHeight,
        row: list.querySelector(".project-file-row").getBoundingClientRect()
          .height,
        scroll: list.scrollHeight,
      }));
    assert(tree.scroll > tree.height, "the fixture has more folders than fit");
    assert(
      tree.height >= 8 * tree.row,
      "the folder tree shows 8+ rows: " + JSON.stringify(tree),
    );
    // A chevron is a 24 px target.
    const chevron = await page
      .locator("#project-directory-list .file-chevron")
      .first()
      .boundingBox();
    assert(chevron.width >= 24 && chevron.height >= 24);

    await page
      .locator("#project-directory-list .project-file-row")
      .filter({ hasText: "Work A" })
      .click();
    assert.equal(await page.locator("#project-selected-paths li").count(), 0);
    await page.click("#project-directory-add-current");
    await page
      .locator("#project-directory-list .project-file-row")
      .filter({ hasText: "Work B" })
      .click();
    await page.click("#project-directory-add-current");
    await page
      .getByRole("button", { name: "Expand Work A", exact: true })
      .click();
    await page
      .locator("#project-directory-list")
      .getByText("Subfolder", { exact: true })
      .waitFor();
    assert.equal(
      await page
        .locator("#project-directory-list")
        .getByText("hidden.txt")
        .count(),
      0,
    );
    await page
      .getByRole("button", { name: "Collapse Work A", exact: true })
      .click();
    assert.equal(
      await page
        .locator("#project-directory-list")
        .getByText("Subfolder", { exact: true })
        .count(),
      0,
    );
    assert.equal(
      await page.locator("#project-folder-browser").isVisible(),
      true,
    );
    assert.equal(await page.locator("#project-selected-paths li").count(), 2);
    await page
      .getByRole("button", { name: "Remove folder Work A", exact: true })
      .click();
    assert.equal(await page.locator("#project-selected-paths li").count(), 1);
    await page.click("#project-dialog-cancel");
    assert.equal(await page.locator("#project-dialog").isVisible(), false);
    if (!(await page.locator("#project-tree").evaluate((el) => el.open)))
      await page.locator("#project-tree > summary").click();
    await page.click("#add-project");
    assert.equal(await page.locator("#project-name").inputValue(), "");
    assert.equal(await page.locator("#project-selected-paths li").count(), 0);
    await page.setViewportSize({ width: 390, height: 844 });
    for (const palette of ["amethyst", "porcelain"]) {
      await page.evaluate((p) => HarnessTheme.apply(p, false), palette);
      assert(
        await page.locator("#project-dialog").evaluate((e) => {
          const r = e.getBoundingClientRect();
          return (
            r.left >= 0 &&
            r.right <= innerWidth &&
            e.scrollWidth <= e.clientWidth
          );
        }),
      );
      await page.screenshot({
        path: "/tmp/project-create-mobile-" + palette + ".png",
      });
    }
    await page.fill("#project-name", "New project");
    await page
      .locator("#project-directory-list .project-file-row")
      .filter({ hasText: "Work A" })
      .click();
    assert.equal(await page.locator("#project-selected-paths li").count(), 0);
    await page.click("#project-directory-add-current");
    await page
      .locator("#project-directory-list .project-file-row")
      .filter({ hasText: "Work B" })
      .click();
    await page.click("#project-directory-add-current");
    assert(
      await page
        .locator("#project-dialog")
        .evaluate((e) => e.scrollWidth <= e.clientWidth),
    );
    await page.screenshot({ path: "/tmp/project-create-browser-mobile.png" });
    await page.click("#project-create");
    await page.locator("#project-dialog").waitFor({ state: "hidden" });
    assert.deepEqual(createdProject, {
      name: "New project",
      paths: ["/home/test-user/Work A", "/home/test-user/Work B"],
    });
    // D39: a name alone creates a folder-less project (paths: []).
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.click("#add-project");
    await page.fill("#project-name", "Notes only");
    await page.click("#project-create");
    await page.locator("#project-dialog").waitFor({ state: "hidden" });
    assert.deepEqual(createdProject, { name: "Notes only", paths: [] });
    assert.deepEqual(errors, []);
    console.log(
      "PASS: compact project modal, accessible selection, removal, cancel/reset, validation, create payloads (with and without folders), mobile and themes",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
