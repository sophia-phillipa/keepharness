# D-019 — Sophia's answers to the parity product questions

Status: accepted. Date: 2026-10-05. Decided by: Sophia. Spec: [Codex-app parity design](../codex-parity-design.md).

## Context

The parity design ([D-018](d-018-codex-parity-architecture.md)) left eleven product questions open (Settings entry, warning placement, Customize with several providers, harness agents, marketplace sections, tool identity, wording, continuation content and open-app button, Back/Forward position, where preferences live).

## Decision

- Q1 Settings: a submenu of sections; `Ctrl+,` opens Settings directly.
- Q3 Customize: one card per tool with provider badges and a "Providers" block in the detail view.
- Q8/Q9 Continuation: Copy and Save as `.md`, then ask whether to open ChatGPT Desktop or Claude Desktop (only if installed), passing a request to read the prompt and continue; if a prefilled open is impossible, open the app and keep the prompt on the clipboard. Refined in [D-026](d-026-wp5-desktop-hand-off.md).
- All other questions (2, 4, 5, 6, 7, 10, 11): where the concept exists in Codex, do exactly what Codex does; only KeepHarness-only concepts follow the design's recommendations.

Consequences recorded from the Codex inventory (passes 2 and 3): Codex shows no not-connected warning, so WP4 is KeepHarness-only and follows the design's recommendation (Plugins menu section, chip dot, switch line); Codex has no agents screen, so "Your agents" stays a separate Settings item "Agents"; Codex's "Manage" goes to Settings › Plugins with chips Plugins | Apps | MCPs | Skills.

## Rationale

The goal is daily use as a Codex replacement: matching Codex removes relearning, and KeepHarness-only concepts have no Codex answer to copy.

## Alternatives

Per-question options are in the source design (for example, Settings opening directly without a submenu, or a provider tab row in Customize). Sophia picked the options above.

## Impact

Shapes WP2, WP3, WP4 and WP5 in the [spec](../codex-parity-design.md).
