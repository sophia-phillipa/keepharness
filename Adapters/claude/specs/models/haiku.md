# Claude Code alias: haiku

**Responsible agent:** `integrate-claude_tail-harness_engineer`.

`haiku` and its resolved version identifiers come from the installed Claude CLI catalog. Effort choices are taken from `supportedEffortLevels`; without that capability only `configured` is offered. Explicit effort selections are passed through `--effort`. See [dynamic catalog](catalogo-cli.md).

The official CLI reference states that `--model` accepts model aliases, including `haiku`, or a full model name: <https://code.claude.com/docs/en/cli-usage>. It does not guarantee account entitlement. The exact resolved model is provider-controlled and was not validated here.

Input, output, sessions, streaming, errors, cancellation and authentication follow the [Claude adapter contract](../README.md). Recheck after a CLI update or if the provider removes/retargets the alias.
