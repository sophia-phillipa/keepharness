// WP7 visual markers (UI): run-step icons, catalog links and the Appearance toggle (part 1).
// Part 2 (prose chips in assistant text) lives in proseChipCases().
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises"),
  path = require("node:path");

const PALETTES = ["paper", "graphite", "violet-bordeaux", "porcelain", "mineral-rose", "amethyst", "petroleum", "arizona"];
const DARK = ["graphite", "amethyst", "petroleum", "arizona"];
const setPalette = (page, name, dark) =>
  page.evaluate(
    ([palette, isDark]) => {
      document.documentElement.dataset.palette = palette;
      document.documentElement.dataset.bsTheme = isDark ? "dark" : "light";
    },
    [name, dark],
  );

const step = (id, data, end = {}) => [
  { type: "tool_start", data: { tool_id: id, ...data } },
  { type: "tool_end", data: { tool_id: id, status: "completed", ...data, ...end } },
];
const events = [
  { type: "running", data: {} },
  // Claude
  ...step("c1", { tool: "Read", target: "src/app.py" }),
  ...step("c2", { tool: "Edit", target: "src/app.py" }),
  ...step("c3", { tool: "Bash", target: "git status" }),
  ...step("c4", { tool: "Skill", skill: "review-pr" }),
  ...step("c5", { tool: "Skill", skill: "mystery-skill" }),
  ...step("c6", { tool: "Task", agent: "code-reviewer" }),
  ...step("c7", { tool: "Grep", target: "TODO" }),
  ...step("c8", { tool: "WebSearch" }),
  ...step("c9", { tool: "TodoWrite" }),
  // Codex
  ...step("x1", { tool: "commandExecution", target: "ls -la" }),
  ...step("x2", { tool: "fileChange", target: "notes.txt" }),
  ...step("x3", { tool: "commandExecution", target: "cat .agents/skills/deploy/SKILL.md", skill: "deploy" }),
  // MCP
  ...step("m1", { tool: "mcp__github__create_issue", server: "github" }),
  ...step("m2", { tool: "mcp__reader__list_dir", target: "docs" }),
  // Hostile names stay text and never become a link or a chip name.
  // Claude streams the input: the skill name only arrives on tool_end, after a plain Read start.
  { type: "tool_start", data: { tool_id: "late1", tool: "Read", target: "SKILL.md" } },
  { type: "tool_end", data: { tool_id: "late1", status: "completed", tool: "Read", target: "SKILL.md", skill: "late-skill" } },
  ...step("h1", { tool: "Skill", skill: "<img src=x onerror=alert(1)>" }),
  { type: "answer_delta", data: { text: "Done." } },
  { type: "completed", data: {} },
].map((event, index) => ({ id: index + 1, ...event }));

const ANSWER = [
  "Run /review-pr on src/app.py, then ask `code-reviewer`; try /nothing, src/missing.py and `unknown-name` too.",
  "",
  "```",
  "/review-pr src/app.py",
  "```",
  "",
  "Also `src/app.py` and `/review-pr` inline.",
].join("\n");
const turn = {
  id: "job-1",
  project: "project-a",
  state: "completed",
  request: { prompt: "Go", model: "fixture", backend: "local", effort: "low" },
  result: { answer: ANSWER, model: "fixture", total_seconds: 5 },
};
const RESOURCES = [
  { id: "skill-review-pr", name: "review-pr", kind: "skill", scope: "project", origin: "claude", revision: "1", selectable: true },
  { id: "skill-deploy", name: "deploy", kind: "skill", scope: "project", origin: "codex", revision: "1", selectable: true },
  { id: "skill-late-skill", name: "late-skill", kind: "skill", scope: "project", origin: "claude", revision: "1", selectable: true },
  { id: "agent-code-reviewer", name: "code-reviewer", kind: "agent", scope: "project", origin: "claude", revision: "1", selectable: true },
];

// Relative luminance and contrast, WCAG 2.2.
const channel = (v) => ((v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
const luminance = ([r, g, b]) => 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
const contrast = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};
const rgb = (css) => css.match(/\d+(\.\d+)?/g).slice(0, 3).map(Number);

// Part 2: prose chips in assistant text, via applyProseMarkers(el) and renderAnswer(el, raw).
async function proseChipCases(page) {
    // Prose chips: only exact catalog names and known paths; code blocks untouched.
    const body = page.locator("#messages .chat-bubble").last();
    const chipTexts = async () => body.locator(".prose-chip").allInnerTexts();
    const expectedChips = ["/review-pr", "src/app.py", "code-reviewer", "src/app.py", "/review-pr"];
    assert.deepEqual(await chipTexts(), expectedChips);
    assert.equal(await body.locator("pre .prose-chip, pre .step-chip").count(), 0, "a code block is never chipped");
    const prose = await body.innerText();
    assert.match(prose, /\/nothing, src\/missing\.py and unknown-name too/);
    assert.equal(await body.locator('.prose-chip[data-resource-id="skill-review-pr"]').count(), 2);
    assert.equal(await body.locator('.prose-chip[data-kind="file"]').count(), 2);
    const rawBefore = await body.evaluate((el) => el.rawAnswer);
    await page.evaluate(() => {
      const el = [...document.querySelectorAll("#messages .chat-bubble")].at(-1);
      applyProseMarkers(el);
      applyProseMarkers(el);
    });
    assert.deepEqual(await chipTexts(), expectedChips, "applying twice adds nothing");
    await page.evaluate(() => {
      const el = [...document.querySelectorAll("#messages .chat-bubble")].at(-1);
      renderAnswer(el, el.rawAnswer);
    });
    assert.deepEqual(await chipTexts(), expectedChips, "a re-render chips exactly once");
    assert.equal(await body.evaluate((el) => el.rawAnswer), rawBefore);
    assert.equal(await body.innerText(), prose, "chips never change the text");

  // Contrast of the prose pill in every palette, dark ones with the dark Bootstrap theme.
  for (const palette of PALETTES) {
    await setPalette(page, palette, DARK.includes(palette));
    const [fg, bg] = await page.evaluate(() => {
      const pill = document.querySelector("#messages .chat-bubble a.prose-chip");
      return [getComputedStyle(pill).color, getComputedStyle(pill).backgroundColor];
    });
    assert.ok(contrast(rgb(fg), rgb(bg)) >= 4.5, palette + " prose pill contrast");
  }
  await setPalette(page, "paper", false);
  // Toggle off removes the pills; on brings them back (a real assertion, not `|| true`).
  await page.evaluate(() => openSettings("appearance"));
  await page.getByTestId("visual-markers-toggle").uncheck();
  await page.waitForFunction(() => !document.querySelector(".prose-chip"));
  assert.equal(await body.innerText(), prose, "unwrapping restores the same text");
  await page.getByTestId("visual-markers-toggle").check();
  await page.waitForFunction(() => document.querySelectorAll("#messages .prose-chip").length > 0);
}

// Guards of the prose chips, through the real bubble() and renderAnswer() paths.
async function proseGuardCases(page) {
  const out = await page.evaluate((answer) => {
    fileTree.cache.set("extra", [
      { path: "src/lib", name: "lib", type: "directory" },
      { path: "tests", name: "tests", type: "directory" },
      { path: "LICENSE", name: "LICENSE", type: "file" },
    ]);
    const made = [];
    const bubble_ = (role, text) => {
      const made_ = bubble(role, text);
      made.push(made_.el);
      return made_;
    };
    const chips = (el) => [...el.querySelectorAll(".prose-chip")].map((c) => [c.textContent, c.dataset.kind]);
    const result = {};
    result.folder = chips(bubble_("assistant", "Open src/lib now.").body);
    result.common = chips(bubble_("assistant", "Run the tests, read the LICENSE; docs and tests.").body);
    result.code = chips(bubble_("assistant", "Use `tests` and `LICENSE`.").body);
    const user = bubble_("user", "/review-pr src/app.py src/lib").body;
    const preview = document.getElementById("page-preview");
    renderAnswer(preview, "/review-pr src/app.py src/lib");
    refreshVisualMarkers();
    result.user = chips(user);
    result.preview = chips(preview);
    // A streamed answer ends with the same chips as the same answer rendered at once.
    const live = bubble_("assistant", "").body;
    for (let n = 1; n <= answer.length; n += 7) live.rawAnswer = renderAnswer(live, answer.slice(0, n));
    live.rawAnswer = renderAnswer(live, answer);
    result.streamed = chips(live);
    result.whole = chips(bubble_("assistant", answer).body);
    fileTree.cache.delete("extra");
    made.forEach((el) => el.remove());
    return result;
  }, ANSWER);
  assert.deepEqual(out.folder, [["src/lib", "folder"]], "a folder path is a folder chip");
  assert.deepEqual(out.common, [], "a common word equal to a root entry is never chipped");
  assert.deepEqual(out.code, [["tests", "folder"], ["LICENSE", "file"]], "a bare name as whole inline code is chipped");
  assert.deepEqual(out.user, [], "a user bubble gets no chips");
  assert.deepEqual(out.preview, [], "the page preview gets no chips");
  assert.ok(out.whole.length >= 5);
  assert.deepEqual(out.streamed, out.whole, "streaming ends with the same chips");
}

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    const errors = [];
    const patches = [];
    let stored = {};
    let resourcesFail = false;
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("dialog", (d) => {
      errors.push("dialog " + d.message());
      d.dismiss();
    });
    const origin = "http://panel.test";
    await page.route(origin + "/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      if (p === "/v1/ui-state") {
        if (route.request().method() === "PATCH") {
          const { values } = route.request().postDataJSON();
          patches.push(values);
          stored = { ...stored, ...values };
          return route.fulfill({ json: { version: 1, values: stored } });
        }
        return route.fulfill({ json: { version: 1, values: stored, limits: {} } });
      }
      if (p.startsWith("/v1/")) {
        let data = {};
        if (p === "/v1/projects")
          data = { projects: ["sem-projeto", "project-a"], details: { "project-a": { label: "Project Alpha" } } };
        else if (p === "/v1/models")
          data = {
            models: [{ id: "fixture", name: "Fixture", backend: "local", efforts: ["low"] }],
            providers: { local: true },
            uploads_enabled: false,
          };
        else if (p === "/v1/version") data = { version: "test", build: "markers" };
        else if (p === "/v1/resources" && resourcesFail) return route.fulfill({ status: 500, json: { error: "down" } });
        else if (p === "/v1/resources") data = { items: RESOURCES, warnings: [] };
        else if (p === "/v1/conversations")
          data = { conversations: [{ id: "c-project", title: "In a project", project: "project-a", state: "completed" }] };
        else if (p.startsWith("/v1/conversations/")) data = { turns: [turn] };
        else if (p.endsWith("/events"))
          return route.fulfill({
            contentType: "text/event-stream",
            body: events.map((e) => "data: " + JSON.stringify(e) + "\n\n").join(""),
          });
        else if (p.startsWith("/v1/jobs/")) data = turn;
        return route.fulfill({ json: data });
      }
      const file = p === "/" ? "index.html" : p.slice(1);
      return route.fulfill({
        body: await fs.readFile(
          path.join(__dirname, file.startsWith("assets/") ? "../harness_ui" : "../agent_service", file),
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
    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
    await page.goto(origin);
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.locator(".conversation-row", { hasText: "In a project" }).locator("button").first().click();
    await page.waitForFunction(() => document.querySelectorAll("#messages .message-activity").length === 1);
    const steps = page.locator("#messages .message-activity").first();
    await steps.locator("summary").click();
    await steps.locator("li").first().waitFor();
    // The catalog and the project file tree are what the UI already loaded.
    await page.waitForFunction(() => document.querySelector('#workspace-resources [data-resource-id="skill-review-pr"]'));
    await page.evaluate(() => {
      fileTree.cache.set("home\0", [{ path: "src/app.py", name: "app.py", type: "file" }]);
      refreshVisualMarkers();
    });

    // 1. Run steps: icon id + label per tool, text content unchanged.
    const row = (text) => steps.locator("li", { hasText: text }).first();
    const expectRow = async (text, icon, category) => {
      const li = steps.locator("li", { has: page.locator('[data-testid="step-marker"]') }).filter({ hasText: text }).first();
      assert.equal(await li.count(), 1, "marker row for " + text);
      assert.equal(await li.locator("use").first().getAttribute("href"), "/assets/icons.svg#" + icon, text);
      assert.equal(await li.locator("svg").first().getAttribute("aria-hidden"), "true");
      assert.equal(await li.getAttribute("aria-label"), (await li.innerText()).trim(), "aria-label equals the row text");
      assert.equal(await li.locator('[data-testid="step-marker"]').getAttribute("data-category"), category, text);
      return li;
    };
    await expectRow("Read src/app.py", "file-text", "read");
    await expectRow("Edited src/app.py", "pencil", "edit");
    await expectRow("Ran git status", "terminal-2", "run");
    await expectRow("Searched TODO", "search", "search");
    await expectRow("Ran ls -la", "terminal-2", "run");
    await expectRow("Edited notes.txt", "pencil", "edit");
    await expectRow("Listed docs", "folder", "list");
    await expectRow("create_issue", "plug", "mcp");
    await expectRow("Searched the web", "world", "web");
    const planRow = steps.locator('[data-category="plan"]');
    assert.equal(await planRow.count(), 1);
    assert.equal(await planRow.locator("use").getAttribute("href"), "/assets/icons.svg#list-check");

    // Skill and agent chips: known names link to the catalog entry, unknown ones stay plain.
    const known = await expectRow("Using skill review-pr", "cube", "skill");
    const knownLink = known.locator("a.step-chip");
    assert.equal(await knownLink.count(), 1, "a known skill links to its catalog entry");
    assert.equal(await knownLink.getAttribute("data-resource-id"), "skill-review-pr");
    const codex = await expectRow("Using skill deploy", "cube", "skill");
    assert.equal(await codex.locator("a.step-chip").count(), 1, "a SKILL.md read by a shell command is a skill step");
    const unknown = await expectRow("Using skill mystery-skill", "cube", "skill");
    assert.equal(await unknown.locator("a").count(), 0, "an unknown skill is a plain chip");
    const agent = await expectRow("Delegated to code-reviewer", "robot", "agent");
    assert.equal(await agent.locator("a.step-chip").getAttribute("data-resource-id"), "agent-code-reviewer");
    assert.equal(await steps.locator("img").count(), 0);
    const hostile = steps.locator("li", { hasText: "<img src=x" });
    assert.equal(await hostile.count(), 0, "a hostile skill name is refused, never displayed as a skill");

    // The skill name arrived only on tool_end: the row is a skill step, not the Read it started as.
    const late = await expectRow("Using skill late-skill", "cube", "skill");
    assert.equal(await late.locator("a.step-chip").getAttribute("data-resource-id"), "skill-late-skill");

    // The link opens the side panel on the catalog entry.
    await knownLink.click();
    await page.waitForFunction(() => !document.getElementById("activity-panel").hidden);
    assert.equal(
      await page.evaluate(() => document.activeElement?.dataset?.resourceId),
      "skill-review-pr",
      "the catalog entry takes focus",
    );

    // 3. Contrast in every palette, text and icon.
    const results = {};
    for (const palette of PALETTES) {
      await setPalette(page, palette, DARK.includes(palette));
      const colors = await page.evaluate(() => {
        const read = (el, prop) => getComputedStyle(el)[prop];
        const chip = document.querySelector("#messages .message-activity a.step-chip"),
          plain = document.querySelector('#messages .message-activity span.step-chip[data-category="run"]');
        return {
          plain: [read(plain, "color"), read(plain, "backgroundColor")],
          link: [read(chip, "color"), read(chip, "backgroundColor")],
          icon: [read(plain.querySelector("svg"), "color"), read(plain, "backgroundColor")],
        };
      });
      for (const [name, [fg, bg]] of Object.entries(colors)) {
        const ratio = contrast(rgb(fg), rgb(bg));
        results[palette + "/" + name] = Math.round(ratio * 100) / 100;
        assert.ok(ratio >= (name === "icon" ? 3 : 4.5), palette + " " + name + " contrast " + ratio.toFixed(2) + " (" + fg + " on " + bg + ")");
      }
    }
    console.log("CONTRAST", JSON.stringify(results));
    await setPalette(page, "paper", false);

    // 3b. Prose chips in the assistant answer (part 2).
    await proseChipCases(page);
    await proseGuardCases(page);

    // 3c. A failed or switched catalog drops every link; a reload brings them back.
    const links = 'a.step-chip, a.prose-chip';
    assert.ok((await page.locator(links).count()) > 0);
    resourcesFail = true;
    await page.evaluate(() => refreshWorkspaceResources());
    await page.waitForFunction((sel) => !document.querySelector(sel), links);
    assert.ok((await page.locator('[data-testid="step-marker"]').count()) > 5, "plain pills stay");
    resourcesFail = false;
    await page.evaluate(() => refreshWorkspaceResources());
    await page.waitForFunction((sel) => document.querySelector(sel), links);

    // 4. Appearance toggle: on by default, off removes every marker, persists in the store.
    await page.evaluate(() => openSettings("appearance"));
    const toggle = page.getByTestId("visual-markers-toggle");
    await toggle.waitFor();
    assert.equal(await toggle.isChecked(), true, "markers are on by default");
    const plainTexts = await steps.locator("li").allInnerTexts();
    await toggle.uncheck();
    await page.waitForFunction(() => !document.querySelector('[data-testid="step-marker"]'));
    assert.deepEqual(await steps.locator("li").allInnerTexts(), plainTexts, "rows fall back to the same plain text");
    assert.equal(await steps.locator("li svg, li a").count(), 0);
    assert.equal(await page.evaluate(() => [...document.querySelectorAll("#messages .message-activity li")].some((li) => li.hasAttribute("aria-label"))), false);
    await page.waitForFunction(() => window.HarnessPrefs.get("visual_markers", true) === false);
    await page.waitForTimeout(1500);
    assert.ok(patches.some((values) => values.visual_markers === false), "the store PATCH carries visual_markers:false");

    // A reload keeps it off; turning it back on restores markers without a reload.
    await page.reload();
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.locator(".conversation-row", { hasText: "In a project" }).locator("button").first().click();
    await page.waitForFunction(() => document.querySelectorAll("#messages .message-activity").length === 1);
    await page.locator("#messages .message-activity summary").first().click();
    await page.locator("#messages .message-activity li").first().waitFor();
    assert.equal(await page.locator('[data-testid="step-marker"]').count(), 0, "off survives a reload");
    await page.evaluate(() => openSettings("appearance"));
    assert.equal(await page.getByTestId("visual-markers-toggle").isChecked(), false);
    await page.getByTestId("visual-markers-toggle").check();
    await page.waitForFunction(() => document.querySelectorAll('[data-testid="step-marker"]').length > 5);
    await page.waitForTimeout(1500);
    assert.ok(patches.some((values) => values.visual_markers === true), "turning it on is stored");

    assert.deepEqual(errors, []);
    console.log("PASS visual-markers");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
