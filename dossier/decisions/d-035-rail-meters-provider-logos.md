# D-035 — Rail quota meters show provider logos instead of names

Status: accepted. Date: 2026-10-06. Decided by: Sophia (after the WP8 visible pass truncated "DeepSeek" to "Dee…"). Spec: [WP8](../codex-parity-design.md#wp8-quota-meters-for-every-connected-provider-frontend--small-python). Refines [D-032](d-032-wp8-quota-meter-contract.md). Release notes: [v0.16.0](../releases/v0.16.0.md).

## Context

Each rail meter is 34 px wide and starts with a short provider name, so longer names such as "DeepSeek" are cut with an ellipsis.

## Decision

The meter shows the provider's logo (OpenAI for Codex, Claude, Gemini, DeepSeek; the generic local-model icon for local) instead of the name. The full provider name stays in the meter's accessible name and tooltip. A DeepSeek logo is added to the shared icon sprite from a permissively licensed source, with its provenance recorded.

## Rationale

A logo fits the 34 px rail without truncation and is recognized faster than a cut word; the accessible name keeps the text for screen readers.

## Alternatives

- Shorter or abbreviated names: still ambiguous and still cut for some providers.
- A wider rail: costs space for every view.

## Impact

`agent_service/ui.js` (`providerQuotaMeter`, reusing the provider icon helper), `agent_service/ui.css` (meter icon size, `--th-*` tokens only), `harness_ui/assets/icons.svg` (new `brand-deepseek` symbol), `tests/harness-provider-quota.spec.cjs`.
