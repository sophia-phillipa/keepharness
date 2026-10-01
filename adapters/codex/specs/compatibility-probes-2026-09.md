# Codex live compatibility probes — September 2026

Observed on 2026-09-30 with **codex-cli 0.157.1**, using real authenticated
`codex app-server --listen stdio://` executions. Model turns used only
**gpt-6-astra / high**; the authenticated `model/list` returned that exact model
and effort. These results supplement the older adapter spec; they do not change
product code or certify other CLI versions, accounts, or sandbox modes.

All inputs came from [the synthetic squad](../../../tests/fixtures/synthetic_squad/).
Each run had a new temporary `HOME`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR` and project.
Only the existing authentication artifact was copied (0600); no real settings,
plugins, personal instructions, or client files were copied. Final fixture
construction uses an environment allowlist. System skills installed by Codex
itself still appeared. No connector was called. Temporary auth and runtime state
were deleted after each probe. The independent `p0` worktree was not used.

`supported` means the operation was observed in the stated configuration;
`fallback` means P1 needs the stated adaptation; `unsupported` is restricted to
the tested native invocation, not a claim about all Codex configurations.

| Probe / execution mode | Version | Exact reproduction / protocol input | Observed evidence | Verdict | Recommended fallback |
| --- | --- | --- | --- | --- | --- |
| 7: execution-scoped stdio MCP | 0.157.1 | `C(mcp)` below; `thread/start`, `mcpServerStatus/list`, `mcpServer/tool/call` for `synthetic/prepare`; then model calls `prepare` | Server inventory included `prepare` and `block`; prepare returned `synthetic-request-001` in **0.001 s**; model emitted `mcpToolCall` and returned the same ID | **supported** | Pass per-process `-c mcp_servers...`; do not edit the user's config. HTTP transport was not tested. |
| 7: long tool, direct app-server RPC | 0.157.1 | `C(mcp)`; `mcpServer/tool/call` with `tool:"block"` | Completed in **95.001 s**, `isError:false`, server elapsed **95.0 s** | **supported** at 95 s | This establishes a lower bound only, not an unlimited timeout. Return a pending ID before waiting for a human. |
| 7: long tool, model dispatch | 0.157.1 | `C(mcp_turn_block)`; ask model to call `prepare`, then `block`, once each | Two `item/completed` events of type `mcpToolCall`; block `status:"completed"`, `durationMs:95000`; whole run **109.591 s** | **supported** at 95 s | Keep gates outside MCP calls; the Harness bridge's separate HTTP timeout still applies. |
| 7: explicit timeout | 0.157.1 | `C(mcp_timeout)` adds `-c mcp_servers.synthetic.tool_timeout_sec=2` | Prepare **0.001 s**; block failed after **2.002 s**, JSON-RPC `-32603`, `timed out awaiting tools/call after 2s` | **supported** | Configure tool budgets explicitly. A timeout is not proof that a remote effect did not execute. |
| 8: individual external `SKILL.md` symlink | 0.157.1 | `C(skills)`; `skills/list` with `cwds:[PROJECT], forceReload:true` | `errors:[]`; six system skills; **no `demo-native`** | **fallback** | Symlink the containing skill directory, not just its `SKILL.md`. |
| 8: external skill-directory symlink and explicit invocation | 0.157.1 | Same process after changing only symlink shape; `turn/start` text `$demo-native ...` plus `{type:"skill",name:"demo-native",path:...}` | `demo-native`, `enabled:true`, `scope:"user"`, **canonical external catalog path**; final `SYNTHETIC_NATIVE_SKILL_MARKER`; **5.133 s** | **supported** | Match canonical resource paths and enabled state; use structured skill input. |
| 8: symlinked project/global agent TOML | 0.157.1 | `C(agents_symlink_trusted)`; temporary Git project trusted in temporary config; ask `spawn_agent` for both names | Both raw `function_call_output` values: `agent type is currently not available`; **15.034 s** | **fallback** | Materialize validated synthetic-equivalent TOML in the execution's temporary agent directories. Do not claim a requested agent ran from assistant text alone. |
| 8: regular-file agent control | 0.157.1 | `C(agents_trusted)`; same trusted temp project, copy each TOML to its temp category root | Two actual `spawn_agent` results with task names; final markers `SYNTHETIC_CODEX_PROJECT_AGENT_WRITER` and `SYNTHETIC_CODEX_GLOBAL_AGENT_REVIEWER`; **21.002 s** | **supported** | Preserve model/effort and resource revision when materializing. Project trust in the **temporary config file** was required in this control. |
| 8: custom prompt text invocation | 0.157.1 | `C(prompt)`; temp `CODEX_HOME/prompts/synthetic-probe.md` symlink; text `/prompts:synthetic-probe alpha beta` | User message remained literal; final asked for the definition; no `SYNTHETIC_COMMAND_ARGUMENTS=alpha beta`; **6.320 s** | **unsupported** as native app-server expansion | Expand trusted prompt content in the Harness, preserving arguments and fenced `$HOME`; test that fallback before release. The earlier `/synthetic-probe alpha beta` variant also produced no command marker. |
| 8: option questions, Default mode | 0.157.1 | `C(question)`; ask model to use `request_user_input` with Blue/Green | No `item/tool/requestUserInput`; assistant reported unavailable in Default; `codex features list` showed `default_mode_request_user_input false`; **15.853 s** | **fallback** | Detect capabilities and use the feature-enabled or Plan variant; otherwise Harness-managed gates. |
| 8: option questions, Plan mode | 0.157.1 | `C(question_plan)`; `turn/start.collaborationMode` below | `item/tool/requestUserInput`, Blue/Green options with descriptions, `isBlocking:true`; reply accepted; final `CHOICE=Blue`; **13.927 s** | **supported** (experimental API) | Route replies by question ID and correlate server request, thread and turn. Do not switch task semantics to Plan silently. |
| 8: option questions, enabled Default mode | 0.157.1 | `C(question_enabled)` adds `-c features.default_mode_request_user_input=true` | Same request/options/reply; `isBlocking:false`, `autoResolutionMs:null`; final `CHOICE=Blue`; **10.511 s** | **supported** behind experimental flag | P1 can opt in per execution, retaining a Harness gate fallback and capability check. |

## Exact commands and messages

Run from the repository root. The runner records its complete argv and protocol
messages. `C(NAME)` in the table is this exact invocation with `NAME` substituted:

```sh
TAIL_HARNESS_LIVE=1 .venv/bin/python tests/live/codex_probes.py NAME
```

Its common child command is below. `CODEX`, `PYTHON`, `SERVER`, and temporary paths
are absolute paths resolved by the helper, not additional shell variables passed
to the model. Here `PYTHON` is this worktree's `.venv/bin/python` and `SERVER` is
`tests/live/probe_mcp_server.py`. No real config is rewritten.

```text
CODEX app-server --listen stdio://
  -c features.hooks=false -c features.apps=false
  -c features.shell_tool=false -c features.unified_exec=false
  -c web_search="disabled" -c check_for_update_on_startup=false
  -c model="gpt-6-astra" -c model_reasoning_effort="high"
```

All MCP variants append these arguments (each `key=value` is one argv element):

```text
-c mcp_servers.synthetic.command="PYTHON"
-c mcp_servers.synthetic.args=["SERVER"]
```

The runner sends `initialize` with `experimentalApi:true`, then `initialized`.
`thread/start` sets `model:"gpt-6-astra"`, the temporary project `cwd`,
`approvalPolicy:"never"`, `sandbox:"read-only"`, and `ephemeral:true`.
Diagnostic agent runs additionally requested `experimentalRawEvents:true`; the
reusable runner now requests that field for every probe. Opaque/encrypted model
payloads are not reproduced in these excerpts.

The prepared MCP tool is local and has **no publish operation**. It responds:

```json
{"content":[{"type":"text","text":"{\"request_id\":\"synthetic-request-001\",\"status\":\"pending\",\"elapsed_seconds\":0.0}"}],"isError":false}
```

The successful question request/reply shape, with generated IDs normalized:

```json
{"method":"item/tool/requestUserInput","id":0,"params":{"threadId":"THREAD","turnId":"TURN","itemId":"CALL","questions":[{"id":"color","header":"Color","question":"Which synthetic color should we choose?","isOther":true,"isSecret":false,"options":[{"label":"Blue","description":"First synthetic color"},{"label":"Green","description":"Second synthetic color"}]}],"isBlocking":true,"autoResolutionMs":null}}
{"id":0,"result":{"answers":{"color":{"answers":["Blue"]}}}}
```

For Plan mode, the extra `turn/start` field is:

```json
{"collaborationMode":{"mode":"plan","settings":{"model":"gpt-6-astra","reasoning_effort":"high","developer_instructions":null}}}
```

For the two trusted agent controls, the runner calls `git init --quiet PROJECT`
and writes **only** `CODEX_HOME/config.toml` under the disposable root:

```toml
[projects."PROJECT"]
trust_level = "trusted"
```

`agents_trusted` replaces each temporary agent symlink with a regular copy;
`agents_symlink_trusted` leaves both symlinks in place. Preliminary controls with
`-c projects."PROJECT".trust_level="trusted"` still emitted a disabled-project
warning; explicit `-c agents.NAME.config_file="PATH"` also failed to make the
symlinked roles available. Those attempts are not the positive trust control.

## P1/P3 implications and limits

P1 can use structured skill inputs after live discovery and canonical-path checks,
and option-question responses with the exact nested `answers` shape above. It
must account for symlink shape, materialize agent files when necessary, and expand
custom prompts before sending app-server text. These are compatibility fallbacks,
not permission grants or a guarantee that a particular model will choose a tool.

P3 can register a fresh effect-executor server through process overrides. The
95-second native success does **not** test `agent_service/mcp_bridge.py`: its
`call()` constructs a separate `httpx.AsyncClient(timeout=90)`. Prepare a request
ID quickly and wait for human approval outside the MCP call. This spike does not
implement approvals, executor credentials, effect execution, or recovery.

The [official app-server reference](https://learn.chatgpt.com/docs/app-server)
documents structured skill inputs and question replies. The
[agent documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents)
describes personal and project TOML files, and the
[MCP reference](https://learn.chatgpt.com/docs/extend/mcp) describes configuration.
The live observations above, including failures, take precedence for this tested
version. Stable and experimental schemas were generated locally with
`codex app-server generate-json-schema [--experimental] --out TEMP_SCHEMA`;
no schema generation was treated as proof of a successful model turn.

## Reusable conformance checks

```sh
# Offline isolation, opt-in guards, secret redaction; provider cases are skipped.
.venv/bin/python -m pytest -q tests/live/test_conformance_probes.py

# One paid representative; select other case IDs deliberately.
TAIL_HARNESS_LIVE=1 .venv/bin/python -m pytest -q tests/live/test_conformance_probes.py -k 'test_codex_conformance and skills'
```

The canonical Codex matrix has ten cases. Assertions check actual tool events,
question options/replies, successful marker propagation, expected missing
capabilities, exact model/effort, and measured timings. Exploratory variants are
manually runnable but not extra pytest cases. Tests never opt into the existing
`TAIL_HARNESS_LIVE` suite. Every process group is terminated during teardown;
normal probes stop at ten minutes, including failed or silent reads. The latest
fixture/runner integration was rerun through pytest: **1 passed, 23 deselected**.

The final default suite passed: **1139 passed, 37 skipped, 12 subtests passed**.
See the [shared validation record](../../../tests/fixtures/synthetic_squad/README.md#validation-on-2026-09-30)
for setup, fixture-only baseline fixes, and the limited protected-file audit.
