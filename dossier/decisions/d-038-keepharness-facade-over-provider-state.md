# D-038 — KeepHarness is a facade over the providers' real state

Status: accepted. Date: 2026-10-07. Decided by: Sophia (W5 review of #21 and #23). Supersedes [D-034](d-034-wp3-customize-product-answers.md) §1, and the harness-owned provider homes and the personal-setup opt-in of 0.15.0 (ledger D01, D02; [provider homes](../docs/provider-homes.md)). Release notes: [v0.16.0](../releases/v0.16.0.md).

## Context

Since 0.15.0 each provider CLI runs in a home owned by KeepHarness (`<state>/providers/home`, its own `CODEX_HOME` and `CLAUDE_CONFIG_DIR`), the owner's `~/.codex` and `~/.claude` are only read behind an opt-in, and runs switch plugins and MCP servers per run from a KeepHarness allow list (`settings.services[provider].integrations`). D-034 §1 made the Plugins page switch edit that allow list and never the CLI's own state.

Both CLIs let a person turn plugins, skills, MCP servers and apps on and off (Codex through `config.toml`; Claude Code through `claude plugin enable|disable`, `skillOverrides` and the MCP settings). An isolated KeepHarness environment drifts from what the person sets in the CLI, in an IDE extension or in the provider's desktop app, and a run can then differ from what the person expects.

## Decision

1. **KeepHarness is an adapter and a facade over each provider CLI**, with per-provider specifics. It reads the CLI's real state (global and project) and shows it; a change made in KeepHarness changes the CLI's real state, the same way the CLI's own command or setting would.
2. **The state is shared both ways.** Anything changed outside KeepHarness (terminal, IDE extension, desktop app, project files) shows in KeepHarness the next time its screens load, for example a plugin that was enabled and is now disabled.
3. **Every run respects the provider's own orchestration, always**: the global setup of that provider and the project's local artifacts (skills, rules, hooks, agents, MCP servers, plugins and instructions files). KeepHarness requests runs; it does not replace or filter that context.
4. **Row switches on the Plugins page write the CLI's state** (plugin, app, MCP server and skill), through the CLI's command when one exists and its documented setting otherwise. The KeepHarness allow list no longer decides what a provider loads.
5. **KeepHarness-native skills, agents, rules and hooks are future work.** When they exist they are injected into runs as additional prompts, on top of the provider's own context; that design is not part of this decision.

## Rationale

One source of truth avoids sync between a KeepHarness copy and the CLI, and avoids broken expectations: a run started from KeepHarness behaves like the same request made in the CLI for that project.

## Alternatives

- Keep the isolated KeepHarness home and replicate the global rules into it: rejected (sync drift, stale or alien state).
- KeepHarness allow list on top of the CLI state (D-034 §1): rejected.
- Skills listed without a switch (draft for #23): rejected; the CLIs can turn skills off, so KeepHarness offers it.

## Follow-up answers (Sophia, 2026-10-07)

- **Sign-in stays in KeepHarness.** Settings › Providers keeps Log in / Renew access, now acting on the CLI's own login. KeepHarness is a facilitator.
- **Detect external changes and say so.** A change made outside KeepHarness is not only shown on the next load: KeepHarness notices it and tells the person.
- **DeepSeek gains autonomy.** Today it runs on the Codex engine with its own home and key. Study DeepSeek's own CLI/harness (where everything is a plugin) and move to it, so DeepSeek is a first-class provider a conversation can switch to, independent of Codex.
- **The `scoped` sandbox mode is desirable, not required**: it goes last in the milestone.

## Impact

Needs a design before code: how runs get the real homes (login, sessions and the `scoped` sandbox), per-provider state readers and writers (atomic writes, concurrent edits by the CLI or an IDE), what guests and scheduled runs may load now that the owner's global hooks and MCP servers apply, DeepSeek (Codex engine with its own home and key), and the migration from the 0.15 harness homes and allow lists. Issues #21, #22 and #23 move behind that design; #24 (labels) is unaffected. Tests use fake homes only, never the owner's real `~/.codex` or `~/.claude`.
