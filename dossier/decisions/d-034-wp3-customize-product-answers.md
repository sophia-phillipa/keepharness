# D-034 — WP3 Customize: switches, Add menu, label and directory grouping

Status: accepted. Date: 2026-10-06. Decided by: Sophia (answers to the four open questions of the WP3 split). Spec: [WP3](../codex-parity-design.md#wp3-customize-screen-l-admin-frontend--small-python). Refines [D-022](d-022-wp3-copy-codex-customize.md). Release notes: [v0.16.0](../releases/v0.16.0.md).

## Context

Splitting WP3 (#14) against the live code left four questions the Codex target does not answer for KeepHarness: what a row switch changes, what to do with the Codex `Add` items KeepHarness has no backend for, the screen's label, and how to group a directory when the provider CLIs report no categories.

## Decision

1. **Row switch = KeepHarness allow list.** The switch on a plugin, app, MCP or skill row turns "allowed in KeepHarness" on or off for every provider where the item is installed, through the existing `/api/settings` (`settings.services[provider].integrations`). It does not change the provider CLI's own state.
2. **`Add` actions are provider pass-throughs, shown only when possible.** `Create plugin`, `Add a marketplace`, `Upload plugin archive`, `Create MCP App` and `Add MCP server` belong to a provider, not to KeepHarness. An item appears only when that provider is installed locally (for example a local Codex) and its CLI supports the action, so KeepHarness can hand the action to it; otherwise the item is removed, not shown disabled. None of these is a KeepHarness-native concept in this version; KeepHarness's own versions are future work.
3. **Label "Plugins"**, as in Codex: the rail button, the Settings submenu item and the page title read "Plugins", with the subtitle "Manage plugins, skills, and MCPs". "Your agents" stays its own Settings item "Agents".
4. **Directory grouped by marketplace.** Without CLI categories, the directory shows one collapsible section per marketplace or source, with the Codex section style.

## Rationale

The allow list is the control KeepHarness already owns; changing CLI state from a switch would hide a provider-side effect. Showing only actions a provider can really perform avoids dead controls and keeps KeepHarness from pretending to own plugin, marketplace or MCP-app creation.

## Alternatives

- Read-only switches mirroring the CLI state: rejected (Q1).
- Codex `Add` items shown disabled with a reason: rejected by Sophia; impossible actions do not appear (Q2).
- Label "Customize": rejected (Q3).
- One flat grid: rejected (Q4).

## Impact

WP3 sub-issues: the row-switch issue wires to `/api/settings`; the `Add` menu becomes a per-provider capability check plus pass-through, which needs its own design and an Opus security review (constant arguments, no command or environment echoed, owner-only, D-030); Settings' "Customize" item is renamed "Plugins" and the agents section "Agents".
