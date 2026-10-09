"""Exercise the installed Codex transport against a local, stateless API fixture.

No credentials, internet service or inference is used. Each adapter call starts a
new CLI process, so the second turn must restore persisted client-side history.
"""

import asyncio
import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock, patch

import pytest

from adapters.deepseek.backend import run_native


@pytest.mark.parametrize(
    "effort,next_model,next_effort",
    [(effort, "deepseek-flash", effort) for effort in ("configured", "none", "high", "max")]
    + [("low", "deepseek-v4-pro", "max")],
)
def test_deepseek_resumes_full_history_with_reasoning(
    tmp_path, monkeypatch, effort, next_model, next_effort
):
    binary = shutil.which("codex")
    if not binary:
        pytest.skip("Installed Codex is required for the local wire contract")
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            number = len(requests)
            output = [
                {
                    "type": "reasoning",
                    "id": f"rs_{number}",
                    "summary": [],
                    "content": [
                        {
                            "type": "reasoning_text",
                            "text": f"reasoning-fixture-{number}",
                        }
                    ],
                },
                {
                    "type": "message",
                    "id": f"msg_{number}",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": f"answer-fixture-{number}",
                            "annotations": [],
                        }
                    ],
                },
            ]
            if number == 1:
                output[1] = {
                    "type": "function_call",
                    "id": "fc_fixture",
                    "call_id": "call_fixture",
                    "name": "update_plan",
                    "arguments": json.dumps({"plan": [{"step": "fixture", "status": "completed"}]}),
                }
            events = [
                {
                    "type": "response.created",
                    "response": {"id": f"resp_{number}", "status": "in_progress"},
                },
                *[
                    {
                        "type": "response.output_item.done",
                        "output_index": index,
                        "item": item,
                    }
                    for index, item in enumerate(output)
                ],
                {
                    "type": "response.completed",
                    "response": {
                        "id": f"resp_{number}",
                        "status": "completed",
                        "output": output,
                        "usage": {
                            "input_tokens": 12,
                            "output_tokens": 4,
                            "total_tokens": 16,
                        },
                    },
                },
            ]
            data = "".join(
                f"event: {item['type']}\ndata: {json.dumps(item)}\n\n" for item in events
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    home = tmp_path / "providers" / "deepseek"
    home.mkdir(mode=0o700, parents=True)
    # A user's OpenAI profile must not suppress DeepSeek reasoning on later calls.
    (home / "config.toml").write_text("model_supports_reasoning_summaries = false\n")
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    key = tmp_path / "fixture.key"
    key.write_text("fixture-not-a-real-key")
    (key.parent / "providers" / "deepseek").mkdir(mode=0o700, parents=True, exist_ok=True)
    (key.parent / "providers" / "home").mkdir(mode=0o700, parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = {
        "binary": binary,
        "api_provider": {
            "url": f"http://127.0.0.1:{server.server_port}",
            "key_file": str(key),
        },
    }
    project = {"permissions": {}}
    events = []

    async def turns():
        for prompt, model, selected_effort in (
            ("Remember fixture-one.", "deepseek-flash", effort),
            ("What did I ask before?", next_model, next_effort),
        ):
            await asyncio.wait_for(
                run_native(
                    config,
                    prompt,
                    lambda kind, data: events.append((kind, data)),
                    project,
                    model,
                    selected_effort,
                    tmp_path / "session",
                    AsyncMock(),
                ),
                timeout=25,
            )

    try:
        asyncio.run(turns())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert len(requests) == 3
    second_input = json.dumps(requests[2]["input"])
    assert "Remember fixture-one." in second_input
    assert "answer-fixture-2" in second_input
    assert "reasoning-fixture-1" in second_input
    assert any(
        item.get("type") == "function_call_output" and item.get("call_id") == "call_fixture"
        for item in requests[1]["input"]
    )
    assert "reasoning-fixture-1" in json.dumps(requests[1]["input"])
    assert all(request.get("previous_response_id") is None for request in requests)
    assert requests[1].get("conversation") is None
    assert any(kind == "session_resumed" for kind, _ in events)
    assert [r.get("reasoning", {}).get("effort") for r in requests] == [
        "high" if value == "configured" else value for value in (effort, effort, next_effort)
    ]
    assert [r["model"] for r in requests] == ["deepseek-flash", "deepseek-flash", next_model]


@pytest.mark.parametrize(
    "effort, expected",
    [
        ("configured", "high"),
        ("none", "none"),
        ("low", "low"),
        ("high", "high"),
        ("max", "max"),
    ],
)
def test_deepseek_owns_effort_and_stateless_transport_policy(tmp_path, effort, expected):
    key = tmp_path / "fixture.key"
    key.write_text("fixture-not-a-real-key")
    (key.parent / "providers" / "deepseek").mkdir(mode=0o700, parents=True, exist_ok=True)
    (key.parent / "providers" / "home").mkdir(mode=0o700, parents=True, exist_ok=True)
    config = {
        "binary": "fixture-codex",
        "api_provider": {"url": "https://api.deepseek.com", "key_file": str(key)},
    }
    with patch(
        "adapters.deepseek.backend.run_turn", AsyncMock(return_value={"answer": "ok"})
    ) as execute:
        result = asyncio.run(
            run_native(
                config,
                "prompt",
                lambda *args: None,
                {"permissions": {}},
                "deepseek-flash",
                effort,
                tmp_path / "session",
                AsyncMock(),
            )
        )
    arguments = execute.await_args.args
    assert arguments[4] == expected
    runtime = arguments[8]
    assert "model_providers.tail_api.supports_websockets=false" in runtime.command
    assert 'model_reasoning_summary="none"' in runtime.command
    assert runtime.session_metadata["adapter_spec_revision"] == 1
    assert result["context_strategy"] == "client_history_replay"


@pytest.mark.parametrize(
    "config", [{}, {"binary": "codex"}, {"binary": "codex", "api_provider": {}}]
)
def test_deepseek_never_falls_back_to_a_global_provider(tmp_path, config):
    from agent_service.tools import ToolError

    with pytest.raises(ToolError, match="deepseek_api_configuration_required"):
        asyncio.run(
            run_native(
                config,
                "prompt",
                None,
                {"permissions": {}},
                "deepseek-flash",
                "high",
                tmp_path,
                None,
            )
        )


def test_pinned_codex_shell_excludes_deepseek_key_with_hostile_config(tmp_path, monkeypatch):
    """Actual model-requested shell output, not an argv-only security assertion."""
    binary = shutil.which("codex")
    if not binary:
        pytest.skip("Installed Codex 0.157.1 is required for the shell contract")
    version = subprocess.run(
        [binary, "--version"], capture_output=True, text=True, timeout=10, check=True
    )
    assert version.stdout.strip() == "codex-cli 0.157.1"
    sentinel = "fixture-unique-shell-secret-7ed159b"
    key = tmp_path / "deepseek.key"
    key.write_text(sentinel)
    (key.parent / "providers" / "deepseek").mkdir(mode=0o700, parents=True, exist_ok=True)
    (key.parent / "providers" / "home").mkdir(mode=0o700, parents=True, exist_ok=True)
    home = tmp_path / "providers" / "deepseek"
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    (home / "config.toml").write_text(
        'cli_auth_credentials_store="auto"\n'
        '[shell_environment_policy]\ninherit="all"\nignore_default_excludes=true\n'
        '[shell_environment_policy.filters]\nKEEPHARNESS_API_KEY="include"\n"*API_KEY"="include"\n"*"="include"\n'
        '[shell_environment_policy.set]\nBENIGN_FIXTURE="preserved"\n'
    )
    monkeypatch.setenv("KEEPHARNESS_API_KEY", "hostile-inherited-key")
    requests, authorization, events = [], [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            authorization.append(self.headers.get("Authorization"))
            number = len(requests)
            if number == 1:
                output = [
                    {
                        "type": "function_call",
                        "id": "fc_env",
                        "call_id": "call_env",
                        "name": "exec_command",
                        "arguments": json.dumps({"cmd": "env", "max_output_tokens": 10000}),
                    }
                ]
            else:
                output = [
                    {
                        "type": "message",
                        "id": "msg_env",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": "shell checked", "annotations": []}
                        ],
                    }
                ]
            items = [
                {
                    "type": "response.created",
                    "response": {"id": f"resp_{number}", "status": "in_progress"},
                },
                *[
                    {"type": "response.output_item.done", "output_index": i, "item": item}
                    for i, item in enumerate(output)
                ],
                {
                    "type": "response.completed",
                    "response": {"id": f"resp_{number}", "status": "completed", "output": output},
                },
            ]
            data = "".join(
                f"event: {item['type']}\ndata: {json.dumps(item)}\n\n" for item in items
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = {
        "binary": binary,
        "unrestricted": True,
        "api_provider": {"key_file": str(key), "url": f"http://127.0.0.1:{server.server_port}"},
    }
    try:
        with patch("adapters.codex.native.resource_inputs", AsyncMock(return_value=[])):
            result = asyncio.run(
                asyncio.wait_for(
                    run_native(
                        config,
                        "Run env once.",
                        lambda kind, data: events.append((kind, data)),
                        {"access_mode": "full", "permissions": {"shell": True}},
                        "deepseek-flash",
                        "high",
                        tmp_path / "session",
                        AsyncMock(return_value=True),
                    ),
                    timeout=30,
                )
            )
            from agent_service.tools import ToolError

            # A failed OS credential probe refuses even with a working pinned CLI/API.
            with (
                patch(
                    "adapters.deepseek.credentials.keyring_entry_exists",
                    side_effect=PermissionError("fixture refusal"),
                ),
                patch("adapters.codex.native.connection") as spawn,
            ):
                with pytest.raises(ToolError, match="^deepseek_credential_isolation$"):
                    asyncio.run(
                        run_native(
                            config,
                            "refused",
                            lambda *_: None,
                            {"permissions": {}},
                            "deepseek-flash",
                            "high",
                            tmp_path / "session",
                            AsyncMock(),
                        )
                    )
                spawn.assert_not_called()
            assert len(requests) == 2
            # CLI table overrides merge with `set`; refuse effective reinsertion before a turn.
            marker_before = (tmp_path / "session" / "native-thread.json").read_bytes()
            with (home / "config.toml").open("a") as stream:
                stream.write('KEEPHARNESS_API_KEY="hostile-reinsertion"\n')
            with pytest.raises(ToolError, match="^deepseek_credential_isolation$"):
                asyncio.run(
                    run_native(
                        config,
                        "refused",
                        lambda *_: None,
                        {"permissions": {}},
                        "deepseek-flash",
                        "high",
                        tmp_path / "session",
                        AsyncMock(),
                    )
                )
            assert len(requests) == 2
            assert (tmp_path / "session" / "native-thread.json").read_bytes() == marker_before
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert len(requests) == 2
    assert authorization == ["Bearer " + sentinel] * 2
    outputs = [
        item["output"]
        for item in requests[1]["input"]
        if item.get("type") == "function_call_output"
    ]
    assert len(outputs) == 1
    assert "PATH=" in outputs[0] and "HOME=" in outputs[0]
    assert "KEEPHARNESS_API_KEY" not in outputs[0]
    assert sentinel not in outputs[0]
    assert "hostile-reinsertion" not in outputs[0]
    assert sentinel not in json.dumps(events)
    assert result["answer"] == "shell checked"
