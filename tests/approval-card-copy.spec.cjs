// OP-R1-12, OP-R1-21, OP-R2-2: cards say what is asked in words, never a protocol name.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const { mount, run } = require("./run-console-fixture.cjs");
const RAW_KIND = "mcpServer/elicitation/request";
const MESSAGE =
  'Allow the master-jev-hook MCP server to run tool "request_decision"?';
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 1280, height: 800 },
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const pending = {
      ...run,
      kind: "approval",
      approval_id: "live-approval",
      approval_kind: RAW_KIND,
      request: { message: MESSAGE },
      expires_at: Date.now() / 1000 + 300,
    };
    await mount(page, async (url) => {
      if (url.pathname === "/v1/activity")
        return {
          json: {
            counts: { running: 1, queued: 0, needs_you: 1 },
            jobs: [run],
            providers: [],
            needs_you: [pending],
          },
        };
    });

    // The chat card shows the request's own message and keeps the protocol name out of sight.
    await page.evaluate((data) => showApproval(data), {
      approval_id: "chat-approval",
      kind: RAW_KIND,
      can_remember: false,
      request: { message: MESSAGE },
    });
    const card = page.locator("#approval-chat-approval");
    assert.match(
      await card.innerText(),
      /Allow the master-jev-hook MCP server to run tool "request_decision"\?/,
    );
    assert.equal(
      await card.locator("details").evaluate((node) => node.open),
      false,
    );
    const visible = await card.evaluate((node) =>
      [...node.children]
        .filter(
          (child) => child.offsetParent !== null && child.tagName !== "DETAILS",
        )
        .map((child) => child.innerText)
        .join("\n"),
    );
    assert.doesNotMatch(visible, /mcpServer|elicitation/);

    // So does the Needs you inbox.
    await page.locator("#attention-bell").click();
    await page.locator("#attention-open-inbox").click();
    const inbox = page.getByRole("dialog", { name: "Needs you", exact: true });
    await inbox.waitFor();
    assert.match(
      await inbox.innerText(),
      /Allow the master-jev-hook MCP server/,
    );
    assert.doesNotMatch(await inbox.innerText(), /elicitation\/request/);

    // Publication enforcement is described, not echoed.
    const labels = await page.evaluate(() => [
      publicationLabel("mediated"),
      publicationLabel("unenforced"),
      publicationLabel(undefined),
    ]);
    assert.deepEqual(labels, [
      "Sent through KeepHarness",
      "Not controlled by KeepHarness",
      "Not controlled by KeepHarness",
    ]);
    assert.deepEqual(errors, []);
    console.log(
      "PASS: approval cards show the request message, no protocol names, publication wording",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
