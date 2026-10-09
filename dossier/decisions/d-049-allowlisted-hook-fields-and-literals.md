# D-049 — Allowlisted hook fields and literals; rule previews stay best effort

Status: accepted. Date: 2026-10-09. Decided by: orchestrator under Sophia's standing autonomy (JEV abstained at 0.60 below the 0.80 medium-risk threshold; Opus security review of 51d19b3 returned CHANGES-REQUIRED). Applies to issue #61. Refines D-048; does not replace it.

## Context

The D-048 implementation masked quoted strings, option values, headers, environment values and URL userinfo, but two gaps remained in the review of 51d19b3:

- Positional words of up to 40 alphanumeric characters, and anything containing a `/` up to 120 characters, passed as "literals". A 40-character AWS secret, an `sk-…` token as the first word, a short JWT and a token inside a path all reached the UI.
- Hook fields outside the known command, named-value and URL keys fell back to the old blocklist scanner, so a future or plugin-defined key (`body`, `data`, `extra`, `cmdArgs`, `target`, `webhook`) could carry a secret raw.

The D-048 title also mentions rule previews, but its body only covers commands.

## Decision

1. **Literal allowlist.** After the first word (the executable), a positional word is shown only when it is a path with an explicit prefix (`/`, `./`, `../`, `~/`) whose segments are short and not hex- or base64-like, or a lowercase subcommand without digits (`^[a-z][a-z-]{0,19}$`). Everything else becomes the placeholder. Long option names are limited to `[a-z][a-z0-9-]{0,30}`; env and header names to `[A-Za-z_][A-Za-z0-9_-]*`.
2. **Field whitelist.** Inside a hook object, only known harmless scalars pass unchanged (type, timeout, async, enabled, event, matcher kind, source path, trust status, display order, hashes and env var names). Known free-text fields (`prompt`, `statusMessage`, `description`, `matcher`) pass through the best-effort text scanner, truncated; `matcher` is additionally run through the literal allowlist. Any other key, and any nested dict or list under an unknown key, is replaced by the placeholder and never recursed into.
3. **Rule previews are best effort.** Rule files are the owner's own prose and already go to the model verbatim; the preview keeps the blocklist scanner and the 8000-character cap. This is an accepted limit, not a defect, and the Rules section says so.
4. **No unsalted digest of the raw hook.** The per-hook `content_sha256` over the raw hook text is replaced by the file fingerprint already kept in `versions`, so a weak secret cannot be confirmed offline against a hash of a masked command.

## Consequences

Tests assert with random keys and the reviewed reproductions that no input value appears verbatim unless allowlisted; the property test that accepted "≤40 alphanumeric characters" is replaced. Over-masking of harmless subcommands with digits (for example `py3`) is accepted.
