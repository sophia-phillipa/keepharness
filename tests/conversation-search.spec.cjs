const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict"),
  fs = require("node:fs/promises"),
  path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    let eventRequests = 0,
      cancelRequests = 0,
      projectFileRequests = 0,
      createdProject = null;
    const origin = "http://127.0.0.1:8094";
    const page = await browser.newPage({
        viewport: { width: 1280, height: 900 },
      }),
      errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const conversations = Array.from({ length: 35 }, (_, i) => ({
      id: "c" + i,
      title: i === 34 ? "Naïve Philosophy" : "Conversation " + i,
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
        if (p === "/v1/project-files") projectFileRequests++;
        if (p === "/v1/projects" && route.request().method() === "POST") {
          createdProject = route.request().postDataJSON();
          return route.fulfill({ json: { project_id: "new-project" } });
        }
        if (p === "/v1/projects")
          data = createdProject
            ? {
                projects: ["sem-projeto", "p", "new-project"],
                details: {
                  p: { label: "Philosophy" },
                  "new-project": { label: createdProject.name },
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
          data = url.searchParams.has("q")
            ? {
                conversations: [
                  { ...conversations[3], snippet: "…the KESTREL launch slipped to Friday…" },
                  { ...conversations[4] },
                ],
              }
            : { conversations };
        if (p === "/v1/conversations/c34")
          data = {
            title: "Naïve Philosophy",
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
    assert.equal(await page.locator("#sidebar input[type=search]").count(), 0);
    assert.equal(await page.locator("#search-conversations svg").count(), 1);
    assert.equal(
      await page.locator("#sidebar #search-conversations").count(),
      0,
    );
    assert.equal(
      await page.locator("#app-topbar #search-conversations span").innerText(),
      "Search runs, plans, files",
    );
    assert.equal(
      await page.locator("#admin-shortcut").getAttribute("href"),
      "http://localhost:8094/admin/",
      "admin shortcut must use the URL configured by the service",
    );
    await page.click("#settings");
    assert.equal(
      await page.locator("#admin-shortcut").isVisible(),
      true,
      "configured admin shortcut must be available in Settings (D43)",
    );
    await page.click("#settings-close");
    assert.equal(
      await page.locator("#projects .conversation-model-icon use").getAttribute("href"),
      "/assets/icons.svg#stack-2",
    );
    assert.equal(
      await page.locator("#projects .conversation-title").textContent(),
      "Naïve Philosophy",
    );
    assert.match(
      await page
        .locator("#projects .conversation-row > button")
        .getAttribute("title"),
      /Qwen3.6/,
    );
    // Mock 4 uses a labeled primary action; project creation remains in its disclosure.
    assert.match(await page.locator("#new").innerText(), /New conversation/i);
    assert.match(await page.locator("#add-project").textContent(), /Add project/i);
    assert.equal(await page.locator(".project-new").count(), 1);
    const projectGroup = page.locator('#projects .project-group');
    const wasOpen = await projectGroup.evaluate(n => n.open);
    const treeWasOpen = await page.locator('#project-tree').evaluate(n => n.open);
    await page.locator('#project-tree').evaluate(n => n.open = true);
    await projectGroup.evaluate(n => n.open = true);
    assert(await page.locator("#projects .conversation-model-icon svg").evaluate(n => { const r = n.getBoundingClientRect(); return r.width > 0 && r.height > 0; }));
    await projectGroup.evaluate((n, value) => n.open = value, wasOpen);
    await page.locator('#project-tree').evaluate((n, value) => n.open = value, treeWasOpen);
    for (const palette of [
      "violet-bordeaux",
      "porcelain",
      "mineral-rose",
      "amethyst",
      "petroleum",
      "arizona",
    ]) {
      await page.evaluate((p) => HarnessTheme.apply(p, false), palette);
      const separator = await page
        .locator("#sidebar .conversation-row")
        .first()
        .evaluate((e) => ({
          shadow: getComputedStyle(e).boxShadow,
          border: getComputedStyle(document.documentElement)
            .getPropertyValue("--th-border")
            .trim(),
        }));
      assert.equal(separator.shadow, "none", "Mock 4 groups use flat rows");
      assert(separator.border);
    }
    await page.evaluate(() => HarnessTheme.apply("violet-bordeaux", false));
    assert.equal(
      await page
        .locator("#projects .conversation-title")
        .evaluate((e) => getComputedStyle(e).webkitLineClamp),
      "1",
    );
    if (!(await page.locator("#project-tree").evaluate((el) => el.open)))
      await page.locator("#project-tree > summary").click();
    // Codex-style rows: hover paints the whole row background, never a ring on the inner button.
    await page.locator(".project-group>summary>button").hover();
    const hover = await page
      .locator(".project-group>summary>button")
      .evaluate((e) => ({
        inner: getComputedStyle(e).boxShadow,
        outer: getComputedStyle(e.parentElement).backgroundColor,
      }));
    assert.equal(hover.inner, "none");
    assert.notEqual(hover.outer, "rgba(0, 0, 0, 0)");
    assert(
      await page
        .locator("#sidebar .section-label")
        .evaluateAll((nodes) =>
          nodes.every((e) => getComputedStyle(e).borderBottomWidth === "0px"),
        ),
    );
    assert.equal(await page.locator("#project-tree > summary").count(), 1);
    // Codex-style sidebar (Sophia, 2026-10-03): a single Chats list replaces the status groups.
    assert.equal(await page.locator(".conversation-state-group").count(), 1);
    for (const [trigger, id] of [
      ["settings", "settings-dialog"],
      ["search-conversations", "conversation-search-dialog"],
    ]) {
      await page.click("#" + trigger);
      const dialog = page.locator("#" + id);
      await dialog.waitFor({ state: "visible" });
      await dialog.locator("h2").click();
      assert(
        await dialog.isVisible(),
        "clicking modal content must keep it open",
      );
      await dialog.click({ position: { x: 5, y: 5 } });
      assert(
        await dialog.isVisible(),
        "clicking modal padding must keep it open",
      );
      await page.mouse.click(2, 2);
      await dialog.waitFor({ state: "hidden", timeout: 1500 });
      assert(
        await page
          .locator("#" + trigger)
          .evaluate((e) => e === document.activeElement),
        "dismissal must restore focus",
      );
    }
    await page.click("#search-conversations");
    await page
      .locator("#conversation-search-dialog")
      .waitFor({ state: "visible" });
    assert(
      await page
        .locator("#conversation-search")
        .evaluate((e) => e === document.activeElement),
    );
    await page.fill("#conversation-search", "NAIVE");
    assert.equal(await page.locator(".conversation-search-result").count(), 1);
    // Project chats live in their folder and the rest under Chats (Codex model, Sophia 2026-10-03).
    assert.equal(
      await page.locator("#sidebar .conversation-row").count(),
      35,
      "modal search must not filter sidebar",
    );
    await page.fill("#conversation-search", "nonexistent");
    assert.equal(await page.locator(".conversation-search-result").count(), 0);
    assert.match(
      await page.locator("#search-results").innerText(),
      /No run, plan step, or loaded file matched/,
    );
    await page.click("#search-clear");
    assert.equal(await page.locator(".conversation-search-result").count(), 35);
    await page.fill("#conversation-search", "qwen-local");
    assert.equal(
      await page.locator(".conversation-search-result").count(),
      1,
      "unified search also matches the run model",
    );
    // A term that only an answer contains finds the conversation, with its snippet and no internal id.
    await page.fill("#conversation-search", "KESTREL");
    await page.locator(".search-snippet").waitFor();
    assert.equal(await page.locator(".conversation-search-result").count(), 1);
    const hit = await page.locator(".conversation-search-result").innerText();
    assert.match(hit, /Conversation 3/);
    assert.match(hit, /KESTREL launch slipped/);
    assert.match(hit, /Local models/);
    assert.doesNotMatch(hit, /[0-9a-f]{32}|\bc3\b/);
    assert.equal(projectFileRequests, 0, "a project without a folder never asks for its files (422)");
    await page.fill("#conversation-search", "Naïve");
    await page.locator(".conversation-search-result").click();
    await page
      .locator("#conversation-search-dialog")
      .waitFor({ state: "hidden" });
    await page.waitForFunction(
      () =>
        document.querySelector("#conversation-title").textContent ===
          "Naïve Philosophy" &&
        document.querySelector("#projects button[aria-current=true]"),
    );
    assert(await page.locator(".project-group").evaluate((e) => e.open));
    assert.equal(
      await page.locator("#active-project-badge").innerText(),
      "Philosophy",
    );
    assert.match(
      await page.locator(".assistant .text").last().innerText(),
      /Recovered answer/,
    );
    assert.equal(
      eventRequests,
      0,
      "completed history must render saved answers without replaying events",
    );
    assert.equal(await page.locator(".assistant").count(), 2);
    assert.match(
      await page.locator(".assistant .text").first().innerText(),
      /Old complete answer/,
    );
    const visible = await page
      .locator("#projects button[aria-current=true]")
      .evaluate((e) => {
        const r = e.getBoundingClientRect(),
          s = document.querySelector(".sidebar-tree").getBoundingClientRect();
        return r.top >= s.top && r.bottom <= s.bottom;
      });
    assert(visible);
    await page.screenshot({ path: "/tmp/keepharness-sidebar-icons-light.png" });
    await page.evaluate(() => HarnessTheme.apply("amethyst", false));
    await page.waitForTimeout(300);
    await page.screenshot({ path: "/tmp/keepharness-sidebar-icons-dark.png" });
    await page.evaluate(() => HarnessTheme.apply("violet-bordeaux", false));
    await page.setViewportSize({ width: 390, height: 844 });
    await page.keyboard.press("Control+k");
    await page
      .locator("#conversation-search-dialog")
      .waitFor({ state: "visible" });
    assert(
      await page.locator("#conversation-search-dialog").evaluate((e) => {
        const r = e.getBoundingClientRect();
        return r.left >= 0 && r.right <= innerWidth;
      }),
    );
    await page.screenshot({ path: "/tmp/keepharness-conversation-search-mobile.png" });
    await page.keyboard.press("Escape");
    await page
      .locator("#conversation-search-dialog")
      .waitFor({ state: "hidden" });
    await page.setViewportSize({ width: 1280, height: 900 });
    if (!(await page.locator("#project-tree").evaluate((el) => el.open)))
      await page.locator("#project-tree > summary").click();
    await page.click("#add-project");
    await page.locator("#project-dialog").waitFor({ state: "visible" });
    await page.fill("#project-name", "New project");
    await page
      .locator("#project-directory-list .project-file-row")
      .filter({ hasText: "Work A" })
      .click();
    await page.click("#project-directory-add-current");
    await page
      .locator("#project-directory-list .project-file-row")
      .filter({ hasText: "Work B" })
      .click();
    await page.click("#project-directory-add-current");
    await page.click("#project-create");
    await page.locator("#project-dialog").waitFor({ state: "hidden" });
    assert.deepEqual(createdProject, {
      name: "New project",
      paths: ["/home/test-user/Work A", "/home/test-user/Work B"],
    });
    assert.equal(
      await page.locator("#active-project-badge").innerText(),
      "New project",
    );
    turn.state = "running";
    turn.result = {};
    conversations.at(-1).state = "running";
    for (const create of ["#new", ".project-new"]) {
      const jobResult = page.waitForResponse(
        (r) => new URL(r.url()).pathname === "/v1/jobs/c34",
      );
      await page.locator("#projects .conversation-row > button").click();
      await jobResult;
      await page.locator("#cancel").waitFor({ state: "visible" });
      await page.waitForFunction(
        () => !document.querySelector("#new").disabled,
      );
      const count = eventRequests;
      await page.locator(create).first().click();
      await page.fill("#prompt", "New independent draft");
      await page.waitForTimeout(1300);
      assert.equal(await page.locator("#cancel").isVisible(), false);
      assert.equal(
        await page.locator("#prompt").inputValue(),
        "New independent draft",
      );
      assert.equal(
        eventRequests,
        count,
        "old stream must not reconnect after starting another conversation",
      );
      assert.equal(
        cancelRequests,
        0,
        "detaching must not cancel the server job",
      );
      assert.equal(await page.locator(".assistant").count(), 0);
      await page.fill("#prompt", "");
    }
    const runningJob = page.waitForResponse(
        (r) => new URL(r.url()).pathname === "/v1/jobs/c34",
      ),
      firstStream = page.waitForRequest(
        (r) => new URL(r.url()).pathname === "/v1/jobs/c34/events",
      );
    await page.locator("#projects .conversation-row > button").click();
    await runningJob;
    await page.locator("#cancel").waitFor({ state: "visible" });
    await firstStream;
    const previousEvents = eventRequests;
    const loadingOther = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/v1/conversations/c0",
      { timeout: 3000 },
    );
    await page
      .locator("#sidebar .conversation-row")
      .filter({ has: page.locator(".conversation-title").filter({ hasText: /^Conversation 0$/ }) })
      .locator(":scope > button")
      .click();
    let opened = false;
    try {
      await loadingOther;
      opened = true;
    } catch {}
    assert(
      opened,
      "conversation navigation must load while another job is streaming",
    );
    await page.waitForFunction(
      () =>
        document.querySelector("#conversation-title").textContent ===
        "Conversation 0",
    );
    await page.waitForFunction(() =>
      Array.from(document.querySelectorAll(".assistant .text")).some((e) =>
        e.textContent.includes("Answer from conversation 0"),
      ),
    );
    assert.match(
      await page.locator(".assistant .text").last().innerText(),
      /Answer from conversation 0/,
      "opening another conversation must replace the active transcript",
    );
    assert.equal(
      await page.locator("#cancel").isVisible(),
      false,
      "the previous running job controls must not remain active in the other conversation",
    );
    await page.locator("#projects .conversation-row > button").click();
    await page.waitForFunction(
      () =>
        document.querySelector("#conversation-title").textContent ===
        "Naïve Philosophy",
    );
    await page.waitForFunction(() =>
      Array.from(document.querySelectorAll(".assistant .text")).some((e) =>
        e.textContent.includes("Answer exclusive to c34"),
      ),
    );
    assert.match(
      await page.locator(".assistant .text").last().innerText(),
      /Answer exclusive to c34/,
      "returning to a running conversation must resume its own answer stream",
    );
    assert(
      eventRequests > previousEvents,
      "returning to a running conversation must reconnect to its event stream",
    );
    await page.click("#search-conversations");
    await page.fill("#conversation-search", "Conversation 0");
    assert.equal(
      await page.locator(".conversation-search-result").isDisabled(),
      false,
      "conversation search results must stay selectable while a job streams",
    );
    await page.locator(".conversation-search-result").click();
    await page.waitForFunction(
      () =>
        document.querySelector("#conversation-title").textContent ===
        "Conversation 0",
    );
    await page.waitForFunction(() =>
      Array.from(document.querySelectorAll(".assistant .text")).some((e) =>
        e.textContent.includes("Answer from conversation 0"),
      ),
    );
    assert.match(
      await page.locator(".assistant .text").last().innerText(),
      /Answer from conversation 0/,
      "search navigation must also isolate the other conversation",
    );
    assert.deepEqual(errors, []);
    console.log(
      "PASS: search dialog, title and answer-text search, accent-insensitive search, no ids, project folder selection, and project badge, clear, empty results, sidebar navigation, keyboard and mobile.",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
