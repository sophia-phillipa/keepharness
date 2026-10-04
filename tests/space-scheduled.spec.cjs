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
    let revision = 0,
      failPut = false,
      holdPut = null;
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
          path: path.join(__dirname, "..", pathname.startsWith("/assets/") ? "harness_ui" : "agent_service", pathname === "/" ? "index.html" : pathname),
        });
      let body = {};
      try {
        if (["POST", "PUT", "DELETE"].includes(method) && pathname !== "/v1/files") body = route.request().postDataJSON() || {};
      } catch {}
      if (!["/v1/models", "/v1/version", "/v1/projects", "/v1/conversations", "/v1/usage", "/v1/activity"].includes(pathname))
        calls.push([method, pathname, body]);
      let data = {},
        status = 200;
      if (method === "PUT" && pathname.startsWith("/v1/pages/")) {
        if (holdPut) await holdPut;
        if (failPut) return route.fulfill({ status: 500, json: { code: "internal_error" } });
      }
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
      else if (pathname === "/v1/conversations")
        data = {
          conversations: [
            { id: "c-run", title: "Weekly AI radar", project: "sem-projeto", state: "completed", last_job_id: "j9", schedule_id: "s1", schedule_title: "Weekly AI radar", updated_at: Date.now() / 1000 - 60 },
          ],
        };
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
    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.15.0"));
    await page.goto("http://space.test/");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });

    // A conversation started by a scheduled task is marked in Chats.
    const scheduledRow = page.locator("#history .conversation-row", { hasText: "Weekly AI radar" });
    assert.equal(await scheduledRow.locator(".conversation-scheduled").innerText(), "Scheduled");
    assert.match(await scheduledRow.locator("button").first().getAttribute("title"), /Scheduled task: Weekly AI radar/);

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

    // Unsaved text survives ×, Esc and a project switch: Space saves first, to the page's project.
    await space.getByRole("button", { name: "New page" }).click();
    await space.getByLabel("Page title").fill("Draft one");
    await space.getByLabel("Page content (Markdown)").fill("typed before closing");
    await space.getByRole("button", { name: "Close Space" }).click();
    await space.waitFor({ state: "hidden" });
    assert.deepEqual(
      pages.map((p) => [p.title, p.project_id, p.body]),
      [["Draft one", "sem-projeto", "typed before closing"]],
    );
    await page.click("#rail-space");
    await space.getByRole("button", { name: /^Draft one/ }).click();
    await space.getByLabel("Page content (Markdown)").fill("typed before Esc");
    await page.keyboard.press("Escape");
    await space.waitFor({ state: "hidden" });
    assert.equal(pages[0].body, "typed before Esc");
    await page.click("#rail-space");
    await space.getByRole("button", { name: /^Draft one/ }).click();
    await space.getByLabel("Page content (Markdown)").fill("typed before switching");
    await space.getByLabel("Project").selectOption("alpha");
    await space.getByText("No pages in this project yet.").waitFor();
    assert.deepEqual([pages[0].project_id, pages[0].body], ["sem-projeto", "typed before switching"]);
    await space.getByLabel("Project").selectOption("sem-projeto");

    // A failed save keeps Space open, with the text and the page's own project.
    failPut = true;
    await space.getByRole("button", { name: /^Draft one/ }).click();
    await space.getByLabel("Page content (Markdown)").fill("kept after a failed save");
    await space.getByLabel("Project").selectOption("alpha");
    await page.waitForFunction(() => document.getElementById("space-project").value === "sem-projeto");
    await space.getByRole("button", { name: "Close Space" }).click();
    await page.waitForFunction(() => !document.getElementById("page-save").disabled);
    assert.equal(await space.isVisible(), true);
    assert.equal(await space.getByLabel("Page content (Markdown)").inputValue(), "kept after a failed save");
    failPut = false;

    // Text typed while a save is in flight stays unsaved, and the next save sends it.
    let release;
    holdPut = new Promise((resolve) => (release = resolve));
    await space.getByRole("button", { name: "Save" }).click();
    await page.waitForFunction(() => document.getElementById("page-save").disabled);
    await space.getByLabel("Page title").fill("Draft renamed");
    await space.getByLabel("Page content (Markdown)").fill("typed during the save");
    holdPut = null;
    release();
    await page.waitForFunction(() => !document.getElementById("page-save").disabled);
    assert.equal(await page.locator("#page-status").innerText(), "Unsaved changes");
    assert.equal(await space.getByLabel("Page title").inputValue(), "Draft renamed");
    assert.equal(pages[0].body, "kept after a failed save");
    const heldRevision = pages[0].revision;
    await space.getByRole("button", { name: "Save" }).click();
    await page.waitForFunction(() => document.getElementById("page-status").textContent === "Saved");
    const lastPut = calls.filter((c) => c[0] === "PUT" && c[1].startsWith("/v1/pages/")).at(-1)[2];
    assert.deepEqual(
      [lastPut.title, lastPut.body, lastPut.revision],
      ["Draft renamed", "typed during the save", heldRevision],
    );
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
    // D03: no internet unless the task opts in.
    const internet = scheduled.getByLabel("Allow internet");
    assert.equal(await internet.isChecked(), false);
    assert.match(await page.locator("#schedule-internet-help").innerText(), /^Off by default: the run gets no web search/);
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
    // The wall-clock time is the server's, which may differ from the browser's.
    assert.equal(await page.locator("#schedule-time").getAttribute("aria-describedby"), "schedule-time-help");
    assert.equal(await page.locator("#schedule-time-help").innerText(), "Server time");
    assert.equal(await page.locator("#schedule-time-help").isVisible(), true);
    await scheduled.getByLabel("Repeat").selectOption("interval");
    assert.equal(await page.locator("#schedule-time-help").isVisible(), false, "an interval has no wall-clock time");
    await scheduled.getByLabel("Repeat").selectOption("weekly");
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
      allow_internet: false,
      cadence: { kind: "weekly", weekday: 0, time: "08:30" },
      enabled: true,
    });
    assert.match(await row.innerText(), /Active · Mondays at 08:30 · next in 3 h/);
    await scheduled.getByRole("button", { name: "Run now" }).click();
    await scheduled.getByText("Started now. It appears in Chats.").waitFor();
    await scheduled.getByRole("button", { name: "Open run" }).waitFor();
    // D15: Last run shows the run's real outcome, read back by the scheduler.
    schedules[0].last_run = { job_id: "j9", at: Math.floor(Date.now() / 1000), state: "cancelled", error: "approval_expiration_limit", needs_you: true };
    await scheduled.getByRole("button", { name: "Close Scheduled" }).click();
    await page.click("#rail-scheduled");
    await page.waitForFunction(() => /Last run needs you/.test(document.querySelector("#schedules-list")?.innerText || ""));
    await row.click();
    await page.waitForFunction(() => /Last run .* · cancelled · needs you/.test(document.querySelector("#schedule-last")?.innerText || ""));
    assert.match(await row.innerText(), /Last run needs you/);
    assert.equal(await internet.isChecked(), false, "a saved task shows its stored choice");
    await internet.check();
    await scheduled.getByLabel("Active").uncheck();
    await scheduled.getByRole("button", { name: "Save task" }).click();
    await page.waitForFunction(() => /Paused/.test(document.querySelector("#schedules-list")?.innerText || ""));
    const saved = calls.filter((c) => c[0] === "PUT" && c[1] === "/v1/schedules/s1").at(-1)[2];
    assert.equal(saved.enabled, false);
    assert.equal(saved.allow_internet, true);
    await row.click();
    assert.equal(await internet.isChecked(), true, "the opt-in comes back when the task is reopened");
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
