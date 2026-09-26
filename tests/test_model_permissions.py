"""Model policies bind to actual GGUFs and reach admission and execution."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient
from test_local_profiles import profiles
from test_workspaces import config

from agent_service import maestro
from agent_service.app import APIError, Service, create_app
from control.local_models import launch_options, runtime_permissions, save_profile, validate_profile


@pytest.fixture(autouse=True)
def mocked_local_process_boundary():
    # Protocol tests use fake RPCs; real bubblewrap is tested in test_local_sandbox.py.
    with patch("adapters.local.backend.wrap", side_effect=lambda command, *args: command):
        yield


def model_config(tmp_path):
    cfg = config(tmp_path)
    cfg["services"].pop("codex")
    cfg["services"]["local"].update(
        mode="native",
        models=["qwen", "gemma"],
        permissions={
            name: True for name in ("read", "write", "upload", "internet", "shell", "hooks")
        },
        model_permissions={
            "qwen": {"read": True, "upload": True, "internet": True},
            "gemma": {"read": False, "upload": False, "internet": False},
        },
    )
    cfg["projects"]["p"]["root"] = str(tmp_path)
    cfg["local"] = {"binary": "/not-executed", "integrations": ["mcp:must-not-leak"]}
    cfg["local_models"] = ["qwen", "gemma"]
    return cfg


def test_profile_policy_binding_and_tool_gate(tmp_path):
    qwen, gemma = profiles(tmp_path)
    for profile in (qwen, gemma):
        save_profile(
            tmp_path,
            validate_profile(
                {
                    **profile,
                    "permissions": {"read": True, "upload": True, "internet": True},
                    "capabilities": {"tools": profile == qwen},
                }
            ),
        )
    runtimes = [
        {"id": "q", "model_file": qwen["model_file"]},
        {"id": "g", "model_file": gemma["model_file"]},
    ]
    bound = runtime_permissions(tmp_path, runtimes, ["q", "g", "unknown"])
    assert bound["q"]["read"] and bound["q"]["internet"] and bound["q"]["upload"]
    assert bound["g"]["upload"] and not bound["g"]["read"] and not bound["g"]["internet"]
    assert not any(bound["unknown"].values())
    assert not any(runtime_permissions(tmp_path, runtimes + [runtimes[0]], ["q"])["q"].values())
    # Reimporting hardware does not erase this same model's access choices.
    save_profile(tmp_path, {**qwen, "performance": {"threads": "4"}})
    assert runtime_permissions(tmp_path, runtimes, ["q"])["q"]["internet"]


def test_per_model_permissions_reach_inference_and_discovery(tmp_path):
    service = Service(model_config(tmp_path))
    identity = ("a", service.config["clients"]["a"])
    try:
        assert [m["model"] for m in maestro.candidates(service.config, "p", uploads=True)] == [
            "qwen"
        ]
        for model in ("qwen", "gemma"):
            data = {"project_id": "p", "backend": "local", "model": model, "prompt": "fixture"}
            job = service.submit(identity, data)["job_id"]
            row = service.job(identity, job)
            with patch(
                "adapters.run_native",
                AsyncMock(return_value={"answer": "fixture"}),
            ) as run:
                asyncio.run(service.infer(row, data))
                backend, _, _, project = run.call_args.args[:4]
                assert project["permissions"]["internet"] is (model == "qwen")
                assert ("root" in project) is (model == "qwen")
                assert backend["integrations"] == []
        with pytest.raises(APIError) as error:
            service.assess(
                identity,
                {
                    "project_id": "p",
                    "backend": "local",
                    "model": "gemma",
                    "effort": "configured",
                    "prompt": "fixture",
                    "file_ids": ["fixture"],
                },
            )
        assert error.value.code == "uploads_denied"
        found = {m["id"]: m for m in service.models()}
        assert found["qwen"]["permissions"]["upload"] and found["qwen"]["capabilities"]["tools"]
        assert not found["gemma"]["permissions"]["upload"]
    finally:
        service.db.close()


def test_no_model_upload_cannot_use_provider_global_grant(tmp_path):
    cfg = model_config(tmp_path)
    cfg["services"]["local"]["model_permissions"]["qwen"] = {}
    app = create_app(cfg)
    client = TestClient(app, headers={"Authorization": "Bearer a"})
    try:
        response = client.post(
            "/v1/files?project_id=p", headers={"X-Filename": "x.txt"}, content=b"fixture"
        )
        assert response.status_code == 403
        assert not app.state.service.can_read_project("p")
    finally:
        client.close()
        app.state.service.db.close()


def test_multigpu_values_and_permissions_are_validated(tmp_path):
    qwen, _ = profiles(tmp_path)
    profile = validate_profile(
        {**qwen, "performance": {"main-gpu": "1", "split-mode": "layer", "tensor-split": "3,1"}}
    )
    assert launch_options(profile)[-6:] == [
        "--main-gpu",
        "1",
        "--split-mode",
        "layer",
        "--tensor-split",
        "3,1",
    ]
    for values in (
        {"main-gpu": "-1"},
        {"split-mode": "exec"},
        {"tensor-split": "nan,1"},
        {"tensor-split": "0,0"},
    ):
        with pytest.raises(ValueError):
            validate_profile({**qwen, "performance": values})
    with pytest.raises(ValueError):
        validate_profile({**qwen, "permissions": {"read": "true"}})
    with pytest.raises(ValueError):
        validate_profile({**qwen, "capabilities": {"tools": 1}})


def test_local_internet_uses_permitted_shell_not_hosted_search(tmp_path):
    from contextlib import asynccontextmanager

    from adapters import run_native as run

    recorded = {}

    class RPC:
        async def call(self, method, params):
            recorded["thread"] = params
            return {"thread": {"id": "fixture"}}

        async def send(self, method, params):
            recorded["turn"] = params

        async def receive(self):
            return {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}

    @asynccontextmanager
    async def connection(command, **kwargs):
        recorded["command"] = command
        yield RPC()

    async def approve(*args):
        raise AssertionError("No tools are executed in this fixture")

    with (
        patch("adapters.codex.native.connection", connection),
        patch("adapters.codex.native.configurations", return_value={"codex": {}}),
        patch("adapters.codex.native.inventory", return_value={"codex": []}),
    ):
        asyncio.run(
            run(
                {"binary": "fixture"},
                "fixture",
                lambda *args: None,
                {"permissions": {"internet": True, "shell": True}},
                "local-test",
                "configured",
                tmp_path / "native",
                "local",
                approve,
            )
        )
    assert 'web_search="disabled"' in recorded["command"]
    assert "features.shell_tool=true" in recorded["command"]
    assert recorded["turn"]["sandboxPolicy"]["networkAccess"] is True
    assert "python3 /tail-web-search.py" in recorded["thread"]["developerInstructions"]


def test_full_mode_auto_approves_native_requests_without_expanding_grants(tmp_path):
    cfg = model_config(tmp_path)
    cfg["services"]["local"]["model_permissions"]["qwen"] = {
        "read": True,
        "write": False,
        "shell": True,
        "internet": False,
    }
    service = Service(cfg)
    identity = ("a", cfg["clients"]["a"])
    data = {
        "project_id": "p",
        "backend": "local",
        "model": "qwen",
        "effort": "configured",
        "prompt": "fixture",
        "access_mode": "full",
    }

    async def native(*args):
        project = args[3]
        assert project["permissions"] == {
            "read": True,
            "write": False,
            "shell": True,
            "internet": False,
        }
        reply = await args[-1]("item/commandExecution/requestApproval", {"command": "pwd"})
        escaped = await args[-1]("permissions/requestApproval", {"permissions": {"network": True}})
        return {"answer": "fixture", "approved": reply["approved"], "escaped": escaped["approved"]}

    try:
        job = service.submit(identity, data)["job_id"]
        with patch("adapters.run_native", side_effect=native):
            result = asyncio.run(service.infer(service.job(identity, job), data))
        assert result["approved"] is True
        assert result["escaped"] is False
        events = service.db.execute(
            "SELECT data FROM events WHERE job=? AND type='approval_automatic'", (job,)
        )
        assert any(
            json.loads(event["data"])["scope"] == "configured_permissions" for event in events
        )
    finally:
        service.db.close()


def test_device_discovery_uses_real_reported_ids_and_fixed_arguments(tmp_path):
    import sys

    from control.local_models import runtime_details

    binary = tmp_path / "llama-server"
    binary.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """import sys
if sys.argv[1:] == ['--list-devices']:
 print('Available devices:')
 print('  Vulkan3: Fixture graphics card (8192 MiB)')
elif sys.argv[1:] == ['--help']:
 print('--main-gpu N\\n--split-mode MODE\\n--tensor-split N,N\\n--device IDS')
else: raise SystemExit(2)
"""
    )
    binary.chmod(0o700)
    result = asyncio.run(runtime_details(binary))
    assert result["devices"] == [{"id": "Vulkan3", "name": "Fixture graphics card (8192 MiB)"}]
    assert set(result["supported_flags"]) == {"main-gpu", "split-mode", "tensor-split", "device"}
    assert result["capabilities_verified"]


@pytest.mark.parametrize("allowed", [False, True])
def test_native_model_policy_network_shell_and_write_scope(tmp_path, allowed):
    from contextlib import asynccontextmanager

    from adapters import run_native as run

    recorded = {}

    class RPC:
        async def call(self, method, params):
            recorded["thread"] = params
            return {"thread": {"id": "fixture"}}

        async def send(self, method, params):
            recorded["turn"] = params

        async def receive(self):
            return {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}

    @asynccontextmanager
    async def connection(command, **kwargs):
        recorded["command"] = command
        yield RPC()

    async def approve(*args):
        raise AssertionError("No tool execution in fixture")

    project_root = tmp_path / "allowed-project"
    project_root.mkdir()
    session = tmp_path / "private-session"
    policy = {name: allowed for name in ("read", "write", "shell", "internet", "hooks", "upload")}
    with (
        patch("adapters.codex.native.connection", connection),
        patch("adapters.codex.native.configurations", return_value={"codex": {}}),
        patch("adapters.codex.native.inventory", return_value={"codex": []}),
    ):
        asyncio.run(
            run(
                {"binary": "fixture"},
                "fixture",
                lambda *args: None,
                {"root": str(project_root), "permissions": policy},
                "allowed-local" if allowed else "restricted-local",
                "configured",
                session,
                "local",
                approve,
            )
        )
    sandbox = recorded["turn"]["sandboxPolicy"]
    assert sandbox["networkAccess"] is allowed
    assert ("python3 /tail-web-search.py" in recorded["thread"]["developerInstructions"]) is allowed
    assert ("features.shell_tool=" + str(allowed).lower()) in recorded["command"]
    assert ("features.unified_exec=" + str(allowed).lower()) in recorded["command"]
    assert ("features.hooks=" + str(allowed).lower()) in recorded["command"]
    if allowed:
        assert sandbox["type"] == "workspaceWrite"
        assert sandbox["writableRoots"] == [str(project_root)]
        assert sandbox["excludeSlashTmp"] and sandbox["excludeTmpdirEnvVar"]
        assert recorded["thread"]["cwd"] == str(project_root)
    else:
        assert sandbox["type"] == "readOnly"
        assert "writableRoots" not in sandbox
        assert recorded["thread"]["cwd"] == str(session / "workspace")
    # These are protocol-policy assertions, not proof of a filesystem read jail.


def test_same_project_workspace_is_allowed_only_for_selected_model(tmp_path):
    from test_workspaces import archive

    cfg = model_config(tmp_path)
    app = create_app(cfg)
    client = TestClient(app, headers={"Authorization": "Bearer a"})
    try:
        uploaded = client.post(
            "/v1/workspaces?project_id=p",
            headers={"X-Filename": "fixture.zip"},
            content=archive({"note.txt": "private fixture"}),
        )
        assert uploaded.status_code == 201, uploaded.text
        payload = {
            "project_id": "p",
            "backend": "local",
            "effort": "configured",
            "workspace_id": uploaded.json()["workspace_id"],
            "prompt": "Read note.txt",
        }
        allowed = client.post("/v1/jobs", json={**payload, "model": "qwen"})
        denied = client.post("/v1/jobs", json={**payload, "model": "gemma"})
        assert allowed.status_code == 202, allowed.text
        assert denied.status_code == 403 and denied.json()["code"] == "workspace_permission_denied"
        assert app.state.service.task is None
    finally:
        client.close()
        app.state.service.db.close()


def test_local_denies_escalation_without_internet_and_starts_isolated_session(tmp_path):
    import json
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from adapters import run_native as run
    from adapters.local.sandbox import ISOLATION_VERSION

    received = []
    calls = []

    class Stdin:
        def write(self, value):
            received.append(json.loads(value))

        async def drain(self):
            pass

    class RPC:
        process = SimpleNamespace(stdin=Stdin())
        events = iter(
            [
                {"id": 90, "method": "item/commandExecution/requestApproval", "params": {}},
                {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
            ]
        )

        async def call(self, method, params):
            calls.append((method, params))
            return {"thread": {"id": "new-isolated-thread"}}

        async def send(self, *args):
            pass

        async def receive(self):
            return next(self.events)

    @asynccontextmanager
    async def connection(*args, **kwargs):
        yield RPC()

    async def approve(*args):
        raise AssertionError("Forbidden escalation must not reach the UI")

    session = tmp_path / "session"
    session.mkdir()
    (session / "native-thread.json").write_text(
        json.dumps({"id": "host-thread", "usage_total": {"outputTokens": 500}})
    )
    with (
        patch("adapters.codex.native.connection", connection),
        patch(
            "adapters.codex.native.configurations",
            side_effect=AssertionError("Host credentials must not be read"),
        ),
    ):
        asyncio.run(
            run(
                {"binary": "fixture"},
                "fixture",
                lambda *args: None,
                {"permissions": {"shell": True, "internet": False}},
                "fixture",
                "configured",
                session,
                "local",
                approve,
            )
        )
    assert calls[0][0] == "thread/start"
    assert calls[0][1]["config"] == {"mcp_servers": {}, "plugins": {}}
    assert received == [{"id": 90, "result": {"decision": "decline"}}]
    assert (
        json.loads((session / "native-thread.json").read_text())["isolation"] == ISOLATION_VERSION
    )


def test_old_local_session_migration_refeeds_conversation_history(tmp_path):
    import json

    cfg = model_config(tmp_path)
    service = Service(cfg)
    identity = ("a", cfg["clients"]["a"])
    try:
        first = {
            "project_id": "p",
            "backend": "local",
            "model": "qwen",
            "prompt": "remember fixture",
        }
        jid = service.submit(identity, first)["job_id"]
        service.finish(
            jid, "completed", {"answer": "remembered answer", "thread_id": "old-host-thread"}
        )
        folder = tmp_path / "sessions" / jid / "local"
        folder.mkdir(parents=True)
        (folder / "native-thread.json").write_text(json.dumps({"id": "old-host-thread"}))
        followup = {**first, "prompt": "what was said?", "parent_job_id": jid}
        next_id = service.submit(identity, followup)["job_id"]
        with patch("adapters.run_native", AsyncMock(return_value={"answer": "fixture"})) as run:
            asyncio.run(service.infer(service.job(identity, next_id), followup))
            assert "remembered answer" in run.call_args.args[1]
            assert "remember fixture" in run.call_args.args[1]
    finally:
        service.db.close()
