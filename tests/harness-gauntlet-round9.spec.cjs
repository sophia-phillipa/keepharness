const { executionModes } = require("./model-fixture.cjs");
// Round-nine synthetic browser regressions: actual rendering, keyboard and network boundaries.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
async function capture(page, name) {
  if (!process.env.EVAL_OUTPUT) return;
  const folder = path.join(process.env.EVAL_OUTPUT, "round9");
  await fs.mkdir(folder, { recursive: true });
  await page.screenshot({ path: path.join(folder, name + ".png") });
}
const { mount, run, span } = require("./run-console-fixture.cjs");
const resource = {
  id: "project/p/reviewer",
  resource_id: "project/p/reviewer",
  revision: "1",
  name: "reviewer",
  kind: "agent",
  mode: "delegated",
  scope: "project",
  origin: "codex",
  selectable: true,
};
const plan = {
  steps: [
    "Planner",
    "Accessibility and interaction reviewer",
    "Implementation and integration engineer",
    "Final reviewer",
  ].map((role) => ({
    role,
    backend: "codex",
    model: "synthetic-model-family-with-extended-context-and-reasoning",
    effort: "configured",
    task: "Review synthetic report",
    reason: "Check evidence",
  })),
};
async function fixture(browser, width = 1024, height = 768) {
  const page = await browser.newPage({ viewport: { width, height } });
  page.setDefaultTimeout(4000);
  const state = {
    spanVersion: 0,
    failNavigation: false,
    emptySpans: false,
    resourceDelay: null,
    rootDelay: null,
    roots: false,
    usage: null,
    gate: null,
    resources: null,
    maestro: false,
    completed: false,
    delay: null,
    running: false,
    plan: false,
    checkpointCount: 0,
    inbox: [],
    jobs: [],
    conflict: false,
    posts: [],
    approvalResponse: null,
    successfulSend: false,
    missingSource: false,
    pendingEvents: false,
  };
  const turn = (id) => ({
    id: id + "-job",
    project: id === "b" ? "q" : "p",
    state:
      state.terminalState ||
      (state.running ? "running" : state.completed ? "completed" : "failed"),
    workflow_checkpoint: true,
    workflow_completed_steps: state.checkpointCount,
    gates: state.gate
      ? [state.gate]
      : state.plan
        ? [{ gate_id: "plan", kind: "maestro_plan", state: "pending", plan }]
        : [],
    request: {
      prompt: "Review " + id,
      backend: "codex",
      model: "fixture-model",
      effort: "configured",
      execution_mode: "native",
    },
    result: state.running
      ? null
      : {
          answer: "Synthetic answer",
          ...(state.completed ? {} : { error: "fixture" }),
          ...(state.usage ? { metrics: state.usage } : {}),
        },
  });
  await mount(page, async (url, request) => {
    if (
      request.method() === "POST" &&
      state.missingSource &&
      url.pathname.endsWith("/resume")
    )
      return { status: 422, json: { code: "workflow_source_unavailable" } };
    if (
      request.method() === "POST" &&
      state.successfulSend &&
      url.pathname === "/v1/jobs"
    ) {
      state.posts.push({ path: url.pathname, data: request.postDataJSON() });
      return { json: { job_id: "child" } };
    }
    if (request.method() === "POST") {
      if (state.conflict)
        return { status: 409, json: { code: "gate_already_resolved" } };
      state.posts.push({ path: url.pathname, data: request.postDataJSON() });
      if (state.approvalResponse) await state.approvalResponse;
      return {
        status: state.approvalResponse ? 200 : 422,
        json: state.approvalResponse
          ? { resolved: true }
          : { error: "synthetic_stop" },
      };
    }
    if (url.pathname === "/v1/projects")
      return {
        json: {
          projects: ["p", "q"],
          details: {
            p: { label: "Synthetic review project" },
            q: { label: "Project Q" },
          },
        },
      };
    if (url.pathname === "/v1/models")
      return {
        json: {
          models: [
            {
              id: "fixture-model",
              backend: "codex",
              execution_modes: executionModes("codex"),
              efforts: ["configured"],
            },
            {
              id: plan.steps[0].model,
              backend: "codex",
              execution_modes: executionModes("codex"),
              efforts: ["configured"],
            },
          ],
          maestro: state.maestro,
          providers: { codex: true },
        },
      };
    if (
      url.pathname === "/v1/resources" &&
      url.searchParams.get("project_id") === "p" &&
      state.resourceDelay
    )
      await state.resourceDelay;
    if (url.pathname === "/v1/resources")
      return {
        json: {
          items: state.resources || [
            {
              ...resource,
              name: "reviewer-" + url.searchParams.get("project_id"),
            },
          ],
          warnings: [],
        },
      };
    if (url.pathname === "/v1/activity")
      return {
        json: {
          counts: { running: 1 },
          jobs: [
            ...state.jobs,
            {
              ...run,
              job_id: "a-job",
              conversation_id: "a",
              project_id: "p",
              backend: "codex",
              wait_reason: "human_approval",
            },
          ],
          needs_you: state.inbox.length
            ? state.inbox
            : state.plan
              ? [
                  {
                    job_id: "a-job",
                    conversation_id: "a",
                    gate_id: "plan",
                    kind: "maestro_plan",
                    options: [
                      { id: "approve", label: "Approve" },
                      { id: "deny", label: "Discard" },
                    ],
                    plan,
                  },
                ]
              : [],
          providers: [],
        },
      };
    if (url.pathname === "/v1/conversations")
      return {
        json: {
          conversations: ["a", "b"].map((id) => ({
            id,
            title: id.toUpperCase() + " report",
            state: "failed",
            backend: "codex",
            model: "fixture-model",
            project: "p",
            last_job_id: id + "-job",
          })),
        },
      };
    if (/^\/v1\/conversations\/(a|b|child)$/.test(url.pathname)) {
      if (state.failNavigation && url.pathname.endsWith("/b"))
        return { status: 503, json: { code: "unavailable" } };
      if (state.delay) await state.delay;
      const id = url.pathname.split("/").at(-1);
      return {
        json: {
          title: id.toUpperCase() + " report",
          execution_mode: "native",
          turns: [turn(id)],
        },
      };
    }
    if (url.pathname.endsWith("/cancel")) {
      state.running = false;
      return { json: { cancelled: true } };
    }
    if (/^\/v1\/jobs\/.*-job$/.test(url.pathname))
      return { json: turn(url.pathname.split("/").at(-1).replace("-job", "")) };
    if (url.pathname.endsWith("/spans") && state.emptySpans)
      return { json: { spans: [] } };
    if (url.pathname.endsWith("/spans"))
      return {
        json: {
          spans: [
            "Planner",
            "Accessibility and interaction reviewer",
            "Implementation and integration engineer",
            "Final reviewer",
            "Root",
            "Gate",
          ].map((name, i) => ({
            ...span,
            start_ts: 10 + i,
            end_ts: 20 + i,
            name: name + state.spanVersion,
            span_id: "span-" + i,
            attrs: {
              ...span.attrs,
              "gen_ai.provider.name": "codex",
              "gen_ai.request.model": "fixture-model",
              effort: "medium",
              enforcement: "unenforced",
              advisory: true,
            },
          })),
        },
      };
    if (
      url.pathname === "/v1/project-files" &&
      url.searchParams.get("project_id") === "p" &&
      state.rootDelay
    )
      await state.rootDelay;
    if (
      url.pathname === "/v1/project-files" &&
      url.searchParams.get("view") === "authorized"
    )
      return {
        json: {
          state: "ready",
          roots: state.roots
            ? [
                {
                  id: "root",
                  path: "/synthetic/" + url.searchParams.get("project_id"),
                },
              ]
            : [],
        },
      };
    if (url.pathname === "/v1/project-files")
      return {
        json: {
          state: "ready",
          root_id: "home",
          path: url.searchParams.get("path") || "",
          roots: [{ id: "home", label: "Local Folders" }],
          entries: url.searchParams.get("path")
            ? []
            : [{ name: "examples", path: "examples", type: "directory" }],
        },
      };
    if (url.pathname.endsWith("/events") && state.pendingEvents)
      await new Promise(() => {});
    if (url.pathname.endsWith("/events"))
      return { body: "", contentType: "text/event-stream" };
  });
  async function open(id) {
    if (width <= 620) await page.locator("#menu").click();
    await page
      .locator("#sidebar .conversation-row > button")
      .filter({ hasText: id.toUpperCase() + " report" })
      .click();
    await page.waitForFunction(
      (title) =>
        !loading &&
        document.querySelector("#conversation-title").textContent === title,
      id.toUpperCase() + " report",
    );
  }
  return { page, open, state };
}
const hit = (locator) =>
  locator.evaluate((node) => {
    const r = node.getBoundingClientRect();
    const h = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    return h === node || node.contains(h);
  });
const settle = (page) =>
  page.evaluate(() =>
    Promise.all(
      document
        .getAnimations()
        .filter((a) => a.effect?.getTiming().iterations !== Infinity)
        .map((a) => a.finished.catch(() => {})),
    ),
  );

(async () => {
  const browser = await chromium.launch(),
    failures = [];
  async function check(name, fn) {
    if (
      process.env.ONLY &&
      !process.env.ONLY.split(",").some((id) => name.startsWith(id))
    )
      return;
    try {
      await fn();
      console.log("PASS " + name);
    } catch (error) {
      failures.push(name + ": " + error.stack);
      console.error("FAIL " + name + ": " + error.message);
    }
  }
  try {
    await check(
      "A1-F1 Timeline default rich cards and decisions own their full targets",
      async () => {
        for (const [width, height] of [
          [1280, 720],
          [1024, 768],
        ])
          for (const theme of ["porcelain", "amethyst", "petroleum"]) {
            const f = await fixture(browser, width, height),
              p = f.page;
            f.state.plan = true;
            f.state.running = true;
            f.state.pendingEvents = true;
            await p.evaluate(
              (theme) => (document.documentElement.dataset.theme = theme),
              theme,
            );
            await f.open("a");
            if (await p.locator("#activity-panel").isHidden())
              await p.locator("#panel-toggle").click();
            await p.evaluate(() => runConsole.openRun("a-job"));
            await p.locator("#run-tab-timeline").click();
            await settle(p);
            const geometry = await p
              .locator("#run-console")
              .evaluate((drawer) => {
                const body = drawer.querySelector(".run-console-body"),
                  port = body.getBoundingClientRect();
                const nodes = [
                  ...drawer.querySelectorAll(
                    ".run-plan-actions button,.run-span-row",
                  ),
                ];
                return {
                  scroll: body.scrollTop,
                  port: { top: port.top, bottom: port.bottom },
                  targets: nodes.map((n) => {
                    const b = n.getBoundingClientRect();
                    return {
                      text: n.textContent,
                      top: b.top,
                      bottom: b.bottom,
                      hits: [b.y + b.height / 2, b.bottom - 1].map((y) => {
                        const h = document.elementFromPoint(
                          b.x + b.width / 2,
                          y,
                        );
                        return h === n || n.contains(h);
                      }),
                    };
                  }),
                };
              });
            await capture(p, `timeline-${width}-${theme}`);
            assert.equal(geometry.scroll, 0);
            assert(geometry.targets.length >= 3);
            for (const t of geometry.targets) {
              assert(
                t.top >= geometry.port.top && t.bottom <= geometry.port.bottom,
                JSON.stringify({ width, theme, geometry }),
              );
              if (
                t.text.includes("Approve plan") ||
                t.text === "Discard" ||
                t.text === "Edit plan"
              )
                assert(t.hits.every(Boolean), JSON.stringify(t));
            }
            assert.equal(
              await p.locator("#run-plan-approve-plan, #run-plan-edit").count(),
              0,
            );
            assert.equal(f.state.posts.length, 0);
            assert(await p.locator("#run-console").isVisible());
            await p.close();
          }
      },
    );
    await check(
      "A2-F2 short single and multi choices provide 24px actual targets",
      async () => {
        for (const width of [400, 1440])
          for (const multi of [false, true]) {
            const f = await fixture(browser, width, 812),
              p = f.page;
            f.state.running = true;
            f.state.pendingEvents = true;
            f.state.gate = {
              gate_id: "choice",
              kind: "options",
              question: "Audience?",
              state: "pending",
              multi_select: multi,
              options: [
                { id: "staff", label: "Staff" },
                { id: "public", label: "Public" },
              ],
            };
            await f.open("a");
            const label = p.locator("#gate-choice label").first();
            await label.scrollIntoViewIfNeeded();
            const b = await label.boundingBox();
            assert(b.width >= 24 && b.height >= 24, JSON.stringify(b));
            assert(await hit(label));
            await p.mouse.click(b.x + b.width / 2, b.y + b.height - 1);
            assert(await p.getByLabel("Staff", { exact: true }).isChecked());
            await capture(p, `choices-${width}-${multi}`);
            await p.close();
          }
      },
    );
    await check(
      "A5-F2 terminal navigation retires disconnected watcher and displays follow-up",
      async () => {
        for (const terminalState of ["completed", "failed", "cancelled"]) {
          const f = await fixture(browser),
            p = f.page;
          f.state.running = true;
          f.state.pendingEvents = true;
          await f.open("a");
          await p.route("**/v1/jobs/a-job/events", (route) =>
            route.abort("connectionreset"),
          );
          await p.evaluate(() => {
            void watch();
          });
          await p.locator("#resume-execution").waitFor();
          f.state.running = false;
          f.state.completed = true;
          f.state.terminalState = terminalState;
          await f.open("b");
          assert(await p.locator("#resume-execution").isHidden());
          assert(await p.locator("#cancel").isHidden());
          assert(!(await p.evaluate(() => busy || streamDisconnected)));
          f.state.successfulSend = true;
          f.state.pendingEvents = false;
          await p.route("**/v1/jobs/child", (route) =>
            route.fulfill({
              json: {
                id: "child",
                state: "completed",
                request: { prompt: "Follow up" },
                result: { answer: "New follow-up answer" },
              },
            }),
          );
          await p.locator("#prompt").fill("Follow up");
          await p.locator("#send").click();
          await p.getByText("New follow-up answer", { exact: true }).waitFor();
          assert.equal(await p.evaluate(() => queuedTurns.length), 0);
          await p.close();
        }
      },
    );
    await check(
      "A5-F3 inbox drafts survive filters reload and remote resolution",
      async () => {
        for (const multi of [false, true]) {
          const f = await fixture(browser),
            p = f.page;
          f.state.inbox = [
            {
              kind: "gate",
              gate_id: "choice",
              job_id: "a-job",
              question: "Audience?",
              multi_select: multi,
              options: [
                { id: "staff", label: "Staff" },
                { id: "public", label: "Public" },
              ],
            },
          ];
          async function inbox(filter) {
            await p.evaluate(
              (filter) => runConsole.openAttention(filter),
              filter,
            );
          }
          await inbox("request");
          await p.locator("#needs-choice-staff").check();
          if (multi) await p.locator("#needs-choice-public").check();
          for (const filter of ["complete", "error"]) {
            await p.keyboard.press("Escape");
            await inbox(filter);
            await p.keyboard.press("Escape");
            await inbox("request");
            assert(await p.locator("#needs-choice-staff").isChecked());
            assert.equal(
              await p.locator("#needs-choice-public").isChecked(),
              multi,
            );
            assert(
              await p
                .getByRole("button", { name: "Submit answer", exact: true })
                .isEnabled(),
            );
          }
          await p.reload();
          await inbox("request");
          assert(await p.locator("#needs-choice-staff").isChecked());
          f.state.inbox = [];
          await p.keyboard.press("Escape");
          await inbox("request");
          await p.locator("#needs-choice-staff").waitFor({ state: "detached" });
          assert.equal(await p.locator("#needs-choice-staff").count(), 0);
          assert.equal(
            await p.evaluate(() => readDraft(gateChoiceKey("choice"))),
            null,
          );
          await p.close();
        }
      },
    );
    await check(
      "A5-F4 Runs uses current titles with distinct technical identities",
      async () => {
        const f = await fixture(browser),
          p = f.page;
        f.state.jobs = ["one", "two"].map((job_id) => ({
          ...run,
          job_id,
          title: "Quarterly report",
          state: "completed",
        }));
        await p.evaluate(() => runConsole.openRun("a-job"));
        await p.locator("#run-tab-runs").click();
        for (const id of ["one", "two"]) {
          assert.equal(
            await p.locator("#console-open-" + id).innerText(),
            "Quarterly report",
          );
          assert(
            (
              await p.locator("#console-open-" + id).getAttribute("title")
            ).includes(id),
          );
        }
        f.state.jobs[0].title = "Renamed report";
        await p.locator("#run-tab-pipeline").click();
        await p.locator("#run-tab-runs").click();
        await p.waitForFunction(
          () =>
            document.querySelector("#console-open-one")?.textContent ===
            "Renamed report",
        );
        assert.equal(
          await p.locator("#console-open-a-job").innerText(),
          "a-job",
        );
        await p.close();
      },
    );
    await check(
      "A4-F4 Markdown containers preserve literal examples and final chip offsets",
      async () => {
        for (const original of JSON.parse(
          await fs.readFile(
            path.join(__dirname, "markdown-invocation-cases.json"),
            "utf8",
          ),
        )) {
          const prompt = original
            .replace(/\/review/g, "/reviewer")
            .replace(/ actual$/, "");
          const f = await fixture(browser),
            p = f.page;
          f.state.resources = [resource];
          await p.locator("#prompt").fill(prompt);
          await p.getByRole("option", { name: /reviewer/ }).click();
          assert.equal(await p.locator(".resource-chip").count(), 1);
          assert.equal(
            await p.evaluate(() => selectedOccurrences()[0].start),
            prompt.lastIndexOf("/reviewer"),
          );
          const rendered = await p.evaluate(
            (text) => answerMarkdown.render(text),
            prompt,
          );
          if (prompt.includes("example")) {
            assert(rendered.includes("/reviewer example"));
            assert(rendered.includes("<code"));
          }
          await p.close();
        }
      },
    );
  } finally {
    await browser.close();
  }
  if (failures.length) {
    console.error(failures.join("\n"));
    process.exitCode = 1;
  }
})();
