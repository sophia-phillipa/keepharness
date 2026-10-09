# D-054 — Remove the Chat | Code switch

Status: accepted (decided by Sophia, 2026-10-09). Supersedes [D-037](d-037-chat-code-views-of-one-conversation.md). Spec: [Codex-style shell](../../docs/codex-style-shell.md). Related: [D-033](d-033-right-panel-accordion.md). Issue: #78.

## Context

[D-037](d-037-chat-code-views-of-one-conversation.md) kept Chat and Code as two views of one conversation. Code only opened the right panel beside the chat, so the switch mirrored the side-panel toggle (`#panel-toggle`; `syncViewSwitch` and `showView` in `agent_service/ui.js`). `body[data-view]` was written and no CSS read it. The switch was a second control for the same state.

Any conversation can be a coding request, so a Code mode adds nothing that the panel toggle does not already give.

## Decision

1. The Chat | Code switch is removed from markup, JavaScript and CSS.
2. `#panel-toggle` and the side panel (Activities | Files, [D-033](d-033-right-panel-accordion.md)) stay as they are.
3. Coding tools stay available in every conversation without a mode. No view or mode selects them.
4. Follow-up: the terminal, the file editor and the wide layout become discrete options, each toggled on its own and stored as backend UI preferences (per [D-023](d-023-wp6-preferences-in-backend-store.md)); no new localStorage keys. The design is pending.

## Rationale

A switch that only mirrors another control is a second path to the same state. Removing it leaves one control for the panel and one rule for tools: they are the same in every conversation, so a mode would hide nothing and imply a difference that does not exist.

## Alternatives

- Keep D-037 (the switch mirrors the panel toggle): a redundant control; rejected by Sophia on 2026-10-09.
- Keep a Code mode that enables coding tools only there: contradicts the rule that any conversation can be a coding request.
- Ship the terminal, editor and wide-layout toggles now: deferred; they need their own design record.

## Impact

- UI: the header switch, its tab roles, arrow-key and Home/End handling, and the `body[data-view]` attribute are removed. The panel toggle and side panel are unchanged.
- Tests: the `view-switch` operator step is removed; the specs that used the Code view open the panel with `#panel-toggle`.
- Storage and backend: no new keys and no contract change. Provider, model, tools and file access are unaffected.
- Layout: the conversation title keeps its `max-width: calc(50% - 250px)` rule, which was sized around the centered switch. Whether to relax it is part of the design pass.

## Follow-up

- Design pending: the terminal, the file editor and the wide layout as individually toggled options, stored as backend UI preferences, with no new localStorage keys. Each option needs its own record before it ships.
- The question Sophia raised in [D-037](d-037-chat-code-views-of-one-conversation.md) (what Code should add beyond the panel) is closed: nothing beyond the panel.
- The open question in D-033 about the initial panel view is unchanged.
