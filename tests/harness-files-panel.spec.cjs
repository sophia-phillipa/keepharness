async function openModelGroup(page, id) {
  const group = page
    .locator("#model-menu details")
    .filter({ has: page.locator('[data-value="' + id + '"]') });
  if ((await group.getAttribute("open")) === null)
    await group.locator("summary").click();
}
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
        viewport: { width: 1280, height: 860 },
      }),
      attached = [],
      submitted = [],
      uploaded = [];
    page.on("pageerror", (e) => console.error("PAGEERROR", e.message));
    page.setDefaultTimeout(5000);
    if (!process.env.HARNESS_URL)
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
    let failDirectoryOnce = true;
    await page.addInitScript(() => {
      if (!sessionStorage.getItem("legacy-activity-view-seeded")) {
        localStorage.setItem("activity-open", "1");
        localStorage.setItem("right-panel-view", "activity");
        sessionStorage.setItem("legacy-activity-view-seeded", "1");
      }
      localStorage.removeItem("sidebar-collapsed");
    });
    await page.route("**/v1/**", async (route) => {
      const url = new URL(route.request().url()),
        path = url.pathname;
      let data = {};
      if (path === "/v1/projects")
        data = {
          projects: ["sem-projeto", "project-a"],
          details: { "project-a": { label: "Project Alpha" } },
        };
      else if (path === "/v1/models")
        data = {
          models: [
            {
              id: "fixture",
              name: "Fixture",
              backend: "local",
              efforts: ["low"],
              permissions: { upload: true },
            },
            {
              id: "qwen-local",
              name: "Qwen3.6-35B-A3B",
              backend: "local",
              efforts: ["configured"],
              permissions: { upload: true },
            },
            {
              id: "deepseek-flash",
              name: "deepseek-flash",
              backend: "deepseek",
              efforts: ["configured"],
              permissions: { upload: true },
            },
            {
              id: "deepseek-v4-pro",
              name: "deepseek-v4-pro",
              backend: "deepseek",
              efforts: ["configured"],
              permissions: { upload: true },
            },
          ],
          providers: { local: true, deepseek: true },
          uploads_enabled: true,
        };
      else if (path === "/v1/conversations/restore-fixture") {
        await new Promise((resolve) => setTimeout(resolve, 150));
        data = {
          title: "Restored",
          turns: [
            {
              id: "restored-job",
              project: "sem-projeto",
              request: {
                backend: "local",
                model: "fixture",
                effort: "low",
                prompt: "restored",
                access_mode: "read_only",
              },
              state: "completed",
              result: { answer: "ok" },
            },
          ],
        };
      } else if (path === "/v1/conversations") data = { conversations: [] };
      else if (path === "/v1/jobs/restored-job/events")
        return route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          body: "",
        });
      else if (path === "/v1/jobs/restored-job")
        data = { state: "completed", result: { answer: "ok" } };
      else if (path === "/v1/jobs" && route.request().method() === "POST") {
        submitted.push(route.request().postDataJSON());
        return route.fulfill({
          json: { job_id: "submitted-" + submitted.length },
        });
      } else if (/^\/v1\/jobs\/submitted-\d+\/events$/.test(path))
        return route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          body: "",
        });
      else if (/^\/v1\/jobs\/submitted-\d+$/.test(path))
        data = { state: "completed", result: { answer: "fixture response" } };
      else if (path === "/v1/files" && route.request().method() === "POST") {
        const name = decodeURIComponent(
          route.request().headers()["x-filename"] || "",
        );
        uploaded.push(name);
        return route.fulfill({
          json: {
            file_id: "local-" + uploaded.length,
            preview_url: "/v1/files/local-" + uploaded.length + "/preview",
            media_type: "image/png",
          },
        });
      } else if (/^\/v1\/files\/[^/]+\/preview$/.test(path))
        return route.fulfill({
          status: 200,
          contentType: "image/svg+xml",
          body: '<svg xmlns="http://www.w3.org/2000/svg" width="128" height="96"><rect width="128" height="96" fill="#aaa"/></svg>',
        });
      else if (path === "/v1/version")
        data = { version: "fixture", build: "files-panel" };
      else if (
        path === "/v1/project-files" &&
        route.request().method() === "GET"
      ) {
        const rootId = url.searchParams.get("root_id"),
          dir = url.searchParams.get("path") || "";
        assert.equal(
          url.searchParams.get("start"),
          "1",
          "folder listings start at the first item",
        );
        assert.equal(url.searchParams.get("limit"), "100");
        if (dir === "src" && failDirectoryOnce) {
          failDirectoryOnce = false;
          return route.fulfill({
            status: 503,
            json: { error: "folder temporarily unavailable" },
          });
        }
        const roots = [
          { id: "system", label: "System" },
          { id: "home", label: "Personal folder" },
          { id: "media-user", label: "User media" },
          { id: "media-system", label: "System media" },
        ];
        if (!rootId) data = { state: "ready", roots, entries: [] };
        else if (rootId === "home" && dir === "")
          data = {
            state: "ready",
            roots,
            root_id: rootId,
            path: dir,
            entries: [
              { path: "photo.png", name: "photo.png", type: "file" },
              { path: "src", name: "src", type: "directory" },
            ],
            limited: false,
          };
        else if (rootId === "home" && dir === "src")
          data = {
            state: "ready",
            roots,
            root_id: rootId,
            path: dir,
            entries: [
              { path: "src/main.py", name: "src/main.py", type: "file" },
            ],
            limited: false,
          };
        else
          data = {
            state: "ready",
            roots,
            root_id: rootId,
            path: dir,
            entries: [
              { path: "external.txt", name: "external.txt", type: "file" },
            ],
            limited: false,
          };
      } else if (
        path === "/v1/project-files/attach" &&
        route.request().method() === "POST"
      ) {
        const body = route.request().postDataJSON();
        attached.push({ query: Object.fromEntries(url.searchParams), body });
        data = {
          attachments: (body.paths || []).slice(0, 1).map((name, i) => ({
            file_id: "selected-" + attached.length + "-" + i,
            name,
            preview_url: name.endsWith(".png")
              ? "/v1/files/selected-" + attached.length + "-" + i + "/preview"
              : undefined,
            media_type: name.endsWith(".png") ? "image/png" : undefined,
          })),
          skipped: (body.paths || [])
            .slice(1)
            .map((path) => ({ path, reason: "limit" })),
        };
      }
      return route.fulfill({ json: data });
    });
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.14"));
    await page.goto(process.env.HARNESS_URL || "http://panel.test/");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    for (const [id, label] of [
      ["deepseek-flash", "DeepSeek V4.1 Flash"],
      ["deepseek-v4-pro", "DeepSeek V4 Pro"],
    ]) {
      await page.locator("#model-trigger").click();
      await openModelGroup(page, id);
      const option = page.locator(`#model-menu [data-value="${id}"]`);
      await option.waitFor({ state: "visible" });
      assert.equal(await option.locator(".provider-logo-icon").count(), 0);
      assert.equal(await option.locator(".model-logo-icon").innerText(), "🐋");
      assert.equal(await option.locator("strong").innerText(), label);
      await option.click();
      assert.equal(await page.locator("#model-label").innerText(), label);
      assert.equal(
        await page.locator("#model-trigger .model-picker-icon").count(),
        1,
      );
      assert.equal(await page.locator("#model-trigger-icon").innerText(), "🐋");
      assert.equal(
        await page.locator("#composer-identity").count(),
        0,
        "no duplicate identity above composer",
      );
      if (id === "deepseek-flash")
        await page.screenshot({ path: "/tmp/tail-model-identity.png" });
    }
    await page.locator("#model-trigger").click();
    await openModelGroup(page, "qwen-local");
    await page.locator('#model-menu [data-value="qwen-local"]').click();
    assert.equal(await page.locator("#model-trigger-icon").innerText(), "✦");
    assert.equal(
      await page.locator("#model-trigger .model-picker-icon").count(),
      1,
    );
    await page.locator("#model-trigger").click();
    await openModelGroup(page, "fixture");
    await page.locator('#model-menu [data-value="fixture"]').click();
    const modes = [
      [
        "ask",
        "?",
        "Ask for approval",
        "Asks before every file change or command. Codex may still run commands that cannot change files.",
      ],
      [
        "auto",
        "↗",
        "Automatic",
        "Edits and runs commands inside the project without asking; asks only to go beyond it.",
      ],
      [
        "full",
        "!",
        "Full access",
        "Runs without asking, within the permissions set by the administrator.",
      ],
      [
        "read_only",
        "◉",
        "Read only",
        "Reads and searches only. Writing, commands and tests are off.",
      ],
    ];
    for (const [mode, icon, label, title] of modes) {
      if ((await page.locator("#access-mode").inputValue()) !== mode) {
        await page.locator("#access-trigger").click();
        await page.locator(`[data-access="${mode}"]`).click();
      }
      assert.equal(await page.locator("#access-label").innerText(), label);
      assert.equal(
        await page.locator("#access-trigger-icon").innerText(),
        icon,
      );
      assert.equal(
        await page.locator("#access-trigger").getAttribute("title"),
        title,
      );
    }
    await page.evaluate(() =>
      sessionStorage.setItem(
        "remote-view",
        JSON.stringify({ conversation: "restore-fixture" }),
      ),
    );
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.waitForFunction(
      () =>
        document.querySelector("#access-trigger")?.dataset.mode === "read_only",
    );
    assert.equal(
      await page.locator("#access-trigger-icon").innerText(),
      "◉",
      "restoring a conversation restores its access icon",
    );
    assert.equal(
      await page.locator("#access-trigger").getAttribute("title"),
      modes[3][3],
    );
    await page.locator("#activity-panel").waitFor({ state: "visible" });
    await page
      .locator("#files-tree [role=treeitem]")
      .first()
      .waitFor({ state: "visible" });
    await page.evaluate(() => localStorage.removeItem("activity-open"));
    assert.equal(await page.locator("#files-view").isVisible(), true);
    assert.deepEqual(
      await page
        .locator("#activity-panel .panel-view-controls button")
        .allTextContents(),
      ["Files", "Activity"],
    );
    assert.equal(
      await page
        .locator("#app-topbar #files-toggle, #app-topbar #activity-toggle")
        .count(),
      0,
    );
    assert.equal(await page.locator("#activity-view").isVisible(), true, "stacked workspace shows activity alongside files");
    assert.equal(
      await page.locator("#files-toggle").getAttribute("aria-expanded"),
      "true",
    );
    assert.equal(
      await page.locator("#project").inputValue(),
      "sem-projeto",
      "system file roots are independent from project selection",
    );
    assert.equal(
      await page.locator("#files-help").textContent(),
      "Drag files or folders into the chat to use them",
    );
    assert.deepEqual(await page.locator(".file-root").allTextContents(), [
      "Local Folders",
      "External Folders",
    ]);
    assert.equal(
      await page
        .getByRole("button", { name: "Local Folders" })
        .getAttribute("aria-expanded"),
      "true",
      "local folders are expanded by default",
    );
    assert.equal(
      await page
        .getByRole("button", { name: "External Folders", exact: true })
        .getAttribute("aria-expanded"),
      "false",
    );
    assert.equal(
      await page
        .locator("#files-tree")
        .evaluate(
          (el) => el.parentElement.querySelector(".file-root").textContent,
        ),
      "Local Folders",
    );
    await page
      .getByRole("button", { name: "Local Folders", exact: true })
      .click();
    assert.equal(await page.locator("#files-tree").isVisible(), false);
    await page
      .getByRole("button", { name: "Local Folders", exact: true })
      .click();
    assert.equal(await page.locator("#files-tree").isVisible(), true);
    const localsBox = await page
        .getByRole("button", { name: "Local Folders", exact: true })
        .boundingBox(),
      externalsBox = await page
        .getByRole("button", { name: "External Folders", exact: true })
        .boundingBox();
    assert(
      externalsBox.y > localsBox.y,
      "external folders follow local folders vertically",
    );
    assert.equal(
      await page.locator("#files-tree input[type=checkbox]").count(),
      0,
    );
    assert.equal(await page.locator("#files-attach").count(), 0);
    await page
      .locator('#files-tree [data-path="photo.png"]')
      .waitFor({ state: "visible" });
    const photo = page.locator('#files-tree [data-path="photo.png"]'),
      src = page.locator('#files-tree [data-path="src"]');
    await page.evaluate(() => {
      window.savedIconCatalog = window.TailFileIcons;
      delete window.TailFileIcons;
      renderProjectFileTree();
    });
    assert.equal(
      await page.locator('#files-tree [data-path="src"]').count(),
      1,
      "missing icon catalog must not prevent browsing",
    );
    assert.equal(
      await src
        .locator(":scope > .project-file-row .project-file-icon")
        .count(),
      1,
      "missing catalog keeps a fallback icon",
    );
    await page.evaluate(() => {
      window.TailFileIcons = window.savedIconCatalog;
      delete window.savedIconCatalog;
      renderProjectFileTree();
    });
    const mappings = await page.evaluate(() =>
      Object.fromEntries(
        [
          "run.sh",
          "data.json",
          "app.js",
          "index.php",
          "design.PSD",
          "art.ai",
          "photo.png",
          "photo.jpg",
          "icon.svg",
          "README.md",
          "package.json",
          "example.unknown",
          "__proto__",
        ].map((name) => [
          name,
          projectFileIcon(name).querySelector("use").getAttribute("href"),
        ]),
      ),
    );
    for (const [name, id] of Object.entries({
      "run.sh": "console",
      "data.json": "json",
      "app.js": "javascript",
      "index.php": "php",
      "design.PSD": "adobe-photoshop",
      "art.ai": "adobe-illustrator",
      "photo.png": "image",
      "photo.jpg": "image",
      "icon.svg": "svg",
      "example.unknown": "file",
      __proto__: "file",
    }))
      assert.equal(mappings[name], "/assets/file-icons.svg#" + id, name);
    assert.equal(
      await src.locator(":scope > .project-file-row use").getAttribute("href"),
      "/assets/file-icons.svg#folder-src",
    );
    const sprite = await page.evaluate(async () => {
      const r = await fetch("/assets/file-icons.svg");
      return { ok: r.ok, body: await r.text() };
    });
    assert(
      sprite.ok && sprite.body.includes('id="adobe-photoshop"'),
      "sprite is served locally",
    );

    await photo.click();
    await page.getByRole("button", { name: "External Folders" }).click();
    assert.equal(
      await page.locator("#files-selection-count").isVisible(),
      false,
      "changing root clears selection",
    );
    await page.getByRole("button", { name: "Local Folders" }).click();
    await page
      .locator('#files-tree [data-path="photo.png"]')
      .waitFor({ state: "visible" });
    await photo.click();
    await page.getByRole("button", { name: "Expand src" }).click();
    await page.locator("#files-error").waitFor({ state: "visible" });
    assert.equal(
      await page
        .getByRole("button", { name: "Expand src" })
        .getAttribute("aria-expanded"),
      "false",
      "failed directory collapses for retry",
    );
    assert.equal(
      await page
        .locator("#files-tree")
        .getByText("Loading…", { exact: true })
        .count(),
      0,
      "failed directory stops showing loading",
    );
    await page.getByRole("button", { name: "Expand src" }).click();
    const child = page.locator('#files-tree [data-path="src/main.py"]');
    await child.waitFor({ state: "visible" });
    assert.equal(
      await src.locator(":scope > .project-file-row use").getAttribute("href"),
      "/assets/file-icons.svg#folder-src-open",
    );
    await child.click({ modifiers: ["Shift"] });
    assert.equal(
      await page.locator("#files-error").isVisible(),
      false,
      "successful retry clears the previous directory error",
    );
    for (const path of ["photo.png", "src", "src/main.py"])
      assert.equal(
        await page
          .locator(`#files-tree [data-path="${path}"]`)
          .getAttribute("aria-selected"),
        "true",
        "Shift selects all visible items, including expanded descendants",
      );
    assert.equal(
      await page.locator("#files-selection-count").innerText(),
      "3 selected",
    );
    await child.click({ modifiers: ["ControlOrMeta"] });
    assert.equal(
      await child.getAttribute("aria-selected"),
      "false",
      "modifier click toggles only the child",
    );
    await child.focus();
    await child.press("Space");
    assert.equal(
      await child.getAttribute("aria-selected"),
      "true",
      "Space toggles selection from keyboard",
    );
    assert.equal(
      await page.locator("#files-selection-count").innerText(),
      "3 selected",
    );
    await photo.evaluate((node) => {
      const data = new DataTransfer();
      node.dispatchEvent(
        new DragEvent("dragstart", {
          bubbles: true,
          cancelable: true,
          dataTransfer: data,
        }),
      );
      window.__filesDrag = data;
    });
    await page.locator("#dropzone").evaluate((node) => {
      const data = window.__filesDrag;
      node.dispatchEvent(
        new DragEvent("dragover", {
          bubbles: true,
          cancelable: true,
          dataTransfer: data,
        }),
      );
      node.dispatchEvent(
        new DragEvent("drop", {
          bubbles: true,
          cancelable: true,
          dataTransfer: data,
        }),
      );
    });
    await page.waitForFunction(
      () => document.querySelectorAll(".attachment").length === 1,
    );
    assert.equal(attached.length, 1);
    assert.equal(attached[0].query.project_id, "sem-projeto");
    assert.deepEqual(attached[0].body.paths, [
      "photo.png",
      "src",
      "src/main.py",
    ]);
    assert.equal(attached[0].query.max_files, "20");
    assert.equal(attached[0].body.backend, "local");
    assert.equal(attached[0].body.model, "fixture");
    assert.equal(
      await page.locator(".attachment button").isDisabled(),
      false,
      "remove control re-enables after attach completes",
    );
    assert.equal(
      await page.locator("#attachment-count").innerText(),
      "1 / 20 files attached",
    );
    assert(await page.locator("#attachment-count").isVisible());
    assert.equal(
      await page
        .locator("#attachment-count")
        .evaluate((el) => getComputedStyle(el).textAlign),
      "right",
    );
    await page
      .locator("#prompt")
      .fill("Draft with attachment before reloading");
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    assert.equal(
      await page.locator("#prompt").inputValue(),
      "Draft with attachment before reloading",
      "reload preserves draft text",
    );
    assert.equal(
      await page.locator(".attachment").count(),
      1,
      "reload preserves the unsent attachment",
    );
    assert.equal(
      await page.locator("#attachment-count").innerText(),
      "1 / 20 files attached",
      "reload preserves attachment count",
    );
    const thumbnail = page.locator(".attachment-preview");
    await thumbnail.waitFor({ state: "visible" });
    assert.equal(await thumbnail.getAttribute("alt"), "photo.png");
    assert.equal(
      await thumbnail.getAttribute("src"),
      "/v1/files/selected-1-0/preview",
    );
    await page.waitForFunction(
      () => document.querySelector(".attachment-preview")?.naturalWidth > 0,
    );
    await page.screenshot({ path: "/tmp/tail-files-panel-desktop.png" });
    await page.locator(".attachment button").click();
    assert.equal(
      await page.locator(".attachment").count(),
      0,
      "attachment can be removed after tree attach",
    );
    assert(
      await page.locator("#attachment-count").isHidden(),
      "last removal hides the count",
    );
    await page.locator("#status").waitFor({ state: "visible" });
    await photo.evaluate((node) => {
      const data = new DataTransfer();
      node.dispatchEvent(
        new DragEvent("dragstart", {
          bubbles: true,
          cancelable: true,
          dataTransfer: data,
        }),
      );
      window.__filesDrag = data;
    });
    await page.locator("#dropzone").evaluate((node) => {
      const data = window.__filesDrag;
      node.dispatchEvent(
        new DragEvent("dragover", {
          bubbles: true,
          cancelable: true,
          dataTransfer: data,
        }),
      );
      node.dispatchEvent(
        new DragEvent("drop", {
          bubbles: true,
          cancelable: true,
          dataTransfer: data,
        }),
      );
    });
    await page.waitForFunction(
      () => document.querySelectorAll(".attachment").length === 1,
    );
    assert.equal(attached.length, 2);
    assert.deepEqual(attached[1].body.paths, ["photo.png"]);
    assert.equal(attached[1].query.max_files, "20");
    await page.locator("#prompt").fill("sent via button");
    let sentResponse = page.waitForResponse(
      (r) => r.url().endsWith("/v1/jobs") && r.request().method() === "POST",
    );
    await Promise.all([page.locator("#send").click(), sentResponse]);
    assert.equal(submitted.length, 1);
    assert.equal(submitted[0].prompt, "sent via button");
    assert.deepEqual(submitted[0].file_ids, ["selected-2-0"]);
    assert.equal(submitted[0].access_mode, "read_only");
    assert.equal("task_label" in submitted[0], false);
    await page.waitForFunction(
      () =>
        document.querySelector("#cancel").hidden &&
        !document.querySelector("#prompt").value,
    );
    await page.locator("#prompt").fill("sent via Enter");
    await page.waitForFunction(() => !document.querySelector("#send").disabled);
    sentResponse = page.waitForResponse(
      (r) => r.url().endsWith("/v1/jobs") && r.request().method() === "POST",
    );
    await Promise.all([page.locator("#prompt").press("Enter"), sentResponse]);
    assert.equal(submitted.length, 2);
    assert.equal(submitted[1].prompt, "sent via Enter");
    assert.deepEqual(submitted[1].file_ids, []);
    await page.waitForFunction(
      () =>
        document.querySelector("#cancel").hidden &&
        !document.querySelector("#prompt").value,
    );
    await page.getByRole("button", { name: "Expand src" }).click();
    await child.waitFor({ state: "visible" });
    await photo.click();
    await child.click({ modifiers: ["ControlOrMeta"] });
    await child.press("Enter");
    await page.waitForFunction(
      () =>
        document.querySelector("#attachment-count").textContent ===
        "1 / 20 files attached",
    );
    assert.deepEqual(
      attached.at(-1).body.paths,
      ["photo.png", "src/main.py"],
      "Enter on a selected file attaches the selected group",
    );
    await page.locator(".attachment button").click();
    await photo.click();
    await child.press("Enter");
    await page.waitForFunction(
      () =>
        document.querySelector("#attachment-count").textContent ===
        "1 / 20 files attached",
    );
    assert.deepEqual(
      attached.at(-1).body.paths,
      ["src/main.py"],
      "Enter on an unselected focused file attaches only that file",
    );
    await page.locator(".attachment button").click();
    await page.locator("#file").setInputFiles(
      Array.from({ length: 21 }, (_, i) => ({
        name: `pic-${i}.png`,
        mimeType: "image/png",
        buffer: Buffer.from("fixture"),
      })),
    );
    await page.waitForFunction(
      () =>
        document.querySelector("#attachment-count")?.textContent ===
        "20 / 20 files attached",
    );
    assert.equal(
      uploaded.length,
      20,
      "local upload stops at the shared twenty-file limit",
    );
    assert.equal(
      await page.locator("#attachments .attachment-preview").count(),
      20,
      "image upload metadata renders thumbnails",
    );
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    assert.equal(
      await page.locator("#attachment-count").innerText(),
      "20 / 20 files attached",
      "reload preserves all twenty attachments",
    );
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 860 });
      const sendBox = await page.locator("#send").boundingBox(),
        promptBox = await page.locator("#prompt").boundingBox();
      assert(
        sendBox.y >= 0 && sendBox.y + sendBox.height <= 860,
        "send remains on screen with twenty attachments",
      );
      assert(
        promptBox.y >= 0 && promptBox.y + promptBox.height <= 860,
        "prompt remains on screen with twenty attachments",
      );
      assert(
        await page
          .locator("#attachments")
          .evaluate((el) => el.scrollHeight > el.clientHeight),
        "attachments scroll instead of displacing the prompt",
      );
    }
    await page.setViewportSize({ width: 1280, height: 860 });
    await page.locator("#file").setInputFiles({
      name: "overflow.png",
      mimeType: "image/png",
      buffer: Buffer.from("fixture"),
    });
    await page
      .getByText(
        "Limit of 20 attachments reached. Remove one before adding another.",
      )
      .waitFor({ state: "visible" });
    assert.equal(uploaded.length, 20);
    assert.equal(
      await page.locator("#attachment-count").innerText(),
      "20 / 20 files attached",
    );
    await page
      .getByRole("button", { name: "Remove attachment pic-0.png" })
      .click();
    assert.equal(
      await page.locator("#attachment-count").innerText(),
      "19 / 20 files attached",
      "removal frees an attachment slot",
    );
    for (const selector of ["#new", "#model-trigger", "#effort-trigger"]) {
      const control = page.locator(selector),
        before = await control.evaluate(
          (el) => getComputedStyle(el).backgroundColor,
        );
      await control.hover();
      const after = await control.evaluate(
        (el) => getComputedStyle(el).backgroundColor,
      );
      assert.notEqual(
        after,
        before,
        `${selector} shows the shared accent hover treatment`,
      );
      assert.notEqual(
        await control.evaluate((el) => getComputedStyle(el).borderRadius),
        "0px",
        `${selector} has rounded control corners`,
      );
    }
    await page.click("#activity-toggle");
    assert.equal(
      await page.locator("#activity-view").isVisible(),
      true,
      "activity shortcut expands its section without closing the workspace",
    );
    assert.equal(await page.locator("#activity-panel").isVisible(), true);
    await page.click("#files-toggle");
    assert.equal(await page.locator("#files-view").isVisible(), true);
    await page.click("#files-toggle");
    assert.equal(
      await page.locator("#activity-panel").isVisible(),
      true,
      "clicking the section shortcut keeps the workspace open",
    );
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(
      await page.locator("#activity-panel").isVisible(),
      false,
      "mobile starts without covering the conversation",
    );
    await page.locator("#model-trigger").click();
    await openModelGroup(page, "deepseek-v4-pro");
    await page.locator('#model-menu [data-value="deepseek-v4-pro"]').click();
    assert.equal(await page.locator("#composer-identity").count(), 0);
    assert.equal(
      await page.locator("#model-trigger .model-picker-icon").count(),
      1,
    );
    await page.screenshot({ path: "/tmp/tail-model-identity-mobile.png" });
    await page.click("#panel-toggle");
    await page.locator("#activity-panel").waitFor({ state: "visible" });
    assert.equal(
      await page.locator("#files-view").isVisible(),
      true,
      "mobile opens to file view",
    );
    const panel = await page.locator("#activity-panel").boundingBox(),
      topbar = await page.locator("#app-topbar").boundingBox();
    assert.equal(panel.x + panel.width, 390);
    assert.equal(
      panel.y,
      topbar.y + topbar.height,
      "drawer starts exactly below topbar",
    );
    const filesButton = await page.locator("#files-toggle").boundingBox(),
      activityButton = await page.locator("#activity-toggle").boundingBox();
    assert(
      filesButton.x < activityButton.x,
      "files control sits immediately left of activity control",
    );
    await page.screenshot({ path: "/tmp/tail-files-panel-mobile.png" });
    await page.locator("#activity-toggle").focus();
    await page.keyboard.press("Escape");
    assert.equal(await page.locator("#activity-panel").isVisible(), false);
    assert.equal(
      await page
        .locator("#panel-toggle")
        .evaluate((el) => el === document.activeElement),
      true,
    );
    await page.click("#panel-toggle");
    assert.equal(await page.locator("#files-view").isVisible(), true);
    await page.evaluate(() => {
      models = [];
      document.querySelector("#model").replaceChildren();
      syncComposerPickers();
    });
    assert.equal(
      await page.locator("#composer-identity").count(),
      0,
      "identity strip is absent",
    );
    await page.setViewportSize({ width: 1280, height: 1000 });
    await page.evaluate(() => {
      const names = [
        "src",
        "tests",
        "docs",
        "images",
        "run.sh",
        "data.json",
        "app.js",
        "index.php",
        "design.psd",
        "art.ai",
        "photo.png",
        "icon.svg",
        "archive.zip",
      ];
      const list = document.createElement("ul");
      list.setAttribute("role", "group");
      renderProjectFileEntries(
        list,
        names.map((name, i) => ({
          name,
          path: name,
          type: i < 4 ? "directory" : "file",
        })),
      );
      document.querySelector("#files-tree").replaceChildren(list);
    });
    await page.screenshot({ path: "/tmp/tail-file-icons.png" });
    console.log(
      "PASS: Files/Activity panel, visible roots/default, click/modifier/keyboard selection and drag attach, access icons/descriptions and conversation restore, plus mobile opening.",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
