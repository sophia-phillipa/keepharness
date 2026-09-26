# Browser entry, identity and panel regression

Status: implemented and verified on 2026-09-21.

After a computer restart, the browser opened the local origin instead of the
previously used Tailscale origin. Both used the same database, but different
identities selected different histories and origin-scoped browser storage
selected different panel layouts.

The initial fix redirected the root page with HTTP 307. Startup still required
HTTP 200 from that route and terminated the child process after ten seconds.
Readiness now checks local `/ui.css`, independently of VPN availability and browser
navigation. A successful request before that deadline is insufficient: the
regression must await completion of `Manager.start()`.

## Permanent acceptance coverage

| Case | Automated regression |
| --- | --- |
| Old local bookmark redirects to the configured origin | `test_local_browser_redirects_but_api_and_remote_origin_do_not`: IPv4, localhost and IPv6 |
| Canonical origin has no loop; assets and API do not redirect | Same HTTP test and `harness-browser-entry.spec.cjs` |
| Private query parameters are not forwarded; redirect is not cached | Location and Cache-Control assertions in the HTTP test |
| Missing sharing, authorized identity or hostname preserves local entry | `test_unshared_or_untrusted_destination_keeps_local_entry` and `test_sharing_receipt_alone_does_not_redirect` |
| Unconfigured external destination is rejected | `test_unshared_or_untrusted_destination_keeps_local_entry` |
| Reconstructed Manager preserves destination; removing sharing updates runtime | `test_browser_destination_survives_restart_and_tracks_sharing` |
| HTTP 307 does not cause a false readiness failure | `test_start_readiness_does_not_probe_redirecting_browser_entry` and `test_real_process_starts_with_redirect_and_stops_cleanly` |
| Actual readiness failure still stops the child | HTTP 503 variant of the readiness test |
| Local and Tailscale histories remain separate in one database | `test_history_and_attachments_keep_owner_and_survive_restart` |
| Deleting a local conversation preserves remote conversation and attachment across restart | Same test; also rejects cross-owner deletion and attachment reads |
| Separate origin preferences survive closing and reopening Chromium | `harness-browser-entry.spec.cjs`, using a real Python backend and temporary browser state |

Tests use temporary databases, processes and browsers. Physical removal of local
incident data was a one-time authorized operation with a private backup and
comparison of preserved records; the suite does not repeat that cleanup. Regression
tests exercise ownership, deletion and persistence contracts with synthetic data.

## Execution and integration

Pytest automatically discovers `tests/test_browser_entry.py`. The CI `unit` job
runs pytest. Its `ui` job runs `scripts/test-ui.sh`, which enumerates every
`tests/*.spec.cjs` file, including the new browser regression.

Targeted validation performed:

```sh
.venv/bin/python -m pytest -q tests/test_browser_entry.py tests/test_control_reload.py tests/test_runtime_reload.py tests/test_distribution.py
PYTHON="$PWD/.venv/bin/python" node tests/harness-browser-entry.spec.cjs
```

Results: 31 Python tests passed; Chromium regression passed. Locally,
`PLAYWRIGHT_MODULE` selected the already-installed Playwright package. These tests
perform no model inference or paid API calls. The full suite was not run, following
the feature-scoped development policy.

Operational verification: local API and Tailscale page still returned 200 after
the original shutdown deadline. Chrome displayed a ready interface and preserved
panel order.
