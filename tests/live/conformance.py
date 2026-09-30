"""Shared isolation and evidence helpers for opt-in live adapter probes."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "synthetic_squad"
_SECRET_ARGUMENTS = re.compile(r"(?i)(token|secret|password|api[_-]?key|authorization)")
_SECRET_FLAGS = {
    "--api-key",
    "--authorization",
    "--oauth-token",
    "--password",
    "--secret",
    "--token",
}
_RUNTIME_ENVIRONMENT = {
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
}


@dataclass(frozen=True)
class IsolatedFixture:
    """Paths and environment for one disposable provider execution."""

    root: Path
    home: Path
    project: Path
    claude_config_dir: Path
    codex_home: Path
    evidence_dir: Path
    env: dict[str, str]


def _link(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(source)


def _copy_auth(source: Path, destination: Path) -> None:
    if not source.is_file():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    destination.chmod(0o600)


def _hook_settings(command: Sequence[str]) -> dict[str, object]:
    return {
        "hooks": {
            "UserPromptSubmit": [
                {
                    "matcher": "*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": " ".join(command),
                            "timeout": 10,
                        }
                    ],
                }
            ]
        }
    }


@contextlib.contextmanager
def isolated_fixture(
    *, copy_claude_auth: bool = False, copy_codex_auth: bool = False
) -> Iterator[IsolatedFixture]:
    """Install the synthetic squad into temporary provider homes by symlink.

    Authentication is copied only when a caller explicitly opts in. The source
    paths are resolved before constructing the isolated environment and neither
    their content nor their filenames are included in returned evidence.
    """

    real_home = Path.home()
    claude_auth = real_home / ".claude" / ".credentials.json"
    codex_auth = real_home / ".codex" / "auth.json"
    with tempfile.TemporaryDirectory(prefix="harness-conformance-") as directory:
        root = Path(directory)
        home = root / "home"
        project = root / "project"
        claude_config = root / "claude-config"
        codex_home = root / "codex-home"
        evidence = root / "evidence"
        for path in (home, project, claude_config, codex_home, evidence):
            path.mkdir(parents=True)

        catalog = FIXTURE_ROOT / "catalog"
        _link(
            catalog / "commands" / "synthetic-probe.md",
            project / ".claude" / "commands" / "synthetic-probe.md",
        )
        _link(
            catalog / "agents" / "demo--writer.md",
            project / ".claude" / "agents" / "demo--writer.md",
        )
        _link(
            catalog / "agents" / "demo--reviewer.md",
            claude_config / "agents" / "demo--reviewer.md",
        )
        _link(
            catalog / "skills" / "demo-native",
            claude_config / "skills" / "demo-native",
        )
        _link(
            catalog / "rules" / "synthetic-path-rule.md",
            project / ".claude" / "rules" / "synthetic-path-rule.md",
        )
        _link(
            catalog / "agents" / "demo--writer.toml",
            project / ".codex" / "agents" / "demo--writer.toml",
        )
        _link(
            catalog / "agents" / "demo--reviewer.toml",
            codex_home / "agents" / "demo--reviewer.toml",
        )
        _link(
            catalog / "commands" / "synthetic-probe.md",
            codex_home / "prompts" / "synthetic-probe.md",
        )
        _link(
            catalog / "skills" / "demo-native",
            codex_home / "skills" / "demo-native",
        )

        hook_log = evidence / "hooks.log"
        python = shutil.which("python3") or shutil.which("python")
        if python is None:
            raise RuntimeError("Python is required for synthetic hook probes")
        project_hook = FIXTURE_ROOT / "hooks" / "project_hook.py"
        global_hook = FIXTURE_ROOT / "hooks" / "global_auto_update_hook.py"
        (project / ".claude" / "settings.json").write_text(
            json.dumps(_hook_settings([python, str(project_hook)])), encoding="utf-8"
        )
        (claude_config / "settings.json").write_text(
            json.dumps(_hook_settings([python, str(global_hook)])), encoding="utf-8"
        )
        (project / "probe-target.txt").write_text("synthetic\n", encoding="utf-8")

        if copy_claude_auth:
            _copy_auth(claude_auth, claude_config / ".credentials.json")
        if copy_codex_auth:
            _copy_auth(codex_auth, codex_home / "auth.json")

        env = {
            key: value
            for key, value in os.environ.items()
            if key in _RUNTIME_ENVIRONMENT or key.startswith("LC_")
        }
        env.update(
            {
                "HOME": str(home),
                "CLAUDE_CONFIG_DIR": str(claude_config),
                "CODEX_HOME": str(codex_home),
                "SYNTHETIC_HOOK_LOG": str(hook_log),
            }
        )
        yield IsolatedFixture(
            root=root,
            home=home,
            project=project,
            claude_config_dir=claude_config,
            codex_home=codex_home,
            evidence_dir=evidence,
            env=env,
        )


def sanitized_argv(argv: Sequence[str]) -> list[str]:
    """Return stable command evidence with inline secret values redacted."""

    result: list[str] = []
    redact_next = False
    for argument in argv:
        if redact_next:
            result.append("[REDACTED]")
            redact_next = False
            continue
        if argument.lower() in _SECRET_FLAGS:
            result.append(argument)
            redact_next = True
        elif _SECRET_ARGUMENTS.search(argument):
            if "=" in argument:
                result.append(argument.split("=", 1)[0] + "=[REDACTED]")
            else:
                result.append("[REDACTED]")
        else:
            result.append(argument)
    return result


def sanitized_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Select non-secret isolation variables for probe evidence."""

    keys = ("HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "SYNTHETIC_HOOK_LOG")
    return {key: environment[key] for key in keys if key in environment}
