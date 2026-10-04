# Layout lint gate

`tests/layout-lint.spec.cjs` fails when the composer or the status strip break their boxes. It runs against route fixtures only (no server, no provider) and is picked up by `scripts/test-ui.sh` like every other `tests/*.spec.cjs`. Standalone: `node tests/layout-lint.spec.cjs`.

## What it renders

- Screens: new chat, new chat with a Claude model ("Default" effort), an agent conversation, a conversation with an attachment, a long table and code answer, a status strip with a long work item, the side panel with the run console open, the search dialog with text typed, and the Setup dialog.
- Widths: 1440x900, 1024x768, 768x1024, 390x844 and 360x800, plus the small desktop windows 800x600 and 720x500 for the agent and status-strip screens. Chat panes narrower than about 340 px (a docked 350 px sidebar in a window under 720 px, phones under 360 px) are not covered; the sidebar auto-collapse is a separate change.
- Themes: Paper and Graphite.

## What fails

1. **Named invariants**, each tagged with the visual defect id of the 0.15.0 gauntlet:
   - V1/V2: composer controls never intersect by more than 2 px and stay inside the card; the agent card sits on its own row above the input; the input is at least 240 px wide from 1024 px up.
   - V3: the 28 px status strip holds its content on one line and its children stay inside it.
   - V4: the "Native conversation" row and the access notice stay inside their box.
   - V5: the placeholder, the effort label and the access label are not clipped.
   - V6: a resize handle (`role=separator`) shares at most 2 px with any control it can actually hit. The panel column handles are 6 px strips on the panel edge and the run console handle a 6 px strip in the flow above its header.
   - V12: a dialog's close button never sits on a line of the dialog's text.
   The V6/V12 screens (side panel and console, search dialog, Setup) run only those rules and the field lint (`lint` in the screen entry); the generic overlap lint of an open side panel at tablet widths is a separate layout concern.
2. **The generic lint** (`tests/support/layout-lint.js`): interactive elements that overlap by more than 2 px (confirmed with `elementFromPoint`, so controls behind a modal, an inert region or a pointer-events-none layer do not count), text that escapes its box, elements beyond their container, input or select text that is clipped or runs under an icon, and horizontal page scroll.

`KNOWN_NOISE` in the spec lists the three lint hits that are not defects (the visually hidden "Send" label, the 5 px taller "?" glyph of the Ask icon and the Ctrl K hint). A new entry needs a reason in the spec.

A failing screen is saved as a PNG in `<tmp>/keepharness-layout-lint/` and the path is printed with the measurements.

## Adding a screen

Add an entry to `SCREENS` (an `open(page)` that leaves the page in that state, using the route fixtures) and, if the screen has its own box rules, a named invariant in `namedInvariants`. Fix the layout rather than extending `KNOWN_NOISE`.
