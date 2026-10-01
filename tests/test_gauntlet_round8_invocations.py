import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from test_workspaces import config as base_config

from adapters.codex.native import resource_inputs
from agent_service import resources
from agent_service.app import Service


def put(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def configure_service(tmp_path, monkeypatch, backend, model):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for key in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "GEMINI_CLI_HOME"):
        monkeypatch.delenv(key, raising=False)
    cfg = base_config(tmp_path / "state")
    root = tmp_path / "project"
    cfg["projects"]["p"]["root"] = str(root)
    cfg["services"][backend] = {
        "enabled": True,
        "mode": "native",
        "models": [model],
        "projects": ["p"],
        "permissions": {"read": True},
    }
    cfg[backend + "_models"] = {model: ["configured"]}
    cfg[backend] = {"binary": "synthetic-unused"}
    return cfg, root


@pytest.mark.parametrize("collides", [False, True], ids=["control", "regression"])
def test_explicit_chain_uses_each_canonical_argument_slice(tmp_path, monkeypatch, collides):
    cfg, root = configure_service(tmp_path, monkeypatch, "gemini", "fixture")
    put(root, ".gemini/commands/first.toml", 'prompt="FIRST {{args}}"')
    put(root, ".gemini/commands/second.toml", 'prompt="SECOND $1"')
    service = Service(cfg)
    identity = ("a", cfg["clients"]["a"])
    try:
        items = {
            item["name"]: item
            for item in service.resource_catalog(identity, "p", "gemini", "fixture")["items"]
        }
        first_arg = ("/second" if collides else "/unselected") + ' "unterminated'
        result = service.submit(
            identity,
            {
                "project_id": "p",
                "backend": "gemini",
                "model": "fixture",
                "effort": "configured",
                "invocations": [
                    {
                        "kind": "command",
                        "resource_id": items["first"]["resource_id"],
                        "args": first_arg,
                        "order": 0,
                        "mode": "inline",
                    },
                    {
                        "kind": "command",
                        "resource_id": items["second"]["resource_id"],
                        "args": "valid",
                        "order": 1,
                        "mode": "inline",
                    },
                ],
            },
        )
        payload = json.loads(service.job(identity, result["job_id"])["payload"])
        assert [value["args"] for value in payload["invocations"]] == [first_arg, "valid"]
        with patch("adapters.run_native", AsyncMock(return_value={"answer": "done"})) as run:
            asyncio.run(service.execute(service.job(identity, result["job_id"])))
        assert first_arg in run.call_args_list[0].args[1]
        assert "SECOND valid" in run.call_args_list[1].args[1]
    finally:
        service.db.close()


@pytest.mark.parametrize(
    "prompt,expected_native",
    [("/review actual", True), ("/review-other literal\n/review actual", False)],
    ids=["control", "regression"],
)
def test_claude_native_command_requires_exact_leading_token(
    tmp_path, monkeypatch, prompt, expected_native
):
    cfg, root = configure_service(tmp_path, monkeypatch, "claude", "haiku")
    put(root, ".claude/commands/review.md", "Review $ARGUMENTS")
    service = Service(cfg)
    identity = ("a", cfg["clients"]["a"])
    try:
        item = next(
            item
            for item in service.resource_catalog(identity, "p", "claude", "haiku")["items"]
            if item["name"] == "review"
        )
        submitted = service.submit(
            identity,
            {
                "project_id": "p",
                "backend": "claude",
                "model": "haiku",
                "effort": "configured",
                "prompt": prompt,
                "resource_selections": [
                    {"id": item["id"], "revision": item["revision"], "token": "/review"}
                ],
            },
        )
        row = service.job(identity, submitted["job_id"])
        payload = json.loads(row["payload"])
        with patch("adapters.run_native", AsyncMock(return_value={"answer": "done"})) as run:
            asyncio.run(service.infer(row, payload))
        selected = run.call_args.args[3]["_resources"][0]
        assert selected["native_command"] is expected_native
        assert selected.get("_inline_fallback", False) is (not expected_native)
        expected_prompt = prompt if expected_native else "/review-other literal\nReview actual"
        assert run.call_args.args[1] == expected_prompt
    finally:
        service.db.close()


class SkillsRPC:
    def __init__(self, path):
        self.path = path

    async def call(self, method, params):
        assert method == "skills/list"
        return {
            "data": [{"skills": [{"name": "large-skill", "path": str(self.path), "enabled": True}]}]
        }


@pytest.mark.parametrize(
    "size", [60_000, 100_000, resources.MAX_BODY_BYTES - len("---\nname: large-skill\n---\n")]
)
def test_codex_executes_unchanged_discoverable_skill(tmp_path, monkeypatch, size):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    project = tmp_path / "project"
    path = put(
        project,
        ".agents/skills/large-skill/SKILL.md",
        "---\nname: large-skill\n---\n" + "x" * size,
    )
    cfg = {
        "projects": {"p": {"root": str(project), "permissions": {"read": True}}},
        "services": {"codex": {"mode": "native"}},
    }
    item = next(
        value
        for value in resources.discover(cfg, "p", "codex", private=True)["items"]
        if value["name"] == "large-skill"
    )
    assert item["selectable"] is True
    result = asyncio.run(resource_inputs(SkillsRPC(path), {"_resources": [item]}, project))
    assert result == [{"type": "skill", "name": "large-skill", "path": str(path)}]


def test_repeated_explicit_command_preserves_each_argument(tmp_path, monkeypatch):
    cfg, root = configure_service(tmp_path, monkeypatch, "gemini", "fixture")
    put(root, ".gemini/commands/first.toml", 'prompt="FIRST {{args}}"')
    service = Service(cfg)
    identity = ("a", cfg["clients"]["a"])
    try:
        item = next(
            item
            for item in service.resource_catalog(identity, "p", "gemini", "fixture")["items"]
            if item["name"] == "first"
        )
        args = ['/first "unterminated', "second  \nexact"]
        job = service.submit(
            identity,
            dict(
                project_id="p",
                backend="gemini",
                model="fixture",
                effort="configured",
                invocations=[
                    dict(
                        kind="command",
                        resource_id=item["resource_id"],
                        args=value,
                        order=index,
                        mode="inline",
                    )
                    for index, value in enumerate(args)
                ],
            ),
        )["job_id"]
        assert [
            value["args"]
            for value in json.loads(service.job(identity, job)["payload"])["invocations"]
        ] == args
    finally:
        service.db.close()
