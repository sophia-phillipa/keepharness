# D-024 — WP6 limits, per-key merge and older builds

Status: accepted. Date: 2026-10-05 (window 7). Decided by: the coordinating session in window 7, with the WP6 review fixes. Refines [D-023](d-023-wp6-preferences-in-backend-store.md). Shipped: [WP6 backend release notes](../releases/v0.16.0.md#wp6-durable-ui-preferences-backend).

## Context

The WP6 draft named both 400/413 for oversize input and left the merge and downgrade rules implicit.

## Decision

1. A single value over its cap (16 KB serialized) is 422 `ui_state_invalid_value` with `field`, never 413. 413 `payload_limit` is only for the whole request body (64 KB).
2. `PATCH` merges per top-level key; the last write wins per key; no ETag.
3. An older build keeps (passes through) stored values it rejects or truncates, and keys a newer build added: they survive a write of another key and are never served.

## Rationale

A per-value 422 with `field` lets the client drop that one key and resend the rest; a 413 means "split the batch". Per-key last-write-wins is enough for one owner and one window. Passing through unknown values keeps a downgrade from erasing a newer build's preferences.

## Alternatives

413 for an oversize value (ambiguous with a body overflow). Whole-document replace or ETag concurrency (more complexity than one window needs). Dropping rejected values on the next write (silent loss after a downgrade).

## Impact

`agent_service/ui_state.py`, `agent_service/ui-prefs.js` error handling, `tests/test_ui_state.py`.
