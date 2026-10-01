const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict"),
  fs = require("node:fs/promises"),
  path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
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
    await page.route(origin + "/**", async (route) => {
      const url = new URL(route.request().url()),
        p = url.pathname;
      if (p.startsWith("/v1/")) {
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
                  data: { text: "Answer exclusive to c34" },
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
                result: { answer: "Answer from conversation 0" },
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
            file.startsWith("assets/") ? "../tail_ui" : "../agent_service",
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
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.2"));
    await page.goto(origin);
    await page.locator("#startup-gate").waitFor({ state: "hidden" });

    page.on("dialog", () => {
      throw Error("Unexpected browser dialog");
    });
    const modal = page.locator("#delete-conversation-dialog");
    async function open() {
      await page
        .locator("#history .conversation-actions summary")
        .first()
        .click();
      await page
        .locator("#history .conversation-actions[open] button")
        .filter({ hasText: "Delete conversation" })
        .click();
      await modal.waitFor({ state: "visible" });
    }
    await open();
    assert.equal(
      await page.locator("#delete-conversation-name").innerText(),
      "Conversation 0",
    );
    assert(
      await page
        .locator("#delete-conversation-cancel")
        .evaluate((e) => e === document.activeElement),
    );
    await page.click("#delete-conversation-cancel");
    assert.equal(deletions, 0);
    await open();
    await page.keyboard.press("Escape");
    assert.equal(await modal.isVisible(), false);
    assert.equal(deletions, 0);
    await open();
    for (const theme of [
      "violet-bordeaux",
      "porcelain",
      "mineral-rose",
      "amethyst",
      "petroleum",
      "arizona",
    ]) {
      await page.evaluate((t) => TailTheme.apply(t, false), theme);
      const mismatches = await modal.evaluate((dialog) => {
        const probe = document.createElement("span");
        dialog.append(probe);
        const checks = [
          [dialog, "backgroundColor", "--th-panel"],
          [dialog, "color", "--th-text"],
          [dialog, "borderTopColor", "--th-border"],
          [
            document.querySelector("#delete-conversation-description"),
            "color",
            "--th-muted",
          ],
          [
            document.querySelector("#delete-conversation-confirm"),
            "backgroundColor",
            "--th-action",
          ],
          [
            document.querySelector("#delete-conversation-confirm"),
            "color",
            "--th-on-action",
          ],
        ];
        const errors = checks
          .filter(([element, property, token]) => {
            probe.style.color = "var(" + token + ")";
            return (
              getComputedStyle(element)[property] !==
              getComputedStyle(probe).color
            );
          })
          .map(([, property, token]) => property + ": " + token);
        probe.remove();
        return errors;
      });
      assert.deepEqual(
        mismatches,
        [],
        theme + " must use the existing theme tokens",
      );
      await page.screenshot({ path: "/tmp/tail-delete-" + theme + ".png" });
    }

    await page.setViewportSize({ width: 390, height: 844 });
    assert(
      await modal.evaluate((e) => {
        const r = e.getBoundingClientRect();
        return r.left >= 0 && r.right <= innerWidth && r.bottom <= innerHeight;
      }),
    );
    await page.screenshot({ path: "/tmp/tail-delete-mobile.png" });
    failDelete = true;
    await page.click("#delete-conversation-confirm");
    await page
      .locator("#delete-conversation-error")
      .filter({ hasText: "Couldn't delete" })
      .waitFor();
    assert(await modal.isVisible());
    assert.equal(conversations.length, 35);
    failDelete = false;
    await page.click("#delete-conversation-confirm");
    await modal.waitFor({ state: "hidden" });
    assert.equal(deletions, 2);
    assert.equal(conversations.length, 34);
    assert.deepEqual(errors, []);
    console.log(
      "PASS: themed deletion modal, cancel, Escape, mobile, API failure and successful deletion without browser prompts",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
