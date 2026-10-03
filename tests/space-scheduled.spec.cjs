// Space › Pages and Scheduled tasks (Sophia, 2026-10-03; Codex desktop Space
// and Scheduled): pages per project usable in chats, and recurring prompts.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    const pages = [],
      schedules = [],
      calls = [],
      uploads = [];
    let revision = 0;
    page.on("pageerror", (e) => console.error("PAGEERROR", e.message));
    page.on("dialog", () => {
      throw Error("Unexpected browser dialog");
    });
    await page.route("http://space.test/**", async (route) => {
      const url = new URL(route.request().url()),
        pathname = url.pathname,
        method = route.request().method();
      if (!pathname.startsWith("/v1/"))
        return route.fulfill({
          path: path.join(__dirname, "..", pathname.startsWith("/assets/") ? "tail_ui" : "agent_service", pathname === "/" ? "index.html" : pathname),
        });
      let body = {};
      try {
        if (["POST", "PUT", "DELETE"].includes(method) && pathname !== "/v1/files") body = route.request().postDataJSON() || {};
      } catch {}
      if (!["/v1/models", "/v1/version", "/v1/projects", "/v1/conversations", "/v1/usage", "/v1/activity"].includes(pathname))
        calls.push([method, pathname, body]);
      let data = {},
        status = 200;
      const now = Date.now() / 1000;
      if (pathname === "/v1/projects") data = { projects: ["sem-projeto", "alpha"], details: { alpha: { label: "Alpha" } } };
      else if (pathname === "/v1/models")
        data = {
          models: [
            { id: "gpt-6-astra", name: "GPT-6 Astra", backend: "codex", efforts: ["low", "medium"], permissions: { upload: true } },
            { id: "claude-sonnet-5-5", name: "Claude Sonnet 5.5", backend: "claude", efforts: ["configured"], permissions: { upload: true } },
          ],
          providers: { codex: true, claude: true },
          uploads_enabled: true,
        };
      else if (pathname === "/v1/conversations") data = { conversations: [] };
      else if (pathname === "/v1/version") data = { version: "fixture", build: "space" };
      else if (pathname === "/v1/files" && method === "POST") {
        uploads.push(url.searchParams.get("project_id"));
        data = { file_id: "f" + uploads.length, name: "page.md" };
      } else if (pathname === "/v1/pages" && method === "GET")
        data = { pages: pages.filter((p) => p.project_id === url.searchParams.get("project_id")).map(({ body: _, ...p }) => p) };
      else if (pathname === "/v1/pages" && method === "POST") {
        const created = { id: "p" + (pages.length + 1), title: body.title, body: body.body, project_id: body.project_id, created_at: now, updated_at: now, revision: "r" + ++revision, size: body.body.length };
        pages.push(created);
        status = 201;
        data = created;
      } else if (pathname.startsWith("/v1/pages/")) {
        const found = pages.find((p) => p.id === pathname.split("/").pop());
        if (method === "GET") data = found;
        else if (method === "PUT") data = Object.assign(found, { title: body.title, body: body.body, updated_at: now, revision: "r" + ++revision });
        else if (method === "DELETE") {
          pages.splice(pages.indexOf(found), 1);
          data = { deleted: true };
        }
      } else if (pathname === "/v1/schedules" && method === "GET") data = { schedules };
      else if (pathname === "/v1/schedules" && method === "POST") {
        if (!["ask", "read_only"].includes(body.access_mode)) {
          status = 422;
          data = { code: "schedule_invalid", field: "access_mode" };
        } else {
          const created = { ...body, id: "s1", revision: "r" + ++revision, next_run: now + 3 * 3600, last_run: null, failures: 0, paused_reason: "" };
          schedules.push(created);
          status = 201;
          data = created;
        }
      } else if (pathname === "/v1/schedules/s1/run") {
        schedules[0].last_run = { job_id: "j9", at: now, state: "submitted" };
        data = { job_id: "j9" };
      } else if (pathname === "/v1/schedules/s1" && method === "PUT") data = Object.assign(schedules[0], body, { revision: "r" + ++revision });
      else if (pathname === "/v1/schedules/s1" && method === "DELETE") {
        schedules.splice(0, 1);
        data = { deleted: true };
      }
      return route.fulfill({ status, json: data });
    });
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.14.0"));
    await page.goto("http://space.test/");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });

    // Space: write a page, preview it, then start a chat with it.
    await page.click("#rail-space");
    const space = page.getByRole("dialog", { name: "Space" });
    await space.getByText("No pages in this project yet.").waitFor();
    await space.getByRole("button", { name: "New page" }).click();
    await space.getByLabel("Page title").fill("Photo brief");
    await space.getByLabel("Page content (Markdown)").fill("# Brief\n\n- Describe colors\n- Read signs");
    await page.keyboard.press("Control+s");
    await space.getByRole("button", { name: /^Photo brief/ }).waitFor();
    assert.deepEqual(calls.find((c) => c[0] === "POST" && c[1] === "/v1/pages")[2], {
      project_id: "sem-projeto",
      title: "Photo brief",
      body: "# Brief\n\n- Describe colors\n- Read signs",
    });
    await space.getByRole("button", { name: "Preview" }).click();
    assert.equal(await space.locator("#page-preview h1").innerText(), "Brief");
    assert.equal(await space.locator("#page-preview li").count(), 2);
    await space.getByRole("button", { name: "Preview" }).click();
    await space.getByLabel("Page content (Markdown)").fill("# Brief\n\nUpdated");
    await space.getByRole("button", { name: "Save" }).click();
    await page.waitForFunction(() => document.getElementById("page-status").textContent === "Saved");
    assert.equal(calls.filter((c) => c[0] === "PUT").at(-1)[2].revision, "r1");
    await space.getByRole("button", { name: "Start chat with this page" }).click();
    await space.waitFor({ state: "hidden" });
    await page.waitForFunction(() => document.querySelectorAll(".attachment").length === 1);
    assert.equal(await page.locator("#conversation-title").innerText(), "Photo brief");

    // Pages belong to a project.
    await page.click("#rail-space");
    await space.getByLabel("Project").selectOption("alpha");
    await space.getByText("No pages in this project yet.").waitFor();
    await space.getByLabel("Project").selectOption("sem-projeto");
    await space.getByRole("button", { name: /^Photo brief/ }).click();
    await space.getByRole("button", { name: "Delete" }).click();
    await space.getByRole("button", { name: "Confirm delete" }).click();
    await space.getByText("No pages in this project yet.").waitFor();
    await space.getByRole("button", { name: "Close Space" }).click();

    // Scheduled: create a weekly task on its own route, run it now, pause it.
    await page.click("#rail-scheduled");
    const scheduled = page.getByRole("dialog", { name: "Scheduled" });
    await scheduled.getByText("Schedule a task").waitFor();
    await scheduled.getByRole("button", { name: "New task" }).click();
    assert.deepEqual(
      await scheduled.getByLabel("Access").locator("option").allInnerTexts(),
      ["Ask for approval", "Read only"],
      "unattended runs never get automatic or full access",
    );
    await scheduled.getByRole("button", { name: "Create task" }).click();
    assert.equal(await page.locator("#schedule-title").getAttribute("aria-invalid"), "true");
    await scheduled.getByLabel("Title").fill("Weekly AI radar");
    await scheduled.getByLabel("Prompt").fill("Summarize this week's AI changes for managers.");
    await scheduled.getByLabel("Project").selectOption("alpha");
    await scheduled.getByLabel("Provider").selectOption("claude");
    await scheduled.getByLabel("Repeat").selectOption("weekly");
    await scheduled.getByLabel("Day").selectOption("0");
    await scheduled.getByLabel("Time").fill("08:30");
    assert.equal(await scheduled.getByLabel("Hours").isVisible(), false);
    await scheduled.getByRole("button", { name: "Create task" }).click();
    const row = scheduled.getByRole("button", { name: /^Weekly AI radar/ });
    await row.waitFor();
    assert.deepEqual(calls.find((c) => c[0] === "POST" && c[1] === "/v1/schedules")[2], {
      title: "Weekly AI radar",
      prompt: "Summarize this week's AI changes for managers.",
      project_id: "alpha",
      backend: "claude",
      model: "claude-sonnet-5-5",
      effort: "configured",
      access_mode: "ask",
      cadence: { kind: "weekly", weekday: 0, time: "08:30" },
      enabled: true,
    });
    assert.match(await row.innerText(), /Active · Mondays at 08:30 · next in 3 h/);
    await scheduled.getByRole("button", { name: "Run now" }).click();
    await scheduled.getByText("Started now. It appears in Chats.").waitFor();
    await scheduled.getByLabel("Active").uncheck();
    await scheduled.getByRole("button", { name: "Save task" }).click();
    await page.waitForFunction(() => /Paused/.test(document.querySelector("#schedules-list")?.innerText || ""));
    assert.equal(calls.filter((c) => c[0] === "PUT" && c[1] === "/v1/schedules/s1").at(-1)[2].enabled, false);
    await scheduled.getByRole("button", { name: "Delete" }).click();
    await scheduled.getByRole("button", { name: "Confirm delete" }).click();
    await scheduled.getByText("No scheduled tasks yet.").waitFor();
    console.log("PASS Space pages and Scheduled tasks");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
