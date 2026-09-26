# AGENTS.md

Guidance for AI coding agents (Codex, Claude Code, others) working on this repository. For installing and using the app, see [README.md](README.md).

## Project layout

- `control/` — local admin panel (Starlette): discovery, provider setup, local model profiles, installer. Entry point `python -m control`.
- `agent_service/` — conversation harness: queues, authorization, history, attachments, MCP bridge (`mcp_bridge.py`), Maestro coordinator.
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

`scripts/test-ui.sh` starts temporary servers on ports 18094/18095 with isolated state and runs every `tests/*.spec.cjs`. Fixtures never call cloud inference or download models.

## Conventions

- Everything in the repository is in English: code, identifiers, comments, UI strings, docs and commit messages. The only exception is the pt-BR section of `README.md`, which must stay in sync with the English section in the same commit.
- `sem-projeto` is a persisted protocol identifier (the "No project" scope); do not rename it.
- Test-driven changes: failing test → minimal implementation → run the affected tests. Run the full Python and browser suites before a release or merge to `main`.
- Every new version needs `dossier/releases/v<VERSION>.md` (behavior, acceptance criteria, migration notes, actual validation) and matching version identifiers.
- Commits follow Conventional Commits (`type(scope): subject`), with a Why / How / What body.
- Never commit secrets, local state, model weights or per-server runtimes (`local-ai/`, `state/`, `*.gguf`, `.env*`). Personal agent settings stay local (`CLAUDE.local.md`, `.claude/settings.local.json`, `.codex/`).
- Agent and skill names follow [the canonical model](dossier/canonical-agents-skills-model.md); project skills live in `.agents/skills/`.
- All other names (files, identifiers, environment variables, keys, tests, error codes, docs) follow [the naming model](dossier/naming-model.md).
