const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict"),
  fs = require("node:fs/promises"),
  path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    let deletions = 0,
      failDelete = false;
    const archived = [],
      archiveRequests = [];
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
          for (const list of [conversations, archived]) {
            const i = list.findIndex((c) => p.endsWith("/" + c.id));
            if (i >= 0) list.splice(i, 1);
          }
          return route.fulfill({ json: {} });
        }
        if (route.request().method() === "PATCH") {
          const body = route.request().postDataJSON(),
            id = p.split("/").pop();
          archiveRequests.push([id, body.archived]);
          const [from, to] = body.archived
            ? [conversations, archived]
            : [archived, conversations];
          to.unshift(...from.splice(from.findIndex((c) => c.id === id), 1));
          return route.fulfill({ json: { id, archived: body.archived } });
        }
        if (p === "/v1/storage")
          return route.fulfill({
            json: {
              project_id: url.searchParams.get("project_id"),
              runs: { used: 900, limit: 1000 },
              bytes: { used: 512 * 1024 ** 2, limit: 2 * 1024 ** 3 },
              warning: true,
            },
          });
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
        if (p === "/v1/conversations")
          data = {
            conversations:
              url.searchParams.get("archived") === "true"
                ? archived
                : conversations,
          };
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
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
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
        .filter({ hasText: "Delete permanently" })
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
      await page.evaluate((t) => HarnessTheme.apply(t, false), theme);
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
      await page.screenshot({ path: "/tmp/keepharness-delete-" + theme + ".png" });
    }

    await page.setViewportSize({ width: 390, height: 844 });
    assert(
      await modal.evaluate((e) => {
        const r = e.getBoundingClientRect();
        return r.left >= 0 && r.right <= innerWidth && r.bottom <= innerHeight;
      }),
    );
    await page.screenshot({ path: "/tmp/keepharness-delete-mobile.png" });
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

    // Archive is one reversible click; the Archived chats list restores or deletes.
    await page.setViewportSize({ width: 1280, height: 900 });
    const first = await page.locator("#history .conversation-row .conversation-title").first().innerText();
    await page.locator("#history .conversation-actions summary").first().click();
    const menu = page.locator("#history .conversation-actions[open] button");
    assert.deepEqual(await menu.allInnerTexts(), [
      "Rename conversation",
      "Archive conversation",
      "Delete permanently",
    ]);
    await menu.filter({ hasText: "Archive conversation" }).click();
    const restoredRow = page
      .locator("#history .conversation-row .conversation-title")
      .filter({ hasText: /^Conversation 1$/ });
    await restoredRow.waitFor({ state: "detached" });
    assert.equal(await modal.isVisible(), false);
    assert.deepEqual(archiveRequests, [["c1", true]]);
    assert.equal(archived[0].title, first);
    await page.keyboard.press("Control+,");
    await page.click('[data-settings="archived"]');
    const row = page.locator("#archived-list .archived-chat");
    await row.first().waitFor();
    assert.equal(await row.locator("span").innerText(), "Conversation 1");
    const usage = page.locator("#storage-usage");
    assert.match(await usage.innerText(), /^Storage: 900 of 1,000 runs · 512 MB of 2\.0 GB of files in this project\. Almost full/);
    assert(await usage.evaluate((e) => e.classList.contains("storage-warning")));
    await row.getByRole("button", { name: "Unarchive Conversation 1" }).click();
    await page.locator("#archived-empty").waitFor({ state: "visible" });
    assert.deepEqual(archiveRequests.at(-1), ["c1", false]);
    await restoredRow.waitFor();

    // Delete permanently from the list takes a second, explicit step in the dialog.
    archived.push(...conversations.splice(conversations.findIndex((c) => c.id === "c2"), 1));
    await page.click('[data-settings="appearance"]');
    await page.click('[data-settings="archived"]');
    await row.first().waitFor();
    await row.getByRole("button", { name: "Delete permanently Conversation 2" }).click();
    await modal.waitFor({ state: "visible" });
    assert.equal(await page.locator("#delete-conversation-name").innerText(), "Conversation 2");
    assert.equal(deletions, 2);
    await page.click("#delete-conversation-confirm");
    await modal.waitFor({ state: "hidden" });
    await page.locator("#archived-empty").waitFor({ state: "visible" });
    assert.equal(deletions, 3);
    assert.deepEqual(archived, []);
    assert.deepEqual(errors, []);
    console.log(
      "PASS: themed permanent-delete modal, cancel, Escape, mobile, API failure, deletion, Archive, Archived chats with Unarchive, Storage line and two-step Delete permanently without browser prompts",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
