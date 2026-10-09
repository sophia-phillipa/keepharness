const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [],
    actions = [];
  p.on("pageerror", (e) => errors.push(e.message));
  p.on("dialog", (d) => d.accept());
  const empty = (id) => ({
    added: false,
    enabled: false,
    models: [],
    projects: ["sem-projeto"],
    permissions: {
      read: false,
      write: false,
      upload: false,
      shell: false,
      internet: false,
      hooks: false,
    },
    mode: ["local", "deepseek"].includes(id) ? "native" : "scoped",
    integrations: [],
  });
  const state = {
    settings: {
      services: Object.fromEntries(
        ["codex", "claude", "local", "deepseek"].map((id) => [id, empty(id)]),
      ),
      projects: [{ id: "demo", label: "Demo", root: "/workspace/demo" }],
      logins: [],
      port: 8095,
      tailnet_port: 8095,
      uploads_enabled: false,
    },
    inventory: {
      platform: "Linux",
      services: [
        { id: "codex", name: "Codex CLI", found: true },
        { id: "claude", name: "Claude Code", found: true },
        { id: "local", name: "Local model", found: false },
        { id: "deepseek", name: "DeepSeek", api: true, found: true },
      ],
      projects: [{ name: "Demo", path: "/workspace/demo" }],
      network: { online: true },
    },
    authentication: {},
    models: {},
    integrations: {
      codex: [{ id: "mcp:drive", name: "Drive", kind: "mcp" }],
      claude: [],
      local: [],
      deepseek: [],
    },
    operations: [],
    credentials: {},
    status: {
      running: false,
      local_url: "http://127.0.0.1:8095/",
      remote_url: "http://demo.tailnet:8095/",
      shared: false,
    },
  };
  const bundle = () => ({
    format: "keepharness-settings",
    version: 1,
    settings: state.settings,
    local_profile: {},
  });
  await p.route("**/api/**", async (route) => {
    const r = route.request(),
      name = new URL(r.url()).pathname.slice(5),
      data = r.method() === "POST" ? r.postDataJSON() : null;
    if (data) actions.push([name, data]);
    let result = {};
    if (name === "state") result = state;
    else if (name === "check")
      result = { authenticated: true, models: { "model-one": ["low"] } };
    else if (name === "provider-token") {
      state.credentials.deepseek = true;
      result = { saved: true };
    } else if (name === "provider-delete") {
      state.settings.services[data.provider] = empty(data.provider);
      result = { removed: true };
    } else if (name === "settings-export") result = bundle();
    else if (name === "settings-import") {
      result = {
        valid: true,
        applied: data.apply === true,
        services: ["codex"],
        projects: 1,
        local_profile: false,
      };
      if (data.apply) state.settings = data.bundle.settings;
    } else if (name === "settings") {
      state.settings = data;
      if (Object.values(data.services).some((s) => s.enabled))
        state.status.running = true;
      result = { saved: true };
    } else if (name === "start") {
      state.status.running = true;
      result = state.status;
    } else if (name === "stop") {
      state.status.running = false;
      result = state.status;
    } else if (name === "tailnet") {
      state.status.shared = data.enabled;
      result = state.status;
    } else if (name === "integration" || name === "model-install")
      result = { id: "test-operation", state: "running" };
    else if (name === "scan") result = state.inventory;
    return route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(result),
    });
  });
  await p.goto(process.env.ADMIN_URL || "http://127.0.0.1:8094/");
  await p.locator("[data-panel=providers]").click();
  await p.waitForSelector("#add-provider");
  assert.equal(await p.locator("#provider-wizard").isVisible(), false);
  assert.equal(await p.locator("#configured-providers article").count(), 0);
  await p.click("#add-provider");
  assert.equal(await p.locator("#dashboard").isVisible(), true);
  assert(actions.some((x) => x[0] === "scan"));
  await p.waitForSelector("#provider-dialog:not([hidden])");
  await p.screenshot({
    path: require("node:path").join(
      require("node:os").tmpdir(),
      "keepharness-wizard-new.png",
    ),
  });
  for (const width of [390, 768]) {
    await p.setViewportSize({ width, height: 844 });
    assert.equal(
      await p
        .locator("#provider-dialog")
        .evaluate((e) => e.scrollWidth > e.clientWidth),
      false,
    );
  }
  await p.setViewportSize({ width: 1440, height: 1000 });
  await p
    .locator("#provider-options")
    .getByText("Claude Code", { exact: false })
    .click();
  const claudeCard = p.locator("#provider-cards [data-provider=claude]");
  assert.match(await claudeCard.innerText(), /full versioned id/);
  assert.doesNotMatch(await claudeCard.innerText(), /CLI aliases/);
  await p.click("#wizard-back");
  await p
    .locator("#provider-options")
    .getByText("Codex", { exact: true })
    .click();
  const codex = p.locator("[data-provider=codex]");
  await codex.getByText("Check account", { exact: true }).click();
  await codex.getByLabel("model-one", { exact: true }).check();
  await codex
    .getByLabel("Make this service available", { exact: true })
    .check();
  await p.click("#wizard-next");
  assert.equal(await p.locator("#provider-wizard").isVisible(), false);
  const permissions = p.locator("#permission-editor");
  await p
    .locator("#inspector-tabs")
    .getByText("Model and hardware", { exact: true })
    .click();
  assert.match(
    await p.locator("#project-list").innerText(),
    /KeepHarness sidebar/,
  );
  const uploadsCopy = await p
    .locator("#uploads")
    .locator("xpath=..")
    .innerText();
  assert.match(uploadsCopy, /100 MiB per file/);
  assert.doesNotMatch(uploadsCopy, /50 MiB/);
  assert.equal(
    await permissions
      .getByLabel("Receive attachments", { exact: true })
      .count(),
    0,
  );
  await p.click("#wizard-next");
  await p.click("#save");
  await p.waitForFunction(() =>
    document.querySelector("#th-toast")?.textContent.includes("Settings saved"),
  );
  const saved = actions.filter((x) => x[0] === "settings").at(-1)[1];
  assert(saved.services.codex.added);
  assert(saved.services.codex.enabled);
  assert.deepEqual(saved.services.codex.models, ["model-one"]);
  assert(saved.projects.some((project) => project.id === "demo"));
  assert.deepEqual(
    saved.services.codex.integrations ?? [],
    [],
    "the per-provider allow list is no longer edited (#46)",
  );
  assert.equal(saved.uploads_enabled, false); // Effective cloud grants are normalized by the server, covered in Python.
  // The provider list re-renders after the save toast; wait for it instead of counting at once.
  await p.waitForFunction(
    () =>
      document.querySelectorAll("#configured-providers article").length === 1,
  );
  await p
    .locator("#configured-providers")
    .getByText("Edit", { exact: true })
    .click();
  assert.equal(
    await p.locator("#provider-cards .provider-meta button").count(),
    0,
  );
  assert.equal(
    await p
      .locator("[data-configured-provider=codex]")
      .getByRole("button", { name: /Check account/ })
      .count(),
    1,
  );
  for (const width of [390, 768, 1440]) {
    await p.setViewportSize({ width, height: 1000 });
    const gap = await p.evaluate(
      () =>
        document.querySelector("#provider-cards").getBoundingClientRect().top -
        document.querySelector("#inspector-tabs").getBoundingClientRect()
          .bottom,
    );
    assert(gap >= 16, `Editor gap at ${width}px: ${gap}`);
  }
  await p.screenshot({
    path: require("node:path").join(
      require("node:os").tmpdir(),
      "keepharness-provider-layout-fixed.png",
    ),
    fullPage: true,
  });
  await p.click("#wizard-cancel");
  await p.waitForFunction(
    () => document.querySelector("#provider-dialog").hidden,
  );
  assert.equal(await p.locator("#provider-wizard").isVisible(), false);
  assert.equal(
    await p.locator("#logins,#vpn-bind,#share,#save-network").count(),
    0,
  );
  await p.click("#add-provider");
  await p
    .locator("#provider-options")
    .getByText("Claude Code", { exact: false })
    .click();
  assert(await p.locator("#wizard-back").isVisible());
  assert.equal(
    await p.locator("#wizard-back").textContent(),
    "Choose another provider",
  );
  await p.click("#wizard-back");
  assert(await p.locator("#provider-options").isVisible());
  await p
    .locator("#provider-options")
    .getByText("DeepSeek", { exact: false })
    .click();
  const ds = p.locator("[data-provider=deepseek]");
  await ds.locator("input[type=password]").fill("fixture-deepseek-token");
  await ds.getByText("Save key and verify", { exact: true }).click();
  await ds.getByLabel("model-one", { exact: true }).check();
  await ds.getByLabel("Make this service available", { exact: true }).check();
  await p.click("#wizard-next");
  await p.click("#save");
  await p.waitForFunction(() =>
    document.querySelector("#th-toast")?.textContent.includes("Settings saved"),
  );
  await p.waitForFunction(
    () =>
      document.querySelectorAll("#configured-providers article").length === 2,
  );
  await p
    .locator("#configured-providers article")
    .filter({ hasText: "DeepSeek" })
    .getByText("Delete", { exact: true })
    .click();
  await p.waitForFunction(
    () =>
      document.querySelectorAll("#configured-providers article").length === 1,
  );
  await p.click("#manage-network");
  assert.equal(
    await p.locator("#network").evaluate((e) => e.matches(":modal")),
    true,
  );
  await p.keyboard.press("Escape");
  assert.equal(await p.locator("#network").isVisible(), false);
  assert.equal(
    await p
      .locator("#manage-network")
      .evaluate((e) => e === document.activeElement),
    true,
  );
  // D11: Full access stays out of the chat's access menu until the owner turns it on here.
  const fullAccess = p.locator("#full-access");
  assert.equal(await fullAccess.isChecked(), false);
  await fullAccess.check();
  await p.waitForFunction(
    () =>
      document.getElementById("full-access").checked &&
      !document.getElementById("full-access").disabled,
  );
  assert.equal(
    actions.filter((x) => x[0] === "settings").at(-1)[1].full_access,
    true,
  );
  assert.equal(state.settings.full_access, true);
  await p.click("#manage-network");
  for (const width of [390, 768]) {
    await p.setViewportSize({ width, height: 844 });
    assert.equal(
      await p
        .locator("#network")
        .evaluate((e) => e.scrollWidth > e.clientWidth),
      false,
    );
  }
  await p.setViewportSize({ width: 1440, height: 1000 });
  await p.locator("#import-settings").setInputFiles({
    name: "invalid.json",
    mimeType: "application/json",
    buffer: Buffer.from("{"),
  });
  await p.locator("#network-feedback:not([hidden])").waitFor();
  await p.locator("#import-settings").setInputFiles({
    name: "settings.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(bundle())),
  });
  await p.waitForFunction(
    () => !document.querySelector("#import-preview").hidden,
  );
  assert.equal(
    actions.filter((x) => x[0] === "settings-import" && x[1].apply === true)
      .length,
    0,
  );
  await p.click("#apply-import");
  await p.waitForFunction(() =>
    document
      .querySelector("#feedback")
      .textContent.includes("Configuration imported"),
  );
  await p.click("#manage-network");
  const downloading = p.waitForEvent("download");
  await p.click("#export-settings");
  const download = await downloading;
  assert.equal(download.suggestedFilename(), "keepharness-settings.json");
  await p.locator("#network-feedback:not([hidden])").waitFor();
  assert.equal(await p.locator("#network-close svg").count(), 1);
  assert(await p.locator("#network-close").getAttribute("title"));
  await p.click("#network-close");
  assert.equal(await p.locator("#network").isVisible(), false);
  await p.click("#theme");
  await p.locator("[data-theme-choice=arizona]").click();
  assert.equal(await p.locator("html").getAttribute("data-palette"), "arizona");
  await p.locator("[data-theme-choice=violet-bordeaux]").click();
  await p.click("#appearance-close");
  await p.screenshot({
    path: require("node:path").join(
      require("node:os").tmpdir(),
      "keepharness-admin-test-desktop.png",
    ),
    fullPage: true,
  });
  state.settings.services.local = {
    ...empty("local"),
    added: true,
    enabled: true,
    models: ["qwen-local"],
  };
  state.local_profile = {
    model_file: "/models/Qwen-test.gguf",
    performance: { "n-gpu-layers": "0", "n-cpu-moe": "8", "cpu-range": "6-13" },
  };
  await p.reload();
  await p
    .locator("[data-configured-provider=local]")
    .getByRole("button", { name: "Edit Local models", exact: true })
    .click();
  assert.equal(await p.locator("#provider-dialog[open]").count(), 1);
  assert.equal(await p.locator("#wizard-next").isVisible(), false);
  assert.equal(await p.locator("#save").isVisible(), true);
  await p.getByText("Qwen-test.gguf", { exact: true }).first().waitFor();
  assert.equal(
    await p
      .locator(".hardware-row")
      .filter({ hasText: "Requested layers" })
      .locator("strong")
      .textContent(),
    "0",
  );
  await p.locator("#inspector-tabs").getByText("Model and hardware").click();
  assert(await p.locator("#local-model-permissions").isVisible());
  await p.locator("#inspector-tabs").getByText("Model and hardware").click();
  await p.screenshot({
    path: require("node:path").join(
      require("node:os").tmpdir(),
      "keepharness-inspector.png",
    ),
    fullPage: true,
  });
  for (const width of [390, 768]) {
    await p.setViewportSize({ width, height: 844 });
    assert.equal(
      await p.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
    );
  }
  assert.deepEqual(errors, []);
  console.log(
    "PASS: dashboard, add/edit/delete wizard, BYOK, models, permissions, projects, uploads, lifecycle, import preview/apply/export, theme and responsive layout",
  );
  await b.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
