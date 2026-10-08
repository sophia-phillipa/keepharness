# D-046 — Retire cloud isolation controls before real-home parity

Status: accepted. Date: 2026-10-08. Decided by: Sophia. Implements issue #53 after the backend retirement in #52.

## Context

[D-044](d-044-scoped-sandbox-under-facade.md) retires Codex/Claude scoped execution and preserves Local isolation. Issue #53 originally depended on #45 and #46. Sophia requested that an unsupported private mode no longer appear as an option, bringing the UI cleanup forward.

## Decision

Remove the Codex/Claude isolation controls, explanations and capability fallbacks now. Missing capabilities block sending until refresh. Preserve historical labels, readable/exportable history and the retired draft lock; continuing requires an explicit new native conversation with no old provider session or automatic submission. Keep all four native permission presets and Local's required isolation distinct from this retirement.

## Consequences

This changes the ordering in D-044, not the scope of #45 or #46. Re-validate #45's attended/scheduled real-home parity and #46's no-old-home-read checks when those changes land. No old homes, marker files or stored conversations are migrated, converted or deleted. The `<state>/providers/home` path remains untouched. See the [execution-mode contract](../conversation-execution-mode.md) and [0.16.0 release notes](../releases/v0.16.0.md).
