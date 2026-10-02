# AGENTS.md

Guidance for AI coding agents (Codex, Claude Code, others) working on this repository. For installing and using the app, see [README.md](README.md).

## Project layout

- `control/` — local admin panel (Starlette): discovery, provider setup, local model profiles, installer. Entry point `python -m control`. `server.py` builds the app, `routes.py` is the HTTP layer (local-only guard, `/api` dispatch tables), `manager.py` owns settings, provider checks and the harness process, `runtime_config.py` holds the runtime-config builders and `persistence.py` the state files.
- `agent_service/` — conversation harness: queues, authorization, history, attachments, MCP bridge (`mcp_bridge.py`), Maestro coordinator. `app.py` builds the app, `routes/` is the HTTP layer, `services/` holds the business rules (`ConversationService`, `ProjectService`, the queue worker), `persistence/` the SQLite repositories and schema migrations, `config.py` the read-only constants and paths, `errors.py` the `HarnessError` hierarchy.
- `adapters/` — one package per provider (`codex`, `claude`, `deepseek`, `gemini`, `local`) with its implementation and `specs/models/` records. See [adapters/README.md](adapters/README.md).
- `tail_ui/` — shared UI assets (themes, components).
- `profiles/` — suggested local model profiles shipped with the package.
- `dossier/` — specifications, use cases, research and release notes (`dossier/releases/v<VERSION>.md`).
- `docs/` — design notes per feature.
- `tests/` — pytest (`test_*.py`) and Playwright browser regressions (`*.spec.cjs`).

## Setup and tests

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
PYTHON="$PWD/.venv/bin/python" ./scripts/test-ui.sh   # needs Node.js and Playwright with Chromium
.venv/bin/python -m build
```

`scripts/test-ui.sh` starts temporary servers on free test ports with isolated state and runs every `tests/*.spec.cjs` and `tests/personas/*.spec.cjs`. Fixtures never call cloud inference or download models.

## Continuous integration

CI must stay green. `.github/workflows/ci.yml` runs conventions, formatting/lint, unit tests on Python 3.11 and 3.14, browser tests, and package build/install checks (six jobs including the Python matrix). After every push or pull request, check the current commit with `gh run list` and inspect failures with `gh run view <run-id> --log-failed`; wait for completion with `gh run watch <run-id>` or `gh pr checks --watch`. Fix a red run at its root cause before starting other work. Never disable, skip, narrow, or make jobs/tests non-blocking to obtain green. Feature-branch pushes run CI once a pull request is open.

Run these commands from an activated disposable virtual environment to mirror the jobs:

```sh
python scripts/check_conventions.py
python -m pip install ruff
ruff format --check .
ruff check .
npx --yes prettier@3 --check "control/*.{js,css}" "agent_service/*.{js,css}" "tests/*.cjs" "scripts/*.cjs"
python -m pip install '.[test]'
# Repeat in Python 3.11 and 3.14 environments; media sandbox tools must work.
TAIL_HARNESS_REQUIRE_MEDIA_SANDBOX=1 python -m pytest -rs --junitxml=reports/unit-local.xml
npm install --no-save playwright
npx playwright install --with-deps chromium
PYTHON="$(command -v python)" ./scripts/test-ui.sh
python -m pip install build
python -m build
python -m pip install dist/*.whl
(cd /tmp && tail-harness --help)
```

The unit and browser jobs require `ffmpeg`, `bubblewrap`, and `poppler-utils`; bubblewrap needs working unprivileged user namespaces. Browser tests also require Node.js and Chromium. Use temporary state and test resources; do not reconfigure a shared host to match the disposable CI runner.

## Conventions

- Everything in the repository is in English: code, identifiers, comments, UI strings, docs and commit messages. The only exception is `README.pt-BR.md`, the Portuguese counterpart of `README.md`, which must stay in sync (same headings, same order) with the English file in the same commit.
- `sem-projeto` is a persisted protocol identifier (the "No project" scope); do not rename it.
- Test-driven changes: failing test → minimal implementation → run the affected tests. Run the full Python and browser suites before a release or merge to `main`.
- Every new version needs `dossier/releases/v<VERSION>.md` (behavior, acceptance criteria, migration notes, actual validation) and matching version identifiers.
- Commits follow Conventional Commits (`type(scope): subject`), with a Why / How / What body.
- Never commit secrets, local state, model weights or per-server runtimes (`local_ai/`, `state/`, `*.gguf`, `.env*`). Personal agent settings stay local (`CLAUDE.local.md`, `.claude/settings.local.json`, `.codex/`).
- Agent and skill names follow [the canonical model](dossier/canonical-agents-skills-model.md); project skills live in `.agents/skills/`.
- All other names (files, identifiers, environment variables, keys, tests, error codes, docs) follow [the naming model](dossier/naming-model.md).
