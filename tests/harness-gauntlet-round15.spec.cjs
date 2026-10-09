// Round 15: synthetic first-view, keyboard ownership and semantic icon regressions.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const { mount, run, span } = require("./run-console-fixture.cjs");
const {
  handleHitZones,
  assertHitAreas,
} = require("./support/handle-hit-zone.cjs");
const hit = (loc) =>
  loc.evaluate((n) => {
    const r = n.getBoundingClientRect();
    return n.contains(
      document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2),
    );
  });
const painted = async (loc) => {
  for (let i = 0; i < 20; i++) {
    if (
      await loc.evaluate((n) => {
        const r = n.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      })
    )
      return true;
    await new Promise((r) => setTimeout(r, 50));
  }
  return false;
};
async function fixture(browser, width = 1440, height = 900) {
  const page = await browser.newPage({ viewport: { width, height } });
  page.setDefaultTimeout(4000);
  const state = { running: false, delay: null };
  const turn = () => ({
    id: "run-a",
    project: "sem-projeto",
    state: state.running ? "running" : "completed",
    request: {
      prompt: "Synthetic task",
      backend: "local",
      model: "fixture",
      effort: "configured",
      execution_mode: "native",
    },
    result: state.running ? null : { answer: "Synthetic answer" },
  });
  await mount(page, async (url) => {
    if (url.pathname === "/v1/conversations")
      return {
        json: {
          conversations: [
            {
              id: "conversation-a",
              title: "Synthetic history",
              state: state.running ? "running" : "completed",
              project: "sem-projeto",
              backend: "local",
              model: "fixture",
              last_job_id: "run-a",
            },
          ],
        },
      };
    if (url.pathname === "/v1/conversations/conversation-a") {
      if (state.delay) await state.delay;
      return { json: { title: "Synthetic history", turns: [turn()] } };
    }
    if (url.pathname === "/v1/jobs/run-a") return { json: turn() };
    if (url.pathname.endsWith("/events"))
      return { body: "", contentType: "text/event-stream" };
    if (url.pathname === "/v1/resources")
      return { json: { items: [], warnings: [] } };
    if (url.pathname === "/v1/activity")
      return {
        json: {
          counts: { running: 1 },
          jobs: [run],
          providers: [],
          needs_you: [],
        },
      };
    if (url.pathname.endsWith("/spans"))
      return {
        json: {
          spans: [
            "completed",
            "pending",
            "approval",
            "failed",
            "cancelled",
            "interrupted",
            "skipped",
          ].map((outcome, i) => ({
            ...span,
            span_id: "state-" + i,
            name: "Step " + outcome,
            kind: outcome === "approval" ? "harness.gate" : "invoke_agent",
            end_ts: ["pending", "approval"].includes(outcome) ? null : 2,
            attrs: { outcome: outcome === "approval" ? "pending" : outcome },
          })),
        },
      };
    if (url.pathname === "/v1/project-files")
      return { json: { roots: [], entries: [], can_authorize: false } };
  });
  return { page, state };
}
(async () => {
  const browser = await chromium.launch(),
    failures = [];
  async function check(id, fn) {
    if (process.env.ONLY && !id.startsWith(process.env.ONLY)) return;
    try {
      await fn();
      console.log("PASS " + id);
    } catch (e) {
      failures.push(id + ": " + e.stack);
      console.error("FAIL " + id + ": " + e.message);
    }
  }
  try {
    await check("A1-F1 first-view publication summary", async () => {
      for (const [width, height] of [
        [1440, 900],
        [1280, 720],
        [1024, 768],
        [400, 812],
      ]) {
        const { page: p } = await fixture(browser, width, height);
        await p.evaluate(() =>
          showPublishGate({
            gate_id: "publication",
            publish: true,
            risk: "high",
            enforcement: "unenforced",
            operation: "jira.create_issue",
            destination: "SYN",
            integration: "synthetic",
            evidence: Array.from({ length: 8 }, () => ({
              digest: "a".repeat(64),
            })),
            artifact_preview: "Synthetic artifact preview",
            arguments_digest: "b".repeat(64),
          }),
        );
        for (const text of [
          "Operation: jira.create_issue",
          "Destination: SYN",
        ]) {
          const n = p.locator(".publish-evidence p").filter({ hasText: text });
          assert(await hit(n), `${width} ${text}`);
        }
        for (const name of ["Approve", "Deny"])
          assert(await hit(p.getByRole("button", { name, exact: true })));
        const evidence = p.getByRole("region", {
          name: "Publication evidence",
        });
        await evidence.focus();
        await p.keyboard.press("End");
        await p.waitForTimeout(200);
        assert(await evidence.evaluate((n) => n.scrollTop > 0));
        assert(
          await p
            .locator(".publish-evidence")
            .innerText()
            .then((t) =>
              t.includes("Artifact preview: Synthetic artifact preview"),
            ),
        );
        await p.close();
      }
    });
    await check("A1-F2 resource text owns boundary", async () => {
      const { page: p } = await fixture(browser);
      for (const theme of ["porcelain", "amethyst", "petroleum"]) {
        await p.evaluate((t) => HarnessTheme.apply(t), theme);
        if (await p.locator("#activity-panel").isHidden())
          await p.locator("#panel-toggle").click();
        await p.locator("#header-execution-mode").click();
        /* D-033: Resources is one section of the Activities accordion */ if (
          await p.locator("#activities-view").isHidden()
        )
          await p.locator("#activity-toggle").click();
        await p.locator("#workspace-resources-head").click({ force: true });
        const geometry = await p
          .locator("#workspace-resources")
          .evaluate((n) => {
            const walker = document.createTreeWalker(n, NodeFilter.SHOW_TEXT);
            let t;
            while ((t = walker.nextNode()))
              if (t.textContent.trim()) {
                const range = document.createRange();
                range.selectNodeContents(t);
                const r = range.getBoundingClientRect();
                if (r.width && r.height) {
                  const h = document.elementFromPoint(
                    r.x + 1,
                    r.y + r.height / 2,
                  );
                  return { owns: n.contains(h), left: r.left, hit: h?.id };
                }
              }
          });
        assert(geometry?.owns, JSON.stringify(geometry));
        assertHitAreas(await handleHitZones(p, "#activity-panel-resize"));
        /* WP-16 L64: drawn as a 6 px strip, grabbed through a 24 px zone clear of controls (WCAG 2.5.8) */ assert(
          await p.locator("#activity-panel-resize").evaluate((n) => {
            const r = n.getBoundingClientRect();
            return [r.left + 1, r.right - 1].every(
              (x) => document.elementFromPoint(x, r.y + r.height * 0.45) === n,
            );
          }),
          "entire resize target owns hit area",
        );
        await p.locator("#header-execution-mode").click();
      }
      await p.close();
    });
    await check("A1-F3 distinct console tab semantics", async () => {
      const { page: p } = await fixture(browser);
      await p.keyboard.press("Control+j");
      for (const theme of ["porcelain", "amethyst"]) {
        await p.evaluate((t) => HarnessTheme.apply(t), theme);
        for (const [name, id] of Object.entries({
          Pipeline: "plan",
          Timeline: "trace",
          Logs: "list",
          Runs: "pulse",
          Agents: "server",
        })) {
          const tab = p.getByRole("tab", { name, exact: true });
          await tab.click();
          assert.equal(
            await tab.locator("use").getAttribute("href"),
            "/assets/icons.svg#" + id,
          );
          assert(await painted(tab.locator("use")));
          assert(await hit(tab));
        }
      }
      await p.close();
    });
    await check("A1-F4 span state markers", async () => {
      const { page: p } = await fixture(browser);
      await p.evaluate(() => runConsole.openRun("run-a"));
      for (const view of ["Pipeline", "Timeline"]) {
        await p.getByRole("tab", { name: view, exact: true }).click();
        for (const [state, id] of Object.entries({
          completed: "check",
          pending: "clock",
          approval: "shield",
          failed: "x",
          cancelled: "x",
          interrupted: "x",
          skipped: "chevron-right",
        })) {
          const row = p
            .locator(".run-span-row")
            .filter({ has: p.locator("strong", { hasText: "Step " + state }) });
          await row.scrollIntoViewIfNeeded();
          assert.equal(
            await row.locator("strong use").getAttribute("href"),
            "/assets/icons.svg#" + id,
          );
          assert(await painted(row.locator("strong use")));
          assert(await hit(row));
          assert.equal(
            await row.locator(".run-span-state").textContent(),
            state === "approval" ? "pending" : state,
          );
        }
      }
      await p.close();
    });
    await check("A1-F5 console sprite actions", async () => {
      const { page: p } = await fixture(browser);
      await p.evaluate(() => {
        active = assistant();
        runConsole.attachAnswer(active.el, "run-a");
      });
      assert.equal(
        await p
          .getByRole("button", { name: "View run", exact: true })
          .locator("svg use")
          .count(),
        1,
      );
      await p.keyboard.press("Control+j");
      assert(await painted(p.locator(".run-console-close use")));
      assert(await painted(p.locator("#run-status-toggle use")));
      assert.equal(
        await p.locator("#run-status-action use").getAttribute("href"),
        "/assets/icons.svg#chevron-down",
      );
      await p.locator(".run-console-close").click();
      assert.equal(
        await p.locator("#run-status-action use").getAttribute("href"),
        "/assets/icons.svg#chevron-up",
      );
      assert(await painted(p.locator("#run-status-action use")));
      await p.setViewportSize({ width: 400, height: 812 });
      assert(
        await p.locator("#run-status-toggle").evaluate((n) => {
          const svg = n.querySelector("svg").getBoundingClientRect();
          const text = [...n.childNodes].find(
            (t) => t.nodeType === Node.TEXT_NODE,
          );
          const range = document.createRange();
          range.selectNodeContents(text);
          const r = range.getBoundingClientRect();
          return Math.abs(svg.y + svg.height / 2 - r.y - r.height / 2) < 5;
        }),
        "mobile status icon and text share a line",
      );
      await p.close();
    });
    await check("A1-F5b workflow card state icon", async () => {
      const { page: p } = await fixture(browser);
      await p.evaluate(() => {
        active = assistant();
        showMaestroPlan({
          steps: [{ role: "Reviewer", backend: "codex", task: "Check" }],
        });
      });
      assert.equal(
        await p.locator(".maestro-plan-card .state-pill svg use").count(),
        1,
      );
      assert(await painted(p.locator(".maestro-plan-card .state-pill use")));
      await p.close();
    });
    await check("A2-F1 history keyboard focus", async () => {
      for (const width of [1440, 400])
        for (const running of [false, true]) {
          const { page: p, state } = await fixture(browser, width, 812);
          state.running = running;
          if (width === 400) await p.locator("#menu").click();
          const row = p
            .locator(".conversation-row > button")
            .filter({ hasText: "Synthetic history" });
          await row.press("Enter");
          await p.waitForFunction(
            () => !loading && conversation === "conversation-a",
          );
          const focus = await p.evaluate(() => ({
            id: document.activeElement.id,
            body: document.activeElement === document.body,
            visible: document.activeElement.checkVisibility(),
            inert: !!document.activeElement.closest("[inert]"),
          }));
          assert(
            !focus.body && focus.visible && !focus.inert,
            JSON.stringify(focus),
          );
          if (width === 400) {
            assert(
              !(await p.locator("#sidebar").getAttribute("class")).includes(
                "open",
              ),
            );
            assert.equal(focus.id, "messages");
          }
          await p.close();
        }
      const { page: p, state } = await fixture(browser);
      let release;
      state.delay = new Promise((r) => (release = r));
      await p
        .locator(".conversation-row > button")
        .filter({ hasText: "Synthetic history" })
        .press("Enter");
      await p.locator("#settings").focus();
      release();
      await p.waitForFunction(
        () => !loading && conversation === "conversation-a",
      );
      assert(
        await p
          .locator("#settings")
          .evaluate((n) => n === document.activeElement),
      );
      await p.close();
    });
    await check("A2-F2 quota releases competing mobile surfaces", async () => {
      for (const width of [400, 1440])
        for (const surface of ["none", "files", "sidebar", "console"]) {
          const { page: p } = await fixture(browser, width, 812);
          if (
            surface === "files" &&
            (await p.locator("#activity-panel").isHidden())
          )
            await p.locator("#panel-toggle").click();
          if (surface === "sidebar" && width === 400)
            await p.locator("#menu").click();
          if (surface === "console") await p.keyboard.press("Control+j");
          await p.keyboard.press("Control+,");
          await p.locator("#settings-quota").press("Enter");
          assert(
            await p
              .locator("#quota-panel")
              .evaluate((n) => !n.closest("[inert]")),
          );
          const refresh = p
            .locator("#quota-panel button")
            .filter({ hasText: "Refresh" });
          assert(await hit(refresh));
          assert(await refresh.evaluate((n) => n === document.activeElement));
          await p.keyboard.press("Escape");
          assert(
            await p
              .locator("#settings-quota")
              .evaluate((n) => n === document.activeElement),
          );
          await p.close();
        }
    });
  } finally {
    await browser.close();
  }
  if (failures.length) {
    console.error(failures.join("\n"));
    process.exitCode = 1;
  }
})();
