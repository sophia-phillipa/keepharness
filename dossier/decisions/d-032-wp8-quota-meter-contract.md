# D-032 — WP8 quota meter contract

Status: accepted · 2026-10-06 · decided by Sophia (every connected provider; DeepSeek shows its prepaid balance in the rail) · refines [D-028](d-028-wp8-quota-meters-every-provider.md). Spec: [WP8](../codex-parity-design.md#wp8-quota-meters-for-every-connected-provider-frontend--small-python).

## Context

The Claude meter was missing because `/v1/activity` read only the stream observation and ignored the usage cache that `claude_quota` fills from `/v1/usage?backend=claude`, so five minutes without a Claude run left no meter. Providers without quota windows (Gemini, local, DeepSeek) and a Codex that was never read had `quota: null`, so the rail dropped them. Sophia wants every connected provider visible, and for DeepSeek the actual prepaid balance rather than "n/a".

## Decision

- `/v1/activity` stays passive: it never fetches a quota and only reads what the server already holds in memory.
- Every provider entry carries a non-null `quota` object `{available, reason, ...}`, filtered through an allow-list of keys (no tokens, account emails or raw provider bodies). Reasons: Codex `quota_not_read`, `quota_stale`, `usage_unavailable`; Claude `quota_not_read`, `quota_stale`, `quota_not_reported` (API key or unlimited plan); Gemini `quota_not_reported`; local `local_no_quota`; DeepSeek `balance_not_read`, `quota_stale`, `quota_not_reported` (no key configured).
- Claude uses the usage cache while it is at most 300 s old (and belongs to the current login), else the stream observation; an older reading is `quota_stale`.
- DeepSeek exposes its cached balance as `{available: true, kind: "balance", balance: {amount, currency}}` while the read is at most 300 s old. The balance read already exists (`/user/balance`, an account API, not inference) behind `/v1/usage?backend=deepseek`; no adapter change.
- The UI primes `/v1/usage?backend=<codex|claude|deepseek>` at most once per 300 s per backend, only while the tab is visible, when the entry says `quota_not_read`, `quota_stale` or `balance_not_read`.
- The rail order is fixed: codex, claude, gemini, deepseek, local. A provider without a reading shows an "n/a" meter with the reason; DeepSeek shows the balance text.

## Rationale

A passive endpoint keeps the 4 s poll free of CLI launches and network calls. A non-null object with a reason lets the UI render one meter per provider without guessing why a reading is missing. Priming from the UI reuses the existing endpoints and their caches.

## Alternatives

- A server background poller: rejected (lifecycle, and it would keep CLIs open with the window closed).
- Fetching inside `/v1/activity`: rejected (breaks the passive invariant and would launch the CLIs every 4 s).
- "n/a" for DeepSeek: rejected by Sophia, who wants the balance in the rail.

## Impact

`agent_service/services/activity_service.py` (`provider_quota`, `QUOTA_KEYS`), `agent_service/services/conversation_service.py` (`observed_claude_quota`, `observed_codex_quota` gap, `observed_deepseek_quota`). `/v1/activity` providers: `quota` is never `null` (was `null` for Gemini, DeepSeek, local and an unread Codex). `/v1/usage?backend=codex` gains `reason: null` on success. The UI follow-up lives in `agent_service/ui.js` and `run-console.js`.
