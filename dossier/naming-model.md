# Naming model

Version 1.0.1 — 2026-09-26

## Purpose and scope

Canonical names for files and directories, Python identifiers, JS/CSS/HTML identifiers, environment variables, JSON/SQLite keys, tests, error codes and docs. Rule of thumb: **each language's native convention; external schemas keep their own names.** Agent and skill names are governed by [the canonical agent and skill model](canonical-agents-skills-model.md), which stays the authority for them.

## Rules

| Item | Rule | Examples from this repo | Exceptions |
|---|---|---|---|
| Python packages/modules | snake_case | `agent_service/mcp_bridge.py`, `control/local_models.py`; `Adapters/` → `adapters/` | — |
| Python scripts | snake_case | `scripts/gauntlet-15.py` → `scripts/gauntlet_matrix.py`; `scripts/vendor-file-icons.py` → `scripts/vendor_file_icons.py` | — |
| Shell, JS, CSS, HTML, Markdown files | kebab-case | `scripts/test-ui.sh`, `agent_service/setup-mcp.sh`, `scripts/admin-live-check.cjs`, `*.spec.cjs` | Conventional names (`README.md`, `AGENTS.md`, `LICENSE`, `index.html`) |
| Docs in `dossier/` and `docs/` | kebab-case, lowercase, no dates in names | `dossier/conversation-execution-mode.md`, `dossier/releases/v0.5.0.md` | Existing UPPER-CASE dated docs (`docs/ACCESS-MENU-20260919.md`) are grandfathered; `UC-00N-*` use-case ids |
| Runtime/data directories | snake_case | `state/`; `local-ai/` → `local_ai/` with a one-time migration on startup | `sem-projeto` is not a directory (see JSON keys) |
| Tests | pytest `tests/test_<feature>.py`; Playwright `tests/<area>-<feature>.spec.cjs` (run by `scripts/test-ui.sh`); persona campaigns `tests/personas/<id>-<slug>.spec.cjs`; opt-in real-provider tests `tests/live/test_live_<subject>.py` | `tests/test_gemini_policy.py`, `tests/admin-sidebar.spec.cjs` | — |
| Python classes | PascalCase noun; role suffix when the class is a layer role: `*Service`, `*Repository`, `*Adapter`, `*Error`, `*Config` | error classes end in `Error` and derive from `HarnessError` (to be added) | Framework subclasses keep the framework's naming |
| Python functions, methods, variables | snake_case; functions are verbs (`load_profile`, `build_runtime_config`); booleans are predicates (`is_ready`, `has_credentials`); no abbreviations (`cfg`, `svc`, `st`, `proc` → `config`, `service`, `state`, `process`) | — | Loop indices; `e` in `except ... as e` |
| Constants | UPPER_SNAKE and immutable (`tuple`, `frozenset`, `MappingProxyType`) | — | A mutable module-level dict/set is configuration, not a constant: name it snake_case |
| Environment variables | Public ones prefixed `TAIL_HARNESS_`: `TAIL_HARNESS_ROOT`, `TAIL_HARNESS_VENV`, `TAIL_HARNESS_AGENT_URL`, `TAIL_HARNESS_AGENT_CLIENT`, `TAIL_HARNESS_AGENT_CONFIG`, `TAIL_HARNESS_LOG_LEVEL`, `TAIL_HARNESS_LIVE` | Already compliant: `TAIL_HARNESS_API_KEY`, `TAIL_HARNESS_LOCAL_KEY`, `TAIL_HARNESS_WHISPER_DIR` | Legacy `LOCAL_AGENT_URL`, `LOCAL_AGENT_CLIENT`, `LOCAL_AGENT_CONFIG`, `TH_VENV` accepted as deprecated aliases only through `control/env.py`, which logs a deprecation warning. Shell-script-local variables use `TH_` (`TH_STATE`, `TH_PID`) and are never a contract. Test-harness inputs `ADMIN_URL`, `HARNESS_URL`, `PLAYWRIGHT_MODULE`, `GAUNTLET_ROUND`, `EVAL_OUTPUT` are grandfathered and documented as such |
| JSON/config/state keys, SQLite | snake_case keys; plural table names; foreign keys `<entity>_id` | `project_id`; tables `jobs`, `approval_rules`, `registered_projects` | External schemas: protocol sentinel `sem-projeto` (persisted "No project" scope id — never rename); llama.cpp flag keys in local profiles (`ctx-size`, `n-gpu-layers`, `cache-ram`… mirror the `llama-server` CLI); Gemini CLI settings keys (`selectedType`, `enforcedType`… in `adapters/gemini/policy.py`); provider API payloads |
| Error codes returned to clients | snake_case strings | `image_validation_unavailable`, `backend_unavailable` | — |
| JS | camelCase functions/variables; PascalCase constructors; UPPER_SNAKE constants; DOM ids, classes and `data-*` attributes kebab-case | `agent_service/ui.js`, `control/admin.js` | Vendor bundles (`markdown-it.min.js`, `tabler.min.js`) |
| CSS custom properties | `--th-<role>` for project tokens | `--th-accent`, `--th-bg` | Vendor tokens (`--tblr-*` from Tabler) untouched; unprefixed `--accent`, `--bg`, `--panel` migrate to `--th-*` |
| Agents and skills | `objective_context_role` | `test_tail-harness_engineer`, `test-gauntlet_tail-harness_procedure` | Defer to [canonical-agents-skills-model.md](canonical-agents-skills-model.md) |
| Git | Conventional Commits `type(scope): subject` in English, body Why / How / What / Validation; branches `type/short-kebab-topic` | `chore/english-github-readiness` | — |

## Layering vocabulary

Class suffixes follow the target layout, so a name states its layer.

| Layer | Suffix | Meaning |
|---|---|---|
| routes | — (functions) | HTTP/MCP entry points: parse the request, call a service, shape the response; no business rules. |
| services | `*Service` | Business rules and orchestration of one capability; depends on repositories and adapters, never on the HTTP layer. |
| persistence | `*Repository` | Reads and writes one kind of entity (JSON state or SQLite); no business decisions. |
| adapters | `*Adapter` | Wraps one external system (provider CLI, API, llama.cpp) behind a harness-owned interface. |
| tools | `*Tool` | One capability exposed to a model (MCP or native tool), with its own schema and validation. |

Supporting suffixes: `*Config` for immutable settings objects, `*Error` for exceptions deriving from `HarnessError`.

## Migration table

| Old | New | Status |
|---|---|---|
| `Adapters/` (package `Adapters`) | `adapters/` (package `adapters`) | done |
| `scripts/gauntlet-15.py` | `scripts/gauntlet_matrix.py` | done |
| `scripts/vendor-file-icons.py` | `scripts/vendor_file_icons.py` | done |
| `local-ai/` (runtime dir) | `local_ai/` (one-time migration on startup) | done |
| `LOCAL_AGENT_URL` | `TAIL_HARNESS_AGENT_URL` (old name as deprecated alias) | done |
| `LOCAL_AGENT_CLIENT` | `TAIL_HARNESS_AGENT_CLIENT` (old name as deprecated alias) | done |
| `LOCAL_AGENT_CONFIG` | `TAIL_HARNESS_AGENT_CONFIG` (old name as deprecated alias) | done |
| `TH_VENV` | `TAIL_HARNESS_VENV` (old name as deprecated alias) | done |
| — | `control/env.py` (alias resolver with deprecation warning) | done |
| — | `HarnessError` base class for `*Error` classes (`agent_service/errors.py`) | done |
| CSS `--accent`, `--bg`, `--panel` (and `--muted`, `--line`, `--tint`, `--control`, `--surface`, `--border`, `--text`, `--admin-sidebar-width`, `--app-topbar-height`, `--reading-size`, `--sidebar-width`, `--conversation-header-bottom`) | `--th-accent`, `--th-bg` (unused), `--th-panel`, `--th-muted`, `--th-border`, `--th-soft`, `--th-panel`, `--th-panel`, `--th-border`, `--th-text`, `--th-admin-sidebar-width`, `--th-app-topbar-height`, `--th-reading-size`, `--th-sidebar-width`, `--th-conversation-header-bottom` — the local, already-shadowed `--accent`/`--muted`/`--line` definitions in `agent_service/ui.css` became `--th-accent-tone`/`--th-muted-tone`/`--th-line-tone` (distinct suffix) since `--th-accent`/`--th-muted` already exist with the real palette value | done |
| Abbreviated identifiers (`cfg`, `svc`, `st`, `proc`) | `config`, `service`, `state`, `process` | planned |

## Enforcement

`scripts/check_conventions.py` runs in CI and locally:

1. File-name lint: applies the file and directory rules above to `git ls-files`, honoring the grandfathered list.
2. Portuguese stop-word scan over tracked text files, with an explicit allowlist (`sem-projeto`, proper names); the pt-BR section of `README.md` is excluded.

A failing check blocks the merge; new exceptions are added to the allowlist in the same commit that justifies them.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.1 | 2026-09-26 | P3 migration executed: `Adapters/`→`adapters/`, script renames, `local-ai/`→`local_ai/` with a startup migration, `TAIL_HARNESS_*` environment variables with deprecated legacy aliases via `control/env.py`, unprefixed CSS custom properties renamed to `--th-*`, compatibility-import shims removed, and `scripts/check_conventions.py`'s grandfather entries for the migrated names dropped. |
| 1.0.0 | 2026-09-26 | Initial naming model: rules, layering vocabulary, migration table and enforcement plan. |
