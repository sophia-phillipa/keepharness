# D-026 — WP5 hand-off to ChatGPT and Claude desktop apps

Status: accepted. Date: 2026-10-05 (window 7). Decided by: Sophia (flow, [D-019](d-019-parity-product-answers.md)) and the coordinating session in window 7 (details). Spec: [WP5](../codex-parity-design.md#wp5-continue-in-another-app-m-python--frontend--desktop). Shipped: merge `9bff3f8`; behavior in the [release notes](../releases/v0.16.0.md#chat-campaign-fixes-batch-2-and-codex-parity-wave-1).

## Context

Sophia wants Copy and Save as `.md`, then an offer to open the installed desktop app with the prompt. Inventory pass 2 found the schemes `codex://threads/new?prompt=` (ChatGPT desktop) and `claude://claude.ai/new?q=` (Claude Desktop). Whether the Codex link only prefills or also sends is unverified, and auto-send is forbidden.

## Decision

1. ChatGPT always opens `codex://threads/new` with no prompt; the full prompt stays on the clipboard (`desktop/policy.cjs` `handoffUrl`).
2. Claude opens `claude://claude.ai/new?q=<encoded prompt>` while the URL is at most 16,000 characters (`HANDOFF_URL_LIMIT`); above that it opens with a short request to paste the prompt from the clipboard.
3. "Include project folders" is on by default for the local owner, off for guests and remote clients, read from a new additive `local_owner` field of `GET /v1/models`.
4. Desktop-only bridge: `desktop/preload.cjs` in the main window exposes two calls over two IPC channels (`keepharness:handoff-apps`, `keepharness:handoff-open`); the handlers answer only the harness origin, take no URL or scheme from the page and never log the prompt.
5. An app counts as installed when `app.getApplicationNameForProtocol("codex://" | "claude://")` is not empty.

## Rationale

Auto-send would put an unreviewed prompt into another vendor's app. Building URLs only in the main process from fixed bases keeps a compromised page from opening arbitrary schemes. Paths are useful to the owner's own desktop app but must not reach a remote client by default.

## Alternatives

Pass `prompt=` to Codex (risk of auto-send, unverified). Detect apps with `xdg-mime` through a shell (a shell call where an Electron API exists). Expose the bridge to every window (the admin and splash do not need it).

## Impact

`desktop/preload.cjs`, `desktop/main.cjs`, `desktop/policy.cjs`, `scripts/package-desktop-linux.sh`, `agent_service/routes/models.py`, `agent_service/ui.js`, `tests/conversation-continuation.spec.cjs`, `desktop/policy.test.cjs`, `desktop/main.test.cjs`.

## Open

Verify by hand whether `codex://threads/new?prompt=` only prefills. If it does, a superseding record may let ChatGPT carry the prompt. The 16,000-character limit is a conservative guess.
