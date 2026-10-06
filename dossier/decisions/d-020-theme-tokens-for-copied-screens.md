# D-020 — Codex-copied screens use KeepHarness theme tokens, never Codex colors

Status: accepted. Date: 2026-10-05. Decided by: Sophia. Spec: [Codex-app parity design](../codex-parity-design.md#theme-rule).

## Context

Parity work copies Codex screens closely. KeepHarness has 8 palettes (`:root[data-palette="graphite|paper|violet-bordeaux|porcelain|mineral-rose|amethyst|petroleum|arizona"]` in `harness_ui/assets/themes.css`).

## Decision

Copying Codex means copying layout, structure, spacing and wording, never Codex's hex colors. New UI (WP2 menu, WP3, WP4/WP5 UI, WP7 icons, WP8 meters) uses only the `--th-*` tokens. Acceptance includes a check on at least one light and one dark palette (text contrast at least 4.5) and no hard-coded colors in the diff.

## Rationale

Hard-coded colors would break every palette but one and fail contrast in the others.

## Alternatives

Copy Codex's colors for fidelity: rejected, the palettes are a KeepHarness-only feature ([D-021](d-021-keep-keepharness-only-features.md)).

## Impact

Every parity WP's review and acceptance checks.
