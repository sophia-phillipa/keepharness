"""``/props`` of a local or network model server is bounded and never trusted.

Both the model list (``enrich``) and the image gate (``validate_images``) read it from a
machine the harness does not control: it must not hang, grow without limit, follow redirects,
use proxies from the environment, or break the caller with an unexpected shape.
"""

import asyncio
import inspect

import httpx
import pytest
from test_workspaces import config

from agent_service.app import APIError, Service
from control import remote_models

REAL_CLIENT = httpx.AsyncClient
NETLOC = "127.0.0.1:8091"
GOOD = {"default_generation_settings": {"n_ctx": 8192}, "modalities": {"vision": True}}
# Valid JSON that would enrich the model, but past the size cap: it must be ignored whole.
OVERSIZED = httpx.Response(
    200,
    content=(
        b'{"chat_template": "' + b"x" * remote_models.MAX_RESPONSE_BYTES + b'", '
        b'"default_generation_settings": {"n_ctx": 8192}, "modalities": {"vision": true}}'
    ),
)


class FakeServer:
    """Answers ``/props`` by netloc; anything else is refused."""

    def __init__(self):
        self.requests = []
        self.options = []
        self.answer = None

    async def __call__(self, request):
        self.requests.append(request)
        if request.url.netloc.decode() != NETLOC or request.url.path != "/props":
            raise httpx.ConnectError("refused", request=request)
        if isinstance(self.answer, Exception):
            raise self.answer
        result = self.answer(request) if callable(self.answer) else self.answer
        return await result if inspect.isawaitable(result) else result


@pytest.fixture
def server(monkeypatch):
    fake = FakeServer()

    def client(**options):
        fake.options.append(options)
        return REAL_CLIENT(transport=httpx.MockTransport(fake), **options)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr("agent_service.tools.video_tools_available", lambda: True)
    monkeypatch.setattr("agent_service.tools.transcription_available", lambda: False)
    return fake


@pytest.fixture
def service(tmp_path):
    cfg = config(tmp_path)
    cfg["services"].pop("codex")
    cfg["services"]["local"]["mode"] = "native"
    instance = Service(cfg)
    yield instance
    instance.db.close()


def local_model(models):
    return next(model for model in models if model["backend"] == "local")


def listed(service):
    return local_model(asyncio.run(asyncio.wait_for(service.models_with_context("p"), 10)))


HOSTILE = [
    pytest.param(httpx.ConnectError("refused"), id="refused"),
    pytest.param(httpx.Response(500), id="http-500"),
    pytest.param(httpx.Response(401), id="http-401"),
    pytest.param(
        httpx.Response(302, headers={"location": "http://elsewhere:1/props"}), id="redirect"
    ),
    pytest.param(httpx.Response(200, text="<html>"), id="not-json"),
    pytest.param(httpx.Response(200, content=b"[" * 100_000), id="deeply-nested"),
    pytest.param(httpx.Response(200, json=[1, 2]), id="list"),
    pytest.param(httpx.Response(200, json="props"), id="string"),
    pytest.param(httpx.Response(200, json=None), id="null"),
    pytest.param(OVERSIZED, id="too-large"),
    pytest.param(httpx.Response(200, json={"modalities": []}), id="modalities-list"),
    pytest.param(httpx.Response(200, json={"modalities": "vision"}), id="modalities-string"),
    pytest.param(httpx.Response(200, json={"default_generation_settings": "x"}), id="settings-str"),
    pytest.param(httpx.Response(200, json={"default_generation_settings": []}), id="settings-list"),
    pytest.param(
        httpx.Response(200, json={"default_generation_settings": {"n_ctx": "big"}}), id="n-ctx-str"
    ),
]


@pytest.mark.parametrize("answer", HOSTILE)
def test_a_hostile_props_answer_never_breaks_the_model_list(service, server, answer):
    server.answer = answer
    model = listed(service)
    assert model["id"] == "installed-model"
    assert "context_window" not in model
    assert not model["capabilities"].get("video")


def test_a_good_props_answer_still_enriches_the_model(service, server):
    server.answer = httpx.Response(200, json=GOOD)
    model = listed(service)
    assert model["context_window"] == 8192
    assert model["capabilities"]["video"] is True
    assert model["capabilities"]["video_execution_modes"] == ["scoped"]


def test_a_slow_props_server_cannot_hold_the_model_list(service, server, monkeypatch):
    async def hang(request):
        await asyncio.sleep(30)

    server.answer = hang
    monkeypatch.setattr(remote_models, "PROBE_SECONDS", 0.2)
    assert "context_window" not in listed(service)


def test_a_trickling_props_server_is_cut_off_by_the_total_deadline(service, server, monkeypatch):
    class Trickle(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                yield b" "
                await asyncio.sleep(0.05)

    server.answer = httpx.Response(200, stream=Trickle())
    monkeypatch.setattr(remote_models, "PROBE_SECONDS", 0.3)
    assert "context_window" not in listed(service)


def test_props_is_read_without_proxies_or_redirects(service, server):
    server.answer = httpx.Response(302, headers={"location": "http://elsewhere:1/props"})
    listed(service)
    assert server.options and all(
        options["trust_env"] is False and options["follow_redirects"] is False
        for options in server.options
    )
    assert {request.url.host for request in server.requests} == {"127.0.0.1"}


def test_the_model_key_is_sent_as_a_bearer_token(service, server, tmp_path):
    key = tmp_path / "model.key"
    key.write_text("sk-model\n")
    service.config.setdefault("local", {})["local_models"] = {
        "installed-model": {"url": "http://" + NETLOC, "key_file": str(key)}
    }
    server.answer = httpx.Response(200, json=GOOD)
    assert listed(service)["context_window"] == 8192
    assert server.requests[0].headers["authorization"] == "Bearer sk-model"


# --------------------------------------------------------------------------- image gate


def gate(service):
    return asyncio.run(asyncio.wait_for(service.validate_images("local", "installed-model"), 10))


def test_images_are_admitted_for_a_server_that_reports_vision(service, server):
    server.answer = httpx.Response(200, json=GOOD)
    assert gate(service) is None


def test_images_are_refused_when_the_server_reports_no_vision(service, server):
    server.answer = httpx.Response(200, json={"modalities": {"vision": False}})
    with pytest.raises(APIError, match="local_vision_not_enabled"):
        gate(service)


@pytest.mark.parametrize("modalities", [[], "vision", 5, None])
def test_a_malformed_modalities_section_means_no_vision(service, server, modalities):
    server.answer = httpx.Response(200, json={"modalities": modalities})
    with pytest.raises(APIError, match="local_vision_not_enabled"):
        gate(service)


UNTRUSTED = [
    pytest.param(answer, id=answer.__class__.__name__ + str(index))
    for index, answer in enumerate(
        [
            httpx.ConnectError("refused"),
            httpx.Response(500),
            httpx.Response(302, headers={"location": "http://elsewhere:1/props"}),
            httpx.Response(200, text="<html>"),
            httpx.Response(200, content=b"[" * 100_000),
            httpx.Response(200, json=[1, 2]),
            OVERSIZED,
        ]
    )
]


@pytest.mark.parametrize("answer", UNTRUSTED)
def test_images_are_unavailable_when_the_answer_cannot_be_trusted(service, server, answer):
    server.answer = answer
    with pytest.raises(APIError, match="image_capability_unavailable"):
        gate(service)


def test_a_slow_server_cannot_hold_the_image_gate(service, server, monkeypatch):
    async def hang(request):
        await asyncio.sleep(30)

    server.answer = hang
    monkeypatch.setattr(remote_models, "PROBE_SECONDS", 0.2)
    with pytest.raises(APIError, match="image_capability_unavailable"):
        gate(service)


def test_the_image_gate_reads_props_without_proxies_or_redirects(service, server):
    server.answer = httpx.Response(200, json=GOOD)
    gate(service)
    assert server.options == [
        {"timeout": remote_models.PROBE_SECONDS, "trust_env": False, "follow_redirects": False}
    ]
