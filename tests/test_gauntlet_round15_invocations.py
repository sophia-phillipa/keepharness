import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from test_gauntlet_round8_invocations import configure_service, put

from agent_service.app import Service


@pytest.mark.parametrize(
    "opening,indent",
    [("- - ```text", "    "), ("- 1. ```text", "     "), ("1. - ```text", "     ")],
)
def test_nested_list_fence(tmp_path, monkeypatch, opening, indent):
    cfg, root = configure_service(tmp_path, monkeypatch, "gemini", "fixture")
    put(root, ".gemini/commands/review.toml", 'prompt="REVIEW {{args}}"')
    prompt = opening + "\n" + indent + "/review example\n" + indent + "```\n\n/review actual"
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
        with patch("adapters.run_native", AsyncMock(return_value={"answer": "done"})) as run:
            asyncio.run(service.infer(row, payload))
        print(
            "PROMPT",
            repr(prompt),
            "CANONICAL",
            repr(payload["invocations"][0]["args"]),
            "ADAPTER",
            repr(run.call_args.args[1]),
        )
        assert payload["invocations"][0]["args"] == "actual"
        assert "/review example" in run.call_args.args[1]
    finally:
        service.db.close()


@pytest.mark.parametrize("token", ["@@review", "//review"])
def test_nested_list_reserved_marker_remains_literal(tmp_path, monkeypatch, token):
    cfg, root = configure_service(tmp_path, monkeypatch, "gemini", "fixture")
    put(root, ".gemini/commands/review.toml", 'prompt="REVIEW {{args}}"')
    service = Service(cfg)
    identity = ("a", cfg["clients"]["a"])
    try:
        prompt = "- - ```text\n    " + token + " example\n    ```\n\nOrdinary question"
        result = service.submit(
            identity,
            dict(
                project_id="p",
                backend="gemini",
                model="fixture",
                effort="configured",
                prompt=prompt,
            ),
        )
        assert result["job_id"]
    finally:
        service.db.close()
