"""Trusted resources use regular execution copies without changing catalog links."""

import asyncio
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock

from adapters.claude.backend import run_native as run_claude
from adapters.codex.backend import run_native as run_codex


def resource(path, kind, text, **extra):
    path.write_text(text)
    return dict(
        kind=kind,
        name="demo--writer",
        resource_id="fixture/" + path.name,
        revision=hashlib.sha256(text.encode()).hexdigest(),
        _text=text,
        _body=text,
        _meta={},
        source=str(path),
        **extra,
    )


def test_codex_agent_regular_copy_and_model_effort(tmp_path, monkeypatch):
    source = tmp_path / "role.toml"
    text = 'name = "demo--writer"\ndeveloper_instructions = "Synthetic task"\nmodel = "gpt-6-astra"\nmodel_reasoning_effort = "high"\n'
    item = resource(source, "agent", text)
    seen = []

    async def turn(
        config, event, project, model, effort, session_dir, approve, workspace, runtime, provider
    ):
        setting = next(
            value
            for value in runtime.command
            if value.startswith('agents."demo--writer".config_file=')
        )
        import json

        copied = Path(json.loads(setting.split("=", 1)[1]))
        assert copied.is_file() and not copied.is_symlink()
        assert copied.read_text() == text
        assert copied.is_relative_to(tmp_path / "session")
        seen.append(copied)
        return {"answer": "done"}

    monkeypatch.setattr("adapters.codex.backend.run_turn", turn)
    asyncio.run(
        run_codex(
            {"binary": "codex"},
            "task",
            lambda *_: None,
            {
                "root": str(tmp_path),
                "permissions": {"read": True, "delegate": True},
                "_resources": [item],
            },
            "gpt-6-astra",
            "high",
            tmp_path / "session",
            AsyncMock(),
        )
    )
    assert not seen[0].exists()
    assert source.read_text() == text


def test_claude_rule_copy_preserves_scope_and_slash_start(tmp_path, monkeypatch):
    text = '---\npaths:\n  - "src/**"\n---\nUse synthetic conventions.'
    item = resource(tmp_path / "rule.md", "rule", text)
    item["_meta"] = {"paths": ["src/**"]}
    seen = []

    async def run(config, prompt, event, cwd, model, home, *args, **kwargs):
        assert prompt == "/demo value"
        context = config["append_system_prompt"]
        assert "src/**" in context and "Use synthetic conventions" in context
        assert "advisory" in context
        copies = list(home.glob("claude-rules-*/*.md"))
        assert len(copies) == 1 and not copies[0].is_symlink()
        seen.extend(copies)
        return {"answer": "done"}

    monkeypatch.setattr("adapters.claude.backend.native.run", run)
    events = []
    asyncio.run(
        run_claude(
            {"binary": "claude"},
            "/demo value",
            lambda *event: events.append(event),
            {
                "root": str(tmp_path),
                "permissions": {"read": True},
                "_resources": [{"kind": "command", "native_command": True}],
                "_rules": [item],
            },
            "haiku",
            "configured",
            tmp_path / "session",
            AsyncMock(),
        )
    )
    assert not seen[0].exists()
    assert any(kind == "resource_fallback" and data["kind"] == "rule" for kind, data in events)
