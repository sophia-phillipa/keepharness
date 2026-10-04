"""Nesting deeper than MAX_JSON_DEPTH is refused with each site's usual error.

200 levels stays inside Python 3.12's recursion limit, so these cases exercise the explicit
depth bound instead of the RecursionError that Python 3.13+ no longer raises.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from agent_service import harness_agents, pages, schedules, workflows
from agent_service.json_depth import MAX_JSON_DEPTH
from agent_service.persistence.json_file_repository import owner_folder_name
from agent_service.services.conversation_service import ConversationService
from control import remote_models
from tests.test_remote_models import admin, network, open_umask  # noqa: F401

DEPTH = 200
assert DEPTH > MAX_JSON_DEPTH


def nested(depth):
    return json.loads('{"a":' * depth + "0" + "}" * depth)


def deep_text(depth=DEPTH):
    return '{"a":' * depth + "0" + "}" * depth


@pytest.fixture
def plain_normalize(monkeypatch):
    """Let the depth bound alone decide whether a stored record loads."""
    for module in (pages, harness_agents, schedules):
        monkeypatch.setattr(module, "normalize", lambda *_: {})
    monkeypatch.setattr(schedules, "run_state", lambda _: {})


def stored(**extra):
    return {"id": "x", "name": "x", "owner": "o"} | extra


@pytest.mark.parametrize(
    "load, code",
    [
        (lambda t: pages.load("x", t), "invalid_page"),
        (lambda t: harness_agents.load("x", t), "invalid_harness_agent"),
        (lambda t: schedules.load("x", t, owner_folder_name("o")), "invalid_schedule"),
    ],
)
def test_stored_records_refuse_deep_json(plain_normalize, load, code):
    load(json.dumps(stored(extra=nested(MAX_JSON_DEPTH - 2))))
    with pytest.raises(ValueError, match=code):
        load(json.dumps(stored(extra=nested(DEPTH))))


def test_probe_refuses_a_deep_model_list(monkeypatch):
    body = '{"data":' + "[" * DEPTH + "]" * DEPTH + "}"
    monkeypatch.setattr(remote_models, "fetch_body", AsyncMock(return_value=body))
    monkeypatch.setattr(remote_models, "require_responses_api", AsyncMock())
    with pytest.raises(ValueError, match="OpenAI-style model list"):
        asyncio.run(remote_models.probe("http://box:8080"))


def test_responses_probe_refuses_a_deep_answer(monkeypatch):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda _: httpx.Response(400, content=deep_text())),
            **kwargs,
        ),
    )
    with pytest.raises(ValueError, match="responses_api_unavailable"):
        asyncio.run(remote_models.require_responses_api("http://model.test"))


def test_local_properties_refuse_deep_json(monkeypatch):
    monkeypatch.setattr(remote_models, "fetch_body", AsyncMock(return_value=deep_text()))
    with pytest.raises(ValueError, match="props_not_an_object"):
        asyncio.run(ConversationService.local_properties(SimpleNamespace(config={}), "m"))


def test_workflow_result_block_with_deep_json_is_not_a_result():
    assert workflows.parse_result(f"```harness-result\n{deep_text()}\n```\n") is None
    assert workflows.parse_result('```harness-result\n{"ok":true}\n```\n') == {"ok": True}


def test_admin_body_refuses_deep_json(admin):  # noqa: F811
    client, _ = admin
    response = client.post("/api/remote-model-remove", content=deep_text())
    assert response.status_code == 400
    assert response.json() == {
        "error": "Invalid request structure. Check the submitted fields."
    }
