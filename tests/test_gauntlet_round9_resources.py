import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from test_gauntlet_round8_invocations import configure_service, put

from agent_service.app import Service


def test_nested_agent_ids_are_unique(tmp_path, monkeypatch):
    cfg, root = configure_service(tmp_path, monkeypatch, "codex", "gpt-6-astra")
    repo = root / "repo"
    nested = repo / "sub"
    cfg["projects"]["p"]["root"] = str(nested)
    put(repo, ".git", "")
    put(
        repo,
        ".codex/agents/reviewer.toml",
        'name="parent-reviewer"\ndeveloper_instructions="Parent"',
    )
    put(
        nested,
        ".codex/agents/reviewer.toml",
        'name="child-reviewer"\ndeveloper_instructions="Child"',
    )
    service = Service(cfg)
    identity = ("a", cfg["clients"]["a"])
    try:
        items = service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
        print("resource identities:", [(i["name"], i["id"]) for i in items])
        for item in items:
            submitted = service.submit(
                identity,
                dict(
                    project_id="p",
                    backend="codex",
                    model="gpt-6-astra",
                    effort="configured",
                    prompt="/" + item["name"] + " inspect",
                    resource_selections=[
                        dict(id=item["id"], revision=item["revision"], token="/" + item["name"])
                    ],
                ),
            )
            payload = json.loads(service.job(identity, submitted["job_id"])["payload"])
            assert payload["invocations"][0]["resource_id"] == item["id"]
        assert len({i["id"] for i in items}) == len(items)
    finally:
        service.db.close()


@pytest.mark.parametrize(
    "prompt", json.loads((Path(__file__).with_name("markdown-invocation-cases.json")).read_text())
)
def test_code_examples_do_not_capture_real_selection(tmp_path, monkeypatch, prompt):
    cfg, root = configure_service(tmp_path, monkeypatch, "gemini", "fixture")
    put(root, ".gemini/commands/review.toml", 'prompt="REVIEW {{args}}"')
    service = Service(cfg)
    identity = ("a", cfg["clients"]["a"])
    try:
        item = next(
            i
            for i in service.resource_catalog(identity, "p", "gemini", "fixture")["items"]
            if i["name"] == "review"
        )
        result = service.submit(
            identity,
            dict(
                project_id="p",
                backend="gemini",
                model="fixture",
                effort="configured",
                prompt=prompt,
                resource_selections=[
                    dict(id=item["id"], revision=item["revision"], token="/review")
                ],
            ),
        )
        row = service.job(identity, result["job_id"])
        payload = json.loads(row["payload"])
        print("canonical args:", repr(payload["invocations"][0]["args"]))
        with patch("adapters.run_native", AsyncMock(return_value={"answer": "done"})) as run:
            asyncio.run(service.infer(row, payload))
        print("executed prompt:", repr(run.call_args.args[1]))
        assert payload["invocations"][0]["args"] == "actual"
        assert "/review example" not in prompt or "/review example" in run.call_args.args[1]
    finally:
        service.db.close()
