# Catalog manifests and pins

A trusted catalog may provide `harness.catalog.json` at its root. Existing catalogs
without a manifest retain conventional `commands/`, `agents/`, `skills/`, `rules/`,
`context/` and `workflows/` discovery.

```json
{
  "version": 1,
  "resources": {"command": ["ports"], "workflow": ["pipelines"]},
  "context": ["knowledge/overview.md"],
  "rules": ["policies/review.md"],
  "runtime": {"venv": true, "requirements": "requirements.txt"},
  "cwd": ".",
  "writable_state": ["memory", "reports"],
  "allowed_hooks": [],
  "integrations": [],
  "preflight": [{"executable": "git", "hint": "Install Git before invoking this catalog."}],
  "provisions_maintenance": true
}
```

Paths must be relative and remain within the catalog after resolving symlinks.
`resources` maps resource kinds to additional directories or files; supported kinds
are `command`, `agent`, `skill`, `rule`, `context`, and `workflow`. Existing provider
format constraints still apply. Context and rules are reference files, never slash
commands. Preflight checks accept exactly one of `file`, `executable`, or
`environment`, with an optional explanatory `hint`. Discovery never executes a
check or installs a dependency. Failed prerequisites appear in the palette.

Admin provisioning creates a separate `catalog_runtime/<catalog_id>/` directory
under the control state directory. `writable_state` names directories below that
root; they are never created in the catalog tree. Python environments live in its
`venv/` subdirectory. A configured requirements file is installed only by explicit
provisioning, using that environment's pip. Preflight compares its SHA-256 digest
with the last successful installation. Runtime receives
`TAIL_HARNESS_CATALOG_STATE_<UPPERCASE_ID>`; hyphens in the ID become underscores.
Multiple catalogs requiring conflicting Python environments or working directories
must be invoked separately. Maintenance commands hide only when
`provisions_maintenance` is true and all manifest prerequisites pass.

Project settings retain the `catalogs` list and add an optional `catalog_pins` map
from catalog ID to `{ "commit": "<full commit>", "root": "<managed worktree>" }`.
An owner can prepare a detached Git worktree beneath
`catalog_pins/<catalog_id>/<commit>/`. Files have write bits removed. Each update
prepares another worktree, returning a resource revision diff before the project
reference changes; previous trees remain available to existing references.
The update operation fetches by default. Pin operations suppress repository Git
hooks. A checkout with modified content fails validation.

Pins disable the catalog update command and omit catalog hooks from runtime.
Runs record each active catalog's commit, dirty state and pin status. Permission
bits prevent accidental edits, but do not provide an OS security boundary against
another process running as the same user; executor confinement remains necessary.
Writable runtime state is separate from these retained revisions.

Drift compares canonical `resource_id`, resource revision, dependency revisions,
and catalog commit across named snapshots. It is a pure comparison and does not
change any project reference or checkout.
