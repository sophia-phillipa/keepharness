# D-037 — Chat and Code are two views of one conversation

Status: superseded by [D-054](d-054-remove-chat-code-switch.md) (decided 2026-10-09). It was accepted on 2026-10-06 from ledger decision D45, 0.15.0 WP-20. Decided by: Sophia (D45, option A). Spec: [Codex-style shell](../../docs/codex-style-shell.md). Related: [D-033](d-033-right-panel-accordion.md).

## Context

The Codex app has a top-center Chat / Work switch. KeepHarness copied the control's place as **Chat | Code**, and the 0.15.0 information-architecture pass (ledger L52, WP-20) had to decide what Code means: a different mode of the conversation, or a different view of it. The answer lived only in the gauntlet ledger (`D45`, options "A a view of the same conversation · B Code sets defaults") and in a code comment; this record moves it into the dossier.

## Decision

1. **Chat and Code are views of the same conversation (D45, option A).** Switching keeps the conversation, draft, project, attachments and resources, model and effort, access mode, run and span selection and authority unchanged.
2. **Code only opens the right panel beside the chat** (since [D-033](d-033-right-panel-accordion.md): Activities | Files). The switch mirrors the panel toggle (`agent_service/ui.js`, `syncViewSwitch`/`showView`); `body[data-view]` reflects it.
3. **Code sets no defaults** (option B rejected): it does not change provider, model, tools, isolation or file access. Root authorization stays in the project flow; browsing a folder is not authorizing it.
4. The Files chip opens its own picker and no longer flips a Chat into Code (D40, 0.15.0).

## Rationale

One conversation with more or less context on screen keeps every safety check in one place and avoids a hidden mode change when the owner only wanted to see files or run activity.

## Alternatives

- B, Code sets defaults (for example a coding model, file tools or a different access mode): rejected in D45.
- A separate Code conversation type: never proposed for KeepHarness; it would split history and permissions.

## Impact

None on code today; this records existing behavior.

## Follow-up

- Sophia asked (2026-10-06) to talk later about the differences between the views: what Code should add beyond the panel, and how it relates to Codex's Chat / Work. Any change supersedes this record.
- Open since D-033: with no stored preference the panel opens on Files; whether Activities should be the initial view is part of that conversation.
