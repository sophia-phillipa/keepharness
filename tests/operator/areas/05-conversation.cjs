// Conversation view: streaming, Stop, reload and resume, route dividers, Markdown tables
// and code, image thumbnails, provider errors and "View run".
"use strict";
const { home, newChat, chooseModel, send, ask } = require("../lib/app.cjs");
const { writeSamples } = require("../lib/files.cjs");

module.exports = {
  id: "conversation",
  title: "Conversation view",
  async run(op) {
    const page = op.page;
    const messages = page.locator("#messages");
    const pill = page.locator("#conversation-state-pill");
    const length = async () => (await messages.innerText()).length;

    await op.step("home", "Open a new conversation with Claude", async () => {
      await home(op);
      await newChat(op);
      await chooseModel(op, "claude-sonnet-5-5");
    }, { critical: true });

    await op.step("streaming", "A long answer streams in piece by piece", async () => {
      await send(op, "OP-SLOW stream a long answer");
      await op.seeText(messages, /chunk-02/, 20000);
      const early = await length();
      await op.seeText(messages, /chunk-06/, 20000);
      op.check((await length()) > early, "the answer did not grow while streaming");
      await op.seeText(pill, /Running|Working|Streaming/i, 5000);
    }, { fixtureOnly: true });

    await op.step("stop", "Stop the running answer", async () => {
      await op.click(page.locator("#cancel"));
      await op.until(async () => !/Running|Working|Streaming/i.test(await pill.innerText()), "the run kept running after Stop", 20000);
      await op.seeText(pill, /Cancel|Stop/i, 5000);
      const text = await messages.innerText();
      op.check(!/chunk-39/.test(text), "the stopped answer still finished");
    }, { fixtureOnly: true });

    await op.step("reload-resume", "Reload in the middle of an answer; it resumes and finishes", async () => {
      await send(op, "OP-SLOW stream again, then reload");
      await op.seeText(messages, /chunk-03/, 20000);
      const title = await page.locator("#conversation-title").innerText();
      await page.reload();
      await page.locator("#startup-gate").waitFor({ state: "hidden", timeout: 30000 });
      await op.seeText(page.locator("#conversation-title"), title.slice(0, 20), 15000);
      await op.seeText(messages, /chunk-39 OPERATOR_OK/, 40000);
    }, { fixtureOnly: true });

    await op.step("divider-model", "Switch model mid-conversation: a divider marks the change", async () => {
      await chooseModel(op, "claude-opus-5-5");
      await ask(op, "After the model switch", /OPERATOR_OK from the claude-opus-5-5/);
      await op.see(messages.getByRole("note").filter({ hasText: /Model changed to/ }));
    }, { fixtureOnly: true });

    await op.step("divider-provider", "Switch provider: the divider says the conversation goes along", async () => {
      await chooseModel(op, "gemini-fixture");
      await ask(op, "After the provider switch", /OPERATOR_OK from the gemini-fixture Gemini fixture/);
      await op.see(messages.getByRole("note").filter({ hasText: /Switched to .* the conversation so far goes with it/ }));
    }, { fixtureOnly: true });

    await op.step("markdown-table", "A Markdown table and a code block render as such", async () => {
      await newChat(op);
      await chooseModel(op, "claude-sonnet-5-5");
      await ask(op, "OP-TABLE show the matrix");
      op.check((await messages.locator("table").last().locator("th").count()) === 7, "the table does not have 7 columns");
      await op.see(messages.locator("pre code").last());
    }, { fixtureOnly: true });

    await op.step("table-headers-whole", "Single-word table headers stay on one line", async () => {
      const broken = await messages.locator("table").last().evaluate((table) =>
        [...table.querySelectorAll("th")].filter((th) => {
          const range = document.createRange();
          range.selectNodeContents(th);
          return !/\s/.test(th.textContent.trim()) && new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size > 1;
        }).map((th) => th.textContent.trim()),
      );
      op.check(!broken.length, "headers broken mid-word: " + broken.join(", "));
    }, { fixtureOnly: true, recover: false });

    await op.step("code-scrolls", "A long code line scrolls inside its block", async () => {
      const ok = await messages.locator("pre").last().evaluate((pre) => {
        for (let node = pre; node && node.id !== "messages"; node = node.parentElement)
          if (/(auto|scroll)/.test(getComputedStyle(node).overflowX)) return true;
        return false;
      });
      op.check(ok, "the long code line is cut without a scroll container");
    }, { fixtureOnly: true, recover: false });

    await op.step("copy-answer", "An answer offers a Copy button", async () => {
      op.check((await messages.getByRole("button", { name: /Copy/ }).count()) > 0, "no Copy button on answers or code");
    }, { fixtureOnly: true, recover: false });

    await op.step("image-thumbnail", "A sent image shows as a thumbnail in the conversation", async () => {
      const files = writeSamples();
      try {
        await page.locator("#file").setInputFiles(files.image);
        await op.see(page.locator("#attachments").getByRole("img", { name: "operator-photo.png" }), 30000);
        await ask(op, "OP-IMAGE what is in this picture?", /received 1 image/);
        await op.see(messages.getByRole("img", { name: "operator-photo.png" }).last());
      } finally {
        files.cleanup();
      }
    }, { fixtureOnly: true });

    await op.step("image-thumbnail-whole", "The thumbnail shows the whole image (not cropped)", async () => {
      const fit = await messages.getByRole("img", { name: "operator-photo.png" }).last().evaluate((img) => getComputedStyle(img).objectFit);
      op.check(fit !== "cover", "the thumbnail is cropped (object-fit: cover)");
    }, { fixtureOnly: true, recover: false });

    await op.step("provider-error", "A provider error reads as a sentence, not a code", async () => {
      await send(op, "OP-ERROR fail on purpose");
      await op.until(async () => /stopped|failed|error|couldn/i.test(await messages.innerText()), "no error message appeared", 30000);
      await op.until(async () => !/Running/i.test(await pill.innerText()), "the failed run kept running", 20000);
      const text = await messages.innerText();
      op.check(!/claude_execution_failed/.test(text), "the raw error code is shown");
    }, { fixtureOnly: true });

    await op.step("real-answer", "Send one short real prompt and read the answer", async () => {
      await newChat(op);
      await chooseModel(op);
      await ask(op, "Reply with exactly the word READY and nothing else.", /READY/i);
    }, { realOnly: true });

    await op.step("view-run", "Open the latest answer's run with View run", async () => {
      const button = messages.getByRole("button", { name: "View run" }).last();
      if (!(await button.count())) op.skip("no finished answer on screen");
      await op.click(button);
      await op.see(page.locator("#run-console"));
      await op.seeText(page.locator("#run-console"), /Pipeline|Timeline/);
    });
  },
};
