# W15 search and shortcut contracts

Scope: #54 and #56 ship together, followed by #55. Base: `c047a96`.

## Settings preference search (#55)

Search reads the labels and descriptions of existing preference controls, rather
than maintaining a separate translation catalog. The initial native preferences
are interface theme, panel position, message text size and visual markers.
Embedded administration contributes Full access and the default MCP model and
effort. Sections containing catalogs or history do not invent preference controls.
Unavailable or disabled controls cannot be activated from a result.

The embedded admin is indexed only when the existing local-host guard allows it.
Its messages are accepted only from the expected iframe window and exact origin;
the admin applies the equivalent parent/referrer check. Only explicitly supported
control identifiers can be focused. Search never sends settings values, secrets,
form contents or mutation requests across that boundary.

Querying, clearing and cancelling do not save preferences. Selecting a result
uses the existing Settings navigation, then focuses the control. An existing
admin document is retained while changing its section so unsaved form values
survive. An unavailable index is explained and can be retried. Preference changes
continue through their existing save paths.

English and pt-BR regression fixtures change the displayed labels, matching the
future #16 translation boundary without implementing a second language store.

## Decisions and evidence

- JEV selected coupled #54/#56 implementation with independent review (0.88;
  614 input tokens, 40 output tokens, 380 ms).
- JEV selected DOM-derived preference labels over a static duplicate index (0.85;
  770 input tokens, 40 output tokens, 259 ms).
- The worktree has no `graphify-out/graph.json`; impact mapping uses focused source
  reads. Owner CLI state and the prohibited global skill paths are excluded.
- The visible desktop pass is explicitly delegated to the orchestrator. Headless
  browser assertions do not constitute that pass.

## Acceptance mapping

Each numbered item below follows the acceptance bullet order in its issue.
Named scenarios print their IDs on success; the files run through
`scripts/test-ui.sh`.

| Issue | Acceptance bullet | Named browser scenarios |
|---|---|---|
| #54 | 1: shared entry points and preserved search | command-search A1, A4; conversation-search |
| #54 | 2: distinguishable, executable results | command-search A2b, A2, A3, A4, A6, C2a–C2c |
| #54 | 3: keyboard, empty state, preserved context, history | command-search A1, A2, A3, A6, C1a, C1b, C2a–C2c |
| #54 | 4: owner/admin guard and remote fallback | command-search A5, A6 |
| #54 | 5: light/dark tokens and contrast | command-search A7; visible pass pending |
| #56 | 1: supported reference, filtering and empty state | keyboard-shortcuts B1, B2 |
| #56 | 2: Ctrl+/, Focus composer and every binding | keyboard-shortcuts B4; command-search A2 |
| #56 | 3: keyboard use, context and focus return | keyboard-shortcuts B3, B3b |
| #56 | 4: modifiers, command entry and no duplicate action | keyboard-shortcuts B1, B3b, B4, B6 |
| #56 | 5: light/dark tokens and contrast | keyboard-shortcuts B5; visible pass pending |
| #55 | 1: individual names/descriptions and reached control | settings-search P1-S1, P4-S1, P6-S1 |
| #55 | 2: no query writes, preserved edits and navigation | settings-search P2-S1, P3-S1, P5-S2, P5-S4, P5-S5, P6-S1 |
| #55 | 3: capabilities and local/remote availability | settings-search P5-S1–P5-S6, P6-S1, P7-S1 |
| #55 | 4: multiple/empty results, keyboard, recovery, persistence | settings-search P1-S1, P3-S1, P4-S1, P5-S1–P5-S6 |
| #55 | 5: displayed labels, English/pt-BR and contrast | settings-search P4-S1, P7-S1; visible pass pending |

## Seven-profile matrix

These are simulations using fixtures, not research with real users.

| Profile | Main path and recovery | Evidence |
|---|---|---|
| P1 beginner | Find by description and destination; clear a no-match query | settings-search P1-S1 |
| P2 rushed user | Type, clear and cancel without writing preferences | settings-search P2-S1 |
| P3 domain professional | Reach a control, save through the backend, navigate away/back | settings-search P3-S1 |
| P4 keyboard/accessibility | Arrow/Home/End/Enter navigation and returned focus; readable palettes | settings-search P4-S1 |
| P5 mobile/unstable connection | Failed admin on 390px viewport; explicit Retry recovers | settings-search P5-S1 |
| P6 harness engineer | Reject forged messages; retain unsaved MCP values across sections | settings-search P6-S1 |
| P7 UI/UX specialist | Displayed translated labels; remote admin controls omitted | settings-search P7-S1 |

## Regression loop and review

The initial Settings spec failed because `#settings-search` was absent. The new
asset test failed with 404 before the public asset allowlist was extended. Nine
focused Python tests then passed. The Settings browser scenarios P1–P4 passed
first; P5 exposed an identical-fragment iframe navigation that did not recover
an error document. Explicit Retry now changes a query parameter only for a
never-responsive document. A previously responsive document is re-queried,
retaining its edits. All seven scenarios passed after this correction.

JEV abstained on the retry alternatives (0.51, below the medium-risk 0.80 threshold;
642 input tokens, 40 output tokens, 254 ms). The local fallback was the minimal URL
change described above, verified by the failing-then-passing recovery scenario.
No token or cost savings are claimed; aggregate task metrics are unavailable.

Independent review found and corrected unavailable New conversation commands,
stale search copy, extra Shift+K handling, insufficient state-preservation tests,
and Theme search focusing the switch rather than the selected palette.
The #54 and #55 artifacts were approved after those fixes. The #56 review also
corrected dead button semantics in reference rows, plus-sign key searches,
command-entry focus coverage, nonempty attachment fixtures, macOS labels and
the ARIA list owner. Its focused re-review approved the corrections with the list owner fix. The final gates are recorded below.

## Visible checks for the orchestrator

Use Paper/Amethyst for command search and shortcuts, and Paper/Graphite for
Settings search, on the real desktop before merge. Keep a draft with an attachment
and a chosen project/provider throughout.

1. Open the same search through the sidebar, Ctrl/Cmd+K and Ctrl/Cmd+Shift+P;
   filter a command, Settings destination, conversation and loaded file. Use
   arrows/Enter, no matches and Escape; check focus return and retained draft.
2. Activate Focus composer and a Settings destination; use Back/Forward. During
   an operation, confirm unavailable commands do not offer a dead action. At
   400px with the sidebar, 800px with the activity panel, and 650px with the run
   console open, confirm Focus composer is absent; after closing the cover,
   confirm the command focuses the composer and retains the draft.
3. Open the shortcut reference using Ctrl/Cmd+/ and its command result. Search by action and `Ctrl+K` (or `Cmd+K`), navigate rows,
   close with Escape, and inspect readable text, spacing and modifier labels.
4. In Settings, search by preference name and description. Open text size or a
   palette, confirm focus, change it, and reopen Settings to verify persistence.
   Query, clear and cancel without changing a preference.
5. In local Settings, search an MCP default, leave an unsaved selection, navigate
   through search to Full access and back, and confirm the selection remains.
   Check unavailable-admin explanation and Retry recovery. With a pending MCP
   edit, save Full access twice and then three times, making each state refresh
   fail with 503. Restore the endpoint and retry search; confirm a new state
   request, restored results, preserved edits and exact preference focus. Also
   fail a Retry request, then retry successfully; while a refresh is pending,
   confirm Retry neither starts another request nor publishes results early.
   On the existing remote fallback, confirm local-admin preferences are absent
   and Plugins works.



## Executed gates

All tests use `/home/sophia/.cache/keepharness-gauntlet/venv-lock/bin/python`
and one task-owned `TMPDIR`. Browser servers use fake HOME, CODEX_HOME and
CLAUDE_CONFIG_DIR values under that root, isolated random ports and no display.
No provider inference or owner's CLI state is used. Scratch files are removed
at task completion.

- Focused Python: `pytest -q -p no:cacheprovider tests/test_execution_defaults.py
  tests/test_ui_prefs_contract.py` — 9 passed.
- Settings browser tests: `settings-search.spec.cjs` — all seven profile scenarios
  passed. Existing `settings-submenu.spec.cjs`, `settings-system-admin.spec.cjs`,
  `harness-admin-shortcut.spec.cjs` and `harness-rail-settings.spec.cjs` passed.
- Full Python (one run): `pytest -q -p no:cacheprovider` — **4,247 passed,
  27 skipped, 14 subtests passed**, 235.29 seconds. Only the two predeclared
  missing-pip environment failures occurred:
  `test_catalog_manifest.py::test_requirements_can_be_added_to_existing_venv` and
  `test_second_identity_build.py::test_second_identity_build_and_install_side_by_side`.
  Both report `No module named pip`; these are not W15 regressions.
- `check_conventions.py` — 0 name errors, 0 Portuguese hits, 0 guest hits.
- Product diff color scan — no added literal CSS colors. Browser contrast
  assertions require at least 4.5:1 in Paper/Amethyst for command search and
  shortcuts, and Paper/Graphite for Settings search.

- Command/shortcut focused browser gate: `command-search.spec.cjs`,
  `keyboard-shortcuts.spec.cjs`, `conversation-search.spec.cjs`,
  `harness-a11y-media.spec.cjs`, `harness-gauntlet-round3.spec.cjs` and
  `persona-ux-eval.spec.cjs` passed (persona matrix: 60/60).
  Keyboard scenarios B1–B6 include every advertised Ctrl and simulated macOS
  Command binding. Final integration reran command-search (including A2b's
  single-query type distinction) and settings-search: both passed.
- Headless operator `--areas 15 --mode fixture`: 9/9 checks passed, zero new
  layout findings. Existing fixture setup was corrected for the Settings submenu,
  initially collapsed/bounded sidebar and competing open surfaces. The runner
  emitted its successful summary but hung during shutdown; it was interrupted,
  and the executor verified its fixture processes/listeners stopped. This is a
  runner shutdown limitation, not a failed keyboard check or a visible pass.
- The executor reported no callable JEV gateway in its subtask and used local
  deterministic decisions. The three JEV records above belong to the principal.


## Independent-review correction round (base `59d62f3`)

The independent review identified one major and three minor findings. All four
were reproduced before correction; the earlier green gates did not exercise
these cases. Their regression coverage is now explicit:

| Finding | Regression | Baseline failure | Correction |
|---|---|---|---|
| Async search removes keyboard focus | command-search C1a, C1b | Delayed updates lose the selected result and Enter does nothing; a removed result leaves focus on body | Preserve focus using semantic result IDs; return to the search input when that result disappears |
| Mobile Focus composer targets inert content | command-search C2 | At 400px, the sidebar remains open and the prompt cannot receive focus | Close the existing sidebar overlay before focusing the composer |
| Admin preference index precedes rendering | settings-search P5-S1, P6-S1 | A delayed account check publishes enabled Default effort before the actual control becomes disabled | Publish only after admin initialization/rendering and republish changes in control availability |
| Operator arrow coverage was lost | harness-a11y-media P5-W15; operator keyboard.sidebar-resize-keys | Omitting ArrowRight still lets the old Home/End-only step pass | Start away from width limits, press real ArrowRight/ArrowLeft, and assert widening/narrowing; omitting either press now fails |

Settings coverage also checks live disable/re-enable updates and the actual
focused element in the admin document. The operator fixture explicitly marks
the tour as seen in durable test preferences and dismisses it before keyboard
checks: the first corrected operator run revealed the tour capturing ArrowRight.
The direction assertions remain intact. The visible-check instructions above
were corrected to remove an unimplemented Settings entry for shortcut reference.

Two separate authors cross-reviewed the command and Settings corrections; the
operator test received independent review. No remaining finding was reported.
The JEV call was blocked because the current approval policy is `never`; local
source-based decisions were used without retry. No paid-call usage or savings
are claimed for this round.

**Correction validation.** All 11 requested browser specs passed in one integrated
run (including the new delayed/mutation cases and the existing seven-profile
Settings matrix). The headless operator keyboard area passed 9/9 with zero new layout findings
and exited 0. `check_conventions.py` returned 0 name errors, 0 Portuguese hits
and 0 guest hits; `git diff --check` passed.
The full Python suite was not repeated for this JavaScript/test-only correction;
the earlier run and its two known missing-pip failures remain separately recorded.
The visible desktop pass remains with the orchestrator.

Commit creation was blocked because the shared Git index is read-only in this
round. The requested local commit-message fallback is `.codex-commits/1.txt`;
its directory is ignored so local commit metadata is not added to the product.
The orchestrator subsequently committed this correction as `5bb2880`.


## Independent-review correction round 2 (base `5bb2880`)

The remaining composer finding was reproduced at 800px with the activity panel
and 650px with the run console. Following the explicit product decision,
Focus composer is now omitted whenever its control is disabled, invisible or
inside an inert region. One predicate guards both listing and activation. This
also replaces round 1's sidebar-closing behavior: search leaves covering panels
open, and the command returns once the composer is available.

A failed state refresh after Full access has been saved leaves an initialized
admin document eligible for explicit index recovery. Retry reads the existing
rendered controls again, retaining pending MCP values and the iframe document;
it neither reloads the form nor writes preferences. A refresh still in flight
remains unready until rendering completes. Initial rendering and message
source/origin/control allowlists keep their existing gates.

| Finding or affected contract | Named regression | Evidence |
|---|---|---|
| Composer covered by sidebar, activity panel or console | command-search C2a, C2b, C2c | All three omission assertions fail on `5bb2880`; the correction passes and each command returns after its cover closes |
| Failed initialized refresh blocks Retry | settings-search P5-S2 | Baseline times out after Retry; correction restores Default model, focuses that exact control, keeps pending model/effort and makes no extra write |
| Recovery must not bypass a refresh in flight | settings-search P5-S3 | Await the child Retry handler before asserting readiness stays false; removing the eligibility guard fails this assertion, while releasing the response produces a focusable result |
| Keyboard focus while admin initializes | settings-search P4-S1 | A held response makes ArrowDown occur during loading; semantic selection survives the authenticated result rebuild |

The reported P4 intermittent assertion also recurred in the focused run. It
compared a locator's resolved DOM node with the active element while asynchronous
rendering could replace that node. The test now makes those comparisons in one
atomic DOM query and verifies semantic focus through a deliberately held admin
response. It does not wait for loading to finish before keyboard navigation,
and adds no sleep or product workaround.

The command and Settings authors independently cross-reviewed the final changes.
Review caught an early recovery implementation that could bypass an in-flight
refresh; the admin-owned failure marker and P5-S3 resolve that finding. The
focused P4 re-review approved its deterministic loading/rebuild coverage. JEV
remained unavailable under approval policy `never`; decisions used the explicit
composer preference and local source/test evidence. Aggregate usage metrics
remain unavailable.

The final recovery regression also waits for the child frame to process Retry
before inspecting readiness, rather than asserting before the posted message
arrives. Removing only the failed-refresh eligibility guard makes P5-S3 fail
(`true` readiness instead of `false`). The approved production asset was restored
byte-for-byte before the final consecutive Settings runs.

**Round 2 validation.** All 11 selected browser specs passed, including the
60/60 persona matrix and automated 4.5:1 palette contrast checks. Settings then
passed five consecutive executions with the strengthened child-message boundary
and deterministic keyboard/rebuild test. `check_conventions.py` returned
0 name errors, 0 name warnings, 0 Portuguese hits and 0 guest hits;
`git diff --check` passed. The full Python suite and the standalone operator area
were not repeated in this JavaScript correction round; their earlier results
remain recorded above. The selected accessibility spec still exercises the
operator arrow-step mutation checks. All runs used the isolated task temp root,
fake homes, owned random ports and no display. The visible desktop pass remains
with the orchestrator and the exact checks above now include the two re-review
cases.

Commit staging was blocked by the read-only shared Git index
(`index.lock: Read-only file system`). The requested Conventional Commit
message was stored in the ignored local fallback `.codex-commits/2.txt`.
The orchestrator committed the reviewed diff as `a30381d`. No Git permission
override was attempted.


## Independent-review correction round 3 (base `a30381d`)

The re-review identified that consecutive refresh failures erased recovery
eligibility: the second failure observed an already unready document instead of
remembering its last valid index. The explicit product decision replaces that
transient eligibility flag with `{lastGoodIndex, refreshing, lastError}`. A failed
refresh retains the last good index, records the error and remains recoverable.
Retry starts a fresh state read unless a refresh is already in flight; successful
recovery rebuilds descriptors from the existing controls so pending edits survive.

| Finding or contract | Named regression | Baseline evidence |
|---|---|---|
| Two failed post-save refreshes | settings-search P5-S2 | Two Full access saves each receive a state 503; Retry never restores Default model on `a30381d` |
| Three failed post-save refreshes | settings-search P5-S4 | Three saves and three state 503s likewise leave Retry unable to restore the result |
| Retry can itself fail repeatedly | settings-search P5-S5 | After one failed post-save refresh, the old Retry republishes without a new state request, failing the repeated-error recovery contract |
| In-flight refresh remains gated | settings-search P5-S3 | Existing held-response and child-message-boundary coverage is retained, with a request-count assertion |
| Older Retry must not supersede a newer load | settings-search P5-S6 | A held Retry response completes during a newer load; only the newer post-render completion may publish |

The new recovery cases check exact state-request and settings-write counts,
retained iframe identity, pending model/effort values and exact-control focus.
Both reported failure counts were executed and aggregated before the baseline
run failed; one failure does not hide the other.

Independent focused review requested an initially refreshing state and explicit
coverage of an older Retry response superseded by a newer load. Both were added;
the final review approved the changes with no remaining findings. Readiness is
derived from the single lifecycle object; the generation guard only rejects
obsolete async completions. JEV remained unavailable under approval policy
`never`; the user specified the lifecycle model directly. The existing regression
playbook and seven-profile Settings matrix were reused. Aggregate usage metrics
are unavailable.

**Round 3 validation.** `command-search.spec.cjs` passed, followed by three
consecutive passing executions of `settings-search.spec.cjs` against the final
reviewed code. Each Settings run includes all seven profiles, the two/three-save
failure regressions, further Retry failures, coalescing, stale completion,
authentication and automated contrast checks. `check_conventions.py` reported
0 name errors, 0 name warnings, 0 Portuguese hits and 0 guest hits;
`git diff --check` passed. Only the requested focused gates were rerun; earlier
11-spec, Python and operator results remain historical evidence above. Tests
used one task temp root, fake homes, owned random ports and no display or cloud
inference. The visible desktop pass remains with the orchestrator; its checklist
above now includes repeated saves, failed Retry and in-flight recovery.

Commit staging was blocked by the read-only shared Git index
(`index.lock: Read-only file system`). The reviewed diff remains on `a30381d`;
the requested local Conventional Commit fallback was `.codex-commits/3.txt`.
The orchestrator subsequently committed the correction as `d127165`. No
permission override was attempted.


## Visible-pass follow-up: D1 Home draft restoration (round 4)

**Status: open, pre-existing; not introduced by W15 and not fixed by the tested
w14a revision.** The visible pass completed all requested checks on fake homes.
All passed except V1.3/D1: Back from a conversation search result returned to an
empty Home composer and No project, losing the visible text/attachment/project
context. The provider remained selected. Existing green W15 specs do not prove
this path: A1/A2 check cancellation or composer focus, A3 checks Settings history,
and A4 opens a conversation without returning to a named-project Home draft.

Visible evidence supplied by the orchestrator: the report at
`/home/sophia/.cache/kho/chat/runs/w15-visible/log.md` and its
`shots/01-paper-draft.png` / `shots/05b-back-state.png`. Those two screenshots were
inspected; no further visible desktop operation was performed in this round.

A temporary browser spec, `search-home-draft.spec.cjs`, reproduced D1 using
real project/model controls, upload, typing, Ctrl+K, a conversation result and
Ctrl+[. Synthetic routes included both `project-a` and `sem-projeto`. The spec
compared all four fields independently and verified zero job POSTs. The identical
spec ran through each revision's `scripts/test-ui.sh`; main and w14a were exported
with `git archive` under the one task temp root. No worktree command, fetch,
merge or write to another checkout was used.

| Revision | Identity | D1 result |
|---|---|---|
| W15 | `d127165a99dd14970989f893c7f108c560ec711c` | Exit 1: text, attachment and project assertions fail; provider passes |
| Main | `bf7f281d3451bb6b726b018080971544a1649df9` | Identical field failures, exit 1 |
| w14a | `281502c5c7531049ae0fd36a41270634448625c1`, actual branch `feat/w14-retire-cloud-scoped` | Identical field failures, exit 1 |

The before/after values were identical across all three runs:

| Field | Before navigation | After Back |
|---|---|---|
| Text | `Preserve this search Home draft` | Empty string |
| Attachment | `pending-note.txt`, 26 bytes | No attachment |
| Project | `project-a` | `sem-projeto` |
| Provider/model | `cloud` (synthetic fixture) | `cloud`, preserved |

**Cause.** `currentBaseView()` / `recordView()` in `agent_service/ui.js` record
Home only as `{kind: "home"}`, without its project/draft identity. Replaying Home
through `applyView()` calls `startNewConversation()`, which chooses `sem-projeto`
when available. `newConversation()` therefore cannot select the original
`conversation-draft:new:project-a` snapshot on that route. These behaviors are
already present in main. W14 changes the execution-mode reset policy for #52,
but retains the missing Home project identity and the No project target. Its
existing Back test uses `sem-projeto`, so it does not cover this named-project
case. Independent source review and the three browser runs agree on the origin.

**Disposition.** Follow the user's record-only branch: no W15 product fix and no
change to w14a's draft-restoration area. The failing diagnostic is not shipped
as a regression spec; its temporary copies are removed with task scratch data.
D1 remains an explicit unmet visible acceptance case until the navigation/draft
owner restores the Home project and draft identity. After that correction, rerun
the same named-project/text/upload/search/Back flow and retain it as a passing
regression. The otherwise successful visible pass must not be reported as fully
approved.

**Round 4 validation.** The diagnostic reproduced D1 on all three revisions
(three expected failing runs, with identical per-field failures). The unchanged
W15 browser gate then passed all 11 selected specs, including the 60/60 persona
matrix. Python UI-state, UI-preference and execution-default/asset contracts
passed: `pytest -q -p no:cacheprovider tests/test_ui_state.py
tests/test_ui_prefs_contract.py tests/test_execution_defaults.py` — 78 passed,
one Starlette TestClient/httpx deprecation warning. Conventions reported
0 name errors, 0 name warnings, 0 Portuguese hits and 0 guest hits; diff checks
passed. These green existing gates do not resolve D1. Independent review
approved the reproduction, origin analysis and record-only disposition.

All comparisons and gates used fake homes, task-owned random ports, the specified
Python environment and no display/cloud inference. The supplied report and two
screenshots were the only reads under the otherwise excluded visible-run folder.
The w14a worktree was read-only; archived comparisons and the diagnostic are
removed with the single task temp root. No product code or persistent test file
changed. The explicit user condition selected this disposition; JEV remained
unavailable under approval policy `never`.

Commit staging was blocked by the read-only shared Git index. The two-document
diff remains on `d127165`; the requested local commit fallback is `.codex-commits/4.txt`.
No Git permission override was attempted.
