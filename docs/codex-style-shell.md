# Codex-style shell

Status: implemented on branch `feat/ui-codex-shell` (2026-10-03), awaiting Sophia's approval before release.

The harness UI follows the layout of the ChatGPT/Codex desktop app, measured on 2026-10-03 through the app's local DevTools port, while keeping every Tail Harness capability. Colors come only from the `--th-*` theme tokens; the two new default palettes (Graphite, Paper) match the reference neutrals and the six existing palettes stay selectable.

## Regions

| Region | Reference | Tail Harness |
| --- | --- | --- |
| Icon rail | 50 px, 34 px buttons (radius 12.5), icons only; Home, Space, Scheduled, Customize at the top, rare actions at the bottom | `#app-topbar` as a rail: brand, sidebar toggle, search, attention bell, files/activity, Runs and pipeline (run console), Agents and skills (Settings › Agents and models); provider quota meters, admin (Settings › System), theme, about and settings at the bottom; every button has a title and an accessible name; the phone top bar drops Runs and Agents |
| Sidebar | product title, New chat, Pinned, Projects (folders expand to their chats, "No chats" when empty, compose icon on hover), Chats | product title, New Conversation, **Projects** (folders start expanded and remember a collapse; a project's chats live only in its folder; compose icon and actions on hover; "No conversations" when empty), then **Chats** for conversations outside the listed projects, ordered needs you, running, queued, then the rest; one status dot per row (yellow needs you, spinner running, ring queued, red failed, accent unread) and a summary dot on a collapsed folder |
| Chat | `#181818`, 768 px column, top-center Chat/Work switch | same column and colours; top-center **Chat / Code** switch; Code opens the files/activity panel beside the chat |
| Empty home | centered heading, 640 px composer card, sub-bar (Choose project · Files · Plugins) | centered heading, 640 px card, sub-bar with **Choose project**, **Files** (opens the files panel), **Agents** (inserts `@` and lists agents), the isolation switch (only for models that declare execution modes) and the access notice (dropped in narrow chat columns) |
| Conversation | plain answers, 22 px round user bubble, one-row pill composer | same; status/activity line above the answer; run model, time and "View run" share the action row |
| Panels | Sources panel with quiet tabs and simple rows | files/activity panel with pill tabs, simple file rows and the authorized-root card; a file selection offers **Attach to message** and **New chat with these files** |
| Settings | full page over sidebar and chat, settings navigation on the left | settings dialog shown as a full page (rail stays), 768 px content column; a **System** group (Providers, Operations, Runs, Catalogs and vault) shows the local admin in the same screen |
| Admin | — | same settings-like navigation, centered content, 16 px cards, pill buttons; embedded mode (`?embedded=1&theme=<palette>`) hides its own navigation and follows the harness theme |

## Our capabilities, adapted rather than removed

- Maestro plan, approvals, questions and publish gates use one 20 px card style with pill actions; the pending state keeps an orange status pill (WCAG AA in light and dark palettes).
- Run console (Pipeline, Timeline, Logs, Runs, Agents) is a quiet bottom panel; on narrow or short windows it opens beside the rail.
- Provider quotas are compact meters in the rail, which modal panels keep operable through `aria-owns`.
- Status counts stay in the status strip under the chat and in the rail bell; the sidebar shows each conversation's state as a dot instead of status groups.
- The composer keeps the `/` palette guidance in its placeholder (clipped to one line inside the pill), the access mode (icon-only in the pill, full access flagged), model and effort pickers, and the isolation indicator in the theme accent.

## Settings › System and the admin

The admin stays its own local app (own port, local-only guard, admin header and cookie). The harness frames it only from Settings › System, creating the iframe on first use. The admin's CSP allows framing only by the harness's own `127.0.0.1`/`localhost` origin (`frame-ancestors`), the harness's CSP allows only the local admin as a frame source (`frame-src`), and the System group appears only when the harness itself is opened on `127.0.0.1`/`localhost`; a page opened over the network keeps using its own machine's tools. The rail's admin button opens Settings › System; a modified click still opens the admin in a new tab.

## Electron readiness

The rail and the sidebar head are drag regions (`-webkit-app-region: drag`) and their controls opt out, so the same UI can run in a frameless Electron window.

## Validation

The browser suite (`scripts/test-ui.sh`) and the Python suite pass on the branch, except the two Python build tests that need a writable virtual environment. Tests changed only where the layout decision itself changed; each change carries a comment naming the decision.
