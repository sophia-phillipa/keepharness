// Synthetic round-eleven regressions against the actual workspace assets.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const { mount, run, span } = require("./run-console-fixture.cjs");
const fs = require("node:fs/promises");
const path = require("node:path");
const settle = (p) =>
  p.evaluate(() =>
    Promise.all(
      document
        .getAnimations()
        .filter((a) => a.effect?.getTiming().iterations !== Infinity)
        .map((a) => a.finished.catch(() => {})),
    ),
  );
async function capture(page, name) {
  if (process.env.EVAL_OUTPUT) {
    await fs.mkdir(process.env.EVAL_OUTPUT, { recursive: true });
    await page.screenshot({
      path: path.join(process.env.EVAL_OUTPUT, name + ".png"),
    });
  }
}
const hit = (locator) =>
  locator.evaluate((n) => {
    const r = n.getBoundingClientRect();
    return n.contains(
      document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2),
    );
  });
async function fixture(browser, width = 1280, height = 720, plan = false) {
  const page = await browser.newPage({ viewport: { width, height } });
  page.setDefaultTimeout(3000);
  const state = { plan, posts: [], delay: null, fail: false, version: 0 };
  const steps = ["Research", "Review"].map((role) => ({
    role,
    backend: "codex",
    model: "fixture",
    effort: "low",
    task: role + " synthetic text",
    reason: "Check facts",
  }));
  const pending = {
    job_id: "a-job",
    conversation_id: "a",
    gate_id: "plan",
    kind: "maestro_plan",
    plan: { steps },
  };
  const turn = () => ({
    id: "a-job",
    project: "p",
    state: "running",
    request: {
      prompt: "Review fixture",
      backend: "codex",
      model: "fixture",
      effort: "low",
      execution_mode: "native",
    },
    result: null,
    gates: state.plan ? [{ ...pending, state: "pending" }] : [],
  });
  await mount(page, async (url, request) => {
    if (request.method() === "POST") {
      state.posts.push({ path: url.pathname, data: request.postDataJSON() });
      if (state.delay) await state.delay;
      return { json: { resolved: true } };
    }
    if (url.pathname === "/v1/projects")
      return {
        json: {
          projects: ["p"],
          details: { p: { label: "Synthetic project" } },
        },
      };
    if (url.pathname === "/v1/models")
      return {
        json: {
          models: [
            { id: "fixture", backend: "codex", efforts: ["low", "high"] },
            { id: "other", backend: "codex", efforts: ["medium", "high"] },
          ],
          providers: { codex: true },
          maestro: true,
        },
      };
    if (url.pathname === "/v1/resources")
      return { json: { items: [], warnings: [] } };
    if (url.pathname === "/v1/conversations")
      return {
        json: {
          conversations: [
            {
              id: "a",
              title: "Synthetic report",
              state: "running",
              project: "p",
              last_job_id: "a-job",
              backend: "codex",
              model: "fixture",
            },
          ],
        },
      };
    if (url.pathname === "/v1/conversations/a")
      return { json: { title: "Synthetic report", turns: [turn()] } };
    if (url.pathname === "/v1/jobs/a-job") return { json: turn() };
    if (url.pathname === "/v1/activity")
      return {
        json: {
          counts: { running: 1 },
          jobs: [
            {
              ...run,
              job_id: "a-job",
              conversation_id: "a",
              project_id: "p",
              backend: "codex",
              model: "fixture",
            },
          ],
          needs_you: state.plan ? [pending] : [],
          providers: [],
        },
      };
    if (url.pathname.endsWith("/spans"))
      return {
        json: {
          spans: [
            {
              ...span,
              span_id: "root",
              name: "Implementation and integration engineer",
              start_ts: Date.now() / 1000 - 123,
              end_ts: null,
              attrs: {
                backend: "codex",
                model: "fixture",
                effort: "high",
                enforcement: "mediated",
                version: state.version,
              },
            },
          ],
        },
      };
    if (url.pathname.endsWith("/events"))
      return { body: "", contentType: "text/event-stream" };
    if (url.pathname === "/v1/project-files") {
      const folder = url.searchParams.get("path") || "";
      if (folder && state.fileDelay) await state.fileDelay;
      if (folder && state.fail)
        return { status: 503, json: { code: "synthetic_unavailable" } };
      const entries =
        folder === "alpha"
          ? [
              { name: "nested", path: "alpha/nested", type: "directory" },
              { name: "child.txt", path: "alpha/child.txt", type: "file" },
            ]
          : folder === "alpha/nested"
            ? [
                {
                  name: "deep.txt",
                  path: "alpha/nested/deep.txt",
                  type: "file",
                },
              ]
            : folder
              ? []
              : [
                  { name: "alpha", path: "alpha", type: "directory" },
                  { name: "beta", path: "beta", type: "directory" },
                  {
                    name: "synthetic.txt",
                    path: "synthetic.txt",
                    type: "file",
                  },
                ];
      return {
        json: {
          state: "ready",
          root_id: "home",
          path: folder,
          roots: [{ id: "home", label: "Local Folders" }],
          entries,
        },
      };
    }
  });
  async function open() {
    if (width <= 620) await page.locator("#menu").click();
    await page
      .locator("#history .conversation-row > button")
      .filter({ hasText: "Synthetic report" })
      .click();
    await page.waitForFunction(
      () =>
        !loading &&
        document.querySelector("#conversation-title").textContent ===
          "Synthetic report",
    );
  }
  async function consoleOpen() {
    await open();
    await page.evaluate(() => runConsole.openRun("a-job"));
    await page.locator("#run-console").waitFor();
    await page.locator(".run-span-row").first().waitFor();
    await settle(page);
  }
  return { page, state, open, consoleOpen };
}
async function publication(page) {
  await page.evaluate(() =>
    showGate({
      gate_id: "publish",
      kind: "publish",
      publish: true,
      effect_id: "effect",
      operation: "jira.create_issue",
      destination: "SYNTHETIC",
      artifact_preview: Array(40).fill("Synthetic evidence").join("\n"),
      options: [
        { id: "approve", label: "Approve" },
        { id: "deny", label: "Deny" },
      ],
    }),
  );
}
(async () => {
  const browser = await chromium.launch();
  const failures = [];
  async function check(id, fn) {
    if (
      process.env.ONLY &&
      !process.env.ONLY.split(",").some((x) => id.startsWith(x))
    )
      return;
    try {
      await fn();
      console.log("PASS " + id);
    } catch (e) {
      failures.push(id + ": " + e.stack);
      console.error("FAIL " + id + ": " + e.message);
    }
  }
  try {
    await check("A1-F1 Timeline text never intersects", async () => {
      for (const [w, h] of [
        [1440, 900],
        [1280, 720],
        [1024, 768],
        [400, 844],
      ])
        for (const theme of ["porcelain", "amethyst", "petroleum"]) {
          const f = await fixture(browser, w, h);
          await f.consoleOpen();
          await f.page.evaluate((t) => TailTheme.apply(t), theme);
          await f.page
            .getByRole("tab", { name: "Timeline", exact: true })
            .click();
          const geometry = await f.page
            .locator(".run-span-row")
            .first()
            .evaluate((n) => {
              const rect = (sel) => {
                const r = document.createRange();
                r.selectNodeContents(n.querySelector(sel));
                return r.getBoundingClientRect().toJSON();
              };
              const a = rect(".run-span-tokens"),
                b = rect(".run-span-duration");
              return {
                a,
                b,
                overlap:
                  Math.max(
                    0,
                    Math.min(a.right, b.right) - Math.max(a.left, b.left),
                  ) *
                  Math.max(
                    0,
                    Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top),
                  ),
              };
            });
          assert.equal(
            geometry.overlap,
            0,
            JSON.stringify({ w, h, theme, ...geometry }),
          );
          await f.page.close();
        }
    });
    await check(
      "A1-F2 Complete Pipeline cards with publication evidence",
      async () => {
        for (const [w, h] of [
          [1280, 720],
          [1024, 768],
        ])
          for (const theme of ["porcelain", "amethyst", "petroleum"]) {
            const f = await fixture(browser, w, h);
            await f.open();
            await publication(f.page);
            await f.page.evaluate((t) => {
              TailTheme.apply(t);
              runConsole.openRun("a-job");
            }, theme);
            await f.page.locator(".run-span-row").first().waitFor();
            await settle(f.page);
            const row = f.page.locator(".run-span-row").first();
            const r = await row.boundingBox(),
              b = await f.page.locator(".run-console-body").boundingBox();
            assert(
              r.y + r.height <= b.y + b.height,
              JSON.stringify({ w, h, r, b }),
            );
            assert(await hit(row));
            assert(
              await row.evaluate((n) => {
                const r = n.getBoundingClientRect();
                return n.contains(
                  document.elementFromPoint(r.left + r.width / 2, r.bottom - 3),
                );
              }),
            );
            await capture(f.page, `publication-${w}-${theme}`);
            const evidence = f.page.locator("#gate-publish .publish-evidence");
            await evidence.scrollIntoViewIfNeeded();
            assert(
              await hit(evidence),
              JSON.stringify(
                await evidence.evaluate((n) => ({
                  e: n.getBoundingClientRect().toJSON(),
                  m: document
                    .getElementById("messages")
                    .getBoundingClientRect()
                    .toJSON(),
                  hit: document
                    .elementFromPoint(
                      n.getBoundingClientRect().x + n.clientWidth / 2,
                      n.getBoundingClientRect().y + n.clientHeight / 2,
                    )
                    ?.outerHTML.slice(0, 180),
                })),
              ),
            );
            for (const button of await f.page
              .locator("#gate-publish button")
              .all()) {
              await button.scrollIntoViewIfNeeded();
              assert(await hit(button));
            }
            await f.page.close();
          }
      },
    );
    await check("A1-F3 Tour spot stays within visible target", async () => {
      const f = await fixture(browser);
      await f.consoleOpen();
      await publication(f.page);
      await f.page
        .locator("#gate-publish button")
        .last()
        .scrollIntoViewIfNeeded();
      await f.page.keyboard.press("Control+j");
      await f.page.evaluate(() => tailHarnessTour.start());
      for (
        let i = 0;
        i < 16 &&
        !(await f.page.locator("#tour-card").innerText()).includes(
          "Publication gate",
        );
        i++
      )
        await f.page.getByRole("button", { name: "Next", exact: true }).click();
      assert.match(
        await f.page.locator("#tour-card").innerText(),
        /Publication gate/,
      );
      await f.page.waitForTimeout(1250);
      const m = await f.page.locator("#messages").boundingBox(),
        s = await f.page.locator(".tour-spotlight").boundingBox();
      assert(s.y >= m.y - 6, JSON.stringify({ m, s }));
      await f.page.close();
    });
    await check(
      "A2-F1 Escape closes only the nested conversation menu",
      async () => {
        for (const w of [400, 1440]) {
          const f = await fixture(browser, w, 844);
          if (w === 400) await f.page.locator("#menu").click();
          else if (await f.page.locator("#activity-panel").isHidden())
            await f.page.locator("#panel-toggle").click();
          const summary = f.page
            .locator("#history .conversation-row summary")
            .first();
          await summary.focus();
          await f.page.keyboard.press("Enter");
          await f.page.keyboard.press("Tab");
          const panels = await f.page.evaluate(() =>
            ["sidebar", "activity-panel"].map((id) => ({
              id,
              cls: document.getElementById(id).className,
              hidden: document.getElementById(id).hidden,
            })),
          );
          await f.page.keyboard.press("Escape");
          assert(await summary.evaluate((n) => document.activeElement === n));
          assert.deepEqual(
            await f.page.evaluate(() =>
              ["sidebar", "activity-panel"].map((id) => ({
                id,
                cls: document.getElementById(id).className,
                hidden: document.getElementById(id).hidden,
              })),
            ),
            panels,
          );
          await f.page.close();
        }
      },
    );
    await check(
      "A2-F2 File tree navigation and expansion semantics",
      async () => {
        const f = await fixture(browser, 1440, 900);
        if (await f.page.locator("#activity-panel").isHidden())
          await f.page.locator("#panel-toggle").click();
        const tree = f.page.locator("#files-tree");
        const alpha = tree.locator('[data-path="alpha"]').first();
        await alpha.waitFor();
        await alpha.focus();
        assert.equal(await alpha.getAttribute("aria-expanded"), "false");
        assert.equal(
          await tree.locator('[role=treeitem][tabindex="0"]').count(),
          1,
        );
        assert.equal(
          await tree.locator('button:not([tabindex="-1"])').count(),
          0,
        );
        await f.page.keyboard.press("ArrowDown");
        assert.equal(
          await f.page.locator(":focus").getAttribute("data-path"),
          "beta",
        );
        await f.page.keyboard.press("End");
        assert.equal(
          await f.page.locator(":focus").getAttribute("data-path"),
          "synthetic.txt",
        );
        await f.page.keyboard.press("Home");
        assert.equal(
          await f.page.locator(":focus").getAttribute("data-path"),
          "alpha",
        );
        await f.page.keyboard.press("ArrowRight");
        await tree.locator('[data-path="alpha/nested"]').waitFor();
        assert.equal(await alpha.getAttribute("aria-expanded"), "true");
        await f.page.keyboard.press("ArrowRight");
        assert.equal(
          await f.page.locator(":focus").getAttribute("data-path"),
          "alpha/nested",
        );
        await f.page.keyboard.press("ArrowRight");
        await tree.locator('[data-path="alpha/nested/deep.txt"]').waitFor();
        await f.page.keyboard.press("ArrowRight");
        assert.equal(
          await f.page.locator(":focus").getAttribute("data-path"),
          "alpha/nested/deep.txt",
        );
        await f.page.keyboard.press("ArrowLeft");
        assert.equal(
          await f.page.locator(":focus").getAttribute("data-path"),
          "alpha/nested",
        );
        await f.page.keyboard.press("ArrowLeft");
        assert.equal(
          await f.page.locator(":focus").getAttribute("aria-expanded"),
          "false",
        );
        await f.page.keyboard.press("ArrowUp");
        assert.equal(
          await f.page.locator(":focus").getAttribute("data-path"),
          "alpha",
        );
        assert(await hit(f.page.locator(":focus")));
        await f.page.close();
      },
    );
    await check(
      "A2-F3 Directory redraw retains focus through success and failure",
      async () => {
        for (const w of [400, 1440])
          for (const fail of [false, true]) {
            const f = await fixture(browser, w, 844);
            if (await f.page.locator("#activity-panel").isHidden())
              await f.page.locator("#panel-toggle").click();
            const alpha = f.page
              .locator('#files-tree [data-path="alpha"]')
              .first();
            await alpha.waitFor();
            let release;
            f.state.fileDelay = new Promise((r) => (release = r));
            f.state.fail = fail;
            await f.page
              .getByRole("button", { name: "Expand alpha", exact: true })
              .focus();
            await f.page.keyboard.press("Enter");
            assert(
              await f.page.evaluate(
                () =>
                  document.activeElement.closest('[data-path="alpha"]') !==
                  null,
              ),
            );
            release();
            await f.page.waitForTimeout(150);
            assert(
              await f.page.evaluate(
                () =>
                  document.activeElement.closest('[data-path="alpha"]') !==
                  null,
              ),
            );
            assert(await hit(f.page.locator(":focus")));
            await f.page.keyboard.press("Tab");
            assert(
              await f.page.evaluate(
                () => document.activeElement.tagName !== "BODY",
              ),
            );
            if (w === 400)
              assert(
                await f.page
                  .locator("#activity-panel")
                  .evaluate((n) => n.contains(document.activeElement)),
              );
            await f.page.close();
          }
      },
    );
    await check(
      "A5-F1 Malformed legacy drafts have a visible recovery path",
      async () => {
        for (const draft of [
          "{",
          "{}",
          '{"steps":null}',
          '{"steps":[null]}',
          '{"steps":[{"task":12}]}',
        ]) {
          const f = await fixture(browser, 1440, 900, true);
          const errors = [];
          f.page.on("pageerror", (error) => errors.push(error.message));
          await f.page.evaluate(
            (value) => sessionStorage.setItem("plan-draft:plan", value),
            draft,
          );
          await f.consoleOpen();
          await f.page.locator("#run-plan-edit").click();
          assert.match(
            await f.page
              .locator(".run-plan-approval [role=status]")
              .innerText(),
            /could not be loaded/,
          );
          await f.page
            .getByRole("button", { name: "Reset edits", exact: true })
            .click();
          const task = f.page.getByLabel("Task for step 1", { exact: true });
          assert.equal(await task.inputValue(), "Research synthetic text");
          await task.fill("Immediate plain text edit");
          assert(
            await f.page
              .getByRole("button", { name: "Run with edits (1)", exact: true })
              .isVisible(),
          );
          assert.deepEqual(errors, []);
          await f.page.close();
        }
      },
    );
    await check(
      "A5-F1 Plan edits use ordinary text and per-step controls",
      async () => {
        const f = await fixture(browser, 1440, 900, true);
        await f.consoleOpen();
        await f.page.locator("#run-plan-edit").click();
        const task = f.page.getByRole("textbox", {
          name: "Task for step 1",
          exact: true,
        });
        const prose =
          'Review the "public" report, Sophia\'s notes\nand Unicode 🐈';
        await task.fill(prose);
        await f.page
          .getByLabel("Model for step 1", { exact: true })
          .selectOption("codex/other");
        await f.page
          .getByLabel("Effort for step 1", { exact: true })
          .selectOption("high");
        await f.page
          .getByRole("button", { name: "Move step 1 down", exact: true })
          .click();
        assert.equal(
          await f.page
            .getByLabel("Task for step 2", { exact: true })
            .inputValue(),
          prose,
        );
        await f.page
          .getByRole("button", { name: "Remove step 1", exact: true })
          .click();
        assert.equal(await task.inputValue(), prose);
        await f.page
          .getByRole("button", { name: "Reset edits", exact: true })
          .click();
        assert.equal(await task.inputValue(), "Research synthetic text");
        assert.equal(
          await f.page
            .getByLabel("Task for step 2", { exact: true })
            .inputValue(),
          "Review synthetic text",
        );
        await task.fill(prose);
        await f.page
          .getByLabel("Model for step 1", { exact: true })
          .selectOption("codex/other");
        await f.page
          .getByLabel("Effort for step 1", { exact: true })
          .selectOption("high");
        await f.page.reload();
        await f.page.locator(".maestro-plan-actions button").last().click();
        assert.equal(await task.inputValue(), prose);
        await capture(f.page, "plan-editor");
        let release;
        f.state.delay = new Promise((r) => (release = r));
        const approve = f.page
          .locator(".run-plan-actions button")
          .filter({ hasText: "Run with edits" });
        await approve.click();
        await f.page.waitForTimeout(100);
        assert.equal(f.state.posts.length, 1);
        assert.equal(f.state.posts[0].data.plan.steps[0].task, prose);
        assert.equal(f.state.posts[0].data.plan.steps[0].model, "other");
        assert.equal(f.state.posts[0].data.plan.steps[0].effort, "high");
        release();
        await f.page.close();
      },
    );
  } finally {
    if (process.env.EVAL_OUTPUT) {
      await fs.mkdir(process.env.EVAL_OUTPUT, { recursive: true });
      for (const [i, page] of browser
        .contexts()
        .flatMap((c) => c.pages())
        .entries())
        await page
          .screenshot({
            path: path.join(
              process.env.EVAL_OUTPUT,
              "round11-failure-" + i + ".png",
            ),
          })
          .catch(() => {});
    }
    await browser.close();
  }
  if (failures.length) {
    console.error(failures.join("\n"));
    process.exitCode = 1;
  }
})();
