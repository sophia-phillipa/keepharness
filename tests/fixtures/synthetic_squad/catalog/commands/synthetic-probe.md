---
description: Report synthetic command arguments without external effects
argument-hint: "<probe arguments>"
allowed-tools: Bash
model: haiku
---

Reply with exactly `SYNTHETIC_COMMAND_ARGUMENTS=$ARGUMENTS` and do not run the
example below. It exists to verify that command content survives discovery.

```bash
printf '%s\n' "$HOME"
```
