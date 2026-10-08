"""Provider boundaries: dispatch never silently falls back to another provider."""

import asyncio
import importlib
import inspect
import json
from unittest.mock import AsyncMock, patch

import pytest

from agent_service.tools import ToolError
from tests.deepseek_fixtures import SAFE_CONFIG, write_deepseek_key


@pytest.mark.parametrize("provider", ["codex", "claude", "gemini", "deepseek", "local"])
def test_native_dispatch_uses_only_the_selected_adapter(provider, tmp_path):
    adapters = importlib.import_module("adapters")
    implementation = importlib.import_module(f"adapters.{provider}.backend")
    config = {"binary": "fixture"}
    project = {"permissions": {}}

    def event(*args):
        return None

    approve = AsyncMock()
    expected = {"answer": "fixture", "backend": provider}
    with patch.object(implementation, "run_native", AsyncMock(return_value=expected)) as run:
        result = asyncio.run(
            adapters.run_native(
                config,
                "prompt",
                event,
                project,
                "model",
                "configured",
                tmp_path,
                provider,
                approve,
            )
        )
    assert result == expected
    run.assert_awaited_once_with(
        config,
        "prompt",
        event,
        project,
        "model",
        "configured",
        tmp_path,
        approve,
    )


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_retired_scoped_dispatch_cannot_reach_provider(provider):
    adapters = importlib.import_module("adapters")
    implementation = importlib.import_module(f"adapters.{provider}.backend")
    assert not hasattr(implementation, "run_scoped")
    with pytest.raises(ToolError, match="execution_mode_unsupported"):
        asyncio.run(adapters.run_scoped({}, "prompt", None, provider=provider))


@pytest.mark.parametrize("provider", ["local", "deepseek", "unknown"])
def test_unsupported_scoped_provider_is_rejected(provider):
    adapters = importlib.import_module("adapters")
    with pytest.raises(ToolError, match="backend_unavailable"):
        asyncio.run(adapters.run_scoped({}, "", None, provider=provider))


def test_unknown_native_provider_is_rejected(tmp_path):
    adapters = importlib.import_module("adapters")
    with pytest.raises(ToolError, match="backend_unavailable"):
        asyncio.run(adapters.run_native({}, "", None, {}, "", "", tmp_path, "unknown", None))


def _parameters(function, *, skip_self=False):
    parameters = list(inspect.signature(function).parameters.values())
    return [(item.name, item.kind) for item in parameters[1 if skip_self else 0 :]]


def test_registries_are_read_only_and_cover_every_scoped_backend():
    adapters = importlib.import_module("adapters")
    with pytest.raises(TypeError):
        adapters.PROVIDERS["other"] = object()
    with pytest.raises(TypeError):
        adapters.SCOPED_PROVIDERS["other"] = object()
    assert set(adapters.PROVIDERS) == {"codex", "claude", "gemini", "deepseek", "local"}
    assert set(adapters.SCOPED_PROVIDERS) == {
        name for name, module in adapters.PROVIDERS.items() if "run_scoped" in vars(module)
    }
    for name, module in adapters.SCOPED_PROVIDERS.items():
        assert adapters.PROVIDERS[name] is module


@pytest.mark.parametrize("provider", ["codex", "claude", "gemini", "deepseek", "local"])
def test_backend_signatures_match_the_adapter_protocols(provider):
    from adapters.base import ProviderAdapter, ScopedProviderAdapter

    implementation = importlib.import_module(f"adapters.{provider}.backend")
    assert _parameters(implementation.run_native) == _parameters(
        ProviderAdapter.run_native, skip_self=True
    )
    if "run_scoped" in vars(implementation):
        assert _parameters(implementation.run_scoped) == _parameters(
            ScopedProviderAdapter.run_scoped, skip_self=True
        )


def test_gemini_scoped_keeps_its_own_unsupported_error():
    adapters = importlib.import_module("adapters")
    with pytest.raises(ToolError, match="^gemini_scoped_unsupported$"):
        asyncio.run(adapters.run_scoped({}, "", None, provider="gemini"))


@pytest.mark.parametrize("provider", ["codex", "claude", "gemini", "deepseek", "local"])
def test_package_attributes_are_the_provider_subpackages_not_their_backends(provider):
    """F-33: the dispatch registry must not shadow ``adapters.<provider>``."""
    import sys

    import adapters
    import adapters.claude.account as account

    assert account is sys.modules["adapters.claude.account"]
    assert getattr(adapters, provider) is sys.modules[f"adapters.{provider}"]


def test_no_phantom_codex_model_fallback():
    """F-53: a turn always carries its own model; nothing falls back to a made-up id."""
    from agent_service.services.conversation_service import ConversationService

    adapters = importlib.import_module("adapters")
    assert inspect.signature(adapters.run_scoped).parameters["model"].default is None
    assert (
        inspect.signature(adapters.run_native).parameters["model"].default
        is inspect.Parameter.empty
    )
    assert "gpt-6-astra" not in inspect.getsource(ConversationService)


# Codex app-server transport, shared by the codex, deepseek and local adapters (HAR-R3-1).
ECHOING_APP_SERVER = "SAFE_CONFIG = " + repr(SAFE_CONFIG) + "\n" + """
import json, sys
def emit(value): print(json.dumps(value), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    method, ident, params = request.get('method'), request.get('id'), request.get('params', {})
    with open(LOG, 'a') as stream:
        stream.write(json.dumps([method, params.get('excludeTurns')]) + '\\n')
    if method == 'thread/resume':
        # Like codex-cli 0.157.1: full-history hydration unless excludeTurns is set.
        image = {'type': 'image', 'url': 'data:image/png;base64,' + 'A' * (4 << 20)}
        turns = [] if params.get('excludeTurns') else [{'items': [{'type': 'userMessage', 'content': [image]}]}]
        emit({'id': ident, 'result': {'thread': {'id': params['threadId'], 'turns': turns}}})
    elif method == 'config/read':
        emit({'id': ident, 'result': SAFE_CONFIG})
    elif method == 'thread/start':
        emit({'id': ident, 'result': {'thread': {'id': 'thread-1'}}})
    elif method == 'turn/start':
        emit({'method': 'turn/started', 'params': {'turn': {'id': 'turn-1'}}})
        # The user input comes back whole, attached images included, on one stdout line.
        emit({'method': 'item/started', 'params': {'item': {'type': 'userMessage', 'id': 'u1', 'content': params['input']}}})
        emit({'method': 'item/agentMessage/delta', 'params': {'delta': 'ok'}})
        emit({'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}})
    elif ident is not None:
        emit({'id': ident, 'result': {}})
"""


def echoing_app_server(tmp_path):
    import sys

    log = tmp_path / "app-server.jsonl"
    executable = tmp_path / "fake-codex"
    executable.write_text(
        "#!" + sys.executable + "\nLOG = " + repr(str(log)) + "\n" + ECHOING_APP_SERVER
    )
    executable.chmod(0o700)
    return executable, log


def run_app_server_turn(executable, session, provider, project, tmp_path):
    import adapters

    config = {"binary": str(executable)}
    if provider == "deepseek":
        key = tmp_path / "deepseek.key"
        write_deepseek_key(key)
        config["api_provider"] = {"url": "http://127.0.0.1:9/v1", "key_file": str(key)}
    return asyncio.run(
        adapters.run_native(
            config, "hello", lambda *_: None, project, "fixture", "low", session, provider,
            AsyncMock(),
        )
    )


@pytest.fixture
def app_server_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setattr("adapters.codex.native.configurations", lambda: {"codex": {}})
    monkeypatch.setattr("adapters.codex.native.inventory", lambda: {"codex": []})


def image_project(tmp_path, size):
    photo = tmp_path / "photo.png"
    photo.write_bytes(b"\x89PNG" + b"\x00" * (size - 4))
    return {"permissions": {}, "_images": [{"media_type": "image/png", "path": str(photo)}]}


@pytest.mark.parametrize("provider", ["codex", "deepseek"])
def test_a_photo_over_2_mib_and_its_resume_keep_the_conversation_usable(
    tmp_path, app_server_home, provider
):
    """QA-R4-1: a 3 MiB photo is a 4 MiB base64 echo; the old 2 MiB line limit raised ValueError."""
    executable, log = echoing_app_server(tmp_path)
    session = tmp_path / "session"
    first = run_app_server_turn(
        executable, session, provider, image_project(tmp_path, 3 << 20), tmp_path
    )
    follow_up = run_app_server_turn(executable, session, provider, {"permissions": {}}, tmp_path)
    assert first["answer"] == follow_up["answer"] == "ok"
    sent = [json.loads(line) for line in log.read_text().splitlines()]
    assert ["thread/start", None] in sent
    # Thread metadata only: the stored turns (the photo again) are never sent back whole.
    assert ["thread/resume", True] in sent


@pytest.mark.parametrize("provider", ["codex", "deepseek"])
def test_a_message_above_the_read_limit_is_a_readable_failure(
    tmp_path, app_server_home, monkeypatch, provider
):
    monkeypatch.setattr("adapters.codex.rpc.READ_LIMIT", 64 * 1024)
    executable, _ = echoing_app_server(tmp_path)
    with pytest.raises(ToolError, match=f"^{provider}_output_limit$"):
        run_app_server_turn(
            executable, tmp_path / "session", provider, image_project(tmp_path, 96 * 1024), tmp_path
        )


def test_the_read_limit_carries_the_largest_attachable_image():
    from adapters.codex.rpc import READ_LIMIT
    from agent_service.tools import MAX_ATTACHMENT_BYTES

    assert READ_LIMIT > MAX_ATTACHMENT_BYTES * 4 // 3


def native_codex_turn(tmp_path, notifications, runs=1):
    """Run the native adapter against scripted notifications; return the mock RPC."""
    from contextlib import asynccontextmanager
    from adapters.codex.backend import run_native

    rpc = AsyncMock()
    rpc.call.return_value = {"thread": {"id": "native-id"}}
    rpc.receive.side_effect = notifications

    @asynccontextmanager
    async def connection(*args, **kwargs):
        yield rpc

    with (
        patch("adapters.codex.native.connection", connection),
        patch("adapters.codex.native.configurations", return_value={"codex": {}}),
        patch("adapters.codex.native.inventory", return_value={"codex": []}),
    ):
        results = [
            asyncio.run(
                run_native(
                    {"binary": "fixture"},
                    "Prompt",
                    lambda *a: None,
                    {},
                    None,
                    "low",
                    tmp_path,
                    None,
                )
            )
            for _ in range(runs)
        ]
    return rpc, results


COMPLETED = {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}


def test_native_codex_resumes_thread_metadata_only(tmp_path):
    rpc, _ = native_codex_turn(tmp_path, [COMPLETED, COMPLETED], runs=2)
    resume = [c.args[1] for c in rpc.call.await_args_list if c.args[0] == "thread/resume"]
    assert len(resume) == 1 and resume[0]["excludeTurns"] is True


def test_native_codex_waits_out_a_retry_codex_announces(tmp_path):
    retry = {
        "method": "error",
        "params": {"error": {"message": "Reconnecting... 1/5"}, "willRetry": True},
    }
    delta = {"method": "item/agentMessage/delta", "params": {"delta": "done"}}
    _, results = native_codex_turn(tmp_path, [retry, delta, COMPLETED])
    assert results[0]["answer"] == "done"


def test_native_codex_failure_keeps_the_provider_message(tmp_path):
    failure = {
        "method": "error",
        "params": {
            "error": {"message": "You've hit your usage limit. Try again at 9:00 PM."},
            "willRetry": False,
        },
    }
    with pytest.raises(ToolError, match="^codex_execution_failed: You've hit your usage limit"):
        native_codex_turn(tmp_path, [failure])
