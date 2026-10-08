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
| #54 | 2: distinguishable, executable results | command-search A2b, A2, A3, A4, A6 |
| #54 | 3: keyboard, empty state, preserved context, history | command-search A1, A2, A3, A6 |
| #54 | 4: owner/admin guard and remote fallback | command-search A5, A6 |
| #54 | 5: light/dark tokens and contrast | command-search A7; visible pass pending |
| #56 | 1: supported reference, filtering and empty state | keyboard-shortcuts B1, B2 |
| #56 | 2: Ctrl+/, Focus composer and every binding | keyboard-shortcuts B4; command-search A2 |
| #56 | 3: keyboard use, context and focus return | keyboard-shortcuts B3, B3b |
| #56 | 4: modifiers, command entry and no duplicate action | keyboard-shortcuts B1, B3b, B4, B6 |
| #56 | 5: light/dark tokens and contrast | keyboard-shortcuts B5; visible pass pending |
| #55 | 1: individual names/descriptions and reached control | settings-search P1-S1, P4-S1, P6-S1 |
| #55 | 2: no query writes, preserved edits and navigation | settings-search P2-S1, P3-S1, P6-S1 |
| #55 | 3: capabilities and local/remote availability | settings-search P5-S1, P6-S1, P7-S1 |
| #55 | 4: multiple/empty results, keyboard, recovery, persistence | settings-search P1-S1, P3-S1, P4-S1, P5-S1 |
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
   an operation, confirm unavailable commands do not offer a dead action.
3. Open the shortcut reference using Ctrl/Cmd+/, its visible Settings entry and
   its command result. Search by action and `Ctrl+K` (or `Cmd+K`), navigate rows,
   close with Escape, and inspect readable text, spacing and modifier labels.
4. In Settings, search by preference name and description. Open text size or a
   palette, confirm focus, change it, and reopen Settings to verify persistence.
   Query, clear and cancel without changing a preference.
5. In local Settings, search an MCP default, leave an unsaved selection, navigate
   through search to Full access and back, and confirm the selection remains.
   Check unavailable-admin explanation and Retry recovery; on the existing
   remote fallback, confirm local-admin preferences are absent and Plugins works.



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
