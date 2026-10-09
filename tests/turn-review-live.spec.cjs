// Real-backend regression for the turn review (#57, D-053): a completed turn lists the files it
// changed under its own answer, read from GET /v1/jobs/{job}/file-changes. The harness runs with
// the fake Codex and Claude CLIs (temporary_chat_server.py --fake-edits); no inference.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const net = require("node:net");
const { spawn } = require("node:child_process");
const { once } = require("node:events");

const CODEX = { model: "gpt-6-astra", effort: "low", answer: "Done." };
const CLAUDE = {
  model: "claude-sonnet-5-5",
  effort: "configured",
  answer: "Done: the edits are in place.",
};
const FAKE_EDITS = "FAKE-EDITS change the files";
const PLAIN = "Only answer, no edits";
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Waits for the turn's file-changes response, then the answer's end state (idle composer).
async function runTurn(page, provider, prompt, index) {
  await page.waitForFunction(
    (model) =>
      [...document.querySelectorAll("#model option")].some(
        (o) => o.value === model,
      ),
    provider.model,
  );
  await page.locator("#model").selectOption(provider.model);
  await page.locator("#effort").selectOption(provider.effort);
  await page.fill("#prompt", prompt);
  const changes = page.waitForResponse((response) =>
    /^\/v1\/jobs\/[^/]+\/file-changes$/.test(new URL(response.url()).pathname),
  );
  const posted = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/v1/jobs" &&
      response.request().method() === "POST",
  );
  await page.click("#send");
  const sent = await posted;
  assert.equal(sent.status(), 202, await sent.text());
  const review = await changes;
  assert.equal(review.status(), 200);
  const answer = page.locator("#messages .message.assistant").nth(index - 1);
  await answer.getByText(provider.answer).waitFor();
  await page.waitForFunction(() => !busy && !submitting);
  return { answer, data: await review.json() };
}

// Rows as [path, op] pairs, in the order the list shows them.
async function rowsOf(list) {
  return list
    .locator('[data-testid="turn-review-file"]')
    .evaluateAll((rows) =>
      rows.map((row) => [
        row.querySelector(".turn-review-path").textContent,
        row.querySelector('[data-testid="turn-review-op"]').textContent,
      ]),
    );
}

function diffBoxes(answer) {
  return answer.locator('[data-testid="turn-review-diff"]');
}

(async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "turn-review-live-"));
  const socket = net.createServer();
  socket.listen(0, "127.0.0.1");
  await once(socket, "listening");
  const port = socket.address().port;
  await new Promise((resolve) => socket.close(resolve));
  const origin = `http://127.0.0.1:${port}`;
  const log = await fs.open(path.join(root, "server.log"), "w");
  const server = spawn(
    process.env.PYTHON || "python3",
    [
      "tests/fixtures/temporary_chat_server.py",
      "--root",
      root,
      "--port",
      String(port),
      "--fake-edits",
    ],
    { stdio: ["ignore", log.fd, log.fd] },
  );
  let browser;
  try {
    let ready = false;
    for (let i = 0; i < 100; i++) {
      if (server.exitCode !== null)
        throw Error(await fs.readFile(path.join(root, "server.log"), "utf8"));
      try {
        if ((await fetch(origin + "/v1/version")).ok) {
          ready = true;
          break;
        }
      } catch {}
      await delay(100);
    }
    assert(ready, "fixture server ready");
    browser = await chromium.launch();
    const page = await browser.newPage({
      viewport: { width: 1280, height: 860 },
    });
    page.setDefaultTimeout(30000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.addInitScript(() =>
      localStorage.setItem("keepharness-tour-seen", "0.16.0"),
    );
    await page.goto(origin);
    await page.locator("#startup-gate").waitFor({ state: "hidden" });

    // Codex: added, modified, moved, deleted, binary, oversized, outside-root rows; declined and
    // failed items are not captured.
    const codex = await runTurn(page, CODEX, FAKE_EDITS, 1);
    assert.equal(codex.data.state, "captured");
    assert.equal(codex.data.files.length, 7);
    const codexToggle = codex.answer.locator(
      '[data-testid="turn-review-toggle"]',
    );
    await codexToggle.waitFor();
    assert.equal(await codexToggle.textContent(), "7 files changed");
    await codexToggle.click();
    assert.equal(await codexToggle.getAttribute("aria-expanded"), "true");
    const codexList = codex.answer.locator('[data-testid="turn-review-list"]');
    assert.equal(await codexList.isVisible(), true);
    assert.deepEqual(await rowsOf(codexList), [
      ["src/new.py", "created"],
      ["src/app.py", "modified"],
      ["src/moved.py", "modified"],
      ["src/gone.py", "deleted"],
      ["src/image.bin", "created"],
      ["src/big.txt", "created"],
      ["path outside the project", "created"],
    ]);
    const codexRows = codex.answer.locator('[data-testid="turn-review-file"]');
    assert.match(
      await codexRows.nth(2).textContent(),
      /renamed from src\/old\.py/,
    );
    const codexDiffs = diffBoxes(codex.answer);
    assert.match(
      await codexDiffs.nth(4).textContent(),
      /Binary file: no text diff/,
    );
    assert.match(
      await codexDiffs.nth(5).textContent(),
      /Diff too large to show/,
    );
    assert.match(await codexDiffs.nth(6).textContent(), /Diff not available/);
    assert.equal(
      (
        await codex.answer.locator('[data-testid="turn-review"]').textContent()
      ).includes("outside secret"),
      false,
      "content of an edit outside the project never reaches the page",
    );
    console.log(
      "PASS codex: 7 rows, created/modified/deleted, binary/oversized/outside labels",
    );

    // Claude: Edit and MultiEdit, a failed Edit (absent), Write (unknown op) and Bash (shell note).
    const claude = await runTurn(page, CLAUDE, FAKE_EDITS, 2);
    assert.equal(claude.data.state, "captured");
    assert.equal(claude.data.shell_unattributed, true);
    assert.equal(claude.data.files.length, 3);
    const appEdits = claude.data.files.find(
      (file) => file.path === "src/app.py",
    ).edits;
    assert.equal(appEdits.length, 1, "the failed Edit leaves no edit record");
    const claudeToggle = claude.answer.locator(
      '[data-testid="turn-review-toggle"]',
    );
    await claudeToggle.waitFor();
    assert.equal(await claudeToggle.textContent(), "3 files changed");
    await claudeToggle.click();
    const claudeList = claude.answer.locator(
      '[data-testid="turn-review-list"]',
    );
    assert.deepEqual(await rowsOf(claudeList), [
      ["src/app.py", "modified"],
      ["src/util.py", "modified"],
      ["notes/todo.md", "unknown"],
    ]);
    const claudeAppDiff = await diffBoxes(claude.answer).nth(0).textContent();
    assert.match(claudeAppDiff, /\+a = 2/);
    assert.equal(
      claudeAppDiff.includes("b = 2"),
      false,
      "failed Edit diff is absent",
    );
    assert.equal(
      await claude.answer
        .locator('[data-testid="turn-review-shell"]')
        .textContent(),
      "Shell commands may have changed other files.",
    );
    console.log("PASS claude: 3 rows, failed Edit absent, shell note shown");

    // Plain Codex prompt in a new conversation: the fake Codex has no thread/resume, so a switch
    // back to Codex inside the editing conversation cannot run here. The sidebar's New conversation starts one.
    await page.locator("#new").click();
    const plain = await runTurn(page, CODEX, PLAIN, 1);
    assert.equal(
      await page.locator("#messages .message.assistant").count(),
      1,
      "the plain prompt runs in a conversation of its own",
    );
    assert.equal(plain.data.state, "none");
    assert.deepEqual(plain.data.files, []);
    await page.evaluate(
      () => new Promise((resolve) => requestAnimationFrame(() => resolve())),
    );
    assert.equal(
      await plain.answer.locator('[data-testid="turn-review-toggle"]').count(),
      0,
    );
    assert.equal(
      await plain.answer.locator('[data-testid="turn-review-list"]').count(),
      0,
    );
    console.log("PASS plain: answer without edits shows no turn-review list");

    assert.deepEqual(errors, []);
    await browser.close();
    browser = null;
    server.kill("SIGTERM");
    await once(server, "exit");
    console.log("PASS shutdown: harness stopped");
  } finally {
    if (browser) await browser.close();
    if (server.exitCode === null) {
      server.kill("SIGTERM");
      await once(server, "exit");
    }
    await log.close();
    await fs.rm(root, { recursive: true, force: true });
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
