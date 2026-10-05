const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict"),
  fs = require("node:fs/promises"),
  path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    let eventRequests = 0,
      cancelRequests = 0,
      createdProject = null;
    const origin = "http://harness.test:8093";
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
                  data: { text: "Exclusive answer for c34" },
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

    assert.equal(
      await page.locator(".conversation-indicator").count(),
      0,
      "old history starts read",
    );
    turn.state = "running";
    turn.result = {};
    conversations.at(-1).state = "running";
    await page.evaluate(() => history());
    assert.equal(
      await page.locator("#projects .conversation-indicator.working").count(),
      1,
    );
    turn.state = "completed";
    turn.result = { answer: "New completed answer" };
    conversations.at(-1).state = "completed";
    await page.evaluate(() => history());
    assert.equal(
      await page.locator("#projects .conversation-indicator.unread").count(),
      1,
    );
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    assert.equal(
      await page.locator("#projects .conversation-indicator.unread").count(),
      1,
      "unread survives reload",
    );
    if (!(await page.locator("#project-tree").evaluate((el) => el.open)))
      await page.locator("#project-tree > summary").click();
    // Project folders start expanded (Codex model); open it only if it is collapsed.
    if (!(await page.locator(".project-group").evaluate((el) => el.open)))
      await page.locator(".project-group > summary").click();
    await page.screenshot({ path: "/tmp/keepharness-conversation-indicators.png" });
    await page.locator("#projects .conversation-row > button").click();
    await page.waitForFunction(() =>
      Array.from(document.querySelectorAll(".assistant .text")).some((e) =>
        e.textContent.includes("New completed answer"),
      ),
    );
    assert.equal(
      await page.locator("#projects .conversation-indicator").count(),
      0,
      "opening answer clears dot",
    );
    await page.evaluate(() => history());
    assert.equal(
      await page.locator("#projects .conversation-indicator").count(),
      0,
      "poll does not restore dot",
    );
    conversations.at(-1).last_job_id = "next-completed";
    await page.evaluate(() => history());
    assert.equal(
      await page.locator("#projects .conversation-indicator.unread").count(),
      1,
      "new job detected even between polls",
    );
    conversations.at(-1).state = "queued";
    await page.evaluate(() => history());
    assert.equal(
      await page.locator("#projects .conversation-indicator.working").count(),
      1,
    );
    conversations.at(-1).state = "failed";
    await page.evaluate(() => history());
    assert.equal(
      await page.locator("#projects .conversation-indicator.working").count(),
      0,
    );
    // C-08: the live-activity feed shows a chat as running between two list refreshes; when the list then
    // reports it completed (same job token), the row must end as "Unread response", not as a silent row.
    const rowOf0 = () => page.locator("#history .conversation-row", { hasText: /Conversation 0(?!\d)/ });
    await page.evaluate(() => window.applyActivitySnapshot({ jobs: [{ conversation_id: "c0", state: "running" }] }));
    assert.equal(await rowOf0().locator(".conversation-indicator.working").count(), 1, "activity feed shows it running");
    await page.evaluate(() => history());
    assert.equal(
      await rowOf0().locator(".conversation-indicator.unread").count(),
      1,
      "background completion shows the unread dot",
    );
    await rowOf0().locator("> button").click();
    await page.waitForFunction(() => document.querySelector("#messages")?.innerText.includes("Answer from conversation 0"));
    assert.equal(await rowOf0().locator(".conversation-indicator").count(), 0, "opening the answer clears the dot");
    assert.deepEqual(errors, []);
    console.log(
      "PASS: working, unread, reload, acknowledge, new completion and terminal states",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
