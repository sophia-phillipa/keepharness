// Hooks and rules (#61, D-048, D-049): the read-only admin sections show structure and names,
// never a secret value, and flag a hook whose masked content changed on disk.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { home, adminOpenUrl, escapeRegex } = require("../lib/app.cjs");

const PROVIDERS = [
  ["codex", /Codex/],
  ["claude", /Claude/],
  ["deepseek", /DeepSeek/i],
];
const SECRETS = ["hunter2", "S3CRET", "u:p@"];
const command = (sig) =>
  `curl -H "Authorization: Bearer hunter2-TOKEN" https://u:p@example.com/x?sig=${sig}`;
const RULE_TAIL = "RULE_TAIL_MARKER";
const prose = () =>
  "# Team rules\n" +
  "Prefer small reviewed changes and explain each trade-off in plain words. ".repeat(
    220,
  ) +
  RULE_TAIL +
  "\n";

// Fixture only: the owner's Claude home inside the fixture's private HOME.
function seed(op, sig) {
  const dir = path.join(op.fixture.root, "home", ".claude");
  fs.mkdirSync(dir, { recursive: true });
  const settings = {
    env: { API_KEY: "hunter2" },
    hooks: {
      PreToolUse: [
        {
          matcher: "Bash",
          hooks: [
            {
              type: "command",
              command: command(sig),
              env: { API_KEY: "hunter2" },
            },
            {
              type: "http",
              url: "https://example.com/hook",
              headers: { "X-Api-Key": "hunter2" },
            },
          ],
        },
      ],
    },
  };
  fs.writeFileSync(
    path.join(dir, "settings.json"),
    JSON.stringify(settings, null, 2),
  );
  fs.writeFileSync(path.join(dir, "CLAUDE.md"), prose());
  return dir;
}

module.exports = {
  id: "hooks-rules",
  title: "Hooks and rules (read-only)",
  async run(op) {
    let page = op.page;
    let own = null;
    let dir = null;
    const list = () => page.getByTestId("plugins-list");
    const idle = () =>
      op.until(
        async () => (await list().getAttribute("aria-busy")) === "false",
        "the Plugins list kept loading",
        30000,
      );
    const section = (pattern) =>
      list()
        .locator("section")
        .filter({ has: page.locator("h2", { hasText: pattern }) });
    const state = (provider) =>
      page.evaluate(
        (p) =>
          fetch(
            "/api/provider-state?provider=" + p + "&project_id=sem-projeto",
          ).then((r) => r.text()),
        provider,
      );
    const noSecrets = (where, text) => {
      for (const secret of SECRETS)
        op.check(!text.includes(secret), `${where} contains "${secret}"`);
    };
    const open = async (chip) => {
      await op.click(page.getByTestId("plugins-chip-" + chip));
      await idle();
    };
    const refresh = async () => {
      await op.click(page.getByTestId("plugins-refresh"));
      await idle();
    };
    // A provider the fixture lacks has no section, or one that says its state is not readable.
    const connected = async (name, pattern) => {
      await open("hooks");
      const box = section(pattern);
      const text = (await box.count()) ? await box.innerText() : "";
      if (!text || /could not be read|not readable/.test(text))
        op.skip(
          `${name} is not connected in the fixture (it only has Claude and Gemini stand-ins)`,
        );
    };

    try {
      await op.step(
        "seed",
        "Seed hooks with secrets, an env entry, an http hook and a long rule file",
        async () => {
          dir = seed(op, "S3CRET");
        },
        { fixtureOnly: true, critical: true },
      );

      await op.step(
        "open",
        "Open Plugins in the admin and read the states",
        async () => {
          if (op.session.target === "browser") {
            own = await op.session.newPage();
            page = own;
            op.session.page = own;
          }
          await page.goto(adminOpenUrl(op));
          await page.goto(op.options.adminUrl + "/#plugins");
          await op.see(page.getByTestId("plugins-panel"));
          await idle();
          await op.see(page.getByTestId("plugins-chip-hooks"));
        },
        { fixtureOnly: true, critical: true },
      );

      for (const [name, pattern] of PROVIDERS) {
        const at = (id) => `${name}-${id}`;
        await op.step(
          at("hooks"),
          `${name}: the Hooks section shows the executable and option names`,
          async () => {
            await open("hooks");
            await connected(name, pattern);
            const text = await section(pattern).innerText();
            await op.seeText(section(pattern), /curl/);
            op.check(/-H/.test(text), "the option name -H is not shown");
            op.check(/PreToolUse/.test(text), "the event is not shown");
          },
          { fixtureOnly: true },
        );

        await op.step(
          at("no-leak"),
          `${name}: neither the page nor the provider-state JSON carries a secret`,
          async () => {
            await open("hooks");
            await connected(name, pattern);
            noSecrets("the Hooks page text", await list().innerText());
            noSecrets("the provider-state JSON", await state(name));
            await open("rules");
            noSecrets("the Rules page text", await list().innerText());
          },
          { fixtureOnly: true },
        );

        await op.step(
          at("names-only"),
          `${name}: env entries and headers show names only`,
          async () => {
            await open("hooks");
            await connected(name, pattern);
            const text = await section(pattern).innerText();
            op.check(/API_KEY/.test(text), "the env name API_KEY is not shown");
            op.check(
              /X-Api-Key/i.test(text),
              "the header name X-Api-Key is not shown",
            );
            op.check(
              !text.includes("hunter2"),
              "an env or header value is shown",
            );
          },
          { fixtureOnly: true },
        );

        await op.step(
          at("rules"),
          `${name}: the Rules section shows a capped preview and the best-effort line`,
          async () => {
            await connected(name, pattern);
            await open("rules");
            const box = section(pattern);
            await op.seeText(box, /Previews are best effort/);
            const preview = box.locator("details pre").first();
            if (!(await preview.count()))
              op.skip(`${name} has no rule file in the fixture`);
            await op.click(box.locator("details summary").first());
            const text = await preview.innerText();
            op.check(text.length > 100, "the preview is empty");
            op.check(
              text.length <= 8100,
              `the preview is ${text.length} characters, over the 8000 cap`,
            );
            op.check(
              !text.includes(RULE_TAIL),
              "the preview shows the whole long file, not a capped one",
            );
          },
          { fixtureOnly: true },
        );

        await op.step(
          at("read-only"),
          `${name}: no switches in either section, and each row shows its source path`,
          async () => {
            await connected(name, pattern);
            for (const chip of ["hooks", "rules"]) {
              await open(chip);
              const box = section(pattern);
              op.check(
                (await box.getByRole("switch").count()) === 0,
                `${chip} has a switch`,
              );
              op.check(
                (await box.getByRole("checkbox").count()) === 0,
                `${chip} has a checkbox`,
              );
              op.check(
                (await box.locator("[aria-checked]").count()) === 0,
                `${chip} has a toggle`,
              );
              await op.seeText(
                box.locator(".plugins-resource-source").first(),
                new RegExp(
                  escapeRegex(
                    path.join(
                      ".claude",
                      chip === "hooks" ? "settings.json" : "CLAUDE.md",
                    ),
                  ),
                ),
              );
            }
          },
          { fixtureOnly: true },
        );

        await op.step(
          at("changed"),
          `${name}: changing only the masked argument on disk marks the hook as changed`,
          async () => {
            await open("hooks");
            await connected(name, pattern);
            // Only Claude's home is seeded here; the notice needs the state read before the edit.
            seed(op, "OTHER-ARG");
            // Reads inside the server's short coalescing window reuse the earlier snapshot: refresh until it is re-read.
            const marker = section(pattern).getByTestId("resource-changed");
            await op.until(
              async () => {
                await refresh();
                await open("hooks");
                return (await marker.count()) > 0;
              },
              "no changed marker appeared after the masked argument changed",
              30000,
            );
            await op.see(marker.first());
            noSecrets(
              "the Hooks page text after the change",
              await list().innerText(),
            );
          },
          { fixtureOnly: true },
        );
      }
    } finally {
      if (dir) {
        fs.rmSync(path.join(dir, "settings.json"), { force: true });
        fs.rmSync(path.join(dir, "CLAUDE.md"), { force: true });
      }
      if (own) {
        await own.close().catch(() => {});
        op.session.page = op.session.context.pages()[0];
      } else if (op.session.target === "desktop")
        await home(op).catch(() => {});
    }
  },
};
