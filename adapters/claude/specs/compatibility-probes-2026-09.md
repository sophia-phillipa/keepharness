# Claude Code live compatibility probes — 2026-09

## Scope and environment

These probes used Claude Code `2.1.284` on 2026-09-30 with synthetic fixture
data and disposable `HOME` and `CLAUDE_CONFIG_DIR` paths. The runner copied only
the existing Claude authentication artifact into the temporary configuration;
it did not read or modify real settings. Each invocation had a 590-second total
execution budget plus bounded process cleanup. Paid prompts used Haiku except
the slash-command control, which deliberately started with Sonnet so its Haiku
frontmatter override was observable.

Common argv, with temporary paths represented by placeholders:

```text
claude --print --verbose --output-format stream-json
  --input-format stream-json --include-partial-messages
  --permission-prompts host --permission-prompt-tool stdio
  --permission-mode default --no-session-persistence
  --model <haiku|sonnet> --tools <probe tools>
  --setting-sources <project|user,project>
  --settings <synthetic JSON> --strict-mcp-config
  [--mcp-config <temporary JSON>] [--forward-subagent-text]
```

The Results table uses this exact wrapper command:

```text
C(NAME) = TAIL_HARNESS_LIVE=1 PYTHONPATH=tests/live \
  python3 tests/live/claude_probes.py NAME
```

`connectors` also ran the same command without `--strict-mcp-config`. The
evidence records sanitized argv, stream events, control requests, exit code,
elapsed time, and a bounded stderr tail. No connector tool was called.

The approved `AskUserQuestion` reply had this exact protocol envelope (the
question and descriptions are abbreviated here only with synthetic values; the
recorded report retains them in full):

```json
{
  "type": "control_response",
  "response": {
    "subtype": "success",
    "request_id": "<request id from control_request>",
    "response": {
      "behavior": "allow",
      "updatedInput": {
        "questions": [
          {
            "question": "Choose the synthetic option?",
            "header": "Choice",
            "options": [
              {"label": "Alpha", "description": "First synthetic option"},
              {"label": "Beta", "description": "Second synthetic option"}
            ],
            "multiSelect": false
          }
        ],
        "answers": {"Choose the synthetic option?": "Alpha"}
      }
    }
  }
}
```

Hook runs passed `--settings '{"disableAllHooks":false}'`. Each selected
settings file contained this shape, with a different fixture script:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "*",
        "hooks": [
          {"type": "command", "command": "<python> <fixture-hook.py>", "timeout": 10}
        ]
      }
    ]
  }
}
```

The execution-scoped MCP runs passed this strict config:

```json
{
  "mcpServers": {
    "synthetic": {
      "command": "<python>",
      "args": ["tests/live/probe_mcp_server.py"]
    }
  }
}
```

## Results

| Probe | Command | CLI | Result | Observed evidence | Harness fallback or limitation |
| --- | --- | --- | --- | --- | --- |
| Native `/command` expansion and frontmatter | `C(command)` | `2.1.284` | **Supported** | `/synthetic-probe alpha beta` returned `SYNTHETIC_COMMAND_ARGUMENTS=alpha beta`. The outer `--model sonnet` run emitted assistant events from `claude-haiku-4-5-20251001`, proving the command's `model: haiku` frontmatter was applied. Exit 0 in 4.246 s. | Native expansion is usable. The fenced Bash example was retained as command content and was not executed. |
| `AskUserQuestion` over stdio | `C(ask_user_question)` | `2.1.284` | **Supported** | A real `control_request` named `AskUserQuestion` contained Alpha and Beta options. The host returned `behavior: allow` with `updatedInput.answers`; the result was `CHOICE=Alpha`. Exit 0 in 6.566 s. | Map the request to a Harness gate and return answers through `updatedInput`. |
| Delegation naming and child correlation | `C(delegation)` | `2.1.284` | **Supported** | The CLI accepted `--tools Task,Read`, exposed Task at initialization, emitted an `Agent` tool call for `demo--writer`, and forwarded child events with the parent's `parent_tool_use_id`. The child returned `SYNTHETIC_PROJECT_AGENT_WRITER`. Exit 0 in 6.909 s. | Treat the CLI allowlist name (`Task`) and stream event name (`Agent`) as versioned aliases; correlate by tool-use ID rather than name alone. |
| `TodoWrite` under `--tools` | `C(todowrite)` | `2.1.284` | **Unsupported** | Initialization exposed no `TodoWrite` tool and no tool-use event occurred. Haiku printed an XML-like pseudo-call as ordinary text. Exit 0 in 4.481 s. | Do not infer support from assistant text. Use the Harness's own task/run state until a real native tool is exposed and reprobed. |
| claude.ai connectors with and without strict MCP | `C(connectors)` | `2.1.284` | **Fallback** | Both isolated initializations reported empty `mcp_servers` and no MCP tools; no connector was called. The isolated inventories did not establish whether the account has connectors. | Execution-scoped MCP remains usable. Not established: no account connectors were listed in either mode. Connector suppression must not be advertised as certified. |
| External-symlink path rule | `C(path_rule)` | `2.1.284` | **Unsupported** | With `.claude/rules/synthetic-path-rule.md` as an external symlink, reading `probe-target.txt` returned only its content. Replacing that link with the identical regular file produced `SYNTHETIC_PATH_RULE_MARKER`. Exits 0 in 7.592 s and 5.001 s. | Materialize/copy path rules inside the project rule directory for Claude Code 2.1.284. Keep the canonical catalog source identity separately. |
| Hook merge isolation | `C(hooks)` | `2.1.284` | **Fallback** | `--setting-sources project` plus `disableAllHooks:false` ran only `project-hook`. `--setting-sources user,project` ran both `project-hook` and the unrelated `global-auto-update-hook`. Both exits 0, about 3 s each. | An explicit project hook list does not mask a global hook. Use the `project` setting source to isolate project hooks; `disableAllHooks:false` only enables them. |
| Execution-scoped MCP `prepare` | `C(mcp_prepare)` | `2.1.284` | **Supported** | Strict MCP config exposed and called `mcp__synthetic__prepare` once; result contained `synthetic-request-001` and `pending`. Exit 0 in 4.883 s. | Register the synthetic/effect server per execution and retain its request ID. |
| Execution-scoped MCP blocking tool | `C(mcp_block)` | `2.1.284` | **Supported** | Strict MCP config called `mcp__synthetic__block` once and waited through its 95-second server delay. Result contained `synthetic-request-001` and `finished`; total 99.319 s, exit 0, empty stderr. | This proves direct CLI behavior only. It does not bypass or certify the Harness bridge's separate 90-second timeout; bridge support needs its own timeout change and probe. |

Supported means the required protocol evidence was observed. Fallback means a
safe usable path exists but the requested native claim lacked a positive
control. Unsupported means the positive control worked while the tested native
surface did not, or no real tool event existed. Model prose alone never counts
as tool evidence.

## Reusable fixture and execution

The standard-library isolation helper is `tests/live/conformance.py`; Claude's
runner and observed assertions are in `tests/live/claude_probes.py`. Default
pytest collection performs only offline fixture checks. A selected paid probe is
run explicitly, for example:

```sh
TAIL_HARNESS_LIVE=1 PYTHONPATH=tests/live \
  .venv/bin/python -m pytest -q tests/live/test_conformance_probes.py \
  -k 'test_claude_conformance and command'
```

Run probes individually so the selected paid scope is visible. Fixture checks
verify cleanup, external symlink targets, explicit private auth copies, parent
environment stripping, and evidence redaction.

## Proportional gauntlet record

This is an internal protocol surface, so no browser or visual claim was made.
Seven contract profiles were exercised against the same final fixture:

| Profile scenario | Observable result |
| --- | --- |
| P1 — unfamiliar operator runs ordinary pytest | Live inference stayed skipped without the opt-in variable. |
| P2 — rushed/repeated runs | Every probe received a new temporary root and cleanup removed it; the helper did not copy real settings. |
| P3 — long-running domain operation | The blocking MCP call preserved its request ID and completed after the 95-second wait. |
| P4 — assistive/machine-readable consumption | Verdicts, controls, tool IDs, elapsed time, and blockers are structured; no UI accessibility claim applies. |
| P5 — interrupted or unavailable integration | Each named probe has one bounded budget; missing connector evidence produced a fallback with a stated blocker. |
| P6 — adapter engineer | Slash commands, gates, delegation, tools, symlink rules, hooks, strict MCP, and long tool waits were checked from stream evidence. |
| P7 — interaction reviewer | Supported, fallback, and unsupported labels use one evidence rule and never treat generated pseudo-calls as execution. |

The initial command run was repeated after clean-exit handling and the
frontmatter control were added. The path-rule run was repeated with an identical
regular-file control. No product code was changed and no full release suite was
run as part of these paid probes.

## Final validation and audit

The default suite passed: **1139 passed, 37 skipped, 12 subtests passed**. The
selected live AskUserQuestion pytest case also passed (5.64 s); all nine recorded
Claude results passed their evidence assertions. See the
[shared validation record](../../../tests/fixtures/synthetic_squad/README.md#validation-on-2026-09-30)
for setup details, the fixture-only baseline fixes, and the unassigned concurrent
change to the real `.claude.json`. The audit does not claim all global state
remained unchanged.
