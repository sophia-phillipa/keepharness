// Composer: attachments, the "/" and "@@" palettes, the Files, Agents and Plugins chips,
// the native/isolated toggle and the draft.
"use strict";
const { home, newChat, chooseModel, ask } = require("../lib/app.cjs");
const { writeSamples } = require("../lib/files.cjs");

module.exports = {
  id: "composer",
  title: "Composer: attachments, palettes, chips",
  async run(op) {
    const page = op.page;
    const files = writeSamples();
    const chip = (name) => page.locator("#attachments").getByRole("button", { name: "Remove attachment " + name });
    const attach = async (file) => {
      await op.highlight(page.locator("#attach"));
      await page.locator("#file").setInputFiles(file);
    };
    const palette = page.locator("#resource-menu");
    try {
      await op.step("home", "Open a new chat in the Alpha research project with Claude", async () => {
        await home(op);
        if (op.fixtureMode) await op.click(page.locator("#sidebar").getByRole("button", { name: "New Conversation in Alpha research" }));
        else await newChat(op);
        await chooseModel(op, "claude-sonnet-5-5");
      }, { critical: true });

      for (const [key, label] of [["text", "a text file"], ["csv", "a CSV file"], ["pdf", "a PDF"], ["unicode", "a file with a Unicode name"], ["image", "an image"]])
        await op.step("attach-" + key, `Attach ${label}; its chip appears`, async () => {
          await attach(files[key]);
          await op.see(chip(require("node:path").basename(files[key])), 30000);
        });

      await op.step("image-preview", "The image chip shows a thumbnail", async () => {
        await op.see(page.locator("#attachments").getByRole("img", { name: "operator-photo.png" }));
      });

      await op.step("attach-duplicate", "Attaching the same file twice is refused", async () => {
        await attach(files.text);
        await op.seeText(page.locator("#status"), /already attached/);
        op.check((await chip("operator-notes.txt").count()) === 1, "the same file is attached twice");
      });

      await op.step("remove-attachment", "Remove the CSV chip", async () => {
        await op.click(chip("operator-data.csv"));
        await op.gone(chip("operator-data.csv"));
      });

      await op.step("size-limit", "A file over 100 MiB is refused with the limit", async () => {
        await attach(files.oversize);
        await op.seeText(page.locator("#status"), /100 MiB/);
        op.check((await chip("operator-oversize.bin").count()) === 0, "the oversize file was attached");
      });

      await op.step("empty-file", "An empty file is refused", async () => {
        await attach(files.empty);
        await op.until(async () => !(await page.locator("#status").innerText()).startsWith("Uploading"), "upload never settled", 20000);
        op.check((await chip("operator-empty.txt").count()) === 0, "an empty file was accepted as an attachment");
      });

      await op.step("send-attachments", "Send the attachments; the fixture reads the image", async () => {
        const answer = await ask(op, "OP-IMAGE describe the attachments", /received 1 image\(s\): image\/png/);
        op.check(/OPERATOR_OK/.test(answer), "no answer");
        op.check((await page.locator("#attachments").getByRole("button").count()) === 0, "chips stayed after sending");
      }, { fixtureOnly: true });

      await op.step("slash-open", 'Type "/" to open the agents, skills and commands palette', async () => {
        await newChat(op);
        if (op.fixtureMode) await op.click(page.locator("#sidebar").getByRole("button", { name: "New Conversation in Alpha research" }));
        await chooseModel(op, "claude-sonnet-5-5");
        await page.locator("#prompt").fill("");
        await page.locator("#prompt").click();
        await op.type("/");
        await op.see(palette);
        await op.seeText(palette, /BUILT-INS/i);
        op.check((await page.locator("#prompt").getAttribute("aria-expanded")) === "true", "the combobox is not expanded");
      });

      await op.step("slash-groups", "The palette groups project skills, commands and agents", async () => {
        await op.seeText(palette, /SKILLS · PROJECT/i);
        await op.seeText(palette, /COMMANDS · PROJECT/i);
        await op.seeText(palette, /AGENTS · PROJECT/i);
      }, { fixtureOnly: true });

      await op.step("slash-user-skills", "User-scope skills a run never loads are not offered as ready", async () => {
        await op.see(palette.getByRole("option", { name: /fixture-check/ }));
        const user = palette.getByRole("option", { name: /user-only/ });
        op.check(!(await user.count()) || (await user.isDisabled()), "a user-scope skill is offered as selectable");
      }, { fixtureOnly: true, recover: false });

      await op.step("slash-filter", "Typing filters the palette; the best match comes first", async () => {
        await page.locator("#prompt").fill("");
        await page.locator("#prompt").click();
        await op.type("/fixture-hel");
        await op.until(async () => /fixture-hello/.test(await palette.getByRole("option").first().innerText()), "fixture-hello is not the first match");
        op.check((await palette.getByRole("option").count()) < 7, "typing did not narrow the palette");
      }, { fixtureOnly: true });

      await op.step("slash-pick", "Pick the command with Enter; it becomes a chip in the message", async () => {
        if (!(await palette.isVisible())) {
          await page.locator("#prompt").fill("");
          await page.locator("#prompt").click();
          await op.type("/fixture-hel");
          await op.see(palette.getByRole("option", { name: /fixture-hello/ }));
        }
        await op.press("Enter");
        await op.until(async () => (await page.locator("#prompt").inputValue()).startsWith("/fixture-hello"), "the command was not inserted");
        await op.see(page.getByRole("button", { name: "Remove /fixture-hello" }));
        await op.click(page.getByRole("button", { name: "Remove /fixture-hello" }));
      }, { fixtureOnly: true });

      await op.step("slash-builtin", 'The "/model" built-in opens the model picker', async () => {
        await page.locator("#prompt").fill("");
        await page.locator("#prompt").click();
        await op.type("/model");
        await op.see(palette.getByRole("option", { name: /model Built-in/ }));
        await op.press("Enter");
        await op.until(async () => page.locator("#model-menu").evaluate((n) => n.matches(":popover-open")), "the model menu did not open");
        await op.press("Escape");
      });

      await op.step("slash-escape", "Escape closes the palette", async () => {
        await page.locator("#prompt").fill("");
        await page.locator("#prompt").click();
        await op.type("/");
        await op.see(palette);
        await op.press("Escape");
        await op.gone(palette);
        await page.locator("#prompt").fill("");
      });

      await op.step("at-agents", 'Type "@@" to list KeepHarness agents', async () => {
        await page.locator("#prompt").click();
        await op.type("@@");
        await op.see(palette);
        await op.seeText(palette, /KeepHarness agents/);
        await op.see(palette.getByRole("button", { name: "Create agent…" }));
        await op.press("Escape");
        await page.locator("#prompt").fill("");
      });

      await op.step("files-chip", "In a fresh chat, the Files chip offers uploads and pages without opening the Code panel", async () => {
        await newChat(op);
        if (op.fixtureMode) await op.click(page.locator("#sidebar").getByRole("button", { name: "New Conversation in Alpha research" }));
        await chooseModel(op, "claude-sonnet-5-5");
        await op.click(page.locator("#files-chip"));
        const menu = page.locator("#files-menu");
        await op.see(menu);
        await op.see(menu.getByRole("button", { name: "Upload…" }));
        await op.gone(page.locator("#activity-panel"));
        await op.press("Escape");
      });

      await op.step("agents-chip", "The Agents chip lists agents and offers Create agent", async () => {
        await op.click(page.locator("#agents-chip"));
        await op.see(page.getByRole("button", { name: "Create agent…" }));
        await op.press("Escape");
      });

      await op.step("plugins-chip", "The Plugins chip lists connectors for this provider", async () => {
        await op.click(page.locator("#plugins-chip"));
        const menu = page.locator("#plugins-menu");
        await op.see(menu);
        await op.seeText(menu, /Connectors and plugins/);
        if (op.fixtureMode) await op.seeText(menu, /personal setup, which is off/); // D01: no personal connector by default
        await op.see(menu.getByRole("button", { name: "Manage connectors and plugins" }));
        await op.press("Escape");
      });

      await op.step("isolation-on", "Turn on the isolated conversation switch", async () => {
        const toggle = page.locator("#isolation-toggle");
        if (await toggle.isDisabled()) op.skip("this model offers one execution mode");
        await op.click(toggle);
        await op.until(async () => (await toggle.getAttribute("aria-checked")) === "true", "isolation did not turn on");
        await op.seeText(page.locator("#execution-mode-label"), /Isolated/);
      });

      await op.step("isolation-off", "Turn it off again: back to a native conversation", async () => {
        const toggle = page.locator("#isolation-toggle");
        if (await toggle.isDisabled()) op.skip("this model offers one execution mode");
        await op.click(toggle);
        await op.until(async () => (await toggle.getAttribute("aria-checked")) === "false", "isolation did not turn off");
        await op.seeText(page.locator("#execution-mode-label"), /Native/);
      });

      await op.step("native-only", "A native-only model explains why isolation is unavailable", async () => {
        await chooseModel(op, "gemini-fixture");
        op.check(await page.locator("#isolation-toggle").isDisabled(), "the switch is enabled for a native-only model");
        await op.seeText(page.locator("#execution-mode-choice"), /only offers native/);
        await chooseModel(op, "claude-sonnet-5-5");
      }, { fixtureOnly: true });

      await op.step("shift-enter", "Shift+Enter adds a line instead of sending", async () => {
        await op.fill(page.locator("#prompt"), "first line");
        await op.press("Shift+Enter");
        await op.type("second line");
        op.check((await page.locator("#prompt").inputValue()) === "first line\nsecond line", "Shift+Enter did not add a line");
      });

      await op.step("draft-reload", "The draft survives a reload", async () => {
        await op.fill(page.locator("#prompt"), "Draft that survives a reload");
        await page.reload();
        await page.locator("#startup-gate").waitFor({ state: "hidden", timeout: 30000 });
        await op.until(async () => (await page.locator("#prompt").inputValue()) === "Draft that survives a reload", "the draft was lost");
        await page.locator("#prompt").fill("");
      });

      await op.step("composer-lint", "Composer controls do not overlap and stay inside the card", async () => {
        const boxes = await page.evaluate(() =>
          Object.fromEntries(["attach", "access-trigger", "model-trigger", "effort-trigger", "send", "prompt", "dropzone"].map((id) => {
            const r = document.getElementById(id)?.getBoundingClientRect();
            return [id, r && { l: r.left, r: r.right, t: r.top, b: r.bottom }];
          })),
        );
        const ids = ["attach", "access-trigger", "model-trigger", "effort-trigger", "send"];
        for (let i = 0; i < ids.length; i++)
          for (let j = i + 1; j < ids.length; j++) {
            const a = boxes[ids[i]], b = boxes[ids[j]];
            const w = Math.min(a.r, b.r) - Math.max(a.l, b.l), h = Math.min(a.b, b.b) - Math.max(a.t, b.t);
            op.check(!(w > 1 && h > 1), `#${ids[i]} overlaps #${ids[j]} by ${w.toFixed(1)}x${h.toFixed(1)} px`);
          }
        for (const id of ids) op.check(boxes[id].r <= boxes.dropzone.r + 1, `#${id} leaves the composer card`);
      });
    } finally {
      files.cleanup();
    }
  },
};
