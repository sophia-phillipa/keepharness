"""Batch D review fixes: scalar JSON bodies, deliberate user sentences and the supervisor race."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_service import harness_agents, pages, schedules
from agent_service.catalog_pin import CatalogPinError
from agent_service.errors import UserMessageError
from agent_service.json_depth import too_deep
from agent_service.persistence.json_file_repository import owner_folder_name
from agent_service.services.conversation_service import ConversationService
from control import manager as manager_module
from control import remote_models
from control.server import Manager
from tests.test_remote_models import admin, network, open_umask  # noqa: F401
from tests.test_route_table import assert_api_error, login_client  # noqa: F401

SCALARS = ["null", "5", "true", '"s"']


@pytest.mark.parametrize("text", SCALARS)
def test_a_scalar_has_no_nesting(text):
    assert too_deep(json.loads(text)) is False
    assert too_deep({"a": [1, "s", None, True]}) is False


@pytest.mark.parametrize("text", SCALARS)
def test_harness_body_refuses_a_scalar_as_object_required(login_client, text):  # noqa: F811
    response = login_client.post(
        "/v1/login", content=text.encode(), headers={"Origin": "http://testserver"}
    )
    assert_api_error(response, 422, "object_required")


@pytest.fixture
def plain_normalize(monkeypatch):
    for module in (pages, harness_agents, schedules):
        monkeypatch.setattr(module, "normalize", lambda *_: {})
    monkeypatch.setattr(schedules, "run_state", lambda _: {})


@pytest.mark.parametrize("text", SCALARS)
@pytest.mark.parametrize(
    "load, code",
    [
        (lambda t: pages.load("x", t), "invalid_page"),
        (lambda t: harness_agents.load("x", t), "invalid_harness_agent"),
        (lambda t: schedules.load("x", t, owner_folder_name("o")), "invalid_schedule"),
    ],
)
def test_stored_records_refuse_a_scalar_with_their_own_error(plain_normalize, load, code, text):
    with pytest.raises(ValueError, match=code):
        load(text)


@pytest.mark.parametrize("text", SCALARS)
def test_local_properties_refuse_a_scalar_as_not_an_object(monkeypatch, text):
    monkeypatch.setattr(remote_models, "fetch_body", AsyncMock(return_value=text))
    with pytest.raises(ValueError, match="props_not_an_object"):
        asyncio.run(ConversationService.local_properties(SimpleNamespace(config={}), "m"))


@pytest.mark.parametrize("text", SCALARS)
def test_responses_probe_with_a_scalar_answer_is_not_a_crash(monkeypatch, text):
    import httpx

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda _: httpx.Response(400, content=text)), **kwargs
        ),
    )
    asyncio.run(remote_models.require_responses_api("http://model.test"))


@pytest.mark.parametrize("text", SCALARS)
def test_admin_scalar_body_is_not_a_json_object(admin, text):  # noqa: F811
    client, _ = admin
    response = client.post("/api/remote-model-remove", content=text)
    assert response.status_code == 400
    assert response.json() == {"error": "The request must be a JSON object."}


def test_an_invalid_deepseek_token_shows_its_sentence(admin):  # noqa: F811
    client, _ = admin
    response = client.post("/api/provider-token", json={"provider": "deepseek", "token": "short"})
    assert response.status_code == 400
    assert response.json() == {"error": "Invalid API token."}


def test_a_catalog_pin_code_reaches_the_panel(admin, monkeypatch):  # noqa: F811
    from control import catalog_admin

    client, _ = admin
    manager = client.app.state.manager
    manager.settings["catalogs"] = [{"id": "c", "namespace": "c", "kind": "git", "root": "/x"}]
    manager.settings["projects"] = [{"id": "p", "root": "/x", "catalogs": ["c"]}]

    def refuse(*_args, **_kwargs):
        raise CatalogPinError("catalog_git_failed")

    monkeypatch.setattr(catalog_admin, "pin_catalog", refuse)
    response = client.post(
        "/api/catalog-pin", json={"action": "pin", "project_id": "p", "catalog_id": "c"}
    )
    assert response.json() == {"error": "catalog_git_failed"}
    assert issubclass(CatalogPinError, (UserMessageError, ValueError))


def test_non_numeric_gpu_layers_show_a_sentence(admin, tmp_path, monkeypatch):  # noqa: F811
    from control import local_models

    client, _ = admin
    monkeypatch.setattr(local_models, "processes", lambda: [])
    binary = tmp_path / "llama-server"
    binary.write_text("")
    model = tmp_path / "m.gguf"
    model.write_text("")
    response = client.post(
        "/api/local-start",
        json={"file": str(model), "binary": str(binary), "gpu_layers": "many"},
    )
    assert response.status_code == 400
    assert response.json() == {"error": "Invalid GPU layers."}


def test_a_vpn_bind_that_is_not_an_ip_shows_a_sentence(tmp_path):
    with pytest.raises(UserMessageError, match="only on the private loopback address 127.0.0.1"):
        Manager(tmp_path).validate({"vpn_bind": "not-an-ip"})


def test_a_legacy_non_loopback_vpn_bind_loads_as_loopback(tmp_path, caplog):
    first = Manager(tmp_path)
    first.settings["vpn_bind"] = "10.44.0.2"
    first.path.write_text(json.dumps(first.settings))
    with caplog.at_level("WARNING", logger="control.manager"):
        loaded = Manager(tmp_path)
    assert loaded.settings["vpn_bind"] == "127.0.0.1"
    assert [r.levelname for r in caplog.records if "vpn_bind" in r.getMessage()] == ["WARNING"]
    assert (
        Manager(tmp_path).settings["vpn_bind"] == "127.0.0.1"
    )  # coerced again until the next save


class _Proc:
    def __init__(self, code):
        self.returncode = code

    async def wait(self):
        return self.returncode


def test_a_stale_failed_start_does_not_overwrite_a_replaced_process(tmp_path, monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(manager_module.asyncio, "sleep", instant)
    manager = Manager(tmp_path)
    manager.proc = _Proc(1)
    spawned, owner_started = _Proc(None), _Proc(None)

    async def start(supervised=False):
        manager.proc = spawned
        return spawned

    async def await_ready(proc):
        manager.proc = owner_started  # an owner Start replaced the process meanwhile
        raise UserMessageError("The service did not become ready in time.")

    manager.start, manager.await_ready = start, await_ready
    manager.startup_error, before = None, manager.started_at
    asyncio.run(manager.restart_after_exit())
    assert manager.startup_error is None
    assert manager.started_at == before
