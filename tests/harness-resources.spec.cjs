const { executionModes } = require("./model-fixture.cjs");
// Resource discovery is refreshed on open and scoped to the selected engine. No model runs.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const chromiumExecutable = process.env.CHROMIUM_EXECUTABLE;
const fs = require("node:fs/promises");
const path = require("node:path");
(async () => {
  const browser = await chromium.launch(
    chromiumExecutable ? { executablePath: chromiumExecutable } : {},
  );
  try {
    const page = await browser.newPage(),
      posts = [],
      resourceQueries = [];
    let delayResources = false;
    let models = [
      {
        id: "codex-model",
        backend: "codex",
        execution_modes: executionModes("codex"),
        efforts: ["low"],
        permissions: { upload: true },
      },
      {
        id: "claude-sonnet-4-6",
        backend: "claude",
        execution_modes: executionModes("claude"),
        efforts: ["low"],
        permissions: { upload: true },
      },
    ];
    await page.route("http://resources.test/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      if (p.startsWith("/v1/")) {
        let data = {};
        if (p === "/v1/projects")
          data = { projects: ["project-a", "project-b"], details: {} };
        if (p === "/v1/models")
          data = {
            models,
            uploads_enabled: true,
            providers: { codex: true, claude: true },
          };
        if (p === "/v1/usage") data = { available: false };
        if (p === "/v1/conversations") data = { conversations: [] };
        if (p === "/v1/resources") {
          const u = new URL(route.request().url());
          resourceQueries.push(Object.fromEntries(u.searchParams));
          if (delayResources)
            await new Promise((resolve) => setTimeout(resolve, 300));
          data = {
            engine: u.searchParams.get("backend"),
            items: [
              {
                id: "p-agent",
                revision: "r1",
                kind: "agent",
                name: "reviewer",
                description:
                  "Review project code, trace dependencies and check correctness, accessibility and test coverage. Report concrete findings with file references and explain which behavior needs to change before implementation.",
                scope: "project",
                origin: "Codex",
                source: "project/.codex/agents/reviewer.toml",
                selectable: true,
              },
              {
                id: "g-duplicate",
                revision: "g2",
                kind: "agent",
                name: "reviewer",
                description: "Global reviewer",
                scope: "global",
                origin: "Codex",
                source: "~/.codex/agents/reviewer.toml",
                selectable: true,
              },
              {
                id: "g-agent",
                revision: "g1",
                kind: "agent",
                name: "writer",
                description: "Write docs",
                scope: "global",
                origin: "Codex",
                source: "~/.codex/agents/writer.toml",
                selectable: true,
              },
              {
                id: "p-skill",
                revision: "s1",
                kind: "skill",
                name: "review",
                description: "Review skill",
                scope: "project",
                origin: "Codex",
                source: "project/.agents/skills/review/SKILL.md",
                selectable: true,
              },
              {
                id: "p-command",
                revision: "c1",
                kind: "command",
                name: "build",
                description: "Build",
                scope: "project",
                origin: "Codex",
                selectable: true,
              },
              {
                id: "disabled-skill",
                revision: "s2",
                kind: "skill",
                name: "unsupported",
                description: "Unavailable",
                scope: "global",
                origin: "Gemini",
                source: "~/.gemini/skills/unsupported/SKILL.md",
                selectable: false,
                unavailable_reason: "Disabled by adapter",
              },
            ],
            warnings: ["Partial catalog due to engine configuration."],
          };
        }
        if (p === "/v1/jobs" && route.request().method() === "POST") {
          posts.push(route.request().postDataJSON());
          data = { job_id: "job-1" };
        }
        if (p === "/v1/jobs/job-1")
          data = {
            id: "job-1",
            state: "completed",
            turns: [],
            result: { answer: "done" },
          };
        if (p.endsWith("/events"))
          return route.fulfill({ body: "", contentType: "text/event-stream" });
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
        contentType: file.endsWith(".js")
          ? "text/javascript"
          : file.endsWith(".css")
            ? "text/css"
            : file.endsWith(".svg")
              ? "image/svg+xml"
              : "text/html",
      });
    });
    await page.addInitScript(() =>
      localStorage.setItem("keepharness-tour-seen", "0.16.0"),
    );
    await page.goto("http://resources.test");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.fill("#prompt", "@");
    await page.waitForSelector('#resource-menu [data-resource-id="p-agent"]');
    assert.equal(
      resourceQueries.length,
      2,
      "workspace and composer each load the current resource catalog",
    );
    const description = page.locator(
      '#resource-menu [data-resource-id="p-agent"] small',
    );
    assert(
      await description.evaluate(
        (el) =>
          el.getBoundingClientRect().height >
          parseFloat(getComputedStyle(el).lineHeight),
      ),
    );
    assert.equal(resourceQueries.at(-1).backend, "codex");
    assert.match(
      await page.locator("#resource-menu .resource-group").first().innerText(),
      /PROJECT/,
    );
    assert.match(
      await page.locator("#resource-menu").innerText(),
      /Partial catalog/,
    );
    if (process.env.RESOURCE_SCREENSHOT) {
      await page.screenshot({
        path: process.env.RESOURCE_SCREENSHOT,
        fullPage: false,
      });
    }
    await page.keyboard.press("ArrowDown");
    assert.equal(
      await page.evaluate(() => document.activeElement.dataset.resourceId),
      "p-agent",
    );
    await page.keyboard.press("ArrowDown");
    assert.equal(
      await page.evaluate(() => document.activeElement.dataset.resourceId),
      "g-duplicate",
    );
    await page.keyboard.press("ArrowDown");
    assert.equal(
      await page.evaluate(() => document.activeElement.dataset.resourceId),
      "g-agent",
    );
    await page.keyboard.press("ArrowUp");
    assert.equal(
      await page.evaluate(() => document.activeElement.dataset.resourceId),
      "g-duplicate",
    );
    await page.keyboard.press("Home");
    assert.equal(
      await page.evaluate(() => document.activeElement.dataset.resourceId),
      "p-agent",
    );
    assert.equal(posts.length, 0);
    await page.keyboard.press("Enter");
    assert.equal(await page.locator("#prompt").inputValue(), "@reviewer ");
    assert.equal(
      await page.locator("#prompt-highlights .prompt-resource").textContent(),
      "@reviewer",
    );
    assert(
      await page
        .locator("#prompt-highlights .prompt-resource")
        .evaluate((el) => {
          const probe = document.createElement("span");
          probe.style.color = "var(--th-accent)";
          el.parentElement.append(probe);
          const matches =
            getComputedStyle(el).color === getComputedStyle(probe).color;
          probe.remove();
          return matches;
        }),
    );
    assert.deepEqual(
      await page.evaluate(
        () =>
          JSON.parse(sessionStorage.getItem("remote-view")).resource_selections,
      ),
      [{ id: "p-agent", revision: "r1", token: "@reviewer" }],
    );
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    assert.equal(await page.locator("#prompt").inputValue(), "@reviewer ");
    assert.equal(
      await page.locator(".prompt-resource").textContent(),
      "@reviewer",
    );
    await page.click("#send");
    await page.waitForTimeout(100);
    assert.equal(posts.length, 1);
    assert.deepEqual(posts[0].resource_selections, [
      { id: "p-agent", revision: "r1", token: "@reviewer" },
    ]);
    await page.fill("#prompt", "/review");
    await page
      .locator('#resource-menu [data-resource-id="p-skill"]')
      .waitFor({ state: "attached" });
    await page.keyboard.press("ArrowUp");
    assert.equal(
      await page.evaluate(() => document.activeElement.dataset.resourceId),
      "p-skill",
    );
    await page.keyboard.press("ArrowDown");
    assert.equal(
      await page.evaluate(() => document.activeElement.dataset.resourceId),
      "p-skill",
    );
    await page.keyboard.press("Escape");
    assert.equal(
      await page.evaluate(() => document.activeElement.id),
      "prompt",
    );
    await page.fill("#prompt", "/un");
    await page
      .locator('#resource-menu [data-resource-id="disabled-skill"]')
      .waitFor({ state: "attached" });
    assert(
      await page
        .locator('#resource-menu [data-resource-id="disabled-skill"]')
        .isDisabled(),
    );
    await page.fill("#prompt", "@rev");
    await page
      .locator('#resource-menu [data-resource-id="p-agent"]')
      .waitFor({ state: "attached" });
    await page.locator('#resource-menu [data-resource-id="p-agent"]').click();
    await page.keyboard.type("@rev");
    await page
      .locator('#resource-menu [data-resource-id="g-duplicate"]')
      .waitFor({ state: "attached" });
    await page
      .locator('#resource-menu [data-resource-id="g-duplicate"]')
      .click();
    assert.equal(
      await page.locator("#prompt").inputValue(),
      "@reviewer @reviewer ",
    );
    assert.deepEqual(
      await page.evaluate(
        () =>
          JSON.parse(sessionStorage.getItem("remote-view")).resource_selections,
      ),
      [
        { id: "p-agent", revision: "r1", token: "@reviewer" },
        { id: "g-duplicate", revision: "g2", token: "@reviewer" },
      ],
    );
    const before = resourceQueries.length;
    await page.selectOption("#model", "claude-sonnet-4-6");
    await page.click("#send");
    assert.equal(posts.length, 1);
    assert.match(await page.locator("#status").innerText(), /invalidated/);
    await page.fill("#prompt", "");
    await page.fill("#prompt", "@w");
    await page
      .locator('#resource-menu [data-resource-id="g-agent"]')
      .waitFor({ state: "attached" });
    await page.locator('#resource-menu [data-resource-id="g-agent"]').click();
    await page.locator("#project").evaluate((el) => {
      el.value = "project-b";
      el.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await page.click("#send");
    assert.equal(posts.length, 1);
    assert.match(await page.locator("#status").innerText(), /invalidated/);
    await page.fill("#prompt", "@writer");
    await page.waitForTimeout(100);
    assert.equal(resourceQueries.at(-1).backend, "claude");
    assert.equal(resourceQueries.at(-1).project_id, "project-b");
    assert.equal(
      resourceQueries.length,
      before + 4,
      "model and project changes refresh both workspace and composer catalogs",
    );
    delayResources = true;
    await page.fill("#prompt", "@late");
    await page.waitForTimeout(50);
    await page.keyboard.press("Escape");
    await page.waitForTimeout(350);
    assert.equal(
      await page
        .locator("#resource-menu")
        .evaluate((el) => el.matches(":popover-open")),
      false,
    );
    await page.fill("#prompt", "person@example.com https://example.test/path");
    assert.equal(
      await page
        .locator("#resource-menu")
        .evaluate((el) => el.matches(":popover-open")),
      false,
    );
    delayResources = false;
    await page.fill("#prompt", "Run @@local");
    await page.click("#send");
    assert.equal(posts.length, 1);
    // @@name now names the user's own agents (Sophia, 2026-10-03); an unknown one is not sent.
    assert.match(
      await page.locator("#status").innerText(),
      /Choose @@local from the @ list/,
    );
    assert.deepEqual(
      resourceQueries
        .map((x) => x.project_id)
        .filter(Boolean)
        .at(-1),
      "project-b",
    );
    await page.fill("#prompt", "@writer");
    await page.locator('#resource-menu [data-resource-id="g-agent"]').click();
    await page.selectOption("#model", "codex-model");
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.click("#send");
    assert.equal(posts.length, 1);
    assert.match(await page.locator("#status").innerText(), /invalidated/);
    await page.fill("#prompt", "@writer");
    await page.locator('#resource-menu [data-resource-id="g-agent"]').click();
    await page.click("#send");
    await page.waitForTimeout(100);
    assert.equal(posts.length, 2);
    await page.setViewportSize({ width: 390, height: 844 });
    // Resize handlers dismiss popovers before the next animation frame.
    await page.evaluate(() => new Promise(requestAnimationFrame));
    await page.fill("#prompt", "@");
    await page
      .locator('#resource-menu [data-resource-id="p-agent"]')
      .waitFor({ state: "visible" });
    const bounds = await page.locator("#resource-menu").boundingBox();
    assert(bounds.x >= 0 && bounds.x + bounds.width <= 390);
    assert(bounds.y >= 0 && bounds.y + bounds.height <= 844);
    if (process.env.RESOURCE_SCREENSHOT)
      await page.screenshot({
        path: process.env.RESOURCE_SCREENSHOT.replace(".png", "-mobile.png"),
      });
    await page.fill("#prompt", "/review");
    await page.locator('#resource-menu [data-resource-id="p-skill"]').click();
    await page.keyboard.type("/build");
    await page.locator('#resource-menu [data-resource-id="p-command"]').click();
    assert.deepEqual(await page.locator(".prompt-resource").allTextContents(), [
      "/review",
      "/build",
    ]);
    if (await page.locator("#activity-panel").isVisible())
      await page.click("#panel-toggle");
    if (process.env.RESOURCE_SCREENSHOT)
      await page.screenshot({
        path: process.env.RESOURCE_SCREENSHOT.replace(".png", "-highlight.png"),
      });
    await page.keyboard.insertText(
      "<b>plain</b> " + "long text ".repeat(100) + "\nend",
    );
    assert.equal(await page.locator("#prompt-highlights b").count(), 0);
    assert.equal(
      await page.locator("#prompt-highlights").innerText(),
      (await page.locator("#prompt").inputValue()) + "\u200b",
    );
    await page.locator("#prompt").evaluate((el) => {
      el.scrollTop = el.scrollHeight;
      el.dispatchEvent(new Event("scroll"));
    });
    assert(
      await page.evaluate(
        () =>
          Math.abs(
            document.querySelector("#prompt").scrollTop -
              document.querySelector("#prompt-highlights").scrollTop,
          ) < 2,
      ),
    );
    await page.fill("#prompt", "ordinary text");
    assert.equal(await page.locator(".prompt-resource").count(), 0);
    assert.equal(
      await page
        .locator("#prompt")
        .evaluate((el) => el.classList.contains("has-resource-highlights")),
      false,
    );
    // Changing the conversation mode invalidates native resource references.
    models.push({
      id: "local-model",
      backend: "local",
      efforts: ["configured"],
      execution_modes: executionModes("local"),
      permissions: { upload: true },
    });
    await page.evaluate(() => sessionStorage.removeItem("remote-view"));
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.fill("#prompt", "@writer");
    await page.locator('#resource-menu [data-resource-id="g-agent"]').click();
    assert.equal(resourceQueries.at(-1).execution_mode, "native");
    await page.selectOption("#model", "local-model");
    assert.equal(
      await page.locator("#isolation-toggle").getAttribute("aria-checked"),
      "true",
    );
    assert.equal(await page.locator(".prompt-resource").count(), 0);
    const sentBeforeModeChange = posts.length;
    await page.click("#send");
    assert.equal(posts.length, sentBeforeModeChange);
    assert.match(await page.locator("#status").innerText(), /invalidated/);
    await page.fill("#prompt", "@");
    await page.locator('#resource-menu [data-resource-id="g-agent"]').waitFor();
    assert.equal(resourceQueries.at(-1).execution_mode, "scoped");
    console.log(
      "PASS: resource selector refresh, engine scoping, selection refs and reserved prefixes",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
