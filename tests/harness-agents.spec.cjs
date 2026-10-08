const { executionModes } = require("./model-fixture.cjs");
// Harness-owned agents (Sophia, 2026-10-03): create an agent with its own
// instructions, purpose, tasks, target output, provider, model and effort,
// then call it with @@name; selecting it moves the composer to its route.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const path = require("node:path");

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
    const agents = [],
      requests = [],
      jobs = [];
    page.on("pageerror", (e) => console.error("PAGEERROR", e.message));
    page.on("dialog", () => {
      throw Error("Unexpected browser dialog");
    });
    const resourceItem = (agent) => ({
      id: "harness/agents/" + agent.id,
      resource_id: "harness/agents/" + agent.id,
      revision: agent.revision,
      kind: "agent",
      name: agent.name,
      description: agent.purpose,
      scope: "harness",
      origin: "harness",
      group: "Your agents",
      backend: agent.backend,
      model: agent.model,
      effort: agent.effort,
      mode: "conversational",
      selectable: true,
      preflight_hint: "Runs on " + agent.model,
    });
    await page.route("http://agents.test/**", async (route) => {
      const url = new URL(route.request().url()),
        pathname = url.pathname,
        method = route.request().method();
      if (!pathname.startsWith("/v1/"))
        return route.fulfill({
          path: path.join(__dirname, "..", pathname.startsWith("/assets/") ? "harness_ui" : "agent_service", pathname === "/" ? "index.html" : pathname),
        });
      let data = {},
        status = 200;
      if (pathname === "/v1/projects") data = { projects: ["sem-projeto"] };
      else if (pathname === "/v1/models")
        data = {
          models: [
            { id: "gpt-6-astra", name: "GPT-6 Astra", backend: "codex", execution_modes: executionModes("codex"), efforts: ["low", "medium", "high"] },
            { id: "claude-sonnet-5-5", name: "Claude Sonnet 5.5", backend: "claude", execution_modes: executionModes("claude"), efforts: ["configured"] },
          ],
          providers: { codex: true, claude: true },
          uploads_enabled: false,
        };
      else if (pathname === "/v1/conversations") data = { conversations: [] };
      else if (pathname === "/v1/version") data = { version: "fixture", build: "harness-agents" };
      else if (pathname === "/v1/catalog") data = { agents: [], skills: [], warnings: [] };
      else if (pathname === "/v1/resources") data = { engine: "codex", items: agents.map(resourceItem), warnings: [] };
      else if (pathname === "/v1/harness-agents" && method === "GET") data = { agents: agents.map((a) => ({ ...a, available: true })) };
      else if (pathname === "/v1/harness-agents" && method === "POST") {
        const body = route.request().postDataJSON();
        requests.push(["POST", body]);
        if (agents.some((a) => a.id === body.name)) {
          status = 409;
          data = { code: "harness_agent_exists", field: "name" };
        } else {
          const agent = { ...body, id: body.name, revision: "r1" };
          agents.push(agent);
          status = 201;
          data = agent;
        }
      } else if (pathname.startsWith("/v1/harness-agents/")) {
        const id = decodeURIComponent(pathname.split("/").pop()),
          body = route.request().postDataJSON(),
          index = agents.findIndex((a) => a.id === id);
        requests.push([method, body, id]);
        if (method === "PUT") {
          agents[index] = { ...agents[index], ...body, revision: "r2" };
          delete agents[index].revision_sent;
          data = agents[index];
        } else if (method === "DELETE") {
          agents.splice(index, 1);
          data = { deleted: true };
        }
      } else if (pathname === "/v1/jobs" && method === "POST") {
        const body = route.request().postDataJSON();
        jobs.push(body);
        data = { job_id: "j1", backend: body.backend, model: body.model, execution_mode: "native" };
      } else if (pathname === "/v1/jobs/j1/events") return route.fulfill({ body: "", contentType: "text/event-stream" });
      else if (pathname === "/v1/jobs/j1")
        data = { id: "j1", project: "sem-projeto", state: "completed", request: jobs.at(-1), result: { answer: "A cat on a red chair." } };
      return route.fulfill({ status, json: data });
    });
    await page.addInitScript(() => localStorage.setItem("keepharness-tour-seen", "0.16.0"));
    await page.goto("http://agents.test/");
    await page.locator("#startup-gate").waitFor({ state: "hidden" });
    await page.locator("#model").selectOption("gpt-6-astra");

    // The rail opens Plugins; Settings > Agents holds "Your agents", empty at first.
    await page.click("#rail-agents");
    await page.click('[data-settings="agents"]');
    const settings = page.locator("#settings-dialog");
    await settings.getByText("No agents yet.", { exact: false }).waitFor();
    await settings.getByRole("button", { name: "Create agent" }).click();
    const dialog = page.getByRole("dialog", { name: "Create agent" });
    await dialog.waitFor();

    // Client-side validation names the field.
    await dialog.getByRole("button", { name: "Create agent" }).click();
    assert.equal(await page.locator("#agent-name").getAttribute("aria-invalid"), "true");
    assert.match(await page.locator("#agent-form-error").innerText(), /lowercase/);
    assert.equal(requests.length, 0, "invalid forms are not sent");

    await dialog.getByLabel("Name").fill("photo-describer");
    await dialog.getByLabel("Purpose").fill("Describe images precisely for later analysis");
    await dialog.getByLabel("Instructions").fill("List objects, colors and visible text. Do not guess.");
    await dialog.getByLabel("Tasks").fill("Describe a photo\n\nRead signs and labels\n");
    await dialog.getByLabel("Target output").fill("A Markdown list");
    await dialog.getByLabel("Provider").selectOption("claude");
    assert.equal(await dialog.getByLabel("Model").inputValue(), "claude-sonnet-5-5");
    assert.equal(await dialog.getByLabel("Effort").inputValue(), "configured");
    await dialog.getByRole("button", { name: "Create agent" }).click();
    await dialog.waitFor({ state: "hidden" });
    assert.deepEqual(requests[0], [
      "POST",
      {
        name: "photo-describer",
        purpose: "Describe images precisely for later analysis",
        instructions: "List objects, colors and visible text. Do not guess.",
        tasks: ["Describe a photo", "Read signs and labels"],
        target_output: "A Markdown list",
        backend: "claude",
        model: "claude-sonnet-5-5",
        effort: "configured",
      },
    ]);
    const row = page.locator("#harness-agents-list .harness-agent");
    await row.waitFor();
    assert.match(await row.innerText(), /@@photo-describer[\s\S]*Describe images[\s\S]*Runs on Claude Sonnet 5\.5 · Claude Code/);

    // A duplicate name comes back from the server and marks the field.
    await settings.getByRole("button", { name: "Create agent" }).click();
    await dialog.getByLabel("Name").fill("photo-describer");
    await dialog.getByLabel("Purpose").fill("Duplicate");
    await dialog.getByLabel("Instructions").fill("Duplicate");
    await dialog.getByRole("button", { name: "Create agent" }).click();
    await page.locator("#agent-name[aria-invalid='true']").waitFor();
    assert.notEqual(await page.locator("#agent-form-error").innerText(), "");
    await dialog.getByRole("button", { name: "Cancel" }).click();

    // Use: the message carries @@name and the composer moves to the agent's route.
    await row.getByRole("button", { name: "Use @@photo-describer in the message" }).click();
    await page.waitForFunction(() => document.getElementById("prompt").value.includes("@@photo-describer "));
    assert.equal(await settings.isVisible(), false);
    assert.equal(await page.locator("#model").inputValue(), "claude-sonnet-5-5");
    await page.locator("#prompt").press("End");
    await page.locator("#prompt").type("what is in this photo?");
    await page.locator("#send").click();
    await page.waitForFunction(() => document.querySelectorAll("#messages article.user").length === 1);
    assert.equal(jobs[0].backend, "claude");
    assert.equal(jobs[0].model, "claude-sonnet-5-5");
    assert.deepEqual(jobs[0].resource_selections, [
      { id: "harness/agents/photo-describer", revision: "r1", token: "@@photo-describer" },
    ]);

    // The agent keeps its route: switching the model ends its conversation on the next send.
    await page.click("#new");
    await page.evaluate(() => setActivePersona({
      name: "photo-describer",
      resource_id: "harness/agents/photo-describer",
      route: { backend: "claude", model: "claude-sonnet-5-5", effort: "configured" },
    }));
    await page.locator("#model").selectOption("gpt-6-astra");
    await page.locator("#prompt").fill("Now reflect on that description");
    await page.locator("#send").click();
    await page.waitForFunction(() => document.querySelectorAll("#messages article.user").length === 1);
    assert.equal(jobs[1].release_persona, true);
    assert.equal(jobs[1].model, "gpt-6-astra");
    assert.equal(await page.locator("#persona-control").isVisible(), false);

    // The @ palette lists the agent under "Your agents" and offers Create agent….
    await page.click("#new");
    await page.locator("#prompt").fill("@");
    const menu = page.locator("#resource-menu");
    await menu.getByRole("option", { name: /photo-describer/ }).waitFor();
    assert.equal(await menu.locator(".resource-group h3").first().textContent(), "Your agents");
    assert(await menu.getByRole("button", { name: "Create agent…" }).isVisible());
    await page.keyboard.press("Escape");
    await page.locator("#prompt").fill("");

    // Edit sends the revision; delete asks once more, then removes it.
    await page.click("#rail-agents");
    await page.click('[data-settings="agents"]');
    await row.getByRole("button", { name: "Edit @@photo-describer" }).click();
    const edit = page.getByRole("dialog", { name: "Edit @@photo-describer" });
    await edit.waitFor();
    assert.equal(await edit.getByLabel("Name").isEditable(), false);
    assert.equal(await edit.getByLabel("Tasks").inputValue(), "Describe a photo\nRead signs and labels");
    await edit.getByLabel("Purpose").fill("Describe images for a blind reader");
    await edit.getByRole("button", { name: "Save agent" }).click();
    await edit.waitFor({ state: "hidden" });
    assert.equal(requests.at(-1)[0], "PUT");
    assert.equal(requests.at(-1)[1].revision, "r1");
    assert.match(await row.innerText(), /blind reader/);
    await row.getByRole("button", { name: "Edit @@photo-describer" }).click();
    await edit.getByRole("button", { name: "Delete agent" }).click();
    assert.equal(requests.at(-1)[0], "PUT", "the first click only asks for confirmation");
    await edit.getByRole("button", { name: "Confirm delete" }).click();
    await edit.waitFor({ state: "hidden" });
    assert.deepEqual(requests.at(-1), ["DELETE", { revision: "r2" }, "photo-describer"]);
    await settings.getByText("No agents yet.", { exact: false }).waitFor();
    console.log("PASS create, use, edit and delete Harness agents");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
