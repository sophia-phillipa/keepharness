# Codex desktop app: structural inventory (October 2026)

Type: reference. Source: the ChatGPT desktop app, build `26.930.31730`, observed on 2026-10-05 on Linux (Wayland, 1431x843 window) by reading the live DOM and accessibility tree. Nothing was sent, run, toggled or changed. Personal content is replaced by placeholders (`<thread title>`, `<project name>`, `<account email>`).

## Coverage

| Area | Status |
| --- | --- |
| Window chrome, application menu | Covered |
| Navigation model | Covered (live back/forward walk done); project open and Settings page-to-page history not walked |
| Sidebar (shape, row anatomy, sections) | Covered, including project and section menus (a thread row has no context menu) |
| Settings (nav and sections) | Covered (third pass added Personalization, Usage & billing, Git, Worktrees, Code Review, Archived chats, and the MCP list inside the Plugins page); Profile and Account not opened (they are external web links) |
| Tools association (Customize: Plugins, Skills) | Covered in detail; third pass added Manage destination, Personal tab, Create MCP App dialog, Skills page behaviour, grouping and state badges |
| Composer | Covered, including the project picker and the `@` and `/` popups (third pass) |
| Thread view (messages, steps, diffs, approvals) | Covered except approval cards (none pending in existing threads) |
| Other sections (Space, Scheduled, Explore, command menu) | Covered; third pass added the Code Review screen and the activity (unread) view |
| Keyboard shortcuts | Covered (app's own list) |

Gaps remaining after the third pass (2026-10-05, same build, same read-only rules): approval cards (none pending), a failed turn's Retry affordance (no failed thread exists and sending is out of scope), the Settings pages Profile and Account (external web links, not opened), the Code Review screen with real pull requests (the app showed `App unavailable` with a `Retry` button), and preference-persistence behaviour beyond the file listing below. Details of the second-pass gaps are now closed in the sections named in the table above.

### Fourth pass: 2026-10-08

Build `26.930.31730`, reconfirmed from the running executable and launcher.
Comparison baseline: KeepHarness `0.16.0` development at `3a0cd4a`.
See the [prioritized fourth-round gaps](codex-parity-gaps-2026-10-08.md).
This pass separates newly observed evidence from the earlier coverage above.

| Area | Fourth-pass coverage and limits |
| --- | --- |
| Window, mode and app menus | ChatGPT-to-Codex mode navigation; File and Help menus; normal launch restored after inspection |
| Directory and absent plugin | Loaded Public directory, categories and card controls; uninstalled Figma detail has `Install plugin`, no switch; no install attempted |
| Plugins management | Manage opens Settings > Plugins, distinct from the directory; five installed rows, all observed on; Add menu inspected without selecting a write action |
| Apps / MCPs / Skills | All three management chips opened; Apps 13, MCPs 7, Skills 3; server/plugin groups, switches and Personal skill labels inspected; counts are account-specific |
| Skills directory / scopes | Installed block and Team / Personal / System tabs observed; no plugin write-scope picker observed; scope-write effects not tested |
| Settings | General and all internal navigation pages opened, including Code Review settings, Git, Worktrees, Archived chats and Keyboard shortcuts; structural inspection only on personal-data pages; Profile and Account external links not opened |
| Composer and per-thread tools | Blank composer, plus menu, permissions and model control inspected without sending or selecting a setting; third-pass insertion behavior remains the reference, not a new enablement test |
| Sidebar / command menu | Thread-row Pin/Archive controls and section controls reconfirmed; command menu opened; no thread names retained |
| Thread | One thread opened solely for toolbar/control structure and its fixed action menu; personal messages neither extracted nor saved |
| Run steps / approvals / Retry | No pending approval or usable completed-step/failed-turn sample established in the structurally inspected thread; earlier run-step descriptions carried forward, no fresh behavioral proof |
| Space / Scheduled / Explore / Code Review | All opened; library controls, New task, Explore destinations and Code Review Retry observed; populated external content not inspected |
| Shortcuts | Internal Keyboard shortcuts page opened and its list recorded; not an execution test of every shortcut |
| Persistence | No preference mutation or persistence experiment; restoring the normal app is not proof of all preference persistence |

New or newly resolved observations:

- The absent-item distinction is now explicit: the directory card has `+` with
  `Install <name>`; the detail has `Install plugin`; installed management rows
  have `Toggle plugin enabled state`. No disabled switch substitutes for Install.
  Installation, OAuth and disable/re-enable effects remain untested.
- The loaded directory includes a Chrome-extension promotion and the additional
  category headings `Small Business`, `Business & Operations`, `Data & Analytics`,
  `Scientific Research`, `Security`, `Other`. These are observed catalog-content
  changes within the same build, not evidence of an app binary upgrade.
- The uninstalled Figma detail includes both an Apps section and a Skills section,
  plus an Information block and `Copy link`. Its public capability descriptions
  are catalog data, not instructions for this audit.
- The inspected thread action menu includes `Move to right pane` and `Open in`;
  `Add scheduled task...` was not present in this sample. Treat availability as
  context-dependent rather than deleting the earlier observation.
- The management page's scope labels and the directory's scope tabs do not prove
  where enablement writes land. No per-thread toggle or project/plugin scope
  write was performed. The observed switch values were on; the appearance of an
  actually disabled plugin remains unverified.

Local evidence is under `~/.cache/codex-runs/keepharness/parity-round4/shots/`
(`c-directory.png`, `c-absent-detail.png`, `c-manage.png`, `c-apps.png`,
`c-mcps.png`, `c-skills.png`, structural JSON and the shortcut list), not in git.
The initial `c-settings.json` contains navigation failures after the rail Code
Review entry was selected instead of its Settings entry. The later targeted
recovery opened Code Review settings, Git, Worktrees, Archived chats and Keyboard
shortcuts successfully; `c-settings-recovery.json` preserves that tool output
with its provenance, and `c-shortcuts.txt` was captured during that recovery.
The existing Chrome was connected through its plugin using a new task-owned tab.
Electron attachment is outside that plugin's exposed capabilities, so the
announced desktop fallback used Playwright over loopback port 9339. The initial
window-close request did not exit the main process within 30 seconds; SIGTERM
targeted only the verified ChatGPT main PID. The inspected debug instance was
then quit through File > Quit ChatGPT and the usual `chatgpt-host` launcher was
started without debug flags. Port 9339 was verified closed. No unrelated Codex
server process was targeted.

Launch note: the debug port is opened by passing `--remote-debugging-port=9339 --remote-debugging-address=127.0.0.1` to the launcher (`chatgpt-host` forwards its arguments to the binary); OS-level accelerators such as `Ctrl+,` and `Ctrl+K` sent through the debug protocol did not reach the app, so pages were opened through the UI buttons. The earlier crashes (Skia `SkFontMgr_FontConfigInterface` "Not implemented") came from launching without the app's own fontconfig. Launching the build through `systemd-run --user` on the host with `FONTCONFIG_FILE=<app dir>/fonts.conf`, `ELECTRON_OZONE_PLATFORM_HINT=auto`, `--disable-gpu --ozone-platform=wayland` and `--remote-debugging-port=9339` (loopback only) rendered text in screenshots and did not crash on any page opened in the second pass.

## Navigation model

- The app has one window with three modes selected from the sidebar header button `Switch mode`. The menu lists `ChatGPT` ("Create, learn, and explore") and `Codex` ("Build, debug, and ship"); the command menu also offers `Switch to Chat`, `Switch to Work`, `Switch to Codex`. The sidebar header shows the current mode name with a chevron.
- Top-left controls, in order: `Back to ChatGPT` (hidden skip link), `Back` (28 px, left arrow), `Forward` (28 px, right arrow), `Hide sidebar` (panel icon), then the in-window application menu `File Edit View Help`.
- `Back` and `Forward` are disabled at a fresh start. `Back` became enabled after a mode switch and after opening Settings, so the history includes mode switches and top-level screens; `Forward` stays disabled until a back step. Shortcuts: `Ctrl+[` back, `Ctrl+]` forward, plus mouse back/forward buttons.
- History walk (live): each of these pushes one history entry: mode switch, `Space`, `Scheduled`, `Plugins` (Customize), switching Customize between `Plugins` and `Skills`, `Home`, and opening a thread from the sidebar. Back and Forward step through them one by one and restore the previous screen and selected sidebar row; opening a new place after a Back step discards the forward entries (`Ctrl+Tab` recently viewed also pushes). Back is disabled only at the very first entry (the start mode's home).
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

Menus (opened with the visible buttons, then Escape): `Project actions for <project>` lists `Pin`, `Edit`, `Section`, `Open in File Manager`, `Archive chats`, `Remove project`. `Project sidebar options` lists `Organize sidebar`, `Sort chats by`. `Chat sidebar options` lists `Organize sidebar`, `Sort chats by`, `Projects`, `New section`. Right-clicking a thread row or a project row opens nothing. A thread row's only controls are the hover buttons `Pin chat` and `Archive chat`; the full thread menu is in the thread toolbar (below).

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
| Plugins | Installed-item management, reached through Customize > Manage; distinct from the directory (see Manage destination below) |
| Passwords | Search, `Saved passwords (n)`, `Refresh`, empty state `No saved passwords` |
| Computer use | `Control` section listing a browser with `Install` |
| Browser | In-app browser: link and URL open destinations, `Show full URL`, clear browsing data, history, annotation screenshots, password manager, contact info, extensions, downloads, site permissions, WebMCP site tools switch, agent permissions table (site pattern, Browsing, Downloads, Uploads; values Requires approval, Always allow, Block), developer mode `Enable full CDP access` |
| Cloud computer | Cookie managers, website approvals (Always ask, Auto approve, Always allow), website permissions with `Add website` |
| Hooks | Empty state `No hooks found` / `Configured hooks will appear here` |
| Connections | `Control this PC`: SSH, `Allow connections` switch, `Add`, `Keep this PC awake` switch |
| Codex Cloud | `Environments`, search, `Create environment`, empty state `No saved environments` |
| Legacy Codex Cloud | Migration notice, `Environments` and `Preferences` tabs, `Create environment` |
| Keyboard shortcuts | See the last section |
| Personalization | `Codex memory` (`Local` scope chip; switch `Enable Codex memories`; switch `Allow memories from tool-assisted chats`, disabled until memories are on; `Delete` for all memories); `Custom instructions` (`Codex instructions`, button `Edit Codex custom instructions`); `Writing` (switch `Reference my writing style`) |
| Usage & billing | Tabs `Overview` and `Analytics`; `Your plan` (plan name and price, `View plans`, link "Settings > Billing on Web"); `Plan limits` (weekly limit with reset countdown and "% left"); `Credits` (balance, `Add more`, `Automatic reload` switch); `Allow Codex to use resets` switch; `Usage limit resets` with tabs `Available <n>` and `History` (rows "Reset used/received" with date) |
| Git | `Branch prefix` (text); `Pull request merge method` (segmented Merge, Squash); `Always force push` (switch); `Create draft pull requests` (switch); `Review delivery` (segmented Inline, Detached); `Watch and fix pull requests` (`Auto-merge when ready` switch, `Pull request watch instructions` textarea); `Commit instructions` and `Pull request instructions` (textareas) |
| Worktrees | Tabs `General` and `Environments`. General: `Worktree root` (text), `Always fetch upstream before creating worktrees` (switch), `Automatically delete old worktrees` (switch), `Auto-delete limit` (number), a managed-worktrees list with `Refresh` and the empty state "No worktrees yet". Environments: `Select a project` list, one row per project with `Add environment to <project>` |
| Code Review | Tabs `Preferences` and `Review agent`. Both show `Repositories` (provider dropdown `Git provider`, `Search repositories`, a paged list of `<owner>/<repo>` rows with the line "Automatic code review: Follow personal preferences", `Previous`/`Next`) and `Personal preferences`: `Automatic review` (switch), `Review trigger` (dropdown, default "On PR open"), `Exhaustive code review` (switch), `Use credits for reviews` (switch) |
| Archived chats | `Delete all`; `Search archived chats`; `Filter archived chats` menu (All chats, Local, Cloud; sort Updated, Created, Alphabetical); `Filter archived chats by project` menu (projects, `Chats`, `Scheduled tasks`). Body groups per project: header with `<n> chats` and a `Project actions` menu (`Delete all in project`); rows with the chat title (a link, except for some), the archive date and two buttons, `Unarchive` and `Delete archived chat <title>` |
| Profile, Account | External rows (arrow icon); open the web, not opened |
| MCP servers | Not a separate page: Settings search results for "MCP" lead to Settings > Plugins > `MCPs` chip (see Manage destination) and to the Hooks, Keyboard shortcuts and Browser rows that mention MCP |
| Settings search | The `Search` field replaces the navigation with a flat result list (setting label plus the page name below it); selecting a result opens that page |

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

### Manage destination and grouping (third pass)

- `Manage` (gear link on both Customize pages) opens Settings > Plugins (`/settings/plugins-settings`), a management page, not a directory. Header: title `Plugins`, subtitle "Manage plugins, skills, and MCPs", `Browse directory` and `Add` (menu: `Create plugin`, `Add a marketplace`, `Add MCP server`). Four filter chips with counts: `Plugins`, `Apps`, `MCPs`, `Skills`, and a search field that follows the chip.
- Plugins chip: one row per installed plugin (icon, name, description, a row menu with an unlabeled first item and `Uninstall`, and an enable switch `Toggle plugin enabled state`). Apps chip: one row per app (name, description, switch `Enable app`). Skills chip: rows with a scope label (`Personal` or `System`), a row menu and a switch `Disable skill`. MCPs chip: group `Servers` (one row per configured server with a gear `Settings` button and an `Enable` switch), group `From plugins` (read-only rows such as `codex_apps`), group `MCP Apps` with `App data` and a `Clear all app data` button.
- MCP server gear opens an in-page form `Update <server> MCP` with `Back`, a red `Uninstall`, the note "If you would like to switch MCP server type, please uninstall first.", and fields `Command to launch`, `Arguments` (rows plus `Add argument`), `Environment variables` (name and value rows plus `Add environment variable`), `Environment variable passthrough`, `Working directory`, and `Save`. `Add MCP server` opens the same form titled `Connect to a custom MCP` with `Name` and a `Type` toggle (`STDIO`, `Streamable HTTP`) first.
- Customize > Plugins directory > `Personal` tab shows the empty state "No plugins found" when no personal plugin exists. The directory has no marketplace sections beyond the category sections listed above and no per-card badge; the only state differences are the `+` button (not installed) and the `...` button (installed). The text `(Beta)` is part of a plugin's name, not a badge.
- `Create MCP App` (directory `Add` menu) opens a dialog `New Plugin`: `Choose icon` (optional PNG up to 10 KB), `Name`, `Description (optional)`, `Connection` radio (`Server URL`, `Tunnel`), `Server URL`, `Authentication` dropdown (`OAuth`, `No authentication`), an `Advanced OAuth settings` expander (disabled until a URL is entered), the warning "Custom MCP servers introduce risk" with a required checkbox `I understand and want to continue`, the trust paragraph, `Upload plugin archive`, `Cancel` and `Create` (disabled until valid). Cancel closes it with no change.
- Skills page (Customize): the skill cards are plain buttons without a card menu or hover controls; the `Add` icon on that page does not open a menu, it starts a new chat in Codex home with a `Skill Creator` chip in the composer (nothing sent). Tabs `Team`, `Personal`, `System` filter the same grid.
- Disabled, needs-auth and unavailable states: no card in the directory or the installed list showed a not-connected warning, a needs-auth badge or a disabled style. Disabling exists only as the Manage-page switches; a switched-off plugin is not distinguished elsewhere in what was observed (all switches were on). No screen for custom agents or subagents exists in Customize or Settings; the only agent-like items are skills, listed under `Skills` in the `/` popup.

### Per-thread enabling

Tools are not toggled per thread in a list; the composer `+` popup (below) lists installed plugins and apps, and picking one inserts it into the prompt. Global enablement is the Settings > General `Plugins` switch.

## Composer

Home (Codex mode) empty state: heading "What should we build?", a mark above it, a `Composer utility bar` with `Choose project` and `Work in` (value `This computer`; menu: This computer, Cloud, Remote), then the text box (placeholder `Do anything`, a suggestion chip `Skill Creator help me create a skill`). Bottom row, left to right: `Add files and more`, `Change permissions` (shield icon plus current mode text), model button, `Dictate`, `Send` (arrow in a white circle; `Start new voice chat` when empty).

| Control | Behaviour |
| --- | --- |
| `Add files and more` | Popup above the composer. Group `Add`: Files and folders; Work in a project ("Choose project for new chats"); Goal ("Set a goal to keep pursuing"); Plan mode ("Turn plan mode on"); Sketch ("Draw a sketch"). Group `Plugins`: each installed plugin (name + description), Pages, Pets, Plugin Management, Sites, Chrome, Browser. Group `Apps`: connected apps (name + description). Group `ChatGPT conversations`: past conversations |
| `Change permissions` | Menu "How should ChatGPT actions be approved?" with `Learn more`; items `Ask for approval` ("Always ask to edit external files and use the internet"), `Approve for me` ("Only ask for actions detected as potentially unsafe"), `Full access` ("Unrestricted access to the internet and any file on your computer") |
| Project picker | The `Choose project` button in the utility bar opens a popover: `Search projects` field, one row per project (icon, name, secondary path label) and `New project`. The first row is highlighted; Escape closes it with no change |
| `@` popup | Typing `@` in an empty composer opens a list above the box with groups `Add` (Files and folders, Work in a project, Goal, Plan mode, Sketch), `Plugins`, `Apps`, `ChatGPT conversations`, then `Files and chats` ("Type to search files or chats") and `Pages`: the same content as the `+` popup plus the file and chat search. Escape closes it |
| `/` popup | Typing `/` opens a list with commands, each with a one-line description and a chevron for the ones with a submenu: `Fast`, `Feedback`, `Goal`, `MCP` (submenu, "Show MCP server status"), `Model` (submenu), `Pet`, `Plan mode`, `Reasoning` (submenu, shows the current effort), `Sketch`, `Status` ("Show chat ID, context usage, and rate limits"), `Usage & billing`, `Work in a project`. Below, a `Skills` group lists every installed skill (name, description, and a trailing scope label `Personal` or `System`, including template skills). Escape closes it |
| Model button | Popover: a model row (opens a radio list: Default "Recommended set of models", GPT-6.1 Sol, GPT-6 Astra, GPT-6 Sol, GPT-6 Luna, GPT-5.6 Sol, GPT-5.6 Terra, GPT-5.6 Luna, GPT-5.5); `Enable fast mode` checkbox ("More usage"); `Power` slider with 6 stops (Light, Medium, High, Extra High, Max, Ultra), adjusted with Left/Right arrows. The button label is `<model> <effort>` |
| Shortcuts | `Ctrl+Shift+M` select effort, `Alt+M` open recent models, `Ctrl+Alt+Shift+O` project picker, `Ctrl+Enter` send in background |

## Thread view

Opening an existing thread from the sidebar replaces the home screen; the sidebar row is highlighted.

| Region | Detail |
| --- | --- |
| Toolbar (`Chat toolbar`, top of the pane) | Project folder icon (accessible name `Project: <project name>`), thread title, `Chat actions` (`...`), `Toggle summary` (list icon), and at the far right `New tab` |
| `Chat actions` menu | `Rename` `Ctrl+Alt+R`; `Pin` `Ctrl+Alt+P`; `New side chat` `Ctrl+Alt+S`; `Fork` (submenu); `Add scheduled task...`; `Share`; `Copy` (submenu); `Open in new window`; `Archive` `Ctrl+Shift+A` |
| `Toggle summary` | A small popover `Outputs` ("Create a file or site") with a `+` button; it is the per-thread outputs summary |
| Pane layout | Centered column, about 740 px wide, scrolled by `thread-scroll-container` (a column-reverse container: `scrollTop` is 0 at the bottom and negative when scrolled up). Older turns collapse behind a `N previous messages` row. A floating round `Scroll to bottom` arrow appears whenever the view is not at the bottom |
| Time markers | Centered separators ("Saturday 6:52 PM", "Sep 6 at 2:55 PM") between turn groups; each message also carries a time under it |

### Message anatomy

- User message: a right-aligned rounded bubble (heading `You said:`), time under it, hover actions `Copy message` and `Edit message`. The first message of a project thread may include a system-style preamble naming the workspace.
- Assistant message (heading `ChatGPT said:`): full-width markdown (paragraphs, lists, bold, inline code, tables, links). Inline file links render with a file icon (open in the right pane); inline citation chips render as small buttons (for example a decision id). Rich cards can follow the text (`Web preview` with `Open in` and `Choose where to open`). Footer actions: `Copy`, `Rate response`, `Fork chat from here`, then the time. The last message is labelled `Latest response`.
- Working summary: a collapsed row `Worked for <duration>` with a chevron sits above the assistant text; expanding it shows the intermediate commentary in place.

### Run steps

Steps are one-line collapsed rows with a leading icon: `Ran <command>` (terminal icon, command truncated with an ellipsis), `Read files`, grouped rows such as `Read files, ran a command` or `Ran commands`, and a named step for a tool call (for example a short description of the call). Expanding a tool step shows a code block (language tag, here `json`) with the call result and a `Show raw tool call output` button. Subagent work appears as a group row ("<a>, <b> and N more finished") with coloured flower icons, and each named subagent is a button in the thread. No separate agent management screen was found in the surfaces covered.

### Diffs and file changes

An `Edited N files` card follows the assistant text: icon, `+<added> -<removed>` totals, `Undo` and `View changes` buttons, then up to three file rows (path with the file name emphasised and `+n -m`) and `Show N more files`. A single-file edit shows `Edited <file> +n -m` with `View changes`. `View changes` opens a `Changes` tab in the right-hand workspace pane: a `Review source` dropdown (default `Last Turn`) with totals, `Changes options`, `Jump to file`, `Show files`, and one row per file with `Open in`, `Open in editor` and `File actions`. `Undo` was not clicked. Approval cards were not found in the threads available (none pending).

### Workspace pane (right)

A project thread can show a right pane beside the thread, split by a draggable separator (`Resize workspace panes`): a tab strip (project tab, file tabs, the `Changes` tab, a `+` new tab button, expand and split-layout buttons), a path breadcrumb with a copy button and an `Open` button (VS Code, with a dropdown). The pane restores its tabs when the thread is reopened; a tab for a file that no longer exists shows the toast `Could not open file`. Other controls in this pane: `Toggle file tree`, `Open options`, `Close`.

### Composer in a thread

`Do anything` text box, `Add files and more`, `Change permissions` (current mode, for example `Full access`), a context-usage ring (`Context usage: 9%`), the model button (`<model> <effort>`), `Dictate` and `Start voice chat`. A resumable thread shows a `Resume` button in place of the voice button.

### Scroll behaviour

A thread opens at the bottom (latest response). The scroll position is kept per thread while the app runs: scrolling a thread up, opening another thread and coming back (by click or by `Back`) returned to the same offset. Whether it survives an app restart was not tested.

## Preferences persistence and opening with text

### Where UI state lives

The app's Chromium profile is `~/.config/Codex/` (listing only, contents not read). Relevant names: `Local State`, `Default/Preferences`, `Default/Local Storage`, `Default/IndexedDB`, `Default/Session Storage`, `browser-sidebar-page-states.json` (the in-app browser sidebar page states), `Last Version`, `Singleton*` lock files (a stale lock remains after an unclean exit). Sidebar width and the last-open thread were not isolated to one file; observable behaviour is that per-thread scroll position and the right-pane tab set are restored when a thread is reopened. The thread catalog itself lives under the Codex home, outside this profile.

### Opening the app with text (deep links)

Read from the app's `package.json`, `.desktop` file and bundled code; no link was triggered.

- Scheme: `codex://` (the `.desktop` file declares `MimeType=x-scheme-handler/codex;`; the app calls `setAsDefaultProtocolClient("codex")` on Linux; a development build uses `codex-dev://`). `x-scheme-handler/chatgpt` has no handler.
- New thread with prefilled text: `codex://threads/new?prompt=<url-encoded text>`. Optional parameters on the same route: `mode=<mode>` (single value), `path=<absolute project path>`, `projectId`, `originUrl`, `browserUrl` (http or https only). The web redirect form `https://chatgpt.com/codex/open-app?q=<text>&mode=...&codex_thread_id=...` is converted to the same route. Whether the text is only prefilled or also sent was not verified, so treat it as untested.
- Other routes: `codex://threads/<thread id>[?hostId=...]`, `codex://settings/<page>` (for example `codex://settings/connections`), `codex://plugins[/<plugin id>[/app/<app>]]`, `codex://space`, `codex://sites/edit`, `codex://launch`, `codex://dots`, `codex://review?pr=<url>&path=<file>&line=<n>&side=left|right`, `codex://shared-thread`. The app can also copy a thread's deep link (`Ctrl+Alt+L`) and open one from the clipboard (debug menu).
- Command line: `--open-project <path>` opens a project; a bare absolute path argument and a `codex://` URL argument are also parsed. A second launch hands its arguments to the running instance.

### Claude Desktop on this machine (for comparison)

Claude Desktop is installed as a Debian package (`claude-desktop`, launcher `com.anthropic.Claude.desktop`, `MimeType=x-scheme-handler/claude;`). Its own desktop actions use `claude://claude.ai/new?surface=chat&source=desktop_action` and `claude://code/new?source=desktop_action`. Prefill: `claude://claude.ai/new?q=<text>` (a value starting with `/` is rejected; only the `q` parameter is allowed) and `claude://code/new?q=<text>` (or `prompt=`) with optional repeated `folder=` and `file=`. Also `claude://code/continue?session=last`. The Claude Code CLI separately registers `claude-cli://` through `claude --handle-uri`.

## Unread indicator and retry (third pass)

- Unread: the sidebar header bell `View activity, needs attention` carries a blue dot, and so does the Home rail icon. The bell toggles an activity view (`Turn off activity view`, `Ctrl+Alt+U`) that replaces the project list with a flat list: a `Priority` group (with a `...` options menu `Activity view options`) holding threads that need attention, each row marked by a blue dot at the right edge, then date groups (for example `Saturday`). Every activity row shows the title plus a two-line snippet of the last assistant message (or the project label when there is none) and the hover buttons `Pin chat` and `Archive chat`. In the normal project list no row dot was seen.
- Retry: a failed turn's Retry was not reachable (no failed thread, no sending). Retry buttons that were seen: the Code Review screen's `App unavailable` card ("Try again to load the app", `Retry`), and a `Retry` on rows in Settings > Voice recent recordings.

## Other top-level sections

| Section | Contents |
| --- | --- |
| Space | Left panel: `New page`, then `All`, `Pages`, `Sites`, `Images`, `Google Drive`, `Trash`. Main: `Search` field, `New` button, tabs `Suggested`, `Favorites`, `Your items`, `Shared with you`, `Open filters`, `Grid view`, `List view`, a dismissible "Start with a template" row of six template cards, a library table (Name, Source, Last activity) with a loading state |
| Scheduled | Left panel: `New task`, groups `Upcoming` and `Paused` listing tasks. Main: a grid of 359x108 px template cards (emoji, title, one-line description) such as weekly meal plan, weekly finances update, find flights |
| Explore | Popover with Projects, Sites, Maps, GPTs, Code Review |
| Code Review | Rail button in Codex mode (git icon). Left panel: title, `Sidebar display options`, `Search or paste a PR link` field, and three collapsible groups `Authored by me`, `Needs my review`, `Needs my team's review` (loading skeleton rows). Main pane showed `App unavailable` with a `Retry` button |
| Command menu | Opened by the sidebar `Search` button, `Ctrl+K` or `Ctrl+Shift+P`. Dialog with a search combobox "Search commands and past chats". Groups: `Chats` (first nine recent chats with project label and `Alt+1..9`), `Quick actions` (New chat, Open folder), `Settings` (every settings page), `Chat` (New standalone chat, Quick chat, Archive chat, Toggle pin), `Navigation` (Switch chat…, Toggle activity view, Focus main chat, Open your dot, Next chat needing attention, Previous/Next chat, Go to recent chat 1-6, Switch to Chat/Work/Codex, Find, Back, Forward), `Panels` (Reopen closed tab, Toggle sidebar, Toggle navigation panel, Toggle bottom panel, Open terminal, Open browser tab, New tab in full view, Cycle workspace layout, New tab), `Skills` (Force reload skills, Go to skills), `Configure` (Theme) |

## Keyboard shortcuts (from the app's own screen, `Ctrl+/`)

A dialog "Keyboard shortcuts" with a search field and a `Close dialog` button. Sections and rows:

- Chat: New chat `Ctrl+N` / `Ctrl+Shift+O`; Quick chat `Ctrl+Alt+N`; Archive chat `Ctrl+Shift+A`; New standalone chat `Ctrl+Alt+O`; Toggle pin `Ctrl+Alt+P`.
- Navigation: Find `Ctrl+F`; Focus main chat `Alt+L`; Focus tab 1-9 `Ctrl+1..9`; Back `Ctrl+[` (mouse back); Forward `Ctrl+]` (mouse forward); Next recently viewed chat `Ctrl+Tab`; Previous `Ctrl+Shift+Tab`; Next/previous tab `Ctrl+Tab`/`Ctrl+Shift+Tab`, `Ctrl+Shift+]`/`[`, `Ctrl+PageDown`/`PageUp`; Next/previous chat `Ctrl+Shift+]`/`[`, `Ctrl+PageDown`/`PageUp`; Next chat needing attention `Ctrl+Alt+A`; Open your dot `Ctrl+.`; Go to recent chat 1-6 `Ctrl+Alt+1..6`; Toggle activity view `Ctrl+Alt+U`.
- Panels: Open browser tab `Ctrl+T`; Reopen closed tab `Ctrl+Shift+T`; New tab in full view `Ctrl+Shift+F`; Cycle workspace layout `Ctrl+Shift+B`; Toggle bottom panel `Ctrl+J`; Toggle navigation panel `Ctrl+Shift+E`; Toggle sidebar `Ctrl+Shift+S` / `Ctrl+B`; New tab `Ctrl+Alt+B`; Open terminal `` Ctrl+` ``.
- Project: Open folder `Ctrl+O`.
- App: Clear all unreads `Shift+Esc`; Show Mini `Alt+Super+P`; Settings `Ctrl+,`.
- General: Close other tabs `Ctrl+Alt+W`; Close tab `Ctrl+W` / `Ctrl+F4`; Close `Ctrl+W` / `Ctrl+F4`; Select effort `Ctrl+Shift+M`; Open project picker `Ctrl+Alt+Shift+O`; Open recent models `Alt+M`; Send message in background `Ctrl+Enter`; Copy deeplink `Ctrl+Alt+L`; Copy working directory `Ctrl+Shift+C`; Force reload browser page `Ctrl+Shift+R`; Open command menu `Ctrl+K` / `Ctrl+Shift+P`; Reload browser page `Ctrl+R`; Rename chat `Ctrl+Alt+R`; Search files `Ctrl+P`; Show keyboard shortcuts `Ctrl+/`; Go to chat 1-9 `Alt+1..9`.
