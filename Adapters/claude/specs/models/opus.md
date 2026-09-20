# Claude Code alias: opus

**Responsible agent:** `provedor_claude`.

`opus` is one of the aliases explicitly exposed by `control/server.py` after successful Claude authentication. The harness invokes it with `--model opus` and exposes effort only as `configured`.

The official CLI reference states that `--model` accepts model aliases, including `opus`, or a full model name: <https://code.claude.com/docs/en/cli-usage>. It does not guarantee account entitlement. The exact resolved model is provider-controlled and was not validated here.

Input, output, sessions, streaming, errors, cancellation and authentication follow the [Claude adapter contract](../README.md). Recheck after a CLI update or if the provider removes/retargets the alias.
