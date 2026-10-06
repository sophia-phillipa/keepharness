# D-023 — WP6 keeps UI preferences in a backend store per owner

Status: accepted. Date: 2026-10-05. Decided by: Sophia (asked for the WP6 amendment from the Electron desktop reference). Spec: [WP6](../codex-parity-design.md#wp6-durable-ui-preferences-m-python--frontend). Shipped: [backend](../releases/v0.16.0.md#wp6-durable-ui-preferences-backend), [frontend](../releases/v0.16.0.md#wp6-durable-ui-preferences-frontend) (merge `91aaa5e`).

## Context

All UI preferences lived in `localStorage`, which is keyed by origin. The harness port is stable in daily use (`control/manager.py` fails on a taken port instead of picking another), but a port change or a lost `runtime.json` silently drops every preference. KeepGlide lost its Electron preferences this way.

## Decision

- `GET`/`PATCH /v1/ui-state`, local owner only, one store per owner in the state folder, never keyed by port or origin; same auth and Origin guard as every write route.
- Allow-listed keys from a writer inventory of `ui.js`, `run-console.js`, `tour.js` and `theme.js`; unknown key on write is 422; on read, unknown or invalid stored fields are dropped and the rest loads.
- Atomic 0600 writes; a corrupt file loads defaults and is kept as a `.bak`; a store that cannot be written reports read-only and the client enqueues nothing.
- One cap contract: client caps never exceed server caps (served in `limits`).
- Migration from `localStorage` erases only the keys the server accepted; the server value wins.
- Debounced writes with a keepalive flush on `pagehide`; `localStorage` remains only as the theme's first-paint cache and for disposable per-launch state; drafts stay in `sessionStorage`. No change broadcast (one harness window; the admin is framed).

## Rationale

Preferences should follow the owner on this machine, not the window origin. The amendment's rules come from failure modes seen in KeepGlide (erasing rejected keys, client caps above server caps, stale re-queues).

## Alternatives

Keep `localStorage`: rejected, the origin is not a stable identity. Broadcast changes to other windows: dropped until a second harness window exists.

## Impact

`agent_service/ui_state.py`, `agent_service/ui-prefs.js`, `agent_service/ui.js`, `tests/test_ui_state.py`, `tests/ui-state-restart.spec.cjs`. Error and merge details: [D-024](d-024-wp6-limits-and-merge.md).

## Open

The Electron session stays the persistent default session; the desktop reference prefers an ephemeral one. Revisit only after every preference lives in the store.
