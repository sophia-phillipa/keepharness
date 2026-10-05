# Codex desktop app: structural inventory (October 2026)

Type: reference. Source: the ChatGPT desktop app, build `26.930.31730`, observed on 2026-10-05 on Linux (Wayland, 1431x843 window) by reading the live DOM and accessibility tree. Nothing was sent, run, toggled or changed. Personal content is replaced by placeholders (`<thread title>`, `<project name>`, `<account email>`).

## Coverage

| Area | Status |
| --- | --- |
| Window chrome, application menu | Covered |
| Navigation model | Partly: shortcuts and menu verified; live back/forward history walk not exercised |
| Sidebar (shape, row anatomy, sections) | Covered; row, section and project context menus not opened (see Gaps) |
| Settings (nav and most sections) | Covered except Personalization, Account, Profile, Usage and billing, Git, Worktrees, Code Review, Archived chats |
| Tools association (Customize: Plugins, Skills) | Covered in detail |
| Composer | Covered except the project picker, `@` and `/` popups |
| Thread view (messages, steps, diffs, approvals) | Not covered |
| Other sections (Space, Scheduled, Explore, command menu) | Covered |
| Keyboard shortcuts | Covered (app's own list) |

Gaps: the app process crashed (Skia `SkFontMgr_FontConfigInterface` "Not implemented" fatal) when the project picker, Settings > Git and Settings > Code Review were opened under the debug launch, and the Personalization page reloaded the renderer. Text also does not render in screenshots in this launch, so layout facts come from DOM geometry. A follow-up pass is needed for the thread view and the missing pages.

## Navigation model

- The app has one window with three modes selected from the sidebar header button `Switch mode`. The menu lists `ChatGPT` ("Create, learn, and explore") and `Codex` ("Build, debug, and ship"); the command menu also offers `Switch to Chat`, `Switch to Work`, `Switch to Codex`. The sidebar header shows the current mode name with a chevron.
- Top-left controls, in order: `Back to ChatGPT` (hidden skip link), `Back` (28 px, left arrow), `Forward` (28 px, right arrow), `Hide sidebar` (panel icon), then the in-window application menu `File Edit View Help`.
- `Back` and `Forward` are disabled at a fresh start. `Back` became enabled after a mode switch and after opening Settings, so the history includes mode switches and top-level screens; `Forward` stays disabled until a back step. Shortcuts: `Ctrl+[` back, `Ctrl+]` forward, plus mouse back/forward buttons.
- The URL never changes (`app://-/index.html`); routing is internal. Settings has a route per page (for example `/settings/git-settings`).
- Settings opens directly (no submenu) from the profile menu, `File > Settings…` or `Ctrl+,`. The settings page replaces the left panel with a settings navigation and keeps the narrow icon rail.
- Rail (36 px buttons, x=8): `Home`, `Space`, `Scheduled`, `Plugins` (opens the Customize screen), `Explore` (dots icon, opens a popover with Projects, Sites, Maps, GPTs, Code Review), then in Codex mode a divider and `Code Review` (git branch icon). Footer: avatar button `Open profile menu`.
- Chat navigation: `Ctrl+Shift+[` / `]` previous/next chat, `Ctrl+Tab` recently viewed, `Alt+1..9` go to chat N, `Ctrl+Alt+1..6` recent chat N.

## Window chrome and application menu

| Menu | Items in order |
| --- | --- |
| File | New Window; New Chat `Ctrl+N`; New Temporary Chat `Ctrl+Shift+N` (disabled); separator; Open Folder… `Ctrl+O`; separator; Close `Ctrl+W`; separator; Settings… `Ctrl+,`; separator; Log Out; Quit ChatGPT `Ctrl+Q` |
| Edit | Undo `Ctrl+Z`; Redo `Ctrl+Shift+Z`; separator; Cut; Copy; Paste; Delete; separator; Select All |
| View | Toggle Sidebar `Ctrl+Shift+S`; Toggle Bottom Panel `Ctrl+J`; Toggle Pinned Summary; Open Terminal `` Ctrl+` ``; Toggle navigation panel `Ctrl+Shift+E`; Switch between Chat and tabs `Alt+Ctrl+B`; separator; Browser > (Open Browser Tab `Ctrl+T`, Focus Browser Address Bar, Reload Browser Page `Ctrl+R`); separator; Find `Ctrl+F`; separator; Previous Chat `Ctrl+Shift+[`; Next Chat `Ctrl+Shift+]`; Back `Ctrl+[`; Forward; separator; Zoom In `Ctrl+Shift+=`; Zoom Out `Ctrl+-`; Actual Size `Ctrl+0`; separator; Toggle Full Screen `F11` |
| Help | Documentation; Keyboard Shortcuts `Ctrl+/`; What's New; separator; Troubleshooting; System Status; Send Feedback; separator; Task Manager; Start Performance Trace; separator; About ChatGPT |

The top-right of the main pane has one button, `New tab` (28 px, plus-in-square icon), at the end of a `Chat toolbar`.

## Profile menu (sidebar footer)

Opens upward from the 36 px avatar (initials) at the bottom-left. Items in order: `<account name>` with a plan badge (`Pro`); `Usage` with remaining-percentage text; `Show Mini` `Alt+Super+P`; `Invite a friend` (disabled); `Settings` `Ctrl+,`; `Help >` (submenu: three recent release-note rows `<title> <date>`, `Full changelog`, `Set up Chrome extension`, `Set up remote`, `Keyboard shortcuts`, `User guide`, `Privacy center`); `Log out`.

## Sidebar

Left panel, 468 px wide, beside the 52 px rail.

| Region | Contents |
| --- | --- |
| Header (y=56) | Mode switch button (name + chevron); `View activity, needs attention` (bell with blue dot); `Search` (opens the command menu) |
| Quick rows (Codex mode) | `New chat` (30 px row, with a `Quick chat` icon button on the right); `Your dot` (agent row) |
| `Projects` section | Collapsible header with `Project sidebar options` (menu) and `Add new project` icon buttons. One row per project (folder icon, name, a `Project actions for <project>` button and a `Start new chat in <project>` button on hover; the latter is disabled for some). Scheduled tasks of a project appear as a nested list `Scheduled tasks in <project>` |
| `Recents` section | Collapsible header with `Chat sidebar options` and `New chat`. Flat list of threads, newest first |
| Thread row | 30 px tall, title truncated with an ellipsis, hover buttons `Pin chat` and `Archive chat` (20 px, right side); pinned rows show `Unpin chat`. In ChatGPT mode, rows also expose `Chat actions` |

Sections are collapsible: clicking a section header toggles `data-app-action-sidebar-section-collapsed`. The list scrolls inside the panel. In ChatGPT mode the same panel shows pinned chats, a project list with colored folder icons and a "Chats in <project>" sublist.

## Settings

Page title `Settings`, a pill `Search settings` field, then grouped navigation (30 px rows with an icon; rows marked "external" show an arrow icon and open the web):

| Group | Pages in order |
| --- | --- |
| Personal | General, Notifications, Import, Profile (external), Appearance, Parental controls, Trusted contact, Voice, Configuration, Personalization, Mini & Pets, Keyboard shortcuts, Usage & billing, Account (external) |
| Integrations | Plugins, Passwords, Computer use, Browser, Cloud computer |
| Coding | Hooks, Connections, Codex Cloud, Legacy Codex Cloud, Code Review, Git, Worktrees |
| Archived | Archived chats |

The command menu additionally lists settings pages that are not in this navigation: Cloud preferences, Environments, Work preferences, Analytics, MCP servers.

Content is a column of cards (about 730 px wide); each row has a title, a one-line description and a control on the right (switch, dropdown, button).

### General

- Permissions: `Default permissions` (switch, on, disabled, "always shown"); `Full access` with a risk paragraph and an "about elevated risks" link; `Show Full access in the composer` (switch).
- General: `Projectless task folder` (path + `Change`); `Default file open destination` (dropdown: VS Code, Default app, File Manager, Terminal); `Language` (dropdown: Auto detect plus about 60 languages); `Confirm before closing a window` (Never, Close shortcut only, Always); `Full view by default` (switch); `Bottom panel` (switch); `Default terminal location` (segmented Bottom / Right); `Prevent sleep while running` (switch); `Compress local chat history` (switch); `Speed` (Standard "Default speed", Fast "faster speed, increased usage"); `Suggested prompts` (switch, "ambient suggestions"); `Open source licenses` (`View`); `Plugins` (switch "Toggle plugins").
- Composer: `Plain text composer` (switch); `Show context window usage` (switch); `Send shortcut` (Enter; `^ + Enter` for multiline prompts; `^ + Enter` always); `Follow-up behavior` (segmented Queue / Steer; `Ctrl+Enter` does the opposite for one message).
- Popout Window: `Popout Window hotkey`; `Default to standalone chat` (switch).
- Notifications: `Turn completion notifications` (Never, Only when unfocused, Always); permission, question and dot notification switches; `Notification sound` (Default, Classic, None, Choose custom sound…).
- Toys: `Confetti cannon` (switch).

### Configuration

First control: a scope dropdown with three values, `Codex`, `Work`, `Admin` (the admin-like area), next to `Open config.toml`. Then: `Approval policy` (On request, Never ask for approval); `Sandbox settings` (Read only, Workspace write, Full access); `Web search` (Disabled, Cached, Indexed, Live); `Output detail` (Model default, Low, Medium, High); `Reasoning summary` (Auto, Concise, Detailed, None); `Available reasoning efforts` (multi-select: Light, Medium, High, Extra High, Max, Ultra); `Codex dependencies` (switch); `Diagnose issues in Codex Workspace` (`Diagnose`); `Reset and install Workspace` (`Reinstall`, with "Current version").

### Other settings pages

| Page | Contents |
| --- | --- |
| Notifications | One row per channel (Codex, Group chats, Health, Library, Marketing, Messages, Personalized tips, Projects, Responses, Tasks, Usage) with a multi-select dropdown of `Push` and `Email` |
| Import | `Keep imports in sync` switch, `Content to sync`, `Customize`; list of detected setups from other AI apps, each with an `Import` button; `Needs attention` block |
| Appearance | Visual style radios; `Theme` dropdown with `Import`, `Copy`; code theme; `Accent` (Default, Blue, Green, Yellow, Pink, Orange, Purple, White, Custom); background and foreground colors; `Font`; `Advanced` |
| Parental controls, Trusted contact | One explanatory paragraph and a single add button |
| Voice | Microphone, Language, Voice, Voice chat hotkey, Dictation, Recent recordings (per row `Retry` and an actions menu: Download recording, Delete recording) |
| Mini & Pets | `Show mini` preview, `Customize`, `My pets`, `Create pet` |
| Plugins | Same directory as the Customize screen (see below) |
| Passwords | Search, `Saved passwords (n)`, `Refresh`, empty state `No saved passwords` |
| Computer use | `Control` section listing a browser with `Install` |
| Browser | In-app browser: link and URL open destinations, `Show full URL`, clear browsing data, history, annotation screenshots, password manager, contact info, extensions, downloads, site permissions, WebMCP site tools switch, agent permissions table (site pattern, Browsing, Downloads, Uploads; values Requires approval, Always allow, Block), developer mode `Enable full CDP access` |
| Cloud computer | Cookie managers, website approvals (Always ask, Auto approve, Always allow), website permissions with `Add website` |
| Hooks | Empty state `No hooks found` / `Configured hooks will appear here` |
| Connections | `Control this PC`: SSH, `Allow connections` switch, `Add`, `Keep this PC awake` switch |
| Codex Cloud | `Environments`, search, `Create environment`, empty state `No saved environments` |
| Legacy Codex Cloud | Migration notice, `Environments` and `Preferences` tabs, `Create environment` |
| Keyboard shortcuts | See the last section |

## Tools association (Customize screen)

Entry points: the rail button `Plugins`; Settings > Plugins; composer `+` menu > `Discover and manage plugins`; `Manage` gear link on the directory header. The screen title is `Customize`.

### Layout

| Region | Detail |
| --- | --- |
| Left panel (468 px) | Title `Customize`; `Search installed plugins` icon (toggles an inline search field); two links `Plugins` (selected by default) and `Skills`; group label `Installed` with one 30 px row per installed plugin (icon, name, hover `More actions`) |
| Main pane | Header row (right aligned, 32 px controls): `Search plugins` field (about 230 px), `Refresh`, `Manage` (gear link), `Add` (white pill with chevron). Under it the tablist `Plugin directory` with `Public` and `Personal` tabs |
| Directory | Sections, each with a collapsible chevron heading: `Popular`, `New & Noteworthy`, `Productivity`, `Communication`, `Creativity`, `Developer Tools`, `Healthcare`, `Finance`, `Travel`, `Entertainment`, `Education` |
| Plugin card | A link, about 414x52 px, two columns per row (x=543 and x=981), 60 px row pitch. Anatomy: 36 px rounded app icon, name, one-line description, right-aligned 28 px action button |

### Card states

| State | Right-side control | Menu |
| --- | --- | --- |
| Not installed | `+` button, label `Install <name>` | none |
| Installed | `...` button, label `More actions` | `Try now`, `Manage`, `Uninstall` |

Installed plugins also appear in the left `Installed` list with the same menu (`Try now`, `Manage`, `Uninstall`).

### Add menu

`Create plugin`, `Add a marketplace`, `Upload plugin archive`, `Create MCP App`.

### Detail view

Opened by clicking a card; the left row stays highlighted. Breadcrumb chevron `Plugins` at the top, then:

1. Large icon (60 px), title.
2. Action row on the right: `More actions` (menu: `Manage`; `Uninstall` with the hint "Disconnects all accounts"), `Copy link`, primary `Try now` (white button).
3. Hero banner (about 720x228 px, gradient background) with three example prompt chips (icon plus arrow); each chip is a button whose accessible name is a sample prompt.
4. Description paragraph.
5. `Apps` heading with a count badge, then a row for each app (icon, name, one-line description, an info icon) and a connection list: one row per connected account (avatar, `Connected to <account email>`, `Actions for <account email>` menu: `Rename account`, `Reconnect`, `Disconnect`), and a full-width `Connect another account` button.
6. `Capabilities` block: Developer, Category, Version, then links `Website`, `Privacy Policy`, `Terms of Service`.
7. Footer paragraph on data sharing with the app.

### Skills page

Same shell (search, `Refresh`, `Manage`, `Add` icon button). Heading text "Extend Codex with task-specific skills". A top block of featured skills (two columns, 56 px cards: name, description, a `Skill enabled` check icon on the right), an expander `See <a>, <b> and N more`, then tabs `Team`, `Personal`, `System` with the same card grid.

### Per-thread enabling

Tools are not toggled per thread in a list; the composer `+` popup (below) lists installed plugins and apps, and picking one inserts it into the prompt. Global enablement is the Settings > General `Plugins` switch.

## Composer

Home (Codex mode) empty state: heading "What should we build?", a mark above it, a `Composer utility bar` with `Choose project` and `Work in` (value `This computer`; menu: This computer, Cloud, Remote), then the text box (placeholder `Do anything`, a suggestion chip `Skill Creator help me create a skill`). Bottom row, left to right: `Add files and more`, `Change permissions` (shield icon plus current mode text), model button, `Dictate`, `Send` (arrow in a white circle; `Start new voice chat` when empty).

| Control | Behaviour |
| --- | --- |
| `Add files and more` | Popup above the composer. Group `Add`: Files and folders; Work in a project ("Choose project for new chats"); Goal ("Set a goal to keep pursuing"); Plan mode ("Turn plan mode on"); Sketch ("Draw a sketch"). Group `Plugins`: each installed plugin (name + description), Pages, Pets, Plugin Management, Sites, Chrome, Browser. Group `Apps`: connected apps (name + description). Group `ChatGPT conversations`: past conversations |
| `Change permissions` | Menu "How should ChatGPT actions be approved?" with `Learn more`; items `Ask for approval` ("Always ask to edit external files and use the internet"), `Approve for me` ("Only ask for actions detected as potentially unsafe"), `Full access` ("Unrestricted access to the internet and any file on your computer") |
| Model button | Popover: a model row (opens a radio list: Default "Recommended set of models", GPT-6.1 Sol, GPT-6 Astra, GPT-6 Sol, GPT-6 Luna, GPT-5.6 Sol, GPT-5.6 Terra, GPT-5.6 Luna, GPT-5.5); `Enable fast mode` checkbox ("More usage"); `Power` slider with 6 stops (Light, Medium, High, Extra High, Max, Ultra), adjusted with Left/Right arrows. The button label is `<model> <effort>` |
| Shortcuts | `Ctrl+Shift+M` select effort, `Alt+M` open recent models, `Ctrl+Alt+Shift+O` project picker, `Ctrl+Enter` send in background |

## Other top-level sections

| Section | Contents |
| --- | --- |
| Space | Left panel: `New page`, then `All`, `Pages`, `Sites`, `Images`, `Google Drive`, `Trash`. Main: `Search` field, `New` button, tabs `Suggested`, `Favorites`, `Your items`, `Shared with you`, `Open filters`, `Grid view`, `List view`, a dismissible "Start with a template" row of six template cards, a library table (Name, Source, Last activity) with a loading state |
| Scheduled | Left panel: `New task`, groups `Upcoming` and `Paused` listing tasks. Main: a grid of 359x108 px template cards (emoji, title, one-line description) such as weekly meal plan, weekly finances update, find flights |
| Explore | Popover with Projects, Sites, Maps, GPTs, Code Review |
| Code Review | Rail button in Codex mode (git icon); page not opened |
| Command menu | Opened by the sidebar `Search` button, `Ctrl+K` or `Ctrl+Shift+P`. Dialog with a search combobox "Search commands and past chats". Groups: `Chats` (first nine recent chats with project label and `Alt+1..9`), `Quick actions` (New chat, Open folder), `Settings` (every settings page), `Chat` (New standalone chat, Quick chat, Archive chat, Toggle pin), `Navigation` (Switch chat…, Toggle activity view, Focus main chat, Open your dot, Next chat needing attention, Previous/Next chat, Go to recent chat 1-6, Switch to Chat/Work/Codex, Find, Back, Forward), `Panels` (Reopen closed tab, Toggle sidebar, Toggle navigation panel, Toggle bottom panel, Open terminal, Open browser tab, New tab in full view, Cycle workspace layout, New tab), `Skills` (Force reload skills, Go to skills), `Configure` (Theme) |

## Keyboard shortcuts (from the app's own screen, `Ctrl+/`)

A dialog "Keyboard shortcuts" with a search field and a `Close dialog` button. Sections and rows:

- Chat: New chat `Ctrl+N` / `Ctrl+Shift+O`; Quick chat `Ctrl+Alt+N`; Archive chat `Ctrl+Shift+A`; New standalone chat `Ctrl+Alt+O`; Toggle pin `Ctrl+Alt+P`.
- Navigation: Find `Ctrl+F`; Focus main chat `Alt+L`; Focus tab 1-9 `Ctrl+1..9`; Back `Ctrl+[` (mouse back); Forward `Ctrl+]` (mouse forward); Next recently viewed chat `Ctrl+Tab`; Previous `Ctrl+Shift+Tab`; Next/previous tab `Ctrl+Tab`/`Ctrl+Shift+Tab`, `Ctrl+Shift+]`/`[`, `Ctrl+PageDown`/`PageUp`; Next/previous chat `Ctrl+Shift+]`/`[`, `Ctrl+PageDown`/`PageUp`; Next chat needing attention `Ctrl+Alt+A`; Open your dot `Ctrl+.`; Go to recent chat 1-6 `Ctrl+Alt+1..6`; Toggle activity view `Ctrl+Alt+U`.
- Panels: Open browser tab `Ctrl+T`; Reopen closed tab `Ctrl+Shift+T`; New tab in full view `Ctrl+Shift+F`; Cycle workspace layout `Ctrl+Shift+B`; Toggle bottom panel `Ctrl+J`; Toggle navigation panel `Ctrl+Shift+E`; Toggle sidebar `Ctrl+Shift+S` / `Ctrl+B`; New tab `Ctrl+Alt+B`; Open terminal `` Ctrl+` ``.
- Project: Open folder `Ctrl+O`.
- App: Clear all unreads `Shift+Esc`; Show Mini `Alt+Super+P`; Settings `Ctrl+,`.
- General: Close other tabs `Ctrl+Alt+W`; Close tab `Ctrl+W` / `Ctrl+F4`; Close `Ctrl+W` / `Ctrl+F4`; Select effort `Ctrl+Shift+M`; Open project picker `Ctrl+Alt+Shift+O`; Open recent models `Alt+M`; Send message in background `Ctrl+Enter`; Copy deeplink `Ctrl+Alt+L`; Copy working directory `Ctrl+Shift+C`; Force reload browser page `Ctrl+Shift+R`; Open command menu `Ctrl+K` / `Ctrl+Shift+P`; Reload browser page `Ctrl+R`; Rename chat `Ctrl+Alt+R`; Search files `Ctrl+P`; Show keyboard shortcuts `Ctrl+/`; Go to chat 1-9 `Alt+1..9`.
