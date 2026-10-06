# D-033 — Right panel: Activities first, accordions, Project and System files

Status: accepted · 2026-10-06 · decided by Sophia after reviewing a browser mock · spec: [Codex-app parity design](../codex-parity-design.md) (Backlog) · release: [v0.16.0](../releases/v0.16.0.md)

## Context

The right panel stacks Files, Background tasks, Resources and Activity as fixed-height `<details>` sections (`agent_service/index.html`, `agent_service/ui.css`), so they compete for space and none is visible enough. A mock of an accordion layout (served locally from `~/.cache/kho/mocks/right-panel-accordion/`) was approved with changes.

## Decision

1. The panel switch reads **Activities | Files**, in that order.
2. **Activities** is an accordion: Activity, Background tasks and Resources are stacked header buttons; exactly one is expanded and fills the remaining height (scrolling inside); collapsed headers show a count when the section has one. WAI-ARIA accordion keyboard pattern (Enter/Space toggles, Up/Down/Home/End move between headers).
3. **Files** is an accordion with two sections, in this order:
   - **Project Files**: only the paths added to the current project.
   - **System Files**: the paths KeepHarness is authorized to browse on the system (the allowed roots), so a prompt can work with a file or folder attached from outside the project while the conversation stays in the project.
4. Only `--th-*` tokens; the open section is remembered as a UI preference through `window.HarnessPrefs` (D-023).

## Rationale

One expanded section gets the whole height instead of four sections sharing it. Splitting files by origin makes it explicit whether a path belongs to the project or comes from outside it, which is the case Sophia needs when attaching an outside file to a project conversation.

## Alternatives

- Keep the stacked fixed-height sections: the problem being fixed.
- Files as one full-height tree (the first mock): rejected by Sophia in favour of the Project/System split.
- Files before Activities: rejected by Sophia.

## Impact

`agent_service/index.html` (panel markup), `agent_service/ui.css`, `agent_service/ui.js` (panel, file tree roots split by project paths vs allowed roots), specs for the panel. Attaching from System Files must respect the existing access rules (UC-003 additive project access, owner-only paths); no new access is granted by the UI.

## Follow-up

Check how the file tree learns the allowed system roots today and whether attaching a System Files path to a project conversation needs a server change; if it does, that change gets its own review.
