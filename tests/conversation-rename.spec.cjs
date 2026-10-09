const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict"),
  fs = require("node:fs/promises"),
  path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    let patches = 0,
      failRename = false;
    let deletions = 0,
      failDelete = false;
    let eventRequests = 0,
      cancelRequests = 0,
      createdProject = null;
    const origin = "http://127.0.0.1:8094";
    const page = await browser.newPage({
        viewport: { width: 1280, height: 900 },
      }),
      errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const conversations = Array.from({ length: 35 }, (_, i) => ({
      id: "c" + i,
      title: i === 34 ? "Philosophical Review" : "Conversation " + i,
      project: i === 34 ? "p" : "sem-projeto",
      state: "completed",
      execution: {
        backend: "local",
        model: i === 34 ? "qwen-local" : "fixture",
      },
    }));
    const turn = {
      id: "c34",
      project: "p",
      state: "completed",
      request: {
        backend: "local",
        model: "qwen-local",
        prompt: "Original text",
      },
      result: { answer: "Recovered answer" },
    };
    const routeFixture = async (route) => {
      const url = new URL(route.request().url()),
        p = url.pathname;
      if (p.startsWith("/v1/")) {
        if (route.request().method() === "PATCH") {
          patches++;
          if (failRename)
            return route.fulfill({
              status: 500,
              json: { detail: "Test failure" },
            });
          conversations.find((c) => p.endsWith("/" + c.id)).title = route
            .request()
            .postDataJSON().title;
          return route.fulfill({ json: {} });
        }
        if (route.request().method() === "DELETE") {
          deletions++;
          if (failDelete)
            return route.fulfill({
              status: 500,
              json: { detail: "Test failure" },
            });
          const i = conversations.findIndex((c) => p.endsWith("/" + c.id));
          conversations.splice(i, 1);
          return route.fulfill({ json: {} });
        }
        if (p.endsWith("/cancel")) cancelRequests++;
        if (p.endsWith("/events")) {
          eventRequests++;
          const id = p.split("/")[3],
            lastEvent = route.request().headers()["last-event-id"];
          const body =
            id === "c34" && turn.state === "running" && lastEvent === "0"
              ? "id: 1\ndata: " +
                JSON.stringify({
                  id: 1,
                  type: "answer_delta",
                  data: { text: "Answer unique to c34" },
                }) +
                "\n\n"
              : "";
          if (body) await new Promise((resolve) => setTimeout(resolve, 250));
          try {
            return await route.fulfill({
              body,
              contentType: "text/event-stream",
            });
          } catch {
            return;
          }
        }
        let data = {};
        if (p === "/v1/projects" && route.request().method() === "POST") {
          createdProject = route.request().postDataJSON();
          return route.fulfill({ json: { project_id: "new" } });
        }
        if (p === "/v1/projects")
          data = createdProject
            ? {
                projects: ["sem-projeto", "p", "new"],
                details: {
                  p: { label: "Philosophy" },
                  new: { label: createdProject.name },
                },
              }
            : {
                projects: ["sem-projeto", "p"],
                details: { p: { label: "Philosophy" } },
              };
        if (p === "/v1/project-directories") {
          const urlPath = url.searchParams.get("path") || "",
            q = url.searchParams.get("q") || "",
            entries = urlPath
              ? []
              : [
                  {
                    name: "Work A",
                    path: "Work A",
                    absolute_path: "/home/test-user/Work A",
                    type: "directory",
                  },
                  {
                    name: "Work B",
                    path: "Work B",
                    absolute_path: "/home/test-user/Work B",
                    type: "directory",
                  },
                ];
          data = {
            roots: [{ id: "home", label: "Local folders" }],
            root_id: "home",
            path: urlPath,
            absolute_path: urlPath
              ? "/home/test-user/" + urlPath
              : "/home/test-user",
            entries: entries.filter((e) =>
              e.name.toLowerCase().includes(q.toLowerCase()),
            ),
            limited: false,
          };
        }
        if (p === "/v1/models")
          data = {
            models: [
              { id: "qwen-local", backend: "local", efforts: ["configured"] },
            ],
            admin_url: "http://localhost:8094/admin/",
          };
        if (p === "/v1/conversations") data = { conversations };
        if (p === "/v1/conversations/c34")
          data = {
            title: "Philosophical Review",
            turns: [
              {
                ...turn,
                id: "older",
                state: "completed",
                result: { answer: "Old complete answer" },
              },
              turn,
            ],
          };
        if (p === "/v1/conversations/c0")
          data = {
            title: "Conversation 0",
            turns: [
              {
                id: "c0",
                project: "sem-projeto",
                state: "completed",
                request: {
                  backend: "local",
                  model: "qwen-local",
                  prompt: "Independent question",
                },
                result: { answer: "Answer for conversation 0" },
              },
            ],
          };
        if (p === "/v1/jobs/c34") data = turn;
        if (p === "/v1/version")
          data = { version: "test", build: "search-test" };
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
    };
    await page.route(origin + "/**", routeFixture);
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.addInitScript(() =>
      localStorage.setItem("keepharness-tour-seen", "0.16.0"),
    );
    await page.goto(origin);
    await page.locator("#startup-gate").waitFor({ state: "hidden" });

    page.on("dialog", () => {
      throw Error("Unexpected browser dialog");
    });
    const modal = page.locator("#rename-conversation-dialog"),
      input = page.locator("#rename-conversation-name"),
      save = page.locator("#rename-conversation-save");
    async function open() {
      await page
        .locator("#history .conversation-actions summary")
        .first()
        .click();
      await page
        .getByRole("button", { name: "Rename conversation", exact: true })
        .first()
        .click();
      await modal.waitFor({ state: "visible" });
    }
    await open();
    assert.equal(await input.inputValue(), "Conversation 0");
    assert(
      await input.evaluate(
        (e) =>
          e === document.activeElement &&
          e.selectionStart === 0 &&
          e.selectionEnd === e.value.length,
      ),
    );
    await page.click("#rename-conversation-cancel");
    assert.equal(patches, 0);
    await open();
    await page.keyboard.press("Escape");
    assert.equal(await modal.isVisible(), false);
    await open();
    for (const value of ["", "   ", "a".repeat(101), "🐋".repeat(101)]) {
      await input.fill(value);
      assert(await save.isDisabled());
      await input.press("Enter");
      assert.equal(patches, 0);
    }
    await input.fill("🐋".repeat(100));
    assert(await save.isEnabled());
    await input.press("Enter");
    await modal.waitFor({ state: "hidden" });
    assert.equal(conversations[0].title, "🐋".repeat(100));
    await open();
    await input.fill("  Résumé <test> & 🐋  ");
    failRename = true;
    await save.click();
    await page
      .locator("#rename-conversation-error")
      .filter({ hasText: "Couldn't rename" })
      .waitFor();
    assert(await modal.isVisible());
    assert.equal(await input.inputValue(), "  Résumé <test> & 🐋  ");
    await page.setViewportSize({ width: 390, height: 844 });
    assert(
      await modal.evaluate((e) => {
        const r = e.getBoundingClientRect();
        return r.left >= 0 && r.right <= innerWidth && r.bottom <= innerHeight;
      }),
    );
    await page.screenshot({ path: "/tmp/keepharness-rename-mobile.png" });
    failRename = false;
    await save.click();
    await modal.waitFor({ state: "hidden" });
    assert.equal(conversations[0].title, "Résumé <test> & 🐋");
    // UX-R1-8: with no hover (touch) the row menu stays visible and opens by tap.
    const touch = await browser.newContext({
      hasTouch: true,
      isMobile: true,
      viewport: { width: 1024, height: 800 },
    });
    try {
      await touch.route(origin + "/**", routeFixture);
      await touch.addInitScript(() =>
        localStorage.setItem("keepharness-tour-seen", "0.16.0"),
      );
      const touchPage = await touch.newPage();
      await touchPage.goto(origin);
      await touchPage.locator("#startup-gate").waitFor({ state: "hidden" });
      const touchRow = touchPage.locator("#sidebar .conversation-row").first();
      await touchRow.waitFor();
      assert(
        (await touchRow
          .locator(".conversation-actions")
          .evaluate((el) => +getComputedStyle(el).opacity)) >= 0.5,
        "row menu is visible without hover",
      );
      await touchRow.locator(".conversation-actions > summary").tap();
      assert(
        await touchRow.locator(".conversation-actions-menu").isVisible(),
        "tapping the row menu opens it",
      );
    } finally {
      await touch.close();
    }
    assert.deepEqual(errors, []);
    assert(
      !/\b(?:window\.)?prompt\s*\(/.test(
        await fs.readFile(
          path.join(__dirname, "../agent_service/ui.js"),
          "utf8",
        ),
      ),
    );
    console.log(
      "PASS: rename modal, focus, cancel, Escape, empty/long names, Unicode, mobile, API failure, trimmed save and no native prompt",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
