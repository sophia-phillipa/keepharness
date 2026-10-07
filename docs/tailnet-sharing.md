# Tailnet sharing (WP-19)

How KeepHarness is reached from other devices on the owner's Tailscale network (the tailnet), by the owner and by guests. Slices S1 (supply chain) and S2a (guest boundary) are described in `dossier/releases/v0.16.0.md` and `docs/local-owner-access.md`; this note records the product decisions that shape the next slices (S2b onward).

## Decisions (Sophia, 2026-10-04)

All six were accepted as recommended.

| # | Question | Decision | Why | Revisit when |
|---|---|---|---|---|
| N1 | A tailnet login that was a guest becomes Owner: what happens to the history it built as a guest (`tailnet-<hash>`)? | Leave it where it is. It shows again if the login is demoted back to guest. No `--adopt-history` command. | Simplest; no rows move between clients. | The owner misses that history; then add a one-time `--adopt-history` that moves jobs, files, workspaces, approval rules, schedules and pages to `local`. |
| N2 | Claim port 80 on the server's tailnet address to redirect the short name and the 100.x address to the full name? | Yes. Default port 80, configurable, can be turned off. | Typing the machine's short name or tailnet IP just works. | — |
| N3 | Granularity of "projects shared with guests". | One owner switch for all registered projects (`shared_projects`, already in S2a, default off). | It exists and covers one guest. | A second guest needs a different set of projects; then a per-guest list. |
| N4 | Switching an existing HTTP share to HTTPS. | Explicit only: the next `share` command or an admin toggle, with a warning in the status until then. Never automatic at upgrade. | An automatic switch silently breaks MCP bridges configured on `http://…:8095`. | — |
| N5 | Which tailnet peers the discovery probe contacts. | Every online peer, tagged devices included, except devices shared in from other tailnets. | Finds the owner's instances without probing other people's machines. | — |
| N6 | Accept 100.64.0.0/10 addresses in `vpn_bind` (asked by OPS-P13)? | No. Tailscale Serve stays the only tailnet path. | Serve is what proves who is connecting (peer uid proof, identity headers); a direct bind would accept credentials over plain HTTP without that proof. | — |

## Consequences for the next slices

- S2b adds the panel switch for `shared_projects` (N3) and the HTTPS toggle with its status warning (N4).
- The short-name redirect listens on the tailnet address only, port 80 by default (N2).
- `vpn_bind` keeps refusing tailnet addresses (N6); the error should point to Serve.
- Demoting a login does not delete or move its guest history (N1).
