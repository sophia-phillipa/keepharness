# Codex-style shell

Status: implemented on branch `feat/ui-codex-shell` (2026-10-03), awaiting Sophia's approval before release.

The harness UI follows the layout of the ChatGPT/Codex desktop app, measured on 2026-10-03 through the app's local DevTools port, while keeping every Tail Harness capability. Colors come only from the `--th-*` theme tokens; the two new default palettes (Graphite, Paper) match the reference neutrals and the six existing palettes stay selectable.

## Regions

| Region | Reference | Tail Harness |
| --- | --- | --- |
| Icon rail | 50 px, 34 px buttons (radius 12.5), icons only, rare actions at the bottom | `#app-topbar` as a rail: brand, sidebar toggle, search, attention bell, files/activity; provider quota meters, admin, theme, about and settings at the bottom; every button has a title and an accessible name |
| Sidebar | product title, New chat, Pinned, Projects (folders expand to their chats), chats | product title, New Conversation, **Projects** (folders expand to their chats, actions on hover), then conversations grouped by orchestration status (Needs you, Running, Queued, Done today, Older; empty groups hidden) with one quiet status line per row |
| Chat | `#181818`, 768 px column, top-center Chat/Work switch | same column and colours; top-center **Chat / Code** switch; Code opens the files/activity panel beside the chat |
| Empty home | centered heading, 640 px composer card, sub-bar (Choose project · Plugins) | centered heading, 640 px card, sub-bar with **Choose project**, the isolation switch and the access notice |
| Conversation | plain answers, 22 px round user bubble, one-row pill composer | same; status/activity line above the answer; run model, time and "View run" share the action row |
| Panels | Sources panel with quiet tabs and simple rows | files/activity panel with pill tabs, simple file rows and the authorized-root card |
| Settings | full page over sidebar and chat, settings navigation on the left | settings dialog shown as a full page (rail stays), 768 px content column |
| Admin | — | same settings-like navigation, centered content, 16 px cards, pill buttons |

## Our capabilities, adapted rather than removed

- Maestro plan, approvals, questions and publish gates use one 20 px card style with pill actions; the pending state keeps an orange status pill (WCAG AA in light and dark palettes).
- Run console (Pipeline, Timeline, Logs, Runs, Agents) is a quiet bottom panel; on narrow or short windows it opens beside the rail.
- Provider quotas are compact meters in the rail, which modal panels keep operable through `aria-owns`.
- Status counts stay in the status strip under the chat and in the rail bell; empty status groups are hidden from the sidebar.
- The composer keeps the `/` palette guidance in its placeholder (clipped to one line inside the pill), the access mode (icon-only in the pill, full access flagged), model and effort pickers, and the isolation indicator in the theme accent.

## Electron readiness

The rail and the sidebar head are drag regions (`-webkit-app-region: drag`) and their controls opt out, so the same UI can run in a frameless Electron window.

## Validation

The browser suite (`scripts/test-ui.sh`) and the Python suite pass on the branch, except the two Python build tests that need a writable virtual environment. Tests changed only where the layout decision itself changed; each change carries a comment naming the decision.
