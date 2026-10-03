const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 1280, height: 860 },
    });
    if (!process.env.HARNESS_URL)
      await page.route("http://panel.test/**", (route) => {
        const pathname = new URL(route.request().url()).pathname;
        return route.fulfill({
          path: require("node:path").join(
            __dirname,
            "..",
            pathname.startsWith("/assets/") ? "tail_ui" : "agent_service",
            pathname === "/" ? "index.html" : pathname,
          ),
        });
      });
    await page.addInitScript(() => {
      localStorage.removeItem("sidebar-collapsed");
      localStorage.setItem("activity-open", "0");
    });
    await page.route("**/v1/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/v1/projects")
        await new Promise((resolve) => setTimeout(resolve, 250));
      const data =
        path === "/v1/projects"
          ? {
              projects: ["project-a", "sem-projeto"],
              details: { "project-a": { label: "Alpha Project" } },
            }
          : path === "/v1/project-git"
            ? { revision: "feature/composer" }
            : path === "/v1/models"
              ? {
                  models: [
                    {
                      id: "fixture",
                      name: "Fixture",
                      backend: "local",
                      efforts: ["low"],
                    },
                    {
                      id: "cloud-fixture",
                      name: "Cloud fixture",
                      backend: "codex",
                      efforts: ["low"],
                    },
                  ],
                  providers: { local: true, codex: true },
                  uploads_enabled: false,
                }
              : path === "/v1/conversations"
                ? { conversations: [] }
                : path === "/v1/version"
                  ? { version: "fixture", build: "fixture" }
                  : {};
      return route.fulfill({ json: data });
    });
    await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.15"));
    await page.goto(process.env.HARNESS_URL || "http://panel.test/");
    await page.locator("#startup-gate").waitFor({ state: "visible" });
    assert.equal(
      await page.locator("#app-topbar").evaluate((el) => el.inert),
      true,
      "topbar stays inert while connection gate is active",
    );
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    assert.equal(
      await page.locator("#app-topbar").evaluate((el) => el.inert),
      false,
    );
    assert.equal(
      await page.locator("#app-topbar > :first-child").getAttribute("id"),
      "app-brand",
    );
    assert.equal(await page.locator("#app-brand").innerText(), "Tail Harness");
    assert.equal(await page.locator("#sidebar .brand").count(), 0);
    const top = await page.locator("#app-topbar").boundingBox();
    assert.equal(top.y, 0);
    assert(top.height >= 48);
    assert.equal(
      await page.locator("#conversation-title").innerText(),
      "New Conversation",
    );
    assert.equal(await page.locator("#new").innerText(), "New Conversation");
    await page.locator("#prompt").fill("Preserved draft");
    // Projects are listed open by default in the Codex-style sidebar.
    if (!(await page.locator("#project-tree").evaluate((el) => el.open)))
      await page.locator("#project-tree > summary").click();
    const projectName = page
      .locator("#projects details summary button")
      .first();
    const originalProject = await page.locator("#project").inputValue();
    await page.evaluate(() => {
      window.projectResetCalls = 0;
      const original = newConversation;
      newConversation = (...args) => {
        window.projectResetCalls++;
        return original(...args);
      };
    });
    // Project folders start expanded (Codex model, Sophia 2026-10-03); each click toggles.
    const startsOpen = await page.locator("#projects details").first().evaluate((el) => el.open);
    for (const open of [!startsOpen, startsOpen, !startsOpen]) {
      await projectName.click();
      assert.equal(
        await page
          .locator("#projects details")
          .first()
          .evaluate((el) => el.open),
        open,
      );
      assert.equal(
        await page.locator("#project").inputValue(),
        originalProject,
      );
      assert.equal(
        await page.locator("#prompt").inputValue(),
        "Preserved draft",
      );
      assert.equal(
        await page.evaluate(() => window.projectResetCalls),
        0,
        "project name only toggles the group",
      );
    }
    await page.locator("#prompt").fill("");
    await page.locator(".project-new").first().click();
    assert.equal(
      await page.locator("#conversation-title").innerText(),
      "New Conversation in project Alpha Project",
    );
    assert.equal(
      await page.locator("#composer-project-name").innerText(),
      "Alpha Project",
    );
    await page.waitForFunction(
      () =>
        document.querySelector("#composer-git").textContent ===
        "feature/composer",
    );
    await page.evaluate(() =>
      paintContext(
        {
          last: { totalTokens: 100 },
          total: { totalTokens: 999 },
          modelContextWindow: 1000,
        },
        { output_tokens: 20, inference_seconds: 2 },
      ),
    );
    assert.match(
      await page.locator("#context-meter").innerText(),
      /Context: 100.*10 tk\/s/,
    );
    assert.doesNotMatch(
      await page.locator("#context-meter").innerText(),
      /Accumulated usage|average/,
    );
    await page.evaluate(() => {
      event({
        id: last + 1,
        type: "context_usage",
        data: {
          last: { totalTokens: 100 },
          metrics: { output_tokens: 40, inference_seconds: 2 },
        },
      });
      if (
        !document
          .querySelector("#context-meter")
          .textContent.includes("20 tk/s")
      )
        throw Error("live throughput missing");
      event({
        id: last + 1,
        type: "usage_metrics",
        data: { output_tokens: 60, inference_seconds: 2 },
      });
    });
    assert.match(await page.locator("#context-meter").innerText(), /30 tk\/s/);
    const labels = await page.evaluate(() => [
      throughputLabel({
        generated_tokens_per_second: 12.5,
        output_tokens: 1,
        inference_seconds: 10,
      }),
      throughputLabel({ output_tokens: 0, inference_seconds: 2 }),
      throughputLabel({ output_tokens: 20, inference_seconds: 0 }),
      throughputLabel({ output_tokens: null, inference_seconds: 2 }),
      throughputLabel({ output_tokens: Infinity, inference_seconds: 2 }),
      throughputLabel({ output_tokens: -1, inference_seconds: 2 }),
      throughputLabel({}),
    ]);
    assert.deepEqual(labels, [
      " · 12.5 tk/s",
      " · 0 tk/s",
      ...Array(5).fill(" · tk/s: —"),
    ]);

    assert.equal(await page.locator("#dropzone #context-meter").count(), 0);
    const contextBox = await page.locator("#context-meter").boundingBox(),
      promptBox = await page.locator("#dropzone").boundingBox();
    assert(
      contextBox.y + contextBox.height <= promptBox.y,
      "metadata sits above the outside border",
    );
    assert(
      await page
        .locator("#prompt")
        .evaluate((el) => el.matches(":placeholder-shown")),
    );
    assert.match(
      await page.locator("#prompt").getAttribute("placeholder"),
      /Send a message.*Enter to send.*Shift\+Enter for a new line/,
    );
    await page.locator("#prompt").fill("Text");
    assert.equal(
      await page
        .locator("#prompt")
        .evaluate((el) => el.matches(":placeholder-shown")),
      false,
    );
    assert(
      await page
        .locator(".composer-info")
        .evaluate((el) => getComputedStyle(el).position === "absolute"),
    );
    await page.locator("#prompt").fill("");
    assert((await page.locator("#prompt").boundingBox()).height < 42);
    await page.screenshot({ path: "/tmp/tail-composer-project.png" });
    await page.locator("#new").click();
    assert.equal(
      await page.locator("#conversation-title").innerText(),
      "New Conversation",
    );
    assert.equal(await page.locator("#project").inputValue(), "sem-projeto");
    assert(await page.locator("#composer-project").isHidden());
    await page.locator("#model").selectOption("cloud-fixture");
    await page.locator("#model").dispatchEvent("change");
    await page.locator("#provider-quotas").waitFor({ state: "hidden" });
    await page.locator("#model").selectOption("fixture");
    await page.locator("#model").dispatchEvent("change");
    await page.locator("#provider-quotas").waitFor({ state: "hidden" });
    assert.match(
      await page.locator("#quota-short").textContent(),
      /No provider quota/,
    );
    assert.equal(
      await page.locator("#menu").getAttribute("aria-controls"),
      "sidebar",
    );
    assert.equal(
      await page.locator("#panel-toggle").getAttribute("aria-controls"),
      "activity-panel",
    );
    await page.click("#panel-toggle");
    const activityDesktop = await page.locator("#activity-panel").boundingBox();
    assert.equal(
      activityDesktop.x + activityDesktop.width,
      1280,
      "desktop activity panel touches viewport edge",
    );
    assert.equal(
      await page.locator("#panel-toggle").getAttribute("aria-expanded"),
      "true",
    );
    await page.click("#panel-toggle");
    await page.click("#menu");
    await page.locator("#sidebar").waitFor({ state: "hidden" });
    assert.equal(
      await page.locator("#menu").getAttribute("aria-expanded"),
      "false",
    );
    assert(await page.locator("#app-topbar").isVisible());
    await page.screenshot({ path: "/tmp/tail-topbar-desktop.png" });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.click("#menu");
    await page.locator("#sidebar.open").waitFor({ state: "visible" });
    const mobileBar = await page.locator("#app-topbar").boundingBox(),
      sidebar = await page.locator("#sidebar").boundingBox();
    assert(
      sidebar.y >= mobileBar.y + mobileBar.height,
      "mobile sidebar begins below global bar",
    );
    assert.equal(
      await page.locator("#menu").getAttribute("aria-expanded"),
      "true",
    );
    await page.click("#panel-toggle");
    const activityMobile = await page.locator("#activity-panel").boundingBox();
    assert.equal(
      activityMobile.x + activityMobile.width,
      390,
      "mobile activity panel touches viewport edge",
    );
    assert(
      activityMobile.y >= mobileBar.y + mobileBar.height,
      "activity drawer begins below topbar",
    );
    await page.click("#panel-toggle");
    await page.screenshot({ path: "/tmp/tail-topbar-mobile.png" });
    for (const width of [1280, 900, 390]) {
      await page.setViewportSize({ width, height: 860 });
      for (const order of ["conversations-right", "conversations-left"]) {
        await page.evaluate((order) => {
          applyPanelOrder(order);
          setPanelOpen(false, false);
          document.body.classList.remove("sidebar-collapsed");
          document.querySelector("#sidebar").classList.add("open");
          fitPanels();
        }, order);
        assert.equal(
          await page.locator("#app-topbar > :first-child").getAttribute("id"),
          "app-brand",
        );
        assert(await page.locator("#app-brand").isVisible());
        assert(
          await page
            .locator("#app-topbar")
            .evaluate((el) => el.scrollWidth <= el.clientWidth),
          "topbar fits viewport",
        );
        const reversed = order === "conversations-right";
        const left = page.locator(reversed ? "#panel-toggle" : "#menu"),
          right = page.locator(reversed ? "#menu" : "#panel-toggle");
        assert(
          (await left.boundingBox()).x < (await right.boundingBox()).x,
          `controls follow panel order at ${width}`,
        );
        await page.click("#panel-toggle");
        const files = await page.locator("#activity-panel").boundingBox(),
          conversations = await page.locator("#sidebar").boundingBox();
        assert(
          reversed ? files.x < conversations.x : conversations.x < files.x,
          `panels follow controls at ${width}`,
        );
        assert.equal(
          await page.locator("#panel-toggle").getAttribute("aria-expanded"),
          "true",
        );
        await page.click("#panel-toggle");
        assert.equal(await page.locator("#activity-panel").isVisible(), false);
        await page.click("#menu");
        assert.equal(await page.locator("#sidebar").isVisible(), false);
        await page.click("#menu");
        assert.equal(await page.locator("#sidebar").isVisible(), true);
      }
    }
    console.log(
      "PASS: global topbar controls, conversation/project titles, local/cloud quota visibility, right-edge activity drawer, and connection gate.",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
