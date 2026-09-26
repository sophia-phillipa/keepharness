# Response formatting

Implemented in Tail Harness on 2026-09-19. Responses previously shown as literal text now render Markdown during streaming, in the final result and in history. Headings, tables, bold, italics, lists, quotes, links and code get their own presentation. Plain JSON objects/arrays and fenced json blocks in Markdown are indented; invalid content stays visible. Copying a response keeps the original source.

The Tail Harness bubbles and side details, error paths, approvals and Maestro events are preserved. User messages and reasoning remain literal. The markdown-it 14.1.0 parser is served locally, with HTML disabled, dangerous links rejected and images suppressed. The bundle, provenance and MIT license live in `agent_service/vendor/` and are included in the package. Reference: https://github.com/markdown-it/markdown-it/blob/14.1.0/README.md

## Validation performed

- tests/response-format.spec.cjs: passed against the active service's files at 127.0.0.1:8095, with a mocked responses API. Exercises fragmented streaming, history/result, tables/lists/quotes, plain/fenced/mixed/object/invalid JSON, raw copy, literal user text, HTML/dangerous links/images, and 390/1280 widths across the real Violet & Burgundy and Amethyst themes.
- `.venv/bin/python -m unittest discover -s tests -p test_response_assets.py -v`: 1 test passed. Verifies the real Starlette route, script order, JavaScript type and content policy.
- `node --check agent_service/ui.js`: passed. Also verified that the bundle and license are listed in the package-data patterns.
- The queue was checked as empty before the restart. Only the panel process was stopped/started via the local admin's /api/stop and /api/start; both HTTP 200. The service's page, library and ui.js returned HTTP 200; the served ui.js contains renderAnswer.
- No real inference, benchmark, full suite, commit, push, merge, tag or branch creation in this feature.

The browser tests can be run in isolation with PLAYWRIGHT_MODULE pointing to the local Playwright install and HARNESS_URL=http://127.0.0.1:8095. Without HARNESS_URL, the test serves the checkout files via local interception. No local-llm-service source is required to run the tests or the renderer.
