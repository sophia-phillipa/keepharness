# Compact menu and composer

Requested adjustment: gear button without a visible label, connection state next to it and the version at the top of the menu with a `Release:` prefix and separator. Menus must share proportions and appearance; only Full access mode uses the theme accent color. Promotional text and initial suggestions leave the conversation area; the project-less section is called Conversations. The composer becomes narrower, centered and uses smaller typography.

The optional Task field leaves the interface. Each conversation gets a menu with persistent rename and delete. Renaming changes the display title, preserving the first message. The footer stops repeating routine states; the animation follows the end of the response during generation and disappears when it finishes. Errors and actionable warnings remain visible.

## Reference consulted

[Material Design — Menus](https://m1.material.io/components/menus.html), consulted on 2026-09-19: recommends 15px for common desktop menus and allows 13px in dense interfaces; menus must respect window edges and scroll when needed. This is a desktop-interface reference, not a harness-specific standard. The 13–14px scale guides the controls; box dimensions are layout decisions of this application.

## Validation

- Backend: `.venv/bin/python -m pytest -q tests/test_conversations.py` — 6 passed, including title validation, authorization and prompt preservation.
- Local server restarted after confirming idleness: stop/start HTTP 200; conversation listing and version HTTP 200.
- `tests/harness-layout.spec.cjs` passed after fixes: six themes, desktop/mobile, 200% zoom, long names, menus and rename with mocked API.
- Main menu captures reviewed visually; icons without a frame and aligned items. The three HTML/JS/CSS files served by the server responded HTTP 200 and matched the checkout files.

- `tests/harness-motion.spec.cjs` passed: animation on the last paragraph during response/thinking, removal on completion, absence of a routine state in the footer, preserved error and 12px margin on the mobile menu.

Does not involve a new version or Git milestone.
