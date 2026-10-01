const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const VIEWPORTS = [
  { width: 1440, height: 900 },
  { width: 1280, height: 720 },
  { width: 1024, height: 768 },
  { width: 400, height: 812 },
];
const THEMES = ["violet-bordeaux", "porcelain", "mineral-rose", "amethyst", "petroleum", "arizona"];
const STATES = [
  "console-closed",
  "console-open",
  "tour-step",
  "plan-card-review",
  "plan-card-auto",
  "publish-gate",
  "slash-palette",
  "workflow-palette",
  "plan-review-selector",
];

const PLAN_GATE = {
  gate_id: "plan-gate",
  kind: "maestro_plan",
  state: "pending",
  plan: {
    steps: [
      { role: "planner", backend: "codex", model: "fixture", effort: "medium", task: "Inspect the compact workspace and preserve the draft." },
      { role: "reviewer", backend: "codex", model: "fixture", effort: "high", task: "Review accessibility, geometry, and recovery evidence." },
    ],
  },
};
const PUBLISH_GATE = {
  gate_id: "publish-gate",
  kind: "publish",
  state: "pending",
  publish: true,
  effect_id: "effect-1",
  enforcement: "mediated",
  operation: "create release note",
  integration: "fixture",
  destination: "project/release",
  arguments: { title: "Compact workspace", visibility: "private" },
  arguments_digest: "sha256:fixture-arguments",
  artifact_preview: "A compact, reviewable release note.",
  artifact_digest: "sha256:fixture-artifact",
};

function outputDirectory() {
  const explicit = process.env.VISUAL_OUTPUT_DIR;
  const directory = explicit ? path.resolve(explicit) : fs.mkdtempSync(path.join(os.tmpdir(), "tail-harness-visual-"));
  fs.mkdirSync(directory, { recursive: true });
  return { directory, temporary: !explicit };
}

function contentType(file) {
  if (file.endsWith(".js")) return "text/javascript";
  if (file.endsWith(".css")) return "text/css";
  if (file.endsWith(".svg")) return "image/svg+xml";
  return "text/html";
}

async function routeVisual(route, options = {}) {
  const url = new URL(route.request().url());
  const method = route.request().method();
  if (!url.pathname.startsWith("/v1/")) {
    const file = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
    const root = file.startsWith("assets/") ? "tail_ui" : "agent_service";
    return route.fulfill({
      body: fs.readFileSync(path.join(__dirname, "..", "..", root, file)),
      contentType: contentType(file),
    });
  }
  let data = {};
  if (url.pathname === "/v1/projects") data = { projects: ["sem-projeto"], details: { "sem-projeto": { label: "Compact workspace" } } };
  else if (url.pathname === "/v1/models") data = { models: [{ id: "fixture", name: "Fixture", backend: "codex", efforts: ["medium", "high"], execution_modes: ["native", "scoped"] }], providers: { codex: true }, uploads_enabled: true };
  else if (url.pathname === "/v1/conversations") data = { conversations: [{ id: "conversation-a", title: "Compact workspace review with a deliberately long title", project: "sem-projeto", state: "running", last_job_id: "run-a", execution: { backend: "codex", model: "fixture" }, updated_at: 1 }] };
  else if (url.pathname === "/v1/conversations/conversation-a") data = {
    title: "Compact workspace review with a deliberately long title",
    execution_mode: "scoped",
    turns: [{ id: "run-a", project: "sem-projeto", state: "running", request: { backend: "codex", model: "fixture", prompt: "Review the compact workspace", access_mode: "ask", effort: "medium" }, result: { answer: "The compact workspace is ready for review." }, gates: [PLAN_GATE, PUBLISH_GATE] }],
  };
  else if (url.pathname === "/v1/activity") data = {
    counts: { running: 1, queued: 1, needs_you: 0 },
    jobs: [
      { job_id: "run-a", conversation_id: "conversation-a", project_id: "sem-projeto", state: "running", title: "Compact visual verification", backend: "codex", model: "fixture" },
      { job_id: "run-b", conversation_id: "conversation-a", project_id: "sem-projeto", state: "queued", title: "Queued contrast audit", wait_reason: "Provider busy", backend: "codex", model: "fixture" },
    ],
    providers: [{ backend: "codex", model: "fixture", state: "ready", running: 1, queued: 1 }],
    needs_you: [],
  };
  else if (url.pathname === "/v1/jobs/run-a") data = { id: "run-a", project: "sem-projeto", state: "running", request: { backend: "codex", model: "fixture" }, result: {}, gates: [PLAN_GATE, PUBLISH_GATE] };
  else if (url.pathname === "/v1/jobs/run-a/spans") data = { spans: [{ span_id: "span-a", trace_id: "run-a", parent_id: null, kind: "invoke_agent", name: "Visual verifier", start_ts: Date.now() / 1000 - 4, end_ts: null, status: "unset", attrs: { "gen_ai.request.model": "fixture", "gen_ai.usage.input_tokens": 23, "harness.outcome": "pending" }, events: [] }] };
  else if (url.pathname === "/v1/resources") data = { items: [
    { id: "agent-reviewer", revision: "1", kind: "agent", name: "reviewer", description: "Review accessibility and geometry", scope: "project", origin: "Codex", selectable: true },
    { id: "skill-ponytail", revision: "1", kind: "skill", name: "ponytail", description: "Prefer the smallest complete implementation", scope: "project", origin: "Codex", selectable: true },
    { id: "catalog/demo/commands/build.md", revision: "1", kind: "command", name: "build", description: "Build the project", scope: "catalog", origin: "demo", catalog_commit: "0123456789abcdef0123456789abcdef01234567", catalog_pinned: true, catalog_dirty: false, selectable: true },
    { id: "project/sem-projeto/workflows/release-review.json", revision: "1", kind: "workflow", name: "release-review", description: "Review a release in two sequential steps", group: "Workflows", scope: "project", origin: "project", selectable: true },
  ], warnings: [] };
  else if (url.pathname === "/v1/project-files") {
    const view = url.searchParams.get("view"), rootId = url.searchParams.get("root_id");
    const roots = options.emptyFiles ? [] : [{ id: view === "tree" ? "home" : "project-root", label: "Compact project", path: "workspace/compact-project-with-a-long-name" }];
    const entries = options.emptyFiles || !rootId ? [] : [
      { path: "compact-layout-review.md", name: "compact-layout-review.md", type: "file", status: "M" },
      { path: "this-is-a-deliberately-long-resource-name-to-test-truncation.json", name: "this-is-a-deliberately-long-resource-name-to-test-truncation.json", type: "file" },
      { path: "src", name: "src", type: "directory" },
    ];
    data = { state: "ready", roots, root_id: rootId || undefined, path: "", entries, limited: false, can_authorize: false };
  }
  else if (url.pathname === "/v1/catalog") data = { agents: [], skills: [], warnings: [] };
  else if (url.pathname === "/v1/usage") data = { available: false };
  else if (url.pathname === "/v1/version") data = { version: "0.13.1", build: "visual-fixture" };
  else if (url.pathname.startsWith("/v1/approvals/") && method === "POST") data = { state: "resolved", choice: route.request().postDataJSON()?.choice };
  return route.fulfill({ json: data });
}

async function mountVisual(page, options = {}) {
  await page.addInitScript(() => {
    localStorage.setItem("tail-harness-tour-seen", "0.13.1");
    localStorage.setItem("activity-open", "1");
  });
  await page.route("http://visual.test/**", route => routeVisual(route, options));
  await page.goto("http://visual.test");
  await page.locator("#startup-gate").waitFor({ state: "hidden" });
  await page.evaluate(() => setPanelOpen(true, false));
  await page.evaluate(() => document.querySelector("#history .conversation-row button")?.click());
  await page.waitForFunction(() => document.getElementById("conversation-title")?.textContent.includes("Compact workspace review"));
  await page.addStyleTag({ content: "html,*,*::before,*::after{animation-duration:0s!important;transition-duration:0s!important;scroll-behavior:auto!important;caret-color:transparent!important}" });
  await page.waitForFunction(() => document.querySelectorAll(".maestro-plan-card,.publish-gate-card").length === 2);
  await page.waitForFunction(() => document.getElementById("workspace-resources-count")?.textContent === "4");
  if (options.emptyFiles) await page.waitForFunction(() => /No project root is authorized/i.test(document.getElementById("authorized-project-roots")?.textContent || ""));
  else await page.waitForFunction(() => document.querySelectorAll("#authorized-project-roots li").length === 3);
}

async function resetState(page) {
  await page.evaluate(() => {
    window.tailHarnessTour?.stop(false);
    const settings = document.getElementById("settings-dialog");
    if (settings?.open) settings.close();
    const menu = document.getElementById("resource-menu");
    if (menu?.matches(":popover-open")) menu.hidePopover();
    for (const card of document.querySelectorAll(".maestro-plan-card,.publish-gate-card")) card.hidden = true;
    const prompt = document.getElementById("prompt");
    prompt.value = "";
    prompt.dispatchEvent(new Event("input", { bubbles: true }));
  });
  if (await page.locator("#run-console").isVisible()) await page.keyboard.press("Control+j");
}

async function selectState(page, state) {
  await resetState(page);
  if (state === "console-open") {
    await page.keyboard.press("Control+j");
    await page.locator("#run-console").waitFor({ state: "visible" });
  } else if (state === "tour-step") {
    await page.evaluate(() => window.tailHarnessTour.start());
    await page.locator("#tour-card").waitFor({ state: "visible" });
  } else if (state === "plan-card-review") {
    await page.locator(".maestro-plan-card").evaluate(node => { node.hidden = false; node.scrollIntoView({ block: "center", behavior: "instant" }); });
  } else if (state === "plan-card-auto") {
    await page.evaluate(steps => showMaestroPlan({ steps, state: "running" }), PLAN_GATE.plan.steps);
    await page.locator(".maestro-plan-card").evaluate(node => node.scrollIntoView({ block: "center", behavior: "instant" }));
  } else if (state === "publish-gate") {
    await page.locator(".publish-gate-card").evaluate(node => { node.hidden = false; node.scrollIntoView({ block: "center", behavior: "instant" }); });
  } else if (state === "slash-palette") {
    await page.locator("#prompt").fill("/");
    await page.locator('#resource-menu [role="option"]').first().waitFor({ state: "visible" });
  } else if (state === "workflow-palette") {
    await page.locator("#prompt").fill("/release");
    await page.locator('#resource-menu [data-resource-kind="workflow"]').waitFor({ state: "visible" });
  } else if (state === "plan-review-selector") {
    await page.locator("#settings").click();
    await page.locator('[data-settings="agents"]').click();
    await page.locator("#maestro-plan-policy").waitFor({ state: "visible" });
  }
  await page.waitForTimeout(20);
}

async function geometry(page, state) {
  return page.evaluate(currentState => {
    const visible = node => {
      if (!node || !node.checkVisibility()) return false;
      const r = node.getBoundingClientRect();
      if (!(r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth)) return false;
      const center = { x: r.left + r.width / 2, y: r.top + r.height / 2 };
      for (let parent = node.parentElement; parent; parent = parent.parentElement) {
        const style = getComputedStyle(parent);
        if (![style.overflow, style.overflowX, style.overflowY].some(value => ["auto", "clip", "hidden", "scroll"].includes(value))) continue;
        const bounds = parent.getBoundingClientRect();
        if (center.x < bounds.left || center.x > bounds.right || center.y < bounds.top || center.y > bounds.bottom) return false;
      }
      return true;
    };
    const selectors = currentState === "tour-step"
      ? ["#tour-card", "#tour-skip", "#tour-next"]
      : ["plan-card-review", "plan-card-auto"].includes(currentState)
        ? [".maestro-plan-card", '.maestro-plan-card button']
        : currentState === "publish-gate"
          ? [".publish-gate-card", ".publish-gate-card button"]
          : ["slash-palette", "workflow-palette"].includes(currentState)
            ? ["#prompt", "#resource-menu", '#resource-menu [role="option"]']
            : currentState === "plan-review-selector"
              ? ["#settings-dialog", '[data-settings="agents"]', "#maestro-plan-policy", "#maestro-plan-policy-help"]
            : currentState === "console-open"
              ? ["#run-console", "#run-tab-pipeline", ".run-console-close", ".run-pipeline-summary", ".run-pipeline-actions button"].concat(innerWidth > 700 ? ["#run-console-resize", "#run-console-maximize", "#prompt", ".composer-submit button:not([hidden])"] : [])
              : ["#menu", "#panel-toggle", "#model-trigger", "#run-status-toggle"];
    const failures = [];
    for (const selector of selectors) {
      const nodes = [...document.querySelectorAll(selector)].filter(visible);
      if (!nodes.length) { failures.push(selector + " is not visible"); continue; }
      for (const node of nodes) {
        const r = node.getBoundingClientRect();
        if (r.left < -1 || r.top < -1 || r.right > innerWidth + 1 || r.bottom > innerHeight + 1) failures.push(`${selector} leaves viewport (${Math.round(r.left)},${Math.round(r.top)} ${Math.round(r.width)}x${Math.round(r.height)})`);
        const x = Math.max(0, Math.min(innerWidth - 1, r.left + r.width / 2));
        const y = Math.max(0, Math.min(innerHeight - 1, r.top + r.height / 2));
        const hit = document.elementFromPoint(x, y);
        const reachable = hit && (hit === node || node.contains(hit) || (node.matches(":disabled") && hit.contains(node)));
        if (!reachable) failures.push(selector + " is covered at its center by " + (hit?.id ? "#" + hit.id : hit?.className || hit?.tagName || "nothing"));
      }
    }
    if (innerWidth >= 1280) {
      const header = document.querySelector("main > header"), bounds = header.getBoundingClientRect();
      for (const node of header.children) {
        if (!visible(node)) continue;
        const rect = node.getBoundingClientRect();
        if (rect.top < bounds.top - 1 || rect.bottom > bounds.bottom + 1)
          failures.push(`#${node.id || node.tagName.toLowerCase()} leaves the conversation header (${Math.round(rect.top)}..${Math.round(rect.bottom)} outside ${Math.round(bounds.top)}..${Math.round(bounds.bottom)})`);
      }
    }
    for (const body of document.querySelectorAll(".workspace-section[open] > .workspace-section-body")) {
      if (!visible(body) || body.scrollTop > 1) continue;
      const bounds = body.getBoundingClientRect();
      const escaped = [...body.querySelectorAll("*")].find(node => {
        if (!visible(node)) return false;
        const rect = node.getBoundingClientRect();
        return rect.width > 2 && rect.height > 2 && rect.top < bounds.top - 1;
      });
      if (escaped) {
        const rect = escaped.getBoundingClientRect();
        failures.push(`#${body.id} content escapes above its scrollport (${escaped.id || escaped.className || escaped.tagName}: ${Math.round(rect.top)} < ${Math.round(bounds.top)})`);
      }
    }
    const targetFailures = [];
    for (const node of document.querySelectorAll("button,[role=tab],summary")) {
      if (!visible(node)) continue;
      const r = node.getBoundingClientRect();
      if (r.width < 24 || r.height < 24) targetFailures.push((node.id ? "#" + node.id : node.textContent.trim().slice(0, 24)) + ` ${r.width.toFixed(0)}x${r.height.toFixed(0)}`);
    }
    const overflow = [];
    for (const node of document.querySelectorAll("#conversation-title,.state-pill,.backend-chip,.header-chip,.workspace-section summary,.workspace-item-name")) {
      if (!visible(node)) continue;
      const style = getComputedStyle(node);
      const uncontained = node.scrollWidth > node.clientWidth + 1 && style.overflowX === "visible" && !["normal", "pre-wrap"].includes(style.whiteSpace);
      const r = node.getBoundingClientRect();
      if (uncontained || r.left < -1 || r.right > innerWidth + 1) overflow.push(node.id || node.className || node.tagName);
    }
    let stepCard = null;
    if (currentState === "console-open") {
      const body = document.getElementById("run-console-panel"), row = body.querySelector(".run-span-row"), previous = body.scrollTop;
      const initial = row.getBoundingClientRect();
      body.scrollTop = Math.max(0, row.offsetTop + row.offsetHeight - body.clientHeight);
      const rect = row.getBoundingClientRect(), bounds = body.getBoundingClientRect();
      const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
      stepCard = { initialTop: initial.top, initialBottom: initial.bottom, top: rect.top, bottom: rect.bottom, bodyTop: bounds.top, bodyBottom: bounds.bottom, height: rect.height, scrollOffset: body.scrollTop };
      if (rect.top < bounds.top - 1 || rect.bottom > bounds.bottom + 1 || rect.left < -1 || rect.right > innerWidth + 1) failures.push(`.run-span-row is not fully reachable inside console scroll (${Math.round(rect.left)},${Math.round(rect.top)} ${Math.round(rect.width)}x${Math.round(rect.height)})`);
      if (!hit || !(hit === row || row.contains(hit))) failures.push(".run-span-row is covered after internal scroll");
      body.scrollTop = previous;
    }
    return {
      scrollWidth: document.documentElement.scrollWidth,
      viewportWidth: innerWidth,
      failures,
      targetFailures,
      overflow,
      stepCard,
      console: visible(document.getElementById("run-console")) ? document.getElementById("run-console").getBoundingClientRect().toJSON() : null,
    };
  }, state);
}

// WCAG 1.4.3 scan for visible text. Large text uses 3:1; normal text uses 4.5:1.
function contrastScan() {
  const canvas = document.createElement("canvas"); canvas.width = canvas.height = 1;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  const parse = value => {
    const match = value.match(/^rgba?\(([^)]+)\)$/);
    if (match) { const p = match[1].split(/[ ,/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; }
    ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = value; ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255];
  };
  const over = (top, bottom) => [0, 1, 2].map(i => top[i] * top[3] + bottom[i] * (1 - top[3])).concat(1);
  const lum = color => [0.2126, 0.7152, 0.0722].reduce((sum, weight, i) => { const value = color[i] / 255; return sum + weight * (value <= .03928 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4); }, 0);
  const ratio = (a, b) => { const values = [lum(a), lum(b)].sort((x, y) => y - x); return (values[0] + .05) / (values[1] + .05); };
  const background = element => {
    const layers = [];
    for (let current = element; current; current = current.parentElement) { const color = parse(getComputedStyle(current).backgroundColor); if (color[3] > 0) layers.push(color); if (color[3] >= 1) break; }
    return layers.reverse().reduce((acc, color) => over(color, acc), [255, 255, 255, 1]);
  };
  const failures = [], seen = new Set(), walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode, element = node.parentElement;
    if (!node.textContent.trim() || !element || seen.has(element) || element.closest("[hidden],[aria-hidden=true],.visually-hidden,option,select,:disabled")) continue;
    seen.add(element);
    const rect = element.getBoundingClientRect();
    if (!rect.width || !rect.height || rect.bottom < 0 || rect.top > innerHeight) continue;
    const style = getComputedStyle(element);
    if (style.visibility !== "visible" || Number(style.opacity) < .1) continue;
    const bg = background(element), measured = ratio(over(parse(style.color), bg), bg), size = parseFloat(style.fontSize);
    const required = size >= 24 || (Number(style.fontWeight) >= 700 && size >= 18.66) ? 3 : 4.5;
    if (measured < required) failures.push(`${element.tagName.toLowerCase()}${element.id ? "#" + element.id : ""} '${node.textContent.trim().slice(0, 28)}' ${measured.toFixed(2)}<${required}`);
  }
  return failures;
}

module.exports = { VIEWPORTS, THEMES, STATES, outputDirectory, mountVisual, selectState, resetState, geometry, contrastScan };
