// Seven simulated profiles exercise publication evidence, human decisions and recovery.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const { mount, run } = require("./run-console-fixture.cjs");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(5000);
    const errors = [],
      posts = [],
      reconciliations = [];
    page.on("pageerror", (error) => errors.push(error.message));
    let denyAuthority = false,
      loseResponse = false,
      holdApproval,
      releaseApproval;
    let effectStatus = "unknown";
    let needsYou = [];
    const metadata = {
      effect_id: "effect-a",
      integration: "synthetic",
      operation: "jira.create_issue",
      endpoint: "https://jira.example.test",
      destination: "SYN",
      arguments: {},
      arguments_digest: "a".repeat(64),
      artifact_digest: "b".repeat(64),
      artifact_preview:
        '{"summary":"Synthetic <img src=x onerror=alert(1)> issue"}',
      enforcement: "unenforced",
    };
    const effectSpan = () => ({
      span_id: "effect-span",
      trace_id: "run-a",
      kind: "harness.effect",
      name: "Publish jira.create_issue",
      start_ts: 1,
      end_ts: 5,
      status: "unset",
      attrs: {
        ...metadata,
        effect_status: effectStatus,
        outcome: effectStatus,
        approved_by: "owner",
        gate_id: "gate-approved",
        receipt_issue_key: effectStatus === "done" ? "SYN-1" : undefined,
      },
      events: [
        "effect_prepared",
        "effect_approved",
        "effect_intent",
        "effect_execution",
        "effect_unknown",
      ].map((name) => ({ name, ts: 1, attrs: {} })),
    });
    await mount(page, async (url, request) => {
      if (url.pathname === "/v1/activity")
        return {
          json: {
            counts: { needs_you: needsYou.length },
            jobs: [run],
            needs_you: needsYou,
            providers: [],
          },
        };
      if (url.pathname.endsWith("/spans"))
        return { json: { spans: [effectSpan()] } };
      if (url.pathname.startsWith("/v1/approvals/")) {
        posts.push(request.postDataJSON());
        if (holdApproval) await holdApproval;
        if (denyAuthority)
          return { status: 403, json: { code: "approval_session_required" } };
        if (loseResponse)
          return {
            status: 503,
            json: { code: "unavailable", error: "Connection interrupted" },
          };
        needsYou = needsYou.filter(
          (item) => !url.pathname.endsWith("/" + item.gate_id),
        );
        return {
          json: {
            resolved: true,
            resolved_by: "owner",
            choice: posts.at(-1).choice,
          },
        };
      }
      if (url.pathname.endsWith("/reconcile")) {
        reconciliations.push(request.postDataJSON());
        if (denyAuthority)
          return { status: 403, json: { code: "approval_session_required" } };
        return { json: { effect_id: "effect-a", status: effectStatus } };
      }
    });
    let sequence = 0;
    async function gate(id, extra = {}) {
      await page.evaluate(
        ({ id, metadata, extra, sequence }) =>
          event({
            id: sequence,
            type: "gate_required",
            data: {
              ...metadata,
              ...extra,
              gate_id: id,
              kind: "publish",
              publish: true,
              question: "Publish this issue?",
              options: [
                { id: "approve", label: "Approve" },
                { id: "deny", label: "Deny" },
              ],
            },
          }),
        { id, metadata, extra, sequence: ++sequence },
      );
      return page.locator("#gate-" + id);
    }
    // P1 beginner: readable evidence, deny an unintended publication.
    let card = await gate("beginner");
    assert.match(await card.innerText(), /Publish approval/);
    for (const text of [
      "jira.create_issue",
      "https://jira.example.test",
      "Artifact digest",
      "Arguments",
      "unenforced",
    ])
      assert((await card.innerText()).includes(text));
    assert.equal(await card.locator("img").count(), 0);
    await card.getByRole("button", { name: "Deny", exact: true }).click();
    await page.waitForFunction(
      () =>
        document.querySelector("#gate-beginner").dataset.state === "resolved",
    );
    assert.match(await card.innerText(), /Publication denied/);
    // P2 rushed user: a held response disables both decisions and prevents duplicates.
    holdApproval = new Promise((resolve) => {
      releaseApproval = resolve;
    });
    card = await gate("rushed");
    await card.getByRole("button", { name: "Approve", exact: true }).click();
    assert.equal(await card.locator("button:enabled").count(), 0);
    await page.evaluate(() =>
      document.querySelector("#gate-rushed button").click(),
    );
    assert.equal(posts.length, 2);
    releaseApproval();
    holdApproval = null;
    await page.waitForFunction(
      () => document.querySelector("#gate-rushed").dataset.state === "resolved",
    );
    assert.match(await card.innerText(), /Publication approved/);
    holdApproval = new Promise((resolve) => {
      releaseApproval = resolve;
    });
    card = await gate("stale");
    await card.getByRole("button", { name: "Approve", exact: true }).click();
    await page.evaluate(
      (sequence) =>
        event({
          id: sequence,
          type: "gate_invalidated",
          data: { gate_id: "stale" },
        }),
      ++sequence,
    );
    releaseApproval();
    holdApproval = null;
    await page.waitForTimeout(100);
    assert.equal(await card.getAttribute("data-state"), "invalidated");
    // P3 domain professional: replay retains binding and invalidation explains re-asking.
    await page.evaluate(
      (metadata) =>
        restoreGates([
          {
            ...metadata,
            gate_id: "restored",
            kind: "publish",
            publish: true,
            state: "invalidated",
            question: "Publish?",
            options: [
              { id: "approve", label: "Approve" },
              { id: "deny", label: "Deny" },
            ],
          },
        ]),
      metadata,
    );
    assert.match(await page.locator("#gate-restored").innerText(), /ask again/);
    assert.equal(
      await page.locator("#gate-restored button:enabled").count(),
      0,
    );
    // P4 keyboard: human session failure is recoverable and focus remains usable.
    denyAuthority = true;
    card = await gate("keyboard");
    await card.getByRole("button", { name: "Approve", exact: true }).focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(() =>
      document
        .querySelector("#gate-keyboard [role=status]")
        .textContent.toLowerCase()
        .includes("enroll"),
    );
    assert.equal(await card.locator("button:enabled").count(), 2);
    denyAuthority = false;
    await page.keyboard.press("Enter");
    await page.waitForFunction(
      () =>
        document.querySelector("#gate-keyboard").dataset.state === "resolved",
    );
    assert.equal(
      await page
        .locator("#prompt")
        .evaluate((node) => node === document.activeElement),
      true,
    );
    // P5 mobile/network: failure preserves evidence; authoritative replay restores terminal state.
    await page.setViewportSize({ width: 400, height: 844 });
    await page.keyboard.press("Escape");
    loseResponse = true;
    card = await gate("mobile");
    await card.getByRole("button", { name: "Approve", exact: true }).click();
    await page.waitForFunction(
      () => document.querySelector("#gate-mobile").dataset.state === "pending",
    );
    assert((await card.innerText()).includes(metadata.artifact_digest));
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.evaluate(
      (sequence) =>
        event({
          id: sequence,
          type: "gate_resolved",
          data: { gate_id: "mobile", choice: "approve", resolved_by: "owner" },
        }),
      ++sequence,
    );
    assert.equal(await card.locator("button:enabled").count(), 0);
    // P6 engineer: unknown is visible; reconciliation requires explicit human decision and cannot retry.
    loseResponse = false;
    await page.locator("#run-status-toggle").click();
    assert.equal(
      await page.locator("#console-run").inputValue(),
      "run-a",
      "Pipeline binds the current run automatically",
    );
    await page
      .getByRole("button", { name: /Publish jira.create_issue/ })
      .click();
    const detail = page.getByRole("region", { name: "Span detail" });
    assert.match(await detail.innerText(), /unknown/);
    assert.match(await detail.innerText(), /owner/);
    assert.match(await detail.innerText(), /Intent/);
    assert.match(await detail.innerText(), /Execution/);
    await detail
      .getByRole("button", { name: "Reconcile", exact: true })
      .click();
    assert.equal(reconciliations.length, 0);
    denyAuthority = true;
    await detail
      .getByRole("button", { name: "Keep unknown", exact: true })
      .click();
    await page.waitForFunction(() =>
      document
        .querySelector(".effect-reconciliation [role=status]")
        .textContent.toLowerCase()
        .includes("enroll"),
    );
    assert.equal(
      await detail
        .getByRole("button", { name: "Keep unknown", exact: true })
        .isEnabled(),
      true,
    );
    denyAuthority = false;
    await detail
      .getByRole("button", { name: "Keep unknown", exact: true })
      .click();
    await page.waitForFunction(() =>
      document
        .querySelector(".effect-reconciliation [role=status]")
        .textContent.includes("unknown"),
    );
    assert.deepEqual(reconciliations, [
      { decision: "keep_unknown" },
      { decision: "keep_unknown" },
    ]);
    await detail
      .getByRole("button", { name: "Check evidence", exact: true })
      .click();
    await page.waitForTimeout(100);
    assert.deepEqual(reconciliations.at(-1), { decision: "check" });
    assert.match(await detail.innerText(), /unknown/);
    assert.equal(
      await detail.getByRole("button", { name: /retry|republish/i }).count(),
      0,
    );
    // P7 UI/UX: an evidenced receipt updates the status; advisory labels remain textual.
    effectStatus = "done";
    await detail
      .getByRole("button", { name: "Check evidence", exact: true })
      .click();
    await page.waitForFunction(() =>
      document.querySelector(".run-span-detail").textContent.includes("SYN-1"),
    );
    assert.match(await detail.innerText(), /Receipt/);
    assert.match(await detail.innerText(), /unenforced/);
    assert.equal(
      await detail
        .getByRole("button", { name: "Reconcile", exact: true })
        .count(),
      0,
    );
    await page.getByRole("button", { name: "Collapse run console" }).click();
    needsYou = [
      {
        ...metadata,
        gate_id: "inbox-publish",
        job_id: "run-a",
        kind: "gate",
        publish: true,
        question: "Publish from completed chat?",
        options: [
          { id: "approve", label: "Approve" },
          { id: "deny", label: "Deny" },
        ],
      },
    ];
    await page.locator("#attention-bell").click();
    await page.locator("#attention-open-inbox").click();
    const inbox = page.getByRole("dialog", { name: "Needs you", exact: true });
    await inbox.getByRole("button", { name: "Approve", exact: true }).waitFor();
    assert((await inbox.innerText()).includes(metadata.artifact_digest));
    denyAuthority = true;
    await inbox.getByRole("button", { name: "Approve", exact: true }).click();
    await page.waitForFunction(() =>
      document
        .querySelector(".needs-you-card [role=status]")
        .textContent.toLowerCase()
        .includes("enroll"),
    );
    denyAuthority = false;
    await inbox.getByRole("button", { name: "Deny", exact: true }).click();
    await inbox.getByText("No live requests need your attention.").waitFor();
    assert.deepEqual(errors, []);
    console.log(
      "PASS 7 simulated profiles: evidence/deny, duplicate defense, replay/invalidation, keyboard/enrollment, mobile/network, unknown/human decisions, receipt/advisory labels",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
