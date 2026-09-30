# Synthetic adapter conformance squad

This fixture contains only marker-based, non-client data for opt-in live CLI
probes. `tests/live/conformance.py` installs its resources into disposable
provider homes and projects with symlinks. The fixture source remains outside
the native command, agent, skill, prompt, and rule category roots.

The two hooks append distinct markers to a temporary log. Despite its name,
`global_auto_update_hook.py` performs no update, network request, or other
effect. It represents an unrelated user hook so the probes can distinguish
project-only hook scope from merged user and project scope.

Live inference is disabled unless `HARNESS_LIVE_PROBES=1` is set. Authentication
artifacts are copied only when a provider runner explicitly requests them; user
settings, provider routing, session identifiers, and other parent environment
state are not inherited.

## Validation on 2026-09-30

- `.venv/bin/python -m pytest -q`: **1139 passed, 37 skipped, 12 subtests passed**
  (103 existing deprecation warnings), 45.16 seconds.
- Nineteen opt-in cases encode the observed Claude/Codex matrix. All nineteen
  recorded real-run reports passed their evidence assertions. Two representative
  cases were also rerun through pytest: Codex skills (**1 passed**, 4.66 seconds)
  and Claude AskUserQuestion (**1 passed**, 5.64 seconds).
- Five offline checks cover external symlinks, cleanup, environment stripping,
  private auth copies, redaction, and the opt-in guard. The native `SKILL.md`
  basename has one exact exception in `.conventions-allow`.
- The preexisting sprite test was made independent of the checkout directory
  name by copying the real sprite into a temporary `tail-harness` fixture.
- `python3 -m venv .venv` lacked `ensurepip` on this host. The installed `uv`
  completed the local environment with `uv venv --seed --allow-existing .venv`,
  followed by `.venv/bin/python -m pip install -e '.[test]'`.

The protected-file audit found unchanged hashes for the real Codex config and
Claude settings/settings.local files. The real `.claude.json` changed at
17:52:59 UTC, between this task's recorded Claude executions (previous events
ended 17:51:56; next began 17:54:08). Attribution is unestablished in the shared
host; the file was not restored or edited by this task. Therefore this audit
cannot certify that all global state stayed unchanged. The provider probes
passed only temporary HOME/config paths and the helper copied credentials,
never settings.
