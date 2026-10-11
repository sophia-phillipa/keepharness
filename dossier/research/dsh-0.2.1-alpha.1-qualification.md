# dsh ACP qualification (KeepHarness #51)

Pinned version: 0.2.1-alpha.1. A PASS row proves only the dsh binary that was run.

| ID | Item | Status | Reason |
| --- | --- | --- | --- |
| U01 | Minimum supported Node.js version | UNVERIFIED | No offline probe proves this item; it stays open under D-043. |
| U02 | Long-term protocol and configuration compatibility | UNVERIFIED | No offline probe proves this item; it stays open under D-043. |
| U03 | Unattended API-key write and credentials file permissions | UNVERIFIED | No offline probe proves this item; it stays open under D-043. |
| U04 | Account login, headless login and token renewal | UNVERIFIED | No offline probe proves this item; it stays open under D-043. |
| U05 | Released ACP behavior, usage accounting, permission and sandbox mapping | UNVERIFIED | usage not emitted by the probe target; permission and sandbox mapping stay open |
| U06 | Exact headless argument and event schema | UNVERIFIED | No offline probe proves this item; it stays open under D-043. |
| U07 | Plugin enable/disable writer and conflict handling | UNVERIFIED | No offline probe proves this item; it stays open under D-043. |
| U08 | Automatic MCP settings import and MCP resources through ACP | UNVERIFIED | No offline probe proves this item; it stays open under D-043. |
| U09 | Session storage root and file generation | UNVERIFIED | No offline probe proves this item; it stays open under D-043. |
| P01 | ACP v1 handshake; session/load not advertised | PASS |  |
| P02 | Turn: prompt streams chunks, then end_turn | UNVERIFIED | needs a real model turn (no API key in the disposable profile) |
| P03 | Create and list a session | FAIL | created session is missing from session/list |
| P04 | Owner allow relayed as allow_once | UNVERIFIED | needs a real model turn (no API key in the disposable profile) |
| P05 | Owner deny relayed as reject_once | UNVERIFIED | needs a real model turn (no API key in the disposable profile) |
| P06 | Cancel stops the stream as cancelled | UNVERIFIED | needs a real model turn (no API key in the disposable profile) |
| P07 | Resume a session from another process | UNVERIFIED | needs a real model turn (no API key in the disposable profile) |
| P08 | Resuming an unknown session reports session-not-found | FAIL | the error is not session-not-found |
| P09 | Pinned version 0.2.1-alpha.1 with P01-P08 passing | FAIL | a protocol probe failed |

## Run record (real pinned package, disposable profile)

Scope: `@deepseek-ai/dsh@0.2.1-alpha.1`, installed from the public registry into a disposable prefix with `--ignore-scripts`. Launched with a cleared environment (PATH, LANG, a disposable HOME, DSH_HOME and DSH_AGENTS_HOME). No API key, no account login, no `KEEPHARNESS_LIVE`, no model turn.

Install facts:

- Package metadata: name `@deepseek-ai/dsh`, version `0.2.1-alpha.1`, license MIT, `bin.dsh` = `lib/bin.js`, repository `git+https://github.com/deepseek-ai/deepseek-harness.git` (directory `apps/cli`).
- The package declares no `gitHead` and no `engines` field, so the source revision and the minimum Node.js version are not stated by the package (U01 and U02 stay open on that basis).
- Lock integrity (registry tarball): `sha512-DJ8pwG/XqzwDrYQTgJXuIga6ku153Z4vJI/B3MzNtudVCkDaOf4O2pUQh1ToroLHPPe4mxhhFeNJ7ttNculqMw==`. The lock has no install script for this package. Install scripts were not exercised because of `--ignore-scripts`.
- Node.js v24.18.0, npm 11.16.0. `dsh --version` prints `0.2.1-alpha.1`.

Raw replies from the real binary (hand-driven, same sequence the probes use):

1. `initialize` answers `protocolVersion` 1 and `agentCapabilities.sessionCapabilities` = `{close, list, resume}`. The `loadSession` key is absent. `authMethods` is empty.
2. `session/new` in a fresh workspace returns a `sessionId` and a `configOptions` model selector.
3. `session/list` in the process that created a session does not list it until `session/close`. After close, or from a second process, the session is listed with its `cwd`.
4. `session/resume` of a session created without any turn succeeds from a second process and returns `configOptions`.
5. `session/resume` of an unknown id answers error `-32602` with the message "session is not resumable". It does not answer `-32002`.
6. Files written under DSH_HOME: `sessions/<encoded-cwd>/<sessionId>/session.v4.jsonl.zstd` and `session.lock`; the index `storages/session_projcache/sessions/<sessionId>.json`. A first run also created `.anonymous-user-id` and `profiles/acp/` (`cordis.yml`, `cordis.patch.yml`, `package.json`, `pnpm-workspace.yaml`). These are observations in a disposable profile, not documented behavior.
7. A turn without a key fails with the message "no API key for provider route deepseek-official", naming `DEEPSEEK_API_KEY` and the credentials service.

Control run: the same script against the offline fixture (`tests/fixtures/fake-dsh-acp/dsh`) gives PASS on P01 to P08 and UNVERIFIED on P09, because the fixture reports no version. The qualifier's own test file (`tests/test_qualify_dsh.py`) passes 20 of 20. So the FAIL rows below come from the real binary, not from the script.

Verdicts (second run, 2026-10-11, after the verdict fixes): PASS 1 (P01) · FAIL 3 (P03, P08, P09) · UNVERIFIED 14 (U01–U09; P02, P04–P07 need a real model turn and the disposable profile has no API key). P09 fails because protocol probes P03 and P08 failed; the pinned version itself was reported.

Gate outcome: not passed. P03 and P08 diverge from the probe contract, and the turn rows never ran. dsh stays absent from selectable engines, and Codex stays the default.

## Next one-PR scopes

Each scope is qualification-only (no runtime adapter, no real-home install, no feature flag, no default change), and each is limited to a contract this run established. Scope 1 of the first run (P01 accepting an absent `loadSession`) is done.

1. Unknown-session resume: the real binary answers `-32602` "session is not resumable". Decide whether the session-not-found mapping accepts this shape, and pin the real reply in a sanitized fixture.
2. Session listing after create: align the fixture and the P03 order with the real behavior (listed after close or from another process), or record the divergence as a blocker.
3. Cross-process resume of a turn-less session: pin the real reply in a sanitized fixture. P07 still needs a key for its turn part.
4. Session storage layout (U09): pin the observed file names in a fixture; no reads of real homes.
5. Turn rows (P02, P04–P07) and usage (U05): need a run with a real API key in a disposable profile; the auth-failure pattern used for UNVERIFIED is inferred from the observed "no API key" message and must be confirmed then.
