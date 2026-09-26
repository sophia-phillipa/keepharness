# Interface block while waiting for the server

The interface starts dimmed, with a centered message and `inert` content, blocking clicks and keyboard focus. Projects, model catalog, the selected project's permissions and history must respond correctly before release. An empty model catalog is valid: it allows access to the server configuration.

The connection is checked every five seconds, with a five-second limit per readiness request and no concurrent probes. A failed check blocks the panel again; recovery redoes the startup automatically, preserving the draft. Open configuration dialogs are closed on drop; the authentication dialog remains accessible. Drop detection depends on the next probe and its timeout, so it is not instantaneous.

Files: `agent_service/index.html`, `agent_service/ui.css`, `agent_service/ui.js`; regression in `tests/harness-connection.spec.cjs`, updated scenario in `tests/harness-ux.spec.cjs`, inclusion in the `scripts/test-ui.sh` runner. Other pre-existing changes were preserved. No version, branch or commit was created for this change.

## Validation performed

- `node --check agent_service/ui.js`: passed.
- `node tests/harness-connection.spec.cjs` with Playwright installed in the local runtime: passed. Real checkout assets, mocked APIs; covers initial unavailability, partial startup, automatic reconnection, drop with an open dialog, keyboard lock, draft, authentication and empty catalog.
- `HARNESS_URL=http://127.0.0.1:18197 node tests/harness-ux.spec.cjs` against an isolated service and mocked APIs: 12 scenarios passed.
- `HARNESS_URL=http://127.0.0.1:18197 node tests/harness-model-permissions.spec.cjs`: passed.
- Screenshots checked at 1280 and 390 pixels, light and dark themes.
- `git diff --check`: passed; Graphify AST map updated without clustering.
- An additional `harness-layout.spec.cjs` test stopped at line 15: it tries to click Settings without opening the menu that already exists in the checkout. It was not changed or declared passing.

The full suite was not run, nor was model inference, benchmarking, or a restart of the user's own service.
