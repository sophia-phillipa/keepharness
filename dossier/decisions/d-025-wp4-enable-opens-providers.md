# D-025 — WP4 "Enable" opens Settings › System › Providers

Status: accepted. Date: 2026-10-05 (window 7). Decided by: the coordinating session in window 7, with the WP4 UI work. Spec: [WP4](../codex-parity-design.md#wp4-cross-provider-tool-warnings-m-python--frontend). Shipped: [WP4 UI release notes](../releases/v0.16.0.md#wp4-cross-provider-tool-warnings-ui) (merge `acc8dfa`).

## Context

The design had the Plugins menu offer "Enable" for a tool installed on the current provider but not allowed. The per-provider allow list lives in the admin; the harness API has no enable endpoint.

## Decision

- "Enable" and "Open Plugins" open Settings › System › Providers (Settings › Customize where the admin nav is unavailable on this host). No enable endpoint is added and no request is sent.
- Wording: "X is installed on P but not enabled - enable it in Settings › System › Providers"; "X is connected on O but not on P"; menu rows "Installed on P, turned off for KeepHarness" and "Not connected on P (connected on O)".
- Labels use the item's display name when it carries its own casing ("GitHub", "PostgreSQL"), else the title-cased family key.

## Rationale

The allow switch already lives behind the admin's owner session and CSRF guard ([D-018](d-018-codex-parity-architecture.md)); a second write path in the harness API would duplicate those checks.

## Alternatives

A harness-side enable endpoint proxying to the admin: rejected for the same reason as the Customize proxy.

## Impact

`agent_service/ui.js` Plugins menu and route carry-over note; `agent_service/integrations_view.py` labels; `tests/composer-plugins.spec.cjs`.
