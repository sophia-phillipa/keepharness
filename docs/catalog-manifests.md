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
check or installs a dependency. An `environment` check is a host prerequisite;
integration credential availability is validated through the separate binding
contract at dispatch, without exporting vault values into the host environment.
Failed prerequisites appear in the palette.

Admin provisioning creates a separate `catalog_runtime/<catalog_id>/` directory
under the control state directory. `writable_state` names directories below that
root; they are never created in the catalog tree. Python environments live in its
`venv/` subdirectory. A configured requirements file is installed only by explicit
provisioning, using the harness installer’s pip with the catalog interpreter as its explicit target. Preflight compares its SHA-256 digest
with the last successful installation. Runtime receives
`KEEPHARNESS_CATALOG_STATE_<UPPERCASE_ID>`; hyphens in the ID become underscores.
Multiple catalogs requiring conflicting Python environments or working directories
must be invoked separately. Maintenance commands hide only when
`provisions_maintenance` is true and all manifest prerequisites pass.

Project settings retain the `catalogs` list and add an optional `catalog_pins` map
from catalog ID to `{ "commit": "<full commit>", "root": "<managed worktree>" }`.
An owner can prepare a detached Git worktree beneath
`catalog_pins/<catalog_id>/<commit>/`. Files have write bits removed. Each update
prepares another worktree, returning a revision diff for all tracked files
(including manifest resources and runtime dependencies) before the project
reference changes; previous trees remain available to existing references.
The update operation fetches by default. Pin operations suppress repository Git
hooks. A checkout with modified content fails validation.

Allowed hooks are explicit native pre-run executable scripts. They require the
effective hooks grant; otherwise the run records that they were skipped. Only the
listed scripts execute, with the catalog runtime environment and working directory
(or script directory by default), a 30-second timeout and 32 KiB per output stream.
Failures stop dispatch, and loaded credential values are redacted from hook events.
Provider and global hooks remain disabled for catalog runs. Isolated execution
cannot run these native hooks.

The trust digest covers each hook script's own bytes. Right before each run the
hooks are hashed once and exactly those bytes are executed from a private
in-memory copy, so replacing or rewriting the file after the check changes
nothing. Files a hook loads or runs itself (sourced scripts, interpreter
modules, data) are outside the digest and remain the catalog owner's trust.

Pins disable the catalog update command and omit catalog hooks from runtime.
Runs record each active catalog's commit, dirty state and pin status. Permission
bits prevent accidental edits, but do not provide an OS security boundary against
another process running as the same user; executor confinement remains necessary.
Writable runtime state is separate from these retained revisions.

Drift compares canonical `resource_id`, resource revision, dependency revisions,
and catalog commit across named snapshots. It is a pure comparison and does not
change any project reference or checkout.
