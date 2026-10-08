const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

// Changes made outside KeepHarness (issue #43, D-039 item 8, D-041 item 9): the notices block on the
// Plugins page and on Settings > Providers, one toast per notice id per page session, and the ack.
const item = (id, name) => ({ id, name, kind: "plugin", status: "installed" });
const GITHUB = item("plugin:github@openai-curated", "GitHub");
const LINEAR = item("plugin:linear@official", "Linear");
const CATALOGS = {
  codex: { items: [GITHUB], warnings: [] },
  claude: { items: [LINEAR], warnings: [] },
};
const stateItem = (id, name, enabled) => ({
  id,
  kind: "plugin",
  name,
  scope: "user",
  enabled,
  source: "config.toml",
  writable: true,
  reason: "",
  affects: [],
});
const snapshot = (provider, items) => ({
  provider,
  engine: provider,
  project_root: null,
  items,
  fingerprint: "fp-" + provider,
  cli_version: "1.0.0",
  warnings: [],
});
const notice = (n, extra) => ({
  id: "n_" + String(n).padStart(16, "0"),
  item_id: GITHUB.id,
  name: "github",
  change: "changed",
  before: true,
  after: false,
  source: "config.toml",
  detected_at: "2026-10-08T10:42:00Z",
  ...extra,
});
const N1 = notice(1);
const N3 = notice(3, {
  item_id: LINEAR.id,
  name: "linear",
  change: "reverted",
  before: true,
  after: false,
  source: ".claude.json",
});
const N4 = notice(4, {
  item_id: 'plugin:<img src=x onerror="window.__pwned=1">',
  change: "added",
  before: null,
  after: true,
  name: "evil",
  source: "<b>evil</b>.toml",
});
const N6 = notice(6, {
  item_id: "plugin:slack@official",
  name: "slack",
  change: "reverted",
  before: false,
  after: true,
  source: ".claude.json",
});
const N7 = notice(7, {
  item_id: "plugin:notion@official",
  name: "notion",
  change: "changed",
  before: false,
  after: true,
});
const N5 = notice(5, {
  item_id: "plugin:figma@openai-curated",
  name: "figma",
  change: "removed",
  before: true,
  after: null,
});

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
    });
    await page.route("http://admin.test/**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith("/api/")) return route.fallback();
      const file = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
      return route.fulfill({
        body: await fs.readFile(
          path.join(
            __dirname,
            file.startsWith("assets/") ? "../harness_ui" : "../control",
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
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const service = () => ({
      added: true,
      enabled: true,
      models: ["m"],
      projects: ["sem-projeto"],
      mode: "native",
      integrations: [],
      permissions: {},
    });
    const state = {
      settings: {
        services: { codex: service(), claude: service() },
        projects: [],
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
          { id: "gemini", name: "Gemini CLI", found: false },
        ],
        projects: [],
        network: { online: true },
      },
      authentication: {},
      models: {},
      integrations: { codex: [], claude: [] },
      operations: [],
      credentials: {},
      status: { running: false },
    };
    // What the server holds as pending notices; an ack removes the ids it is given.
    const server = { codex: [N1], claude: [] };
    const snapshots = {
      codex: snapshot("codex", [stateItem(GITHUB.id, "github", false)]),
      claude: snapshot("claude", [stateItem(LINEAR.id, "linear", false)]),
    };
    const gets = [],
      acks = [];
    let getFailure = null,
      holdGet = null,
      conflict = null; // test hooks: a failing read, a read held back, a 409 for the next write
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      const name = url.pathname.replace(/^.*\/api\//, "");
      const method = route.request().method();
      if (name === "provider-state" && method === "GET") {
        const provider = url.searchParams.get("provider");
        gets.push(url.search);
        if (getFailure === provider)
          return route.fulfill({ status: 500, json: { error: "unreadable" } });
        const external = server[provider]; // answered with what the server held when the read arrived
        if (holdGet) await holdGet;
        return route.fulfill({
          json: { snapshot: snapshots[provider], external_changes: external },
        });
      }
      if (name === "provider-state" && method === "POST" && conflict) {
        const external = conflict;
        conflict = null;
        return route.fulfill({
          status: 409,
          json: {
            error: "provider_state_conflict",
            snapshot: snapshots.codex,
            external_changes: external,
          },
        });
      }
      if (name === "provider-state/notices:ack" && method === "POST") {
        const body = route.request().postDataJSON();
        acks.push({
          body,
          header: route.request().headers()["x-harness-admin"],
        });
        server[body.provider] = server[body.provider].filter(
          (n) => !body.notice_ids.includes(n.id),
        );
        return route.fulfill({ json: {} });
      }
      if (name === "integration-catalog")
        return route.fulfill({
          json: CATALOGS[route.request().postDataJSON().provider],
        });
      return route.fulfill({ json: name === "state" ? state : {} });
    });

    const pluginsBlock = page.locator("#plugins-notices");
    const providersBlock = page.locator("#provider-state-notices");
    const toast = page.locator("#th-toast");
    const shown = (block) => block.locator('[data-testid="provider-notice"]');
    const focusWindow = () =>
      page.evaluate(() => window.dispatchEvent(new Event("focus")));
    const hideToast = () =>
      page.evaluate(() => {
        document.getElementById("th-toast").hidden = true;
      });
    const setVisibility = (value) =>
      page.evaluate((v) => {
        Object.defineProperty(document, "visibilityState", {
          value: v,
          configurable: true,
        });
        document.dispatchEvent(new Event("visibilitychange"));
      }, value);
    // Several notices fold into a <details>; open it so the lines can be read and clicked.
    const unfold = async (block) => {
      const folded = block.locator("details");
      if ((await folded.count()) && !(await folded.evaluate((d) => d.open)))
        await folded.locator("summary").click();
    };
    const go = (hash) =>
      page.evaluate((h) => {
        location.hash = h;
      }, hash);

    // A single notice on the Plugins page: provider, item, change, file and time as text, a marker on its row, one toast.
    await page.goto("http://admin.test/#plugins");
    await shown(pluginsBlock).first().waitFor();
    assert.equal(await shown(pluginsBlock).count(), 1);
    assert.match(
      await shown(pluginsBlock).innerText(),
      /^Codex › plugin github@openai-curated was turned off \(config\.toml, \d\d:\d\d\)\.\s*Dismiss$/,
    );
    assert.equal(
      await shown(pluginsBlock).locator("code").innerText(),
      "github@openai-curated",
      "the item id is shown in <code>",
    );
    assert.equal(await pluginsBlock.getAttribute("role"), "region");
    assert.equal(
      await pluginsBlock.getAttribute("aria-label"),
      "Changes made outside KeepHarness",
    );
    assert.equal(
      await pluginsBlock.locator("strong").innerText(),
      "Changed outside KeepHarness",
    );
    assert.equal(
      await pluginsBlock.evaluate(
        (el) => el === el.closest("#plugins-panel").firstElementChild,
      ),
      true,
      "the block sits at the top of the page",
    );
    assert.match(
      await toast.innerText(),
      /^Changed outside KeepHarness: Codex › plugin github@openai-curated was turned off \(config\.toml, \d\d:\d\d\)\./,
    );
    await page.locator('[data-testid="plugins-mode"]').click();
    const rows = page.locator('[data-testid="plugin-row"]');
    await rows.first().waitFor();
    const row = (name) =>
      rows.filter({
        has: page.locator('[data-testid="plugin-name"]', {
          hasText: new RegExp("^" + name + "$"),
        }),
      });
    assert.equal(
      await row("GitHub").locator('[data-testid="plugin-changed"]').count(),
      1,
      "the row of the changed item is marked",
    );
    assert.equal(
      await row("Linear").locator('[data-testid="plugin-changed"]').count(),
      0,
    );
    assert.deepEqual(gets.sort(), [
      "?provider=claude&project_id=sem-projeto",
      "?provider=codex&project_id=sem-projeto",
    ]);

    // Settings > Providers shows the same notice; the toast does not fire again for the same id.
    await hideToast();
    await go("#providers");
    await shown(providersBlock).first().waitFor();
    assert.match(
      await shown(providersBlock).innerText(),
      /^Codex › plugin github@openai-curated was turned off/,
    );
    assert.equal(await providersBlock.isVisible(), true);
    assert.equal(
      await toast.isHidden(),
      true,
      "no second toast for the same notice id",
    );
    await go("#plugins");
    await shown(pluginsBlock).first().waitFor();
    assert.equal(
      await toast.isHidden(),
      true,
      "re-entering a screen does not toast again",
    );

    // A new id arriving on return to the tab toasts once. Several notices fold into a closed <details> (the contract's
    // collapse) and the reverted wording names the CLI, the item and the user's choice.
    server.claude = [N3];
    const before = gets.length;
    await focusWindow();
    await pluginsBlock.locator("details").waitFor();
    assert.equal(gets.length - before, 2, "focus re-reads both CLIs once");
    assert.equal(
      await pluginsBlock.locator("strong").innerText(),
      "2 changes outside KeepHarness",
    );
    assert.equal(
      await pluginsBlock.locator("details").evaluate((d) => d.open),
      false,
      "the lines start folded",
    );
    assert.equal(await shown(pluginsBlock).first().isHidden(), true);
    await unfold(pluginsBlock);
    assert.match(
      await shown(pluginsBlock).nth(1).innerText(),
      /^Reverted by Claude Code: plugin linear@official is off again \(\.claude\.json, \d\d:\d\d\)\. Your choice was on\.\s*Dismiss$/,
    );
    assert.match(
      await toast.innerText(),
      /^Reverted by Claude Code: plugin linear@official is off again/,
    );
    await hideToast();
    const reads = gets.length;
    await focusWindow();
    while (gets.length < reads + 2) await page.waitForTimeout(50);
    await page.waitForTimeout(100);
    assert.equal(
      await toast.isHidden(),
      true,
      "known ids are not announced again",
    );
    assert.equal(
      await pluginsBlock.locator("details").evaluate((d) => d.open),
      true,
      "a re-read keeps the lines unfolded",
    );

    // Coming back to the tab fires focus and visibilitychange together: one read per CLI; a hidden tab reads nothing.
    const together = gets.length;
    await setVisibility("hidden");
    await page.waitForTimeout(150);
    assert.equal(gets.length, together, "a hidden tab does not read");
    await page.evaluate(() => {
      Object.defineProperty(document, "visibilityState", {
        value: "visible",
        configurable: true,
      });
      window.dispatchEvent(new Event("focus"));
      document.dispatchEvent(new Event("visibilitychange"));
    });
    while (gets.length < together + 2) await page.waitForTimeout(50);
    await page.waitForTimeout(150);
    assert.equal(
      gets.length - together,
      2,
      "focus and visibilitychange together make one read per CLI",
    );
    server.claude = [N3, N6];
    await setVisibility("visible");
    await page.waitForFunction(
      () =>
        document.querySelectorAll(
          '#plugins-notices [data-testid="provider-notice"]',
        ).length === 3,
    );
    server.claude = [N3];
    await setVisibility("visible");
    await page.waitForFunction(
      () =>
        document.querySelectorAll(
          '#plugins-notices [data-testid="provider-notice"]',
        ).length === 2,
    );
    await hideToast();

    // A read that fails clears that CLI's old notices, and a toast waits for the screen it belongs to.
    getFailure = "claude";
    await focusWindow();
    await page.waitForFunction(
      () =>
        document.querySelectorAll(
          '#plugins-notices [data-testid="provider-notice"]',
        ).length === 1,
    );
    getFailure = null;
    server.codex = [N1, N7];
    let release;
    holdGet = new Promise((resolve) => {
      release = resolve;
    });
    await focusWindow();
    await go("#providers");
    await go("#connection");
    release();
    holdGet = null;
    await page.waitForTimeout(300);
    assert.equal(
      await toast.isHidden(),
      true,
      "no toast on a screen that does not own the notices",
    );
    await go("#plugins");
    await toast.waitFor({ state: "visible" });
    assert.match(
      await toast.innerText(),
      /^Changed outside KeepHarness: Codex › plugin notion@official was turned on/,
    );
    server.codex = [N1];
    server.claude = [N3];
    await focusWindow();
    await page.waitForFunction(
      () =>
        document.querySelectorAll(
          '#plugins-notices [data-testid="provider-notice"]',
        ).length === 2,
    );
    await unfold(pluginsBlock);

    // Dismiss one: the ack carries that id for that provider and scope, with the admin header; both screens drop it.
    // The dismissed notice is read again from the server (the contract's GET after the ack).
    const afterAck = gets.length;
    await page
      .getByRole("button", {
        name: /^Dismiss: Codex › plugin github@openai-curated was turned off/,
      })
      .click();
    await page.waitForFunction(
      () =>
        document.querySelectorAll(
          '#plugins-notices [data-testid="provider-notice"]',
        ).length === 1,
    );
    assert.deepEqual(acks, [
      {
        body: {
          provider: "codex",
          project_id: "sem-projeto",
          notice_ids: [N1.id],
        },
        header: "1",
      },
    ]);
    assert.equal(
      gets.length - afterAck,
      2,
      "an ack is followed by one read per CLI",
    );
    assert.equal(
      await row("GitHub").locator('[data-testid="plugin-changed"]').count(),
      0,
      "the marker goes with the notice",
    );
    assert.equal(
      await row("Linear").locator('[data-testid="plugin-changed"]').count(),
      1,
    );
    assert.equal(
      await pluginsBlock.locator("strong").innerText(),
      "Changed outside KeepHarness",
    );
    await go("#providers");
    await shown(providersBlock).first().waitFor();
    assert.equal(await shown(providersBlock).count(), 1);
    assert.match(
      await shown(providersBlock).innerText(),
      /^Reverted by Claude Code: plugin linear@official is off again/,
    );

    // A read sent before the ack and answered after it must not bring the dismissed notice back.
    const N9 = notice(9, {
      item_id: "plugin:late@official",
      name: "late",
      change: "added",
      before: null,
      after: true,
    });
    server.codex = [N9];
    server.claude = [];
    await focusWindow();
    await shown(providersBlock).first().waitFor();
    holdGet = new Promise((resolve) => {
      release = resolve;
    });
    await focusWindow();
    await page.waitForTimeout(100);
    await providersBlock
      .getByRole("button", {
        name: /^Dismiss: Codex › plugin late@official was added/,
      })
      .click();
    await page.waitForTimeout(100);
    release();
    holdGet = null;
    await providersBlock.waitFor({ state: "hidden" });
    await page.waitForTimeout(300);
    assert.equal(
      await providersBlock.isHidden(),
      true,
      "the late read does not bring the dismissed notice back",
    );
    await go("#plugins");
    await rows.first().waitFor();
    assert.equal(
      await pluginsBlock.isHidden(),
      true,
      "nor on the other screen",
    );
    await go("#providers");

    // Names and files are text, never HTML. Two providers with several notices each get a Dismiss all each.
    server.codex = [N4, N5];
    server.claude = [N3, N6];
    await focusWindow();
    await page.waitForFunction(
      () =>
        document.querySelectorAll(
          '#provider-state-notices [data-testid="provider-notice"]',
        ).length === 4,
    );
    await unfold(providersBlock);
    assert.equal(await providersBlock.locator("img, b").count(), 0);
    assert.equal(await page.evaluate(() => window.__pwned), undefined);
    assert.match(
      await providersBlock.innerText(),
      /Codex › plugin <img src=x onerror="window\.__pwned=1"> was added \(<b>evil<\/b>\.toml, \d\d:\d\d\)\./,
    );
    assert.match(
      await pluginsBlock.evaluate((el) => el.textContent),
      /<b>evil<\/b>\.toml/,
    );
    assert.match(
      await shown(providersBlock).filter({ hasText: "figma" }).innerText(),
      /^Codex › plugin figma@openai-curated was removed/,
    );
    assert.equal(
      await providersBlock
        .locator('[data-testid="provider-notice-dismiss-all"]')
        .count(),
      2,
      "each provider with several notices has its own Dismiss all",
    );
    await providersBlock
      .getByRole("button", { name: "Dismiss all Codex" })
      .click();
    await page.waitForFunction(
      () =>
        document.querySelectorAll(
          '#provider-state-notices [data-testid="provider-notice"]',
        ).length === 2,
    );
    assert.deepEqual(acks.at(-1).body, {
      provider: "codex",
      project_id: "sem-projeto",
      notice_ids: [N4.id, N5.id],
    });
    await page.waitForFunction(
      () =>
        document.activeElement ===
        document.querySelector("#provider-state-notices button"),
    );
    assert.equal(
      await page.evaluate(
        () =>
          document.activeElement.closest("#provider-state-notices") !== null,
      ),
      true,
      "focus stays in the notices block",
    );
    assert.equal(
      await providersBlock
        .getByRole("button", { name: "Dismiss all Codex" })
        .count(),
      0,
    );

    // The last dismiss hides the block on both screens; the focus lands on the heading, which is not left focusable.
    await providersBlock
      .getByRole("button", { name: "Dismiss all Claude Code" })
      .click();
    await providersBlock.waitFor({ state: "hidden" });
    assert.deepEqual(acks.at(-1).body, {
      provider: "claude",
      project_id: "sem-projeto",
      notice_ids: [N3.id, N6.id],
    });
    assert.equal(
      await page.evaluate(
        () => document.activeElement === document.querySelector("#overview h1"),
      ),
      true,
    );
    await page.locator("#add-provider").focus();
    assert.equal(
      await page.evaluate(() =>
        document.querySelector("#overview h1").hasAttribute("tabindex"),
      ),
      false,
      "the heading is not left in the tab order",
    );
    await go("#plugins");
    await rows.first().waitFor();
    assert.equal(await pluginsBlock.isHidden(), true);
    assert.equal(
      await page.locator('[data-testid="plugin-changed"]').count(),
      0,
    );

    // A 409 on a switch carries external_changes: the block, the row marker and the status all update.
    conflict = [notice(8, { change: "changed", before: false, after: true })];
    await page
      .getByRole("switch", { name: "GitHub in Codex", exact: true })
      .click();
    await shown(pluginsBlock).first().waitFor();
    assert.match(
      await shown(pluginsBlock).innerText(),
      /^Codex › plugin github@openai-curated was turned on \(config\.toml, \d\d:\d\d\)\./,
    );
    assert.equal(
      await row("GitHub").locator('[data-testid="plugin-changed"]').count(),
      1,
      "the 409 marks the changed row",
    );
    assert.match(
      await toast.innerText(),
      /^Changed outside KeepHarness: Codex › plugin github@openai-curated was turned on/,
    );

    // D-042: legacy folder sources and current file sources use the same wording on
    // both screens and in the toast, including on a narrow screen.
    await page.setViewportSize({ width: 390, height: 844 });
    for (const [n, source, file] of [
      [10, "foo", "SKILL.md"],
      [11, "SKILL.md", "SKILL.md"],
      [12, "settings.json", "settings.json"],
    ]) {
      const skillNotice = notice(n, {
        item_id: "skill:foo",
        name: "foo",
        change: "removed",
        before: true,
        after: null,
        source,
      });
      server.codex = [];
      server.claude = [skillNotice];
      await page.getByRole("button", { name: "Refresh", exact: true }).click();
      const line = pluginsBlock.locator(`[data-notice-id="${skillNotice.id}"]`);
      await line.waitFor();
      const sentence = await line.locator("span").innerText();
      assert.match(
        sentence,
        /^Claude Code › skill foo was removed \((SKILL\.md|settings\.json), \d\d:\d\d\)\.$/,
      );
      assert.ok(sentence.includes(`(${file}, `));
      assert.equal(
        sentence.match(/foo/g).length,
        1,
        "the skill name appears once",
      );
      assert.equal(
        await toast.innerText(),
        "Changed outside KeepHarness: " + sentence,
      );
      await hideToast();
      await go("#providers");
      const providerLine = providersBlock.locator(
        `[data-notice-id="${skillNotice.id}"]`,
      );
      await providerLine.waitFor();
      assert.equal(await providerLine.locator("span").innerText(), sentence);
      assert.equal(
        await toast.isHidden(),
        true,
        "switching screens does not repeat the toast",
      );
      await go("#plugins");
      await line.waitFor();
    }

    // No polling: an idle page makes no further reads (the contract's 7 s).
    const idle = gets.length;
    await page.waitForTimeout(7000);
    assert.equal(gets.length, idle);
    assert.deepEqual(errors, []);
    console.log("PASS admin provider notices");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
