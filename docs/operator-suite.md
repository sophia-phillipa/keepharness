# Operator suite

Scripted walkthroughs that operate KeepHarness the way a person does. Each area is a
sequence of narrated steps; every step shows a caption, highlights what it touches,
asserts an outcome, takes a screenshot and runs the layout lint of the layout-lint gate
(`tests/support/layout-lint.js`: overlaps, text that escapes its box, fields whose text
runs under an icon). It complements the `*.spec.cjs`
regressions: those pin one behavior each with mocked APIs, while the operator suite
drives the real app end to end, area by area.

Code: `tests/operator/` (`run-operator.cjs`, `lib/`, `areas/`, `fixture/`,
`known-defects.json`) and the wrapper `scripts/operator-suite.sh`.

## Requirements

- Node.js with the `playwright` package (set `NODE_PATH` or `PLAYWRIGHT_MODULE` if it is
  not installed next to the repository) and its Chromium.
- The project virtualenv (`.venv`, or `PYTHON=/path/to/python`) for the fixture servers.
- `xvfb-run` for headless runs; `xprop` for the desktop icon check (optional).
- For the desktop area: the packaged app (`--app`, `$KEEPHARNESS_DESKTOP_BIN`, or
  `dist/keepharness-<version>-linux-x64/keepharness-bin`). Without it that area is skipped.

## Run it

Headless, fixture mode, every area (for agents and CI):

```sh
scripts/operator-suite.sh --app /path/to/keepharness-bin
```

Visibly, on your own screen, inside the desktop app, at a watchable pace:

```sh
scripts/operator-suite.sh --visible --target desktop --app /path/to/keepharness-bin
```

Use `--target browser` (the default) to watch a Chromium window instead, `--pace 900`
to slow it down, and `--areas composer,approvals` to run a few areas (`--list` shows them).

Against a real instance, with at most three short real prompts (Claude first, then Codex):

```sh
scripts/operator-suite.sh --mode real --url http://127.0.0.1:18421 \
  --admin-url http://127.0.0.1:18420 --budget 3 --areas shell,new-chat,run-console
```

Real mode never enrolls the browser for approvals, explores the admin read-only, and skips
steps that create, edit or delete anything unless `--allow-writes` is given. Ports 8094
and 8095 are refused unless `--allow-default-ports` is passed on purpose.

## Fixture mode

`fixture/serve_fixture.py` starts an admin and a harness on their own ports (18510/18511
by default, `--fixture-ports` to change), with a private `HOME`, a temporary state folder,
a fixture project (`Alpha research`) with files, an agent, a skill and a command, and one
connector. The owner's personal setup is off, as in a real install; the plugins area turns it on by
rewriting the harness config, and the admin area signs in with a one-time link minted from the
fixture's secret. Both provider CLIs are `fixture/fake_provider.py`: a Claude Code stand-in
(stream-json) and a Gemini stand-in (ACP). Nothing calls a cloud model. The newest marker
in a prompt picks the behavior: `OP-SLOW`, `OP-TABLE`, `OP-IMAGE`, `OP-TOOLS`,
`OP-APPROVAL`, `OP-QUESTION`, `OP-ERROR`; anything else gets a short streamed reply that
contains `OPERATOR_OK`. The servers and their state are removed when the run ends.

The harness accepts 12 submitted messages a minute and a fixed number of requests per
minute; the operator keeps under the first, waits out any `429` answer, retries a throttled
step once when it sent no message, and presses "Resume tracking" when the app offers it.
Each of these is written on the step, so a throttled pass is never silent.

## Output

`operator-report/<timestamp>/index.html` (git-ignored): one section per area, each step
with its caption, result, screenshot and lint findings; `summary.json` has the same data.
The last console line is `OPERATOR SUMMARY {...}` with the counts. The exit code is 1 when
a step fails unexpectedly or an area errors, 2 when the suite cannot start.

Results: `pass`, `fail`, `skipped` (with the reason) and `known (ID)`: a failure listed in
`tests/operator/known-defects.json` under the gauntlet ledger id. When a listed step passes,
the report says its allow-list entry can go: delete it in the fix's commit so the list
shrinks as fixes land. Lint findings are labeled with a ledger id when a `lint` rule there
matches, `noise` for measured-but-intended cases, and `new` otherwise; new lint findings are
reported, not failed.

## Add a step

Areas live in `tests/operator/areas/NN-name.cjs` and export `{ id, title, run(op) }`:

```js
await op.step("rename", "Rename the conversation from its row", async (page) => {
  await op.click(page.getByRole("button", { name: "Rename conversation" }));
  await op.fill(page.locator("#rename-conversation-name"), "New name");
  await op.click(page.locator("#rename-conversation-save"));
  await op.seeText(page.locator("#conversation-title"), /New name/);
}, { writes: true });
```

- Locate by id, role and accessible name; never by layout classes.
- Act with `op.click`, `op.fill`, `op.type`, `op.press`, `op.select` (they highlight and
  pace in visible mode; `op.fill` types key by key when visible, except date and time fields,
  which take the value in one step because a 12-hour field needs its AM/PM segment); assert with `op.see`, `op.gone`, `op.seeText`, `op.until`,
  `op.check`; `op.skip(reason)` when the step does not apply.
- Shared helpers (`home`, `newChat`, `chooseModel`, `send`, `ask`, `waitAnswer`, `row`)
  are in `lib/app.cjs`.
- Options: `fixtureOnly`, `realOnly`, `writes`, `target: "desktop"`, `critical` (skip the
  rest of the area after a failure), `recover: false` (keep the screen after a failure),
  `lint: false`.
- A new step that exposes a ledger defect keeps its correct assertion and gets an entry in
  `known-defects.json`; never assert the buggy behavior.
