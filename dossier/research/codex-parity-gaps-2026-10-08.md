# Codex app and KeepHarness: fourth parity round

Kind: reference. Date: 2026-10-08. Baseline: KeepHarness `0.16.0` in development,
`3a0cd4a`; ChatGPT desktop in Codex mode, build `26.930.31730`.

This audit compares observable behavior with the [Codex inventory](codex-app-inventory-2026-10.md),
[parity design](../codex-parity-design.md), [D-041](../decisions/d-041-provider-state-routes-and-notices.md)
and [W10 contract](../provider-state-w10-contract.md). Recommendations are proposals,
not changes to those decisions or to product code. D-019 requires copying shared
concepts, D-020 preserves KeepHarness palettes, and D-021 protects its additional features.

## Evidence and boundaries

Codex was inspected without sending messages, installing, uninstalling, changing
settings or switching enablement. Navigation between pages and opening menus are
read-only inspection. Personal thread content is excluded; structural references use
`<thread title>`, `<project name>` and `<account email>` placeholders.

The Chrome plugin connected successfully to the existing Chrome and a new task-owned
tab. Its exposed capabilities did not include attaching to an Electron process;
the announced desktop fallback used Playwright over the authorized loopback debug
endpoint. Screenshots and local evidence belong under
`~/.cache/codex-runs/keepharness/parity-round4/shots/` and are not committed.

The issue inventory used `gh issue list --state all --limit 1000`: 42 issues,
numbers 6–47. Issue status does not prove implementation status: #21 and #43 are
open, while the current release notes and checkout already include provider-state
switches and external-change notices. The older design work-package table and
earlier release sections are historical, not the current implementation baseline.

KeepHarness ran from this worktree's `desktop/` source using the already-cached
Electron `44.4.1` runtime, extracted into the run directory. The existing
`tests/operator/fixture/serve_fixture.py` served admin/harness on 18740/18741;
its HOME, state, project files and fake provider commands were private. Desktop
profile, data/config directories and loopback CDP port 18742 were also isolated.
The window was visible on `DISPLAY=:0` (the host also exposes
`WAYLAND_DISPLAY=wayland-0`); interactions used approximately 900 ms pauses.
Neither production ports 8094/8095 nor the other session's ports were used.

The initial UI-gate check was clear. A later check found another session's
`test-ui.sh`, so Electron was deferred while the Codex inspection and document
skeleton continued. The next check found neither that process nor listeners on
18094/18095, and only then was Electron opened; the 60-minute fallback was not
needed. No concurrent gate was stopped or modified.

| Surface | KeepHarness evidence | Limit |
| --- | --- | --- |
| Shell, sidebar and thread | Visible desktop, fresh draft and fake-provider conversations; thread menu screenshot | No production conversations |
| Settings | Appearance, Models, Plugins, Agents, Archived chats and all five System destinations opened; `k-settings.json` / screenshots | Auxiliary account/client dialogs were not activated to grant access |
| Plugin management | Actual isolated admin plus `k-plugin-fixture.png` / `.json`, six assertions | Row-state cases use mocked responses adapted from the existing switch spec, not real CLI writes |
| Composer, files and tools | Model/effort/access menus, Files and Plugins chips, `/` resources, screenshots | Chips were hidden on the completed thread; continued in a fresh draft where they were visible |
| Command search / shortcuts | Search dialog and `Ctrl+/` focus behavior | Does not claim every shortcut was executed |
| Run steps | Existing `06-run-console.cjs` operator area, visible, fake `OP-TOOLS` run; `op/summary.json` | Fixture output, not cloud inference |
| Approvals | Fake `OP-APPROVAL` card with command, Technical details, Allow once and Deny; `k-approval-blocked.png` | Deny returned the not-enrolled explanation; access enrollment was rejected by automatic approval review and was not performed |
| Approval cleanup | Normal Cancel control; `k-walk-finish.json`: Cancelled, zero needs-you | Allow/deny completion remains unvalidated; the guard is not labelled a product defect |
| Space / Scheduled | Both rail destinations, empty states and New task editor; screenshots | No scheduled execution or federated content integration test |
| Explore | No corresponding control in the inspected shell/source | Not evidence that equivalent provider tools cannot be invoked conversationally |

The read-only source reviewer used focal `rg`/source inspection: Graphify was
available, but this worktree had no graph to query. Relevant implementation
anchors are `control/customize.js` (`installed`, `renderList`, row switches),
`agent_service/index.html` (Settings/composer/Space/Scheduled),
`agent_service/ui.js` (`conversationRow`, `renderConversationSearch`,
`showSettingsPage`, `openSpace`, `loadPages`, `openScheduled`, `showApproval`),
and `agent_service/run-console.js` (`spanDetail`). Absence claims are bounded to
those visible surfaces and source paths, not an exhaustive backend audit.

## Plugins catalog switches

### Observed Codex behavior

The directory and installed-item management are different surfaces. An absent
directory card has a `+` control named `Install <name>`. The inspected Figma detail
has `Install plugin` and no switch. Installed directory cards instead expose a
`More actions` menu. Settings > Plugins shows the installed plugins with
`Toggle plugin enabled state` switches. Apps have `Enable app`; configured MCP
servers have `Enable`; Skills have scope labels and enablement controls. MCPs
separates `Servers` from `From plugins`.

The fourth pass observed five installed plugins, thirteen apps, seven MCP entries
and three skills in management. These are this account's observed counts, not
product limits. No plugin scope picker was observed. Skills directory tabs
`Team`, `Personal`, `System` and management scope labels do not establish a
write-scope contract. Installation completion, authentication, the effect of
switching off, and precedence after a scope write remain untested by design.

### Current KeepHarness contract

D-041 uses the union of catalog rows and snapshot-only plugins. A provider gets
a switch only when its snapshot contains the same item ID. Catalog-only entries
say `Not installed in <CLI>` and have no switch. Each switch writes its provider's
fingerprint and the item's deciding scope. Track A pages request `sem-projeto`;
there is no project selector. A read-only installed item has a disabled switch
with its reason. Absence and inability to write are different states.

There is also a terminology trap: `control/customize.js` excludes catalog entries
whose status is `available` from its current installed list. A "catalog-only row"
in D-041 means a displayed catalog entry without a matching state item, not every
available directory item. An ID mismatch or unreadable state must be diagnosed
before concluding that an apparently installed CLI plugin is truly absent.

### Recommendations and alternatives

These three low-risk documentation recommendations were submitted separately to
the Master-JEV gateway on 2026-10-08; each result was checked against the observed
surface and D-041. They authorize no implementation or provider write.

| Question | Eligible alternatives | Recommended option and boundary | JEV result |
| --- | --- | --- | --- |
| Absent catalog item | Active switch that installs implicitly; disabled switch; explicit Install action | **Install**, with the Codex `+` / `Install <name>` directory affordance and `Install plugin` detail action. Only expose an executable install path supported by that provider. Keep installation, authentication and enablement separate; show an explanation when installation is unsupported. Reserve disabled switches for installed but non-writable state, with the reason. | `install`, confidence 0.99 |
| Project and write scope | Current implicit deciding-layer write; one universal scope dropdown; explicit project context plus supported provider write scopes | **Explicit project context**, then a scope selector in provider details when more than one write scope is supported. Show the effective value, deciding source and override consequences. Do not offer managed or Codex project-layer writes that the adapter rejects. With one eligible scope, show its label instead of a redundant picker. This extends KeepHarness, rather than asserting a Codex plugin scope picker exists. | `explicit`, confidence 0.97 |
| One switch per provider or per row | Independent provider switches; one row switch changing every provider; one switch for an explicitly filtered provider | **Independent provider switches**, labelled with the CLI, grouped compactly with provider details. An item can be on in Codex, off in Claude, absent elsewhere or read-only; one aggregate switch obscures both the target and partial failure. A provider-filtered view can later show one switch for that explicitly selected provider, but must not silently fan out writes. | `provider`, confidence 0.95 |

Preserve per-provider fingerprints, stale-state conflict recovery, deciding-source
labels and read-only reasons. Thread tool selection is a separate concern: the
third-pass Codex observation describes inserting a tool into the composer, not
changing global installation or proving that all tool use requires that insertion.
Do not implement a per-thread global enable/disable switch on that evidence.

## Prioritized differences

Priority: P0 blocks daily use or is a confirmed bug; P1 is visible friction;
P2 is polish. Size estimates: XS is a small wording/control adjustment, S a local
UI change, M a feature across existing UI/API seams, L a substantial feature,
XL a subsystem. Estimates describe a possible implementation, not a commitment.

Evidence tags: **C4** = observed in this Codex pass; **C3** = earlier inventory,
not freshly proven; **K4** = isolated desktop walk; **KF** = synthetic responses
in that desktop; **S** = focal source inspection at the baseline. **Research**
means the difference is documented but the implementation boundary needs design
or provider-capability evidence. An em dash means no dedicated covering issue
was found among the 42 searched issues, not that an issue should be created now.

No newly demonstrated P0 product defect emerged from the bounded walk. The
highest-priority work below is the incomplete Plugins management flow. Existing
switches, notices, search, scheduling and run inspection are not counted as absent.

| ID | Area | What Codex does | What KeepHarness does | User impact | Priority | Size | Existing issue |
| --- | --- | --- | --- | --- | --- | --- | --- |
| G01 | Plugins entry points | Rail Customize, Settings management and Manage are linked (C4). | Settings/rail Plugins opens the older skill catalog; new admin `#plugins` is separately reachable (K4/S). | The expected entry does not reach installed-plugin management. | P1 | S | #26 open; #24 closed is the rename only |
| G02a | Plugin directory | Categorized Public/Personal card directory, search, Refresh (C4). | Installed-list shell; `available` catalog entries are filtered out; no matching directory (K4/S). | Discovery requires leaving the expected flow. | P1 | L | #25 open |
| G02b | Plugin details | Detail page exposes description, Apps/Skills, developer information and actions (C4). | No equivalent card-to-detail view in the management shell (K4/S). | Users cannot inspect a plugin's capabilities before choosing it. | P1 | M | #25 open; its current scope omits several Codex actions |
| G03 | Absent-item installation | `+` / `Install <name>` and `Install plugin`; no absent-item switch (C4). | Unmatched displayed catalog entry says `Not installed in <CLI>`; no Install action (KF/S). | The explanation has no next action; adding an enable switch would misrepresent installation. | P1 | M | #25, #27 open; #21 covers existing switches |
| G04 | Apps management | Installed apps have their own list and enable switches (C4). | Apps chip says `Apps are not listed here yet.` (KF/S). | Cannot manage app enablement from this screen. | P1 | M | #22 open |
| G05 | MCP management | Configured Servers, From plugins and MCP Apps groups; server controls (C4; form detail C3). | MCPs chip is a placeholder; connector configuration exists elsewhere (KF/S). | Daily connector management leaves the Plugins workflow. | P1 | L | #22 open; #44 open covers trust, not all UI |
| G06 | Skills management | Scope labels, switches and directory filters (C4). | Skills chip is a placeholder; older catalog and `/` resource picker already exist (K4/KF/S). | Discovery and enablement/scope are split across surfaces. | P1 | M | #23 open; #19 closed is the read endpoint |
| G07 | Add actions | Management Add offers Create plugin, Add a marketplace, Add MCP server (C4); directory has its own Add choices (C3). | No corresponding Add menu in new manager (K4/KF/S). | Installation/configuration paths are incomplete. | P1 | M | #27 open |
| G08 | Project/write context | Management shows skill scopes; no plugin write-scope selector was observed (C4). | Manager always requests `sem-projeto` and writes the deciding scope (KF/S). | Cannot intentionally inspect another project's effective state; scope ambiguity is a KeepHarness-specific extension gap. | P1 | M | #21 open; #23 covers skill scopes |
| G10 | Command menu | Commands, settings and chats share a searchable command surface (C4/C3). | Global search covers runs/conversations and project files (K4/S). | Keyboard users must leave search to navigate settings or commands. | P1 | M | — |
| G11 | Settings search | Search settings finds individual preferences and their page (C3; field C4). | No settings search over existing sections (K4/S). | More navigation and recall for an increasingly large Settings area. | P1 | M | —; #6 closed covers submenu navigation |
| G12 | UI language | General includes Language (C4). | No interface-language selector; UI is English (K4/S). | Sophia cannot choose the requested Portuguese UI. | P1 | L | #16 open |
| G17 | Shortcut reference | Searchable Keyboard shortcuts page/list (C4). | Appearance lists a few shortcuts; `Ctrl+/` focuses the composer, not a reference dialog (K4/S). | Shortcuts are harder to discover and muscle memory differs. | P1 | S | — |
| G20 | Post-turn file review | Edited-files summary and Changes review pane (C3; not re-exercised on personal files). | No equivalent normal-turn summary found in the focused chat path; approval diffs and Files panel do exist (S/K4). | Reviewing a completed edit requires a different path; confirm on a synthetic editing run before implementation. | P1 | L | —; #17 closed covers the accordion only |
| G09 | Provider-row presentation | One switch for the single-provider installed plugin (C4). | Independent provider switches plus scope/source notes on one row (KF). | Denser rows; preserve independent state while improving grouping. | P2 | S | #21 open, implementation present |
| G13 | Pin conversations | Pin/Unpin on chat rows and thread menu (C4). | Conversation menu lacks Pin; project favorites already exist (K4/S). | Frequent threads cannot be pinned by the same action. | P2 | M | — |
| G14 | Sidebar organization | Project/chat section menus, sorting and custom sections (C3; section controls C4). | Attention-first chat ordering and project favorites; no equivalent custom section/sort controls found (S). | Different organization workflow for a large thread list. | P2 | M | — |
| G15 | Model/effort presentation | Model and effort belong to one popover/button (C3; control C4). | Separate provider-grouped Model and Effort controls (K4). | Extra navigation and different control placement, with routing preserved. | P2 | M | — |
| G16 | Composer additions | `+` groups attachments, project, Goal, Plan mode, Sketch and tools (C3; menu opened C4). | Attach file plus separate Files/Agents/Plugins and `/` resource controls (K4/S). | Different discovery and insertion paths; research unsupported Goal/Sketch behavior before adding controls. | P2 | M | #26 covers the Plugins entry only |
| G18a | Thread branching | Fork and New side chat in thread actions (C4). | Rename, continuation, Archive and Delete in conversation menu (K4/S). | No equivalent branch/side-chat workflow from that menu. | P2 | L | — |
| G18b | Thread window/workspace actions | Move to right pane, Open in new window and Open in in the sampled menu (C4). | Chat/Code views and right panel, without those per-thread menu actions (K4/S). | Different multitasking workflow. | P2 | L | — |
| G18c | Thread sharing | Share and Copy submenu in thread actions (C4). | Continuation can copy/save a redacted handoff; no equivalent Share action there (K4/S). | Sharing/hand-off semantics differ; research privacy and destination requirements. | P2 | M | — |
| G19 | Inline tool details | Collapsed per-step results with raw-output affordance (C3). | Chat milestones and Worked for summary; detailed inspection in the run console (K4/S). | Extra navigation for a single tool result; execution visibility already exists. | P2 | M | #9 closed covers markers, not an inline inspector |
| G21 | Workspace | File/Changes tabs, split workspace and terminal controls (C3). | Files/Activities accordion and Chat/Code views (K4/S). | Different file-review and multitasking layout. Research the smallest useful shared workflow. | P2 | XL | #17 closed covers current accordion |
| G22 | Space | Library with Pages/Sites/Images/Drive/Trash, filters and grid/list controls (C4/C3). | Project page list/editor, preview, attach and start-chat actions (K4/S). | No federated library surface. Research federation; preserve page CRUD. | P2 | XL | — |
| G23 | Scheduled grouping | Separate Upcoming and Paused groups (C4/C3). | Tasks show Active/Paused state in the same list (K4/S). | More scanning when many tasks are paused. | P2 | S | — |
| G24 | Schedule discovery | Template-card gallery (C4/C3). | Direct New task editor with cadence, provider/model/effort and context (K4). | Fewer examples for starting a task. Research useful templates; keep the richer editor. | P2 | M | —; #45 covers execution homes only |
| G25 | Explore | Projects, Sites, Maps, GPTs, Code Review popover (C4). | No equivalent Explore launcher; current features have their own entries (K4/S). | No shared destination hub. Research supported destinations rather than adding dead links. | P2 | L | — |
| G26a | Git settings | Commit/PR instructions, branch prefix, merge/review defaults (C4/C3). | No dedicated Git preference page (K4/S). | Git workflow preferences remain outside the app's Settings. Research provider support. | P2 | L | — |
| G26b | Worktrees | Managed-worktree controls and project environments (C4/C3). | No equivalent Worktrees settings page (K4/S). | Workspace lifecycle is not managed through the same UI. Research execution and cleanup contracts. | P2 | XL | — |
| G26c | Code Review | Dedicated rail plus repository/review preferences; content screen offers Retry in this account (C4). | No dedicated PR review workspace/settings (K4/S). | PR workflow has no matching destination. Research integration; a populated Codex review was not observed. | P2 | XL | — |
| G27 | Personalization | Codex memories and custom-instruction preferences (C4/C3). | Agents/resources and provider configuration, no equivalent memory page (K4/S). | Different way to configure durable behavioral context. Research provider-specific semantics. | P2 | L | — |
| G28a | Notification preferences | Completion/sound/channel preferences and notification pages (C4/C3). | Attention and existing notifications, without matching Settings pages (K4/S). | Preference discovery and available controls differ; notifications are not absent. | P2 | M | — |
| G28b | Composer preferences | Send shortcut, plain text, context usage and Queue/Steer preferences (C4). | Existing composer/queue behavior without equivalent preference rows (K4/S). | Users cannot reproduce the same configurable sending workflow. | P2 | M | — |
| G28c | General desktop preferences | Default file destination, sleep, window-close and terminal placement options (C4). | No corresponding General page; some desktop behavior exists independently (K4/S). | Desktop defaults cannot be configured in the same place. Research each native capability. | P2 | L | #18 open is Always on top only |
| G29 | Voice | Composer Dictate/voice controls and Voice settings (C4/C3). | Audio attachments/transcription support, without equivalent composer dictation controls (K4/S). | Different speech-input workflow; audio is not wholly unsupported. | P2 | L | — |
| G30a | Import | Detected setup imports and synchronization controls (C4/C3). | Provider setup/catalog discovery; no matching Import page (K4/S). | Migrating other-app setup is a different workflow. Research ownership and safe import. | P2 | L | #36 open is related login/migration work, not full UI coverage |
| G30b | Appearance customization | Theme/font/code-theme and advanced customization (C4/C3). | Eight curated palettes, text sizes, panel order and visual markers (K4). | Fewer customization dimensions; palettes must remain distinct from Codex colors. | P2 | M | #30 open is related theme work |
| G30c | Usage and billing | Plan, credits, resets and analytics pages (C4/C3). | Multi-provider quota/usage affordances (K4/S). | Provider billing actions have no identical unified view. Research provider links/data; do not replace meters. | P2 | L | — |
| G30d | Profile / Account | External web links (C4 navigation; destination content uninspected). | Local single-owner app; no matching ChatGPT account pages (S). | Different account model. Research provider-specific external destinations only if useful. | P2 | S | —; #41/#42 concern the local owner model |
| G30e | Parental controls / Trusted contact | Dedicated configuration surfaces (C4/C3). | No equivalent local Settings surfaces (K4/S). | Capability/model difference; research relevance and authority before any implementation. | P2 | XL | — |
| G30f | Mini / Pets | Mini-window and pet customization surface (C4/C3). | No equivalent Settings page (K4/S). | Optional desktop interaction difference. Research; not a daily-use blocker established here. | P2 | L | — |
| G30g | Passwords | Saved-password management page (C4/C3). | No browser password-manager page (K4/S). | Different credential-management surface. Research; never introduce a second credential store for visual parity alone. | P2 | XL | — |
| G30h | Computer use / Browser | Browser integration and browsing-permission settings (C4/C3). | Connection / MCP configures a different integration boundary (K4/S). | Browser control cannot be configured through a corresponding native page. Research backend capability. | P2 | XL | — |
| G30i | Cloud computer / Codex Cloud | Cloud environment, approval and migration surfaces (C4/C3). | Native/isolated local execution and multi-provider setup (K4/S). | No equivalent cloud-workspace administration. Research; no claim of provider-cloud support. | P2 | XL | — |
| G30j | Hooks | Dedicated hook list (C4/C3). | Provider hooks/trust handled through provider behavior, no matching Settings page (S). | Hook discovery differs. Research a truthful read-only view before management. | P2 | M | —; #44 is related trust work |
| G30k | Connections | Control-this-PC/SSH settings (C4/C3). | Connection / MCP and Connect a client serve different purposes (K4/S). | Remote-control setup differs from client attachment. Research, preserving existing MCP access. | P2 | XL | — |
| G31 | Preference wording | Individual setting descriptions explain their purpose (C4). | Appearance says theme/model/effort are saved "in this browser", although backend preference persistence shipped (K4/release WP6). | Misleading expectation across launches/clients; wording correction only. | P2 | XS | #13 closed covers persistence, not this wording |

JEV priority triage returned partial classifications. Accepted P1 suggestions were
G01 and G08; most accepted remaining suggestions were P2. Abstentions were
resolved locally without retry. G05 was raised from JEV's P2 to P1 because the
observed placeholder interrupts the daily Plugins management workflow despite
configuration elsewhere. Priority is an impact judgment, not a correctness
certificate. Estimates and research rows have not been validated by implementation.

Implementation order follows dependencies: G01 makes the shipped management
reachable; G02/G03/G07 complete discovery and installation; G04–G06 connect the
remaining management chips; G08 makes context explicit. Command/settings search,
language and shortcut reference can proceed independently. Research rows should
first establish a supported behavior contract, not imitate inert controls.

## KeepHarness additions to preserve

Multi-provider routing and model/effort changes; cross-provider tool availability
warnings; provider-state provenance and external-change notices; eight palettes;
dedicated Agents; workflows and the step engine; the Runs rail and console;
Attention filters; project Space pages; catalogs and vault; Connection / MCP;
backup and restore; provider quota meters; schedules; and continuation into another
desktop app remain protected. A richer Codex counterpart does not justify removing
the KeepHarness capability. Historical guest-scope wording in D-021 does not
reverse the later single-owner and guest-removal decisions (#41/#42).

## Validation and remaining limits

This is a documentation audit, not a release gate or proof of live provider
integration. Read-only Codex limits and fixture-only KeepHarness observations must
remain explicit in any implementation work derived from the list.

| Check | Result |
| --- | --- |
| Visible existing run-console operator area | 12 passed, 0 failed, 0 skipped; 0 new layout findings (`shots/op/summary.json`) |
| `node <shots>/plugins.cjs` | 6 assertions passed; absent/read-only/two-provider rows and one simulated scoped write |
| `node <shots>/finish-walk.cjs` | 2 assertions passed; not-enrolled guard and no pending request after cancelling the fake run |
| Exploratory `walk.cjs` / `walk-rest.cjs` | Both exited 1: first encountered hidden draft-only chips in a completed thread; second waited for Deny completion on an unenrolled fixture session. Continued only unfinished observations; neither is reported as a passing suite or a reproduced product defect. |
| `.venv/bin/python scripts/check_conventions.py` | Passed: 0 name errors, 0 name warnings, 0 Portuguese hits, 0 guest hits |
| Gap-table structure and `git diff --check` | 48 rows; every row has a priority, size and issue slot; no whitespace errors |
| Codex mutation paths | Not run: send, install/uninstall, toggles, setting writes, live approval/Retry and scope precedence |
| Full Python/browser suites | Not run; this task changes documentation only and requests the conventions gate |
| Independent document review | No blocking findings after adding Settings recovery provenance and the conventions result; source/issue/evidence review only |
| Process cleanup | Isolated fixture and desktop units inactive with MainPID 0; no listeners on 18740/18741/18742 or 9339; task-created Chrome tab closed; normal ChatGPT running without debug arguments |

Remaining evidence gaps: real installation/authentication and ID reconciliation,
disabled Codex plugin appearance, effective scope precedence, per-thread tool
availability after disablement, pending Codex approvals, failed-turn Retry,
populated PR review, external Profile/Account destinations and restart persistence.
The broad Settings/cloud/library research rows do not establish that all Codex
services can or should be reproduced by a local multi-provider harness.
