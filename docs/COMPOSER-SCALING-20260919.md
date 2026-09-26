# Model/effort menus and screen proportions

Model and effort now use the same native popover as access: border, descriptions, marked selection, arrow/Home/End navigation, Enter to confirm and Escape/Tab to close. Options are built from the catalog and the efforts actually available for the model, without inventing capabilities. The existing selects remain the source of value for submission, permissions and saved preference. The triggers reflect changes, session recovery and execution locks.

The conversation and composer use a central column up to 1040px CSS. The empty box grows to about 132-134px on desktop; fixed/rem typography avoids excessive growth on 4K TVs. Send buttons are 44px; reorganization by actual container width preserves usability with side panels open and on mobile. Menus have their width and height bounded by the window. No monitor settings, system scaling, grants or backend were changed.

## Verification

- `tests/harness-layout.spec.cjs`: passed across six themes and widths 3840, 1920, 1366, 768 and 390; width/height limits, no overlap, 200% zoom, activity panel, long names, model/effort menus, effort updates per model, keyboard, payload and preference after reload. Also created a 1920x1080 context with deviceScaleFactor=2, representing 4K at 200% scaling. Browser-level validation, not a claim of physically testing readability on a TV at a distance.
- `tests/harness-ux.spec.cjs`: 12 scenarios passed.
- `tests/harness-model-permissions.spec.cjs`: passed.
- The connection test was extended to open the model menu and check that it closes on loss of access.
- JavaScript syntax and `git diff --check`; the Graphify AST map was updated.

Real checkout assets with mocked APIs in the browser tests. No inference, new version, commit, branch or full suite. Pre-existing changes preserved.
