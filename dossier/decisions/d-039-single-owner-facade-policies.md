# D-039 — Single-owner facade: policies that follow the CLIs

Status: accepted. Date: 2026-10-07. Decided by: Sophia (W8 review of the #32 design). Builds on [D-038](d-038-keepharness-facade-over-provider-state.md). Supersedes the multi-identity part of [UC-001](../UC-001-multi-user-execution.md) (ten-user concurrency, per-identity budgets), the D04 "owner-only" clamp for remote clients, the part of D04/D12 where "Read only" disables connectors, the D03/D15 scheduled-run clamps (refuse Automatic/Full, internet off by default) and the "scheduled run is isolated from the owner's setup" rule of `docs/scheduled-tasks.md`. Those documents are not edited. Design: [provider facade design](../provider-facade-design.md).

## Context

D-038 made KeepHarness a facade over the real CLI state. The design review (W8) found that the guest and run-class policies built around that (a baseline for guests, a hooks-and-MCP-free scheduled class) only matter if KeepHarness serves more than one person. It does not: the server runs at home with Sophia's accounts signed in, and the client runs on her work machine over Tailscale. She is not using KeepHarness now, so 0.15 backward compatibility is not needed.

## Decision

Governing principle: every concept starts as close as possible to the original CLI, its own command first, else its documented config file; revisit later.

1. **No guests; KeepHarness is single-owner.** `local` and the allow-listed Tailscale logins are the owner, with every project. The shared VPN key (client `vpn`) is removed. The remote-access gate is the only boundary and must stay owner-only and fail closed: admin panel loopback-only (`control/routes.py:66-73`), Funnel refused with `funnel_denied` (`agent_service/services/conversation_service.py:458-460`), a Tailscale login only through Serve on a tailscaled socket (`:534-545`), allow list validated at `control/manager.py:502-511`.
2. **Scheduled runs are the owner's.** They load everything (hooks, MCP, plugins) and use whatever permission the schedule chooses, including Automatic/Full and internet, like the owner's own `codex exec` / `claude -p`. There are no run classes.
3. **Claude MCP switches edit `~/.claude.json` directly, with safeguards.** The file is credential-bearing: never log or copy its session fields, redact logs. Back it up before each write into a KeepHarness-owned directory (`<state>/backups/claude-json/`), not `~/.claude/backups/` (Claude Code rotates its own backups there). A revert detector shows "reverted by Claude Code". After the write, re-parse and type-check the touched key and restore the backup on a new error. `settings.json` edits are validated against https://json.schemastore.org/claude-code-settings.json and restored on a new error (`-p` silently ignores invalid settings files).
4. **Trust follows the CLIs.** A project trusted by either CLI (Codex `projects."<path>".trust_level="trusted"`; Claude `projects["<path>"].hasTrustDialogAccepted`) is trusted. Otherwise KeepHarness shows a CLI-style trust prompt and, on accept, writes trust into the CLI's own state (Codex app-server `config/batchWrite`; Claude through the backed-up `~/.claude.json` path). Until accepted, Claude runs with `--settings '{"disableAllHooks":true}'` and no project MCP; Codex `trust_level="untrusted"` already skips project config, hooks and rules. Project `.mcp.json` servers follow the CLI's approval: unapproved names go to `--settings` as `disabledMcpjsonServers`, and KeepHarness asks the owner and writes the approval.
5. **Hooks are active as in any harness.** Codex hooks pending review are never bypassed (no `--dangerously-bypass-hook-trust`): KeepHarness shows "hooks pending review" and points to the CLI's own trust path.
6. **Permissions are the CLI's own.** KeepHarness presets map onto each CLI's sandbox/approval modes only; "Read only" no longer disables connectors.
7. **Switches write where the CLI would write:** user scope by default, project scope through the CLI's scope flag (`claude plugin enable -s user|project|local`) or the project's config file; no locked rows unless the CLI cannot write that layer.
8. **External changes are announced** on the Plugins page, on Settings › Providers and with a toast.
9. **No migration** of logins, sessions or allow lists. The 0.15 homes stay untouched on disk and are ignored; the release notes name the path. The integrations allow list (`services.*.integrations`) is dropped; the Tailscale login allow list stays, as it is the owner's gate. The guard that DeepSeek's home never holds the ChatGPT `auth.json` stays.
10. **DeepSeek:** the target is its own harness `dsh` ([research](../research/deepseek-cli-2026-10.md)); the interim stays the Codex engine with its own home and key until a spike (in #33) proves `dsh`.

## Rationale

With one person on two machines, the gate decides who gets in and nothing inside needs to guard the owner from herself. Her own `codex exec` or `claude -p` runs hooks and any permission mode she picks, so a KeepHarness run that is stricter than the CLI breaks the D-038 expectation that a run behaves like the same request typed in the CLI. The safeguards that remain protect the places where KeepHarness writes into files the CLIs also write (`~/.claude.json`) and the one real boundary left, an untrusted repository.

## Alternatives

- Keep guests and run classes (baseline for guests, no hooks or MCP for scheduled runs by default): rejected; it defends against callers that do not exist and keeps about 20 guest code paths alive.
- Keep the shared VPN key as an owner-equivalent credential: rejected; one less credential with full power, and Tailscale covers remote use.
- Delegate the Claude MCP switch to `/mcp`: rejected; direct edit with backup, validation and a revert detector follows the CLI's own file and keeps the switch in KeepHarness.
- A KeepHarness-only trust flag: rejected; it would diverge from what the CLIs trust.
- Migration of the 0.15 homes, logins and allow lists: rejected; Sophia is not using the app now and sign-in is one click.

## Impact

Removes the guest code paths, the VPN key and `control/integrations.py`; changes the run builders, the Plugins switches and the remote gate tests. Needs tests for the owner-only gate, trust, project MCP approval (marker file), `~/.claude.json` backup and restore, and DeepSeek key leakage. Release notes name the ignored `<state>/providers/home`.

## Follow-up

The issue split is in the [design](../provider-facade-design.md#proposed-issue-split); the guest and multi-identity removal is split in three (4a-4c) before entering a wave. Revisit if KeepHarness ever serves a second person (a new decision then restores per-identity policy), or if Claude Code gains a documented API for MCP switches (replace the direct edit).
