# Message box based on the visual reference

Capsule with rounded corners, a wide writing area and a compact bottom bar. Attach uses a frameless + symbol; access appears with a shield; model and effort sit next to each other, with no framed fields or redundant labels; send uses a circular button with neutral contrast. The existing palette is still respected. No microphone was added without matching functionality.

The selectors remain native and keyboard-accessible. Automatic access preserves the existing administrative limits. Model names appear without emojis, with GPT-6 Astra/GPT-5.6 among the known names. The optional Task field was moved out of the capsule. Help remains available for screen readers, including the permissions associated with the model. Narrow containers reorganize the bar into two lines, even with side panels open.

Changed: `agent_service/index.html`, `agent_service/ui.css`, `agent_service/ui.js` and `tests/harness-layout.spec.cjs`. Pre-existing changes preserved. No commit, branch creation, new version or restart of the service in use.

## Validation

- `node tests/harness-layout.spec.cjs` with the local runtime's Playwright: six themes, 1515/768/390px, access modes, long names, activity panel, 200% zoom, keyboard selection, settings, attach and send payload; captures of the light, amethyst and Arizona themes. Checkout assets with mocked APIs, no inference.
- `node tests/harness-ux.spec.cjs` on an isolated temporary server: 12 scenarios passed.
- `node tests/harness-model-permissions.spec.cjs`: passed.
- `node tests/harness-connection.spec.cjs`: passed, including block and reconnection.
- `node --check agent_service/ui.js` and `git diff --check`: passed.

The layout test previously incompatible with the menu was updated to open the real menu and check the final behavior. The full suite was not run because there was no Git milestone.
