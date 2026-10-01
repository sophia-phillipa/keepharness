# Integration credentials

Configure nonsecret integration contracts separately from their credentials. Admin
stores credentials in the private `harness.secrets.json` vault and only returns
binding names and field names. Writes replace the file atomically with mode 0600;
concurrent owner writes serialize through a lock. Existing Jira credential files
remain readable through the same store implementation. To share the vault with
Jira publication, set `secret_vault_path` (an explicit legacy
`effect_credentials_path` takes precedence).

An advisory contract identifies its integration, eligible provider `consumers`,
`environment` mapping from environment variable names to credential field names,
explicit `precedence` (`vault` or `environment`), and `mediated: false`. Optional
`endpoint` is nonsecret HTTPS configuration. A binding names the integration,
`project_id`, optional attached `catalog_id`, and `credential_binding`. Unattached
catalogs and other projects cannot use that binding. Conflicting environment
values fail closed. Harness authority and executable-loader environment names
cannot be used as credential destinations.

Native Codex, Claude, Gemini and DeepSeek receive advisory values only in the
execution's subprocess environment, without changing the harness environment or
putting credentials in command arguments. Scoped and local adapter modes reject
advisory credential injection with `integration_environment_unsupported`; their
isolated environments need an adapter-specific transport before this is supported.
The provider may access advisory credentials: this mode does not enforce approval.

`mediated: true` never injects credentials into a provider. The supported Jira
publication operation uses the effect executor and its existing immutable,
single-use human approval. Configuring a generic mediated contract alone does not
implement a driver. When integration bindings are present, the executor checks the
current project/catalog scope before preparing, dispatching and reconciling an
effect. The legacy contract format remains accepted when the bindings key is absent.

0600 is an owner-file permission, not isolation from unrestricted same-user shells.
An enforced execution additionally requires that all credential stores remain
outside worker mounts. Native execution and unrestricted shell remain advisory.
Pinned catalog files and prompt policy likewise do not isolate a same-user native
shell that can change permissions. Do not describe these modes as enforced.

## Adapting scripts with embedded credentials

Remove literal credentials and their fallback values from the script. Read each
required value from its declared environment variable and fail with a neutral
missing-configuration message. Keep endpoint configuration separate. Never print
authentication headers, environment dumps, credentials, or raw remote responses.
Rotate any credentials previously committed to a repository using the provider's
owner controls; removing them from the latest file does not remove history.

Declare the script's integration contract in the catalog manifest and provision a
project/catalog binding in Admin. Test it with synthetic values in a temporary
project and a local fake service. For operations requiring enforced human approval,
replace the direct publication call with the supported mediated executor operation;
plain environment injection remains advisory even if the script displays a gate.

Runtime output is sanitized for loaded credential values before events, results,
panels and diagnostics are persisted. Streaming answer/reasoning fragments retain
credential prefixes across deltas to prevent split-value reconstruction. This is
literal-value redaction, not a defense against intentional encoding or transformation
of credentials by a process that already received them. Provider-owned native
session files are outside the harness redaction boundary.

Catalog runtime prerequisites apply to selected resources. Scoped/local executions
report `catalog_runtime_mode_unsupported` for manifest cwd, Python environment or
writable-state requirements until their adapter has a corresponding mount and
environment transport. Context/rule-only catalogs remain available. Native `allowed_hooks` are trusted executable pre-run scripts. Only an explicit
hooks grant permits execution; each script receives the catalog cwd (or its own
parent directory) and execution-local environment. Runs fail on a nonzero exit,
30-second timeout, or output above 32 KiB per stream. Captured output is redacted.
Provider/global hooks remain disabled for catalog runs. Pinned catalogs suppress
pre-run hooks and maintenance updates. Isolated modes report the missing hook
transport in the resource palette.

Queue leases cover project/workspace/model roots, mutable catalog roots, catalog
runtime directories and project-scoped work-item keys. They coordinate jobs in one
harness process; unrelated applications or a second harness process are outside
this ownership boundary. Provider lanes default to capacity one; the inference
capacity guard also remains around every Maestro planner and step. Conversation
turns stay serial, and each queued wait records its reason. Changing a running
job's work-item tag is rejected until its write lease is released.
