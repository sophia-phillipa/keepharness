# D-040 — Single-owner collapse: implementation details for #41 and #42

Status: accepted. Date: 2026-10-07. Decided by: the W10 orchestration session under Sophia's standing W10 instruction (decide spec gaps with the JEV plus an Opus review, record them here). Builds on [D-039](d-039-single-owner-facade-policies.md) item 1. Design: [provider facade design](../provider-facade-design.md) §2.5, split items 4b and 4c.

## Context

D-039 makes KeepHarness single-owner. The #41/#42 mapping (W10) found gaps that D-039 does not settle: the owner's representation, rows already stored under per-login client ids, how deep the owner-flag cleanup goes, and how far the `guest` scan reaches.

## Decision

1. **Representation.** `clients` holds only `local`, with every project. `tailscale_logins` maps every allow-listed login to `local`, and `LOCAL_CLIENT` stays the owner's name. Every `identity == LOCAL_CLIENT` branch loses its guest arm. `approve-device --owner <login>` resolves to `local`. The remote gate (#38) is unchanged and fails closed: an empty allow list admits nobody remotely. (JEV: map_to_local, confidence 0.97.)
2. **Stored `tailnet-<hash>` rows are orphaned, not migrated.** Rows and state folders stay on disk untouched and are not shown, because queries filter by the owner `local`. Every lookup of an unknown owner falls back safely and never raises `KeyError` (`maestro.py:336,687`, `conversation_service.py:2614`). This follows D-039 item 9 (no migration). Those clients were limited to `sem-projeto` and were barely used. (JEV abstained at 0.33; this is the local choice, the smallest that loses no data.)
3. **Owner flags: minimal cleanup.** Branches whose else-arm served guests are removed. Internal `owner=` parameters that become always-true may stay, passing the owner value, and `local_owner` in `/v1/models` stays `true`. A full removal of those parameters is a follow-up, not part of W10.
4. **Run classes.** #42 removes every `guest` identifier and the guest run class. The scheduled-run clamps (internet off by default, the refused modes) are a separate behavior owned by the "runs on real homes" item (#45). They stay until then, and only their guest-named identifiers are renamed.
5. **`shared_projects`.** The setting, its validation and its UI are removed. An old `settings.json` that still holds the key loads without an error, and the key is ignored.
6. **Bearer tokens** identify only `local`.
7. **Remote enrollment message.** A remote login that has no approval session is told to run the approval command on the computer where KeepHarness runs. The message no longer mentions a "guest" or an "owner" as someone else.
8. **Conventions scan scope.** The #42 conventions test scans code and tests (`*.py`, `*.js`, `*.cjs`, `*.html`, `*.css`, `*.sh`) and has an explicit allow list. `dossier/` and `docs/` are history, which D-039 leaves unedited, and are not scanned.

## Rationale

These are the smallest changes that meet the #41/#42 acceptance and keep the #38 gate intact, without migrating data D-039 says to leave alone.

## Alternatives

- Keep a `tailnet-*` client per login with every project: rejected, because it keeps the per-client machinery that #41 removes.
- An SQL remap of the old owners to `local`: rejected for now, because it is a migration D-039 does not ask for. Revisit if Sophia wants those rows back.

## Follow-up

- Remove the always-true `owner=` parameters in a later cleanup.
- #45 owns the scheduled-run clamps.
