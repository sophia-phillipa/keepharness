// Composer sub-bar shortcuts and starting a chat from a set of files
// (Sophia, 2026-10-03; Codex model: Choose project · Files · Plugins).
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 1280, height: 860 },
    });
    const attached = [];
    let noAgents = false;
    page.on("pageerror", (e) => console.error("PAGEERROR", e.message));
    await page.route("http://panel.test/**", async (route) => {
      const url = new URL(route.request().url()),
        pathname = url.pathname;
      if (!pathname.startsWith("/v1/"))
        return route.fulfill({
          path: path.join(
            __dirname,
            "..",
            pathname.startsWith("/assets/") ? "harness_ui" : "agent_service",
            pathname === "/" ? "index.html" : pathname,
          ),
        });
      let data = {};
      if (pathname === "/v1/projects")
        data = {
          projects: ["sem-projeto", "project-a"],
          details: { "project-a": { label: "Project Alpha" } },
        };
      else if (pathname === "/v1/models")
        data = {
          models: [
            {
              id: "fixture",
              name: "Fixture",
              backend: "local",
              efforts: ["low"],
              permissions: { upload: true },
            },
          ],
          providers: { local: true },
          uploads_enabled: true,
        };
      else if (pathname === "/v1/conversations") data = { conversations: [] };
      else if (pathname === "/v1/version")
        data = { version: "fixture", build: "files-agents" };
      else if (pathname === "/v1/resources")
        data = {
          items: noAgents
            ? []
            : [
                {
                  id: "catalog/demo/agents/reviewer.toml",
                  resource_id: "catalog/demo/agents/reviewer.toml",
                  revision: "a1",
                  kind: "agent",
                  name: "reviewer",
                  description: "Review implementation evidence",
                  scope: "catalog",
                  origin: "demo",
                  group: "Agents",
                  selectable: true,
                },
              ],
          warnings: [],
        };
      else if (
        pathname === "/v1/project-files" &&
        route.request().method() === "GET"
      ) {
        const roots = [{ id: "home", label: "Personal folder" }];
        data = url.searchParams.get("root_id")
          ? {
              state: "ready",
              roots,
              root_id: "home",
              path: "",
              entries: [
                { path: "brief.md", name: "brief.md", type: "file" },
                { path: "photo.png", name: "photo.png", type: "file" },
              ],
              limited: false,
            }
          : { state: "ready", roots, entries: [] };
      } else if (
        pathname === "/v1/project-files/attach" &&
        route.request().method() === "POST"
      ) {
        const body = route.request().postDataJSON();
        attached.push(body.paths);
        data = {
          attachments: body.paths.map((name, i) => ({
            file_id: "selected-" + attached.length + "-" + i,
            name,
          })),
          skipped: [],
        };
      }
      return route.fulfill({ json: data });
    });
    await page.addInitScript(() =>
      localStorage.setItem("keepharness-tour-seen", "0.16.0"),
    );
    await page.goto("http://panel.test/");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });

    // The sub-bar of a new conversation offers Files and Agents beside the project button.
    const bar = page.locator("#execution-mode-choice");
    await bar.waitFor({ state: "visible" });
    for (const name of [/No project/, /^Files$/, /^Agents$/])
      assert(await bar.getByRole("button", { name }).isVisible(), String(name));

    // OP-R1-14: Agents opens the agent list and leaves the draft exactly as it was.
    await page.fill("#prompt", "Review this");
    await bar.getByRole("button", { name: "Agents" }).click();
    assert.equal(await page.locator("#prompt").inputValue(), "Review this");
    const reviewer = page
      .locator("#resource-menu")
      .getByRole("option", { name: /reviewer/ });
    await reviewer.waitFor();
    await page.keyboard.press("Escape");
    assert.equal(await page.locator("#resource-menu").isVisible(), false);
    assert.equal(await page.locator("#prompt").inputValue(), "Review this");
    // Choosing an agent adds its token after the draft.
    await bar.getByRole("button", { name: "Agents" }).click();
    await reviewer.click();
    assert.equal(
      await page.locator("#prompt").inputValue(),
      "Review this @reviewer ",
    );
    await page.fill("#prompt", "");
    // With no agent for the model the list offers to create one, in plain words.
    noAgents = true;
    await page.evaluate(() => clearResourceItems());
    await bar.getByRole("button", { name: "Agents" }).click();
    const menu = page.locator("#resource-menu");
    await menu.getByRole("button", { name: "Create agent…" }).waitFor();
    assert.match(await menu.innerText(), /No agents for this model yet\./);
    assert.doesNotMatch(await menu.innerText(), /No resource compatible/);
    assert.equal(await page.locator("#prompt").inputValue(), "");
    await page.keyboard.press("Escape");
    noAgents = false;

    // OP-R1-22: Files opens a picker and leaves the conversation in Chat; browsing the project's
    // files is one explicit item of that picker.
    await bar.getByRole("button", { name: "Files" }).click();
    const filesMenu = page.locator("#files-menu");
    await filesMenu.waitFor({ state: "visible" });
    assert.equal(
      await page.locator("#activity-panel").isHidden(),
      true,
      "the chip does not open the panel",
    );
    for (const name of ["Upload…", "Browse project files…"])
      assert(await filesMenu.getByRole("button", { name }).isVisible(), name);
    assert.match(
      await filesMenu.innerText(),
      /Recent uploads[\s\S]*Space pages/,
    );
    await page.keyboard.press("Escape");
    assert.equal(await filesMenu.isHidden(), true);
    assert(
      await page
        .locator("#files-chip")
        .evaluate((el) => el === document.activeElement),
      "focus returns to the chip",
    );
    // A selection of project files offers attach and a new chat.
    await bar.getByRole("button", { name: "Files" }).click();
    await filesMenu.getByTestId("files-menu-browse").click();
    await page.locator("#activity-panel").waitFor({ state: "visible" });
    const brief = page.locator('#files-tree [data-path="brief.md"]'),
      photo = page.locator('#files-tree [data-path="photo.png"]');
    await brief.waitFor({ state: "visible" });
    const actions = page.getByRole("group", { name: "Selected files" });
    assert.equal(
      await actions.isVisible(),
      false,
      "no actions without a selection",
    );
    await brief.focus();
    await brief.press("Space");
    assert.equal(
      await actions
        .getByRole("button", { name: "New chat with this file" })
        .isVisible(),
      true,
    );
    await photo.focus();
    await photo.press("Space");
    const newChat = actions.getByRole("button", {
      name: "New chat with these files",
    });
    assert.equal(await newChat.isVisible(), true);
    assert.equal(
      await actions
        .getByRole("button", { name: "Attach to message" })
        .isVisible(),
      true,
    );

    await page.fill("#prompt", "Summarize these files");
    await newChat.click();
    await page.waitForFunction(
      () => document.querySelectorAll(".attachment").length === 2,
    );
    assert.deepEqual(attached, [["brief.md", "photo.png"]]);
    assert.equal(
      await page.locator("#conversation-title").innerText(),
      "New Conversation",
    );
    assert.equal(
      await page.locator("#prompt").inputValue(),
      "Summarize these files",
      "the draft is kept",
    );
    assert(
      await page
        .locator("#prompt")
        .evaluate((el) => el === document.activeElement),
    );
    console.log(
      "PASS composer Files and Agents chips; new chat from selected files",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
