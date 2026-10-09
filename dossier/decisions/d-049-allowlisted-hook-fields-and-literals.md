# D-049 — Allowlisted hook fields and literals; rule previews stay best effort

Status: accepted. Date: 2026-10-09. Decided by: orchestrator under Sophia's standing autonomy (JEV abstained at 0.60 below the 0.80 medium-risk threshold; Opus security review of 51d19b3 returned CHANGES-REQUIRED). Applies to issue #61. Refines D-048; does not replace it.

## Context

The D-048 implementation masked quoted strings, option values, headers, environment values and URL userinfo, but two gaps remained in the review of 51d19b3:

- Positional words of up to 40 alphanumeric characters, and anything containing a `/` up to 120 characters, passed as "literals". A 40-character AWS secret, an `sk-…` token as the first word, a short JWT and a token inside a path all reached the UI.
- Hook fields outside the known command, named-value and URL keys fell back to the old blocklist scanner, so a future or plugin-defined key (`body`, `data`, `extra`, `cmdArgs`, `target`, `webhook`) could carry a secret raw.

The D-048 title also mentions rule previews, but its body only covers commands.

## Decision

1. **Literal allowlist.** After the first word (the executable), a positional word is shown only when it is a path with an explicit prefix (`/`, `./`, `../`, `~/`) whose segments are short and not hex- or base64-like, or a lowercase subcommand without digits (`^[a-z][a-z-]{0,19}$`). Everything else becomes the placeholder. Long option names are limited to `[a-z][a-z0-9-]{0,30}`; env and header names to `[A-Za-z_][A-Za-z0-9_-]*` and not token-shaped.
2. **Field whitelist.** Inside a hook object, only known harmless scalars pass unchanged (type, timeout, async, enabled, event, matcher kind, source path, trust status, display order and env var names). Known free-text fields (`prompt`, `statusMessage`, `description`, `matcher`) pass through the best-effort text scanner, truncated; `matcher` is additionally run through the literal allowlist. Any other key, and any nested dict or list under an unknown key, is replaced by the placeholder and never recursed into.

   Every field the code accepts, and the shape rule that lets a value through (anything else becomes the placeholder; keys are matched case-insensitively, and a key that is not name-shaped or looks like a token is dropped; a key that looks like a secret name is shown as `[REDACTED]`):

   | Field | Shape rule |
   |---|---|
   | `type`, `handlerType` | closed enum: `command`, `prompt`, `agent`, `http`, `mcp_tool` |
   | `trustStatus` | closed enum: `trusted`, `untrusted`, `modified`, `managed` |
   | `source` | closed enum: `user`, `project`, `local`, `managed`, `plugin`, `system`, `mdm`, `sessionFlags`, `unknown` |
   | `status` | closed enum: `configured`, `enabled`, `disabled`, `pending review`, `pending project trust`, `unknown` |
   | `tool`, `server`, `pluginId`, `origin`, `event`, `eventName` | identifier-shaped name (`[A-Za-z0-9_.@:-]{1,64}`), not token-like, and without digits |
   | `model` | identifier-shaped; each part split on `-`, `.`, `:`, `@` is all letters, all digits or a mixed part of at most 6 characters; fewer than 3 lower-to-upper transitions in the whole value |
   | `sourcePath` | a path with an explicit prefix whose segments pass `is_path` |
   | `timeout`, `timeoutSec`, `displayOrder`, `additionalContextLimit` | number (not a bool) |
   | `async`, `enabled`, `isManaged`, `disableAllHooks` | bool |
   | `command`, `commandLine`, `cmd`, `args`, `argv`, `run`, `script`, `exec` | command masking (D-048) |
   | `env`, `environment`, `envVars`, `env_vars`, `headers`, `httpHeaders`, `http_headers` | named-value masking: names kept when name-shaped, values masked |
   | `url`, `uri`, `endpoint` | URL masking (userinfo and query values masked) |
   | `matcher` | literal allowlist |
   | `prompt`, `statusMessage`, `description` | text scanner, truncated to 300 characters |
   | `allowedEnvVars` | list of up to 100 name-shaped, non-token-like names |
3. **Rule previews are best effort.** Rule files are the owner's own prose and already go to the model verbatim; the preview keeps the blocklist scanner and the 8000-character cap. This is an accepted limit, not a defect, and the Rules section says so.
4. **No unsalted digest of the raw hook in the API.** The raw-hook digest stays server-side only (`StateItem.content_digest`, stripped in `snapshot_json`), so a weak secret cannot be confirmed offline against a hash of a masked command while hook changes are still detected. The digest is persisted in the owner-only state files (`provider-state-seen.json`, receipts, mode 0600).

## Consequences

Tests assert with random keys and the reviewed reproductions that no input value appears verbatim unless allowlisted; the property test that accepted "≤40 alphanumeric characters" is replaced. Over-masking of harmless subcommands with digits (for example `py3`) is accepted.

## Accepted residues

- Legitimate names with digits, such as server `context7` or pluginId `tool2@market`, are masked (fail-closed).
- A lowercase-only word shorter than 20 characters in subcommand position still passes (for example `sshpass mysecretpass`, `echo letmein`).
- At most 3 subcommand literals are shown after the executable (`git push origin main`); later plain words are masked.
- Codex `currentHash` is no longer shown (assumed to hash the hook definition).
- The Codex snapshot fingerprint now derives from `content_digest` and still reaches the API mixed with layers and flags.
- The Codex fingerprint also mixes the raw-byte digests of `hooks.json` and `AGENTS.md` (pre-existing, not introduced by D-048 or D-049).
