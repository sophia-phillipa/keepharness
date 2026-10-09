# D-048 — Fail-closed masking of hook commands and rule previews

Status: accepted. Date: 2026-10-08. Decided by: orchestrator under Sophia's standing autonomy (JEV abstained at 0.67 below the 0.90 high-risk threshold). Applies to issue #61.

## Context

The read-only Hooks and Rules sections show hook commands, headers, environment entries and rule previews. Four rounds of a blocklist redactor (regex, then a word scanner) each leaked credentials. The last security review reproduced 13 new leaks: secrets inside `sh -c` scripts, `--header=` values, JSON bodies, `$'…'` quoting and line continuations, plus older gaps such as `-u user:password`, `--passphrase` and a token used as the URL user. A blocklist that guesses which words are secret cannot be made complete.

## Decision

Mask by default instead of guessing secrets. The UI and API receive the executable and option names; every argument value is replaced by a placeholder unless it is a plain path or a short allowlisted literal. That covers quoted strings, the right side of `--opt=value`, header values, environment values, URL userinfo and words that follow an option. The full command text is never sent. Each item keeps its source file path, so the owner can read the full command in their own editor.

## Alternatives

- Layered blocklist (keep the scanner, recurse into quoted scripts, add regex and JSON passes, a short-flag table and a wider word list). Rejected: open-ended, and four rounds already leaked.
- Hide commands entirely (event, matcher and executable only). Rejected: it falls below the native Codex Hooks page and Claude `/hooks` floor that #61 must meet.

## Consequences

Command display becomes structural rather than literal, for example `curl -H ‹value› --data ‹value› https://‹host›/…`. Tests assert that no input value appears verbatim unless it is allowlisted, rather than listing secret patterns. The #61 release notes record the change. Over-masking is accepted; under-masking is a defect.
