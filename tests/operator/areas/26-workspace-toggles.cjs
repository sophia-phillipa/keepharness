// Workspace toggles (D-055, #78): the header switches for the wide layout, the file editor and the
// terminal, walked on the desktop app. Every step leaves the three switches off. The terminal opens a
// real shell session, so each step that opens it is a write (real mode needs --allow-writes). The
// per-provider steps send no prompt; each runs only in real mode and skips when its model is missing.
"use strict";
const { home, newChat, chooseModel, catalog } = require("../lib/app.cjs");

const WIDE = "#wide-toggle";
const EDITOR = "#editor-toggle";
const TERMINAL = "#terminal-toggle";
const NOT_ENROLLED = /not enrolled to approve actions yet/;
const PROVIDERS = [
  ["codex", "Codex", "gpt-5.6-sol"],
  ["claude", "Claude", "claude-sonnet-5-5"],
  ["deepseek", "DeepSeek", "deepseek-flash"],
];

module.exports = {
  id: "workspace-toggles",
  title: "Workspace toggles (wide layout, file editor, terminal)",
  async run(op) {
    const page = op.page;
    let size = null;
    const pressed = async (selector) => (await page.locator(selector).getAttribute("aria-pressed")) === "true";
    const wideOn = () => page.evaluate(() => document.body.classList.contains("wide-layout"));
    const editorPane = () => page.getByTestId("editor-pane");
    const terminalPane = () => page.getByTestId("terminal-pane");
    const messagesWidth = () => page.locator("#messages").evaluate((el) => el.getBoundingClientRect().width);

    // Fixture only: the terminal routes need an enrolled browser, enrolled with the same one-time link
    // as area 07 (real mode keeps whatever enrollment the owner already made).
    const enrollBrowser = async () => {
      if ((await page.context().cookies()).some((cookie) => cookie.name === "harness_session")) return;
      await page.goto(op.session.base + "/approve-device?nonce=" + encodeURIComponent(op.fixture.nonce));
      await op.click(page.getByRole("button", { name: "Enable approvals on this browser" }));
      await page.waitForURL((u) => !u.pathname.startsWith("/approve-device"), { timeout: 15000 }).catch(() => {});
    };

    // Fixture only: real mode never creates or picks a project; it uses whatever the composer shows
    // (a named project or "No project", which the terminal opens in the home folder).
    // Fixture mode picks the first named project, or creates one, then waits for the scope label.
    const ensureProject = async () => {
      if (!op.fixtureMode) return;
      const label = page.locator("#project-button-label");
      if (!/No project/.test(await label.innerText())) return;
      await op.click(page.locator("#project-button"));
      const named = page.locator("#project-menu").getByRole("option").filter({ hasNotText: /No project/ });
      if ((await named.count()) > 0) {
        await op.click(named.first());
      } else {
        await op.press("Escape");
        await op.click(page.locator("#add-project"));
        await op.see(page.locator("#project-dialog"));
        await op.fill(page.locator("#project-name"), "Operator workspace");
        await op.click(page.locator("#project-create"));
        await op.gone(page.locator("#project-dialog"), 20000);
        await op.click(page.locator("#project-button"));
        await op.click(page.locator("#project-menu").getByRole("option", { name: /Operator workspace/ }));
      }
      await op.until(async () => !/No project/.test(await label.innerText()), "no project was selected", 10000);
    };

    // Clicks one header switch until its aria-pressed reads the wanted state.
    const switchTo = async (selector, wanted) => {
      if ((await pressed(selector)) !== wanted) await op.click(page.locator(selector));
      await op.until(
        async () => (await pressed(selector)) === wanted,
        `${selector} did not turn ${wanted ? "on" : "off"}`,
      );
    };
    const wideTo = async (wanted) => {
      await switchTo(WIDE, wanted);
      await op.until(async () => (await wideOn()) === wanted, `body.wide-layout did not turn ${wanted ? "on" : "off"}`);
    };

    // Leaves the three switches off and the window at its starting size, whatever a step left behind.
    const restore = async () => {
      for (const selector of [TERMINAL, EDITOR, WIDE]) {
        if (await pressed(selector).catch(() => false)) await op.click(page.locator(selector)).catch(() => {});
      }
      if (size) await op.session.resize(size.width, size.height).catch(() => {});
    };

    try {
      await op.step(
        "open",
        "Start on the start screen and open a new chat",
        async () => {
          if (op.fixtureMode) await enrollBrowser();
          await home(op);
          await newChat(op);
        },
        { critical: true },
      );

      await op.step("wide-on-off", "Wide layout: the switch sets body.wide-layout and widens the messages column, then clears both", async () => {
        // The wide layout only uses the room the side panel leaves, so close the panel for this step.
        const panel = page.locator("#activity-panel");
        const reopen = await panel.isVisible();
        if (reopen) await op.click(page.locator("#panel-toggle"));
        try {
          if (reopen) await op.gone(panel);
          const narrow = await messagesWidth();
          const mainWidth = await page.locator("main").evaluate((el) => el.getBoundingClientRect().width);
          await wideTo(true);
          const wide = await messagesWidth();
          if (mainWidth >= 820) op.check(wide > narrow, `the messages column did not widen: ${narrow} -> ${wide}`);
          else op.check(wide >= narrow, `the messages column shrank in wide layout: ${narrow} -> ${wide}`);
          await wideTo(false);
        } finally {
          if (reopen) {
            await op.click(page.locator("#panel-toggle"));
            await op.see(panel);
          }
        }
      });

      await op.step("editor-on-off", "File editor: the switch opens the pane on its empty state, then closes it", async () => {
        await switchTo(EDITOR, true);
        await op.see(editorPane());
        await op.seeText(page.getByTestId("editor-empty"), /Open a file from the Files panel/);
        await switchTo(EDITOR, false);
        await op.gone(editorPane());
      });

      await op.step(
        "terminal",
        "Terminal: open it, wait for the cwd label, run echo, see the output, end the session, close the pane",
        async () => {
          await ensureProject();
          await switchTo(TERMINAL, true);
          await op.see(terminalPane());
          // A desktop window that was never enrolled cannot start a session: the pane says so instead.
          const notice = async () => (await page.getByTestId("terminal-notice").textContent()) || "";
          await op.until(
            async () =>
              ((await page.getByTestId("terminal-cwd").textContent()) || "").trim() !== "" ||
              NOT_ENROLLED.test(await notice()),
            "the terminal cwd label stayed empty",
            20000,
          );
          if (NOT_ENROLLED.test(await notice()))
            op.skip("this desktop window is not enrolled for approvals (keepharness approve-device --owner local)");
          await op.fill(page.getByTestId("terminal-input"), "echo kh-operator-ok");
          await op.press("Enter");
          // The typed line and the command output both contain the marker.
          await op.until(
            async () => ((await page.getByTestId("terminal-output").innerText()).match(/kh-operator-ok/g) || []).length >= 2,
            "the echo output did not appear in the terminal",
            20000,
          );
          await op.click(page.getByTestId("terminal-end-session"));
          await switchTo(TERMINAL, false);
          await op.gone(terminalPane());
        },
        { writes: true },
      );

      await op.step(
        "combined",
        "All three on: the editor sits beside the chat, nothing overflows horizontally, then all three off",
        async () => {
          await wideTo(true);
          await ensureProject();
          await switchTo(EDITOR, true);
          await switchTo(TERMINAL, true);
          await op.see(editorPane());
          await op.see(terminalPane());
          const measured = await page.evaluate(() => {
            const box = (selector) => document.querySelector(selector)?.getBoundingClientRect();
            const editor = box('[data-testid="editor-pane"]');
            const messages = box("#messages");
            return {
              editor: { left: editor.left, right: editor.right },
              messages: { left: messages.left, right: messages.right },
              overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
            };
          });
          op.check(measured.overflow <= 1, `the page overflows horizontally by ${measured.overflow}px`);
          op.check(
            measured.editor.left >= measured.messages.right - 1 || measured.editor.right <= measured.messages.left + 1,
            `the editor overlaps the chat: editor ${JSON.stringify(measured.editor)}, messages ${JSON.stringify(measured.messages)}`,
          );
          await switchTo(TERMINAL, false);
          await switchTo(EDITOR, false);
          await wideTo(false);
        },
        { writes: true },
      );

      for (const [key, name, id] of PROVIDERS) {
        await op.step(
          `provider-${key}`,
          `${name} (${id}): a new chat toggles the editor and the wide layout on and off, no prompt sent`,
          async () => {
            const { models } = await catalog(op);
            if (!models.some((m) => m.id === id)) op.skip(`${id} is not available on this machine`);
            await newChat(op);
            await chooseModel(op, id);
            await switchTo(EDITOR, true);
            await op.see(editorPane());
            await switchTo(EDITOR, false);
            await op.gone(editorPane());
            await wideTo(true);
            await wideTo(false);
          },
          { realOnly: true },
        );
      }

      await op.step(
        "narrow",
        "Narrow window 900x800: opening the terminal closes the editor overlay",
        async () => {
          size = await page.evaluate(() => ({ width: innerWidth, height: innerHeight }));
          if (!(await op.session.resize(900, 800))) op.skip("this target cannot resize the window");
          // The project picker sits under the header, so pick the project before the editor overlay opens.
          await ensureProject();
          await switchTo(EDITOR, true);
          await op.see(editorPane());
          op.check((await editorPane().getAttribute("aria-modal")) === "true", "the editor is not the overlay at 900 px");
          // The open overlay covers the header toggles below 1000 px, so the shortcut opens the terminal.
          await op.press("Control+Backquote");
          await op.see(terminalPane());
          await op.gone(editorPane());
          op.check(!(await pressed(EDITOR)), "the editor toggle stayed pressed");
          await switchTo(TERMINAL, false);
        },
        { writes: true },
      );

      // Real mode leaves no project behind: "Remove from list" hides the entry only. The folder and the
      // conversations stay; the "Delete folder" item is never used here.
      await op.step(
        "cleanup-project",
        'Remove the "Operator workspace" project from the list if an earlier run left it there',
        async () => {
          const trigger = page.getByRole("button", { name: "Actions for project Operator workspace", exact: true });
          if ((await trigger.count()) === 0) op.skip('no project named "Operator workspace" is listed');
          await op.click(trigger);
          await op.click(page.locator(".project-actions-menu:popover-open").getByRole("button", { name: "Remove from list" }));
          await op.gone(trigger);
        },
        { realOnly: true, writes: true, always: true },
      );
    } finally {
      await restore();
      // Later areas run as an unenrolled browser again, as area 07 leaves it.
      if (op.fixtureMode && op.session.target === "browser") await page.context().clearCookies({ name: "harness_session" }).catch(() => {});
    }
  },
};
