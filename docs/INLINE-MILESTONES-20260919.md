# Collapsed steps in the conversation

Each response has a collapsed group of steps. Expanding it shows only thinking, tool and milestone titles, with completion or failure indication. History fetches titles on demand. Repeated events are deduplicated and tool completion updates its row by identifier. The answer and its completion stay in the conversation body.

The right panel concentrates general state and milestones. Raw inputs, outputs, metrics and raw thinking text do not appear in these groups. The original records remain preserved. Additive metadata in the adapters allows showing only the executable name; when unavailable, the title stays generic, without trying to reconstruct commands or arguments.

## Validation

- `tests/harness-side-details.spec.cjs`: history, live execution, expansion, deduplication, absence of raw content, side panel and mobile.
- `tests/response-format.spec.cjs`: Markdown, streaming, JSON, copy and rendering safety.
- `tests/harness-layout.spec.cjs`: six themes, desktop, laptop, mobile, scale, keyboard and selectors.
- `tests/harness-ux.spec.cjs`: 12 scenarios; `tests/harness-model-permissions.spec.cjs`: passed.
- Targeted metadata and native-integration tests: 11 passed.

Browser tests use real assets and mocked APIs. No inference, commit or full-suite run. Prior checkout changes preserved.
- `tests/harness-connection.spec.cjs`: passed; unavailability, automatic recovery, keyboard lock, authentication and draft preservation.
- JavaScript syntax and `git diff --check` passed; Graphify AST map updated.
- The application was restarted by the administrator after confirming idle state. Stop/start and version returned HTTP 200; served HTML, JS and CSS matched the checkout byte for byte.
