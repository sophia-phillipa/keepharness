# D-042 — W10 visible-pass findings: which home the Plugins page reads, harness sync noise, notice wording

Status: accepted. Date: 2026-10-08. Decided by: the W10 orchestration session under Sophia's standing W10 instruction (decide gaps with the JEV plus an Opus review, record them here). Builds on [D-038](d-038-keepharness-facade-over-provider-state.md), [D-041](d-041-provider-state-routes-and-notices.md).

## Context

The W10 visible pass (packaged app at c7b24fd) found:

- **Home.** The Plugins page and the notices listed skills under `<state>/providers/home/.codex` and `.claude` (the 0.15 harness homes), not the owner's CLI homes. #21 asks the switches to "write the CLI's real state". The adapters resolve `CODEX_HOME` / `CLAUDE_CONFIG_DIR` / `$HOME` at call time, so something in the control process points them at the harness homes.
- **D1.** The desktop Settings has no entry to the admin Plugins page. Covered by #26 (W6).
- **D2.** The first read raised 82 "removed" notices caused by the harness's own skill sync (skills moved to `skills/.trash`), and `.trash` entries were listed as skills. This breaks F12 (KeepHarness's own writes never notify).
- **D3.** A notice repeats the name when the item id equals its name ("skill foo was removed (foo, 07:50)").

JEV: D1 out_of_scope (1.0); D2 fix_in_w10 (0.86); Home and D3 abstained (0.43 and below), so the local choice below applies.

## Decision

1. **Home: fix in W10.** The provider-state service reads and writes the owner's CLI homes (`CODEX_HOME` or `~/.codex`, `CLAUDE_CONFIG_DIR` or `~/.claude`, `~/.claude.json`), never `<state>/providers/home`, whatever the harness run environment sets for chats. Dropping the 0.15 homes for runs stays with #45/#46.
2. **D2: fix in W10.** Hidden folders (a name starting with `.`) are never skills, for both providers. The harness's own skill sync does not produce notices.
3. **D3: fix in W10.** The parenthesis shows the source file and time only; the name is not repeated.
4. **D1: known gap**, recorded in the v0.16.0 notes, fixed by #26.

## Rationale

#21 and D-038 make the Plugins page a facade over the CLI's real state; reading the harness homes shows state the CLIs do not use. D2 and D3 are defects against D-041's notice contract.
