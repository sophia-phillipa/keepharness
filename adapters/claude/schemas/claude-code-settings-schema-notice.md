# Claude Code settings schema (vendored)

- File: `claude-code-settings.schema.json`
- Source: https://json.schemastore.org/claude-code-settings.json (redirects to https://www.schemastore.org/claude-code-settings.json)
- Fetched: 2026-10-07 (230217 bytes), unmodified
- License: SchemaStore is Apache-2.0 (https://github.com/SchemaStore/schemastore)
- Draft-07; use `jsonschema.Draft7Validator`. `additionalProperties` is true at the top level, so unknown keys pass.
- Used by the Claude provider-state adapter to validate `settings.json` bytes before a `skillOverrides` write (issue #40).
