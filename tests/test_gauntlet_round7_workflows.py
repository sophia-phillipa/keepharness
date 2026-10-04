import asyncio
import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from test_execution_modes import service
from test_invocation_normalization import invocation_service
from test_queue_lanes import enqueue
from test_workspaces import config

from agent_service.app import Service
from agent_service.errors import APIError, ToolError
from agent_service.invocations import normalize_chips
from agent_service.resources import prepare_prompt
from agent_service.routes import conversations


def step(**fields):
    return dict(
        role="review",
        backend="codex",
        model="gpt-6-astra",
        effort="low",
        task="Review synthetic evidence",
        reason="Review",
        **fields,
    )


def test_A4_S11_valid_native_plan_approval_with_scoped_default(tmp_path):
    async def scenario():
        settings = config(tmp_path)
        settings["services"]["codex"]["mode"] = "scoped"
        service = Service(settings)
        identity = ("a", settings["clients"]["a"])
        jid = service.submit(
            identity,
            dict(project_id="p", backend="maestro", prompt="Review", execution_mode="native"),
        )["job_id"]
        row = service.job(identity, jid)
        data = json.loads(row["payload"])
        plan = {"steps": [step(requires={"mode": "native"})]}
        with patch.object(
            service,
            "infer",
            AsyncMock(side_effect=[{"answer": json.dumps(plan)}, {"answer": "done"}]),
        ) as infer:
            task = asyncio.create_task(service.execute(row))
            try:
                for _ in range(100):
                    if service.approvals or task.done():
                        break
                    await asyncio.sleep(0)
                assert service.approvals, task.exception() if task.done() else "No gate"
                gid = next(iter(service.approvals))
                failure = None
                try:
                    service.gates.resolve(gid, identity, {"choice": "approve"})
                except ToolError as error:
                    failure = str(error)
                print(
                    "S11 mode",
                    data["execution_mode"],
                    "default scoped; approval error",
                    failure,
                    "gate state",
                    service.gates.repository.get(gid)["state"],
                )
                assert failure is None, "Valid native plan cannot be approved: " + str(failure)
                assert (await task)["answer"] == "done"
                assert infer.await_count == 2
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                service.db.close()

    asyncio.run(scenario())


def test_A4_S12_invalid_mode_edit_does_not_consume_gate(tmp_path):
    async def scenario():
        settings = config(tmp_path)
        settings["services"]["codex"]["mode"] = "scoped"
        service = Service(settings)
        identity = ("a", settings["clients"]["a"])
        jid = service.submit(
            identity,
            dict(project_id="p", backend="maestro", prompt="Review", execution_mode="native"),
        )["job_id"]
        row = service.job(identity, jid)
        with patch.object(
            service,
            "infer",
            AsyncMock(
                side_effect=[{"answer": json.dumps({"steps": [step()]})}, {"answer": "done"}]
            ),
        ) as infer:
            task = asyncio.create_task(service.execute(row))
            try:
                for _ in range(100):
                    if service.approvals or task.done():
                        break
                    await asyncio.sleep(0)
                gid = next(iter(service.approvals))
                failure = None
                try:
                    service.gates.resolve(
                        gid,
                        identity,
                        {
                            "choice": "approve",
                            "plan": {"steps": [step(requires={"mode": "scoped"})]},
                        },
                    )
                except ToolError as error:
                    failure = str(error)
                await asyncio.sleep(0)
                outcome = (
                    await asyncio.gather(task, return_exceptions=True)
                    if task.done()
                    else ["pending"]
                )
                print(
                    "S12 gate state",
                    service.gates.repository.get(gid)["state"],
                    "resolve error",
                    failure,
                    "run outcome",
                    str(outcome),
                    "inference calls",
                    infer.await_count,
                )
                assert service.gates.repository.get(gid)["state"] == "pending", (
                    "Invalid edit consumes gate before execution rejects it"
                )
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                service.db.close()

    asyncio.run(scenario())


def test_A4_S13_saved_workflow_respects_native_conversation(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    settings = config(tmp_path / "state")
    settings["services"]["codex"]["mode"] = "scoped"
    root = tmp_path / "project"
    (root / "workflows").mkdir(parents=True)
    settings["projects"]["p"]["root"] = str(root)
    (root / "workflows/review.json").write_text(
        json.dumps({"id": "review", "steps": [step(requires={"mode": "native"})]})
    )
    service = Service(settings)
    identity = ("a", settings["clients"]["a"])
    try:
        item = next(
            x
            for x in service.resource_catalog(
                identity, "p", "codex", "gpt-6-astra", execution_mode="native"
            )["items"]
            if x["kind"] == "workflow"
        )
        print("S13 palette selectable", item["selectable"])
        error = None
        try:
            jid = service.submit(
                identity,
                dict(
                    project_id="p",
                    backend="codex",
                    model="gpt-6-astra",
                    effort="low",
                    execution_mode="native",
                    prompt="/review",
                    resource_selections=[
                        {"id": item["id"], "revision": item["revision"], "token": "/review"}
                    ],
                ),
            )["job_id"]
        except ToolError as failure:
            error = str(failure)
        print("S13 admission error", error)
        assert error is None, "Selectable matching native workflow rejected: " + str(error)
        with patch.object(service, "infer", AsyncMock(return_value={"answer": "done"})):
            assert asyncio.run(service.execute(service.job(identity, jid)))["answer"] == "done"
    finally:
        service.db.close()


def test_A4_S14_long_valid_workflow_argument_executes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    settings = config(tmp_path / "state")
    root = tmp_path / "project"
    (root / "workflows").mkdir(parents=True)
    settings["projects"]["p"]["root"] = str(root)
    definition = {
        "id": "review",
        "steps": [
            {
                "kind": "builtin",
                "resource_id": "builtin/roles/review",
                "args": "x" * 8001,
                "backend": "codex",
                "model": "gpt-6-astra",
                "effort": "low",
            }
        ],
    }
    (root / "workflows/review.json").write_text(json.dumps(definition))
    service = Service(settings)
    identity = ("a", settings["clients"]["a"])
    try:
        item = next(
            x
            for x in service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
            if x["kind"] == "workflow"
        )
        jid = service.submit(
            identity,
            dict(
                project_id="p",
                backend="codex",
                model="gpt-6-astra",
                effort="low",
                prompt="/review",
                resource_selections=[
                    {"id": item["id"], "revision": item["revision"], "token": "/review"}
                ],
            ),
        )["job_id"]
        error = None
        with patch.object(service, "infer", AsyncMock(return_value={"answer": "done"})) as infer:
            try:
                asyncio.run(service.execute(service.job(identity, jid)))
            except ToolError as failure:
                error = str(failure)
        print(
            "S14 selectable",
            item["selectable"],
            "admitted job",
            jid,
            "error",
            error,
            "calls",
            infer.await_count,
        )
        assert error is None, "Admitted workflow fails hidden legacy task limit: " + str(error)
    finally:
        service.db.close()


def test_blockquote_fence_does_not_capture_selected_command():
    prompt = "Example:\n> ```sh\n> /review literal-example\n> ```\n/review actual-task"
    item = {
        "id": "review",
        "resource_id": "project/p/.codex/prompts/review.md",
        "kind": "command",
        "name": "review",
        "_body": "RUN[$ARGUMENTS]",
    }
    invocation = normalize_chips(
        prompt,
        [{"id": "review", "token": "/review"}],
        [item],
    )[0]
    assert invocation.args == "actual-task"
    assert prepare_prompt(prompt, [item]) == (
        "Example:\n> ```sh\n> /review literal-example\n> ```\nRUN[actual-task]"
    )


def test_explicit_invocation_cannot_omit_required_resource_backend(tmp_path, monkeypatch):
    service, identity = invocation_service(tmp_path, monkeypatch)
    source = tmp_path / "project/.codex/agents/reviewer.toml"
    source.write_text(source.read_text() + '\nbackend="claude"\n')
    try:
        item = next(
            item
            for item in service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
            if item["name"] == "reviewer"
        )
        payload = {
            "project_id": "p",
            "backend": "codex",
            "model": "gpt-6-astra",
            "effort": "low",
            "invocations": [
                {
                    "kind": "agent",
                    "resource_id": item["resource_id"],
                    "args": "inspect",
                    "order": 0,
                    "mode": "delegated",
                }
            ],
        }
        with pytest.raises(APIError, match="invocation_backend_mismatch"):
            service.submit(identity, payload)
    finally:
        service.db.close()


def test_chip_control_rejects_required_resource_backend(tmp_path, monkeypatch):
    service, identity = invocation_service(tmp_path, monkeypatch)
    source = tmp_path / "project/.codex/agents/reviewer.toml"
    source.write_text(source.read_text() + '\nbackend="claude"\n')
    try:
        item = next(
            item
            for item in service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
            if item["name"] == "reviewer"
        )
        payload = {
            "project_id": "p",
            "backend": "codex",
            "model": "gpt-6-astra",
            "effort": "low",
            "prompt": "/reviewer inspect",
            "resource_selections": [
                {
                    "id": item["id"],
                    "revision": item["revision"],
                    "token": "/reviewer",
                }
            ],
        }
        with pytest.raises(APIError, match="invocation_backend_mismatch"):
            service.submit(identity, payload)
    finally:
        service.db.close()


def test_same_line_chain_validates_each_commands_own_arguments():
    prompt = '/first ok /second "raw'
    items = [
        {
            "id": "first",
            "resource_id": "project/p/.codex/prompts/first.md",
            "kind": "command",
            "name": "first",
            "_body": "FIRST[$1]",
        },
        {
            "id": "second",
            "resource_id": "project/p/.codex/prompts/second.md",
            "kind": "command",
            "name": "second",
            "_body": "SECOND[$ARGUMENTS]",
        },
    ]
    selections = [
        {"id": "first", "token": "/first"},
        {"id": "second", "token": "/second"},
    ]
    invocations = normalize_chips(prompt, selections, items)
    assert [value.args for value in invocations] == ["ok ", '"raw']
    assert prepare_prompt(prompt, items, selections) == prompt


def spin(count=30):
    async def wait():
        for _ in range(count):
            await asyncio.sleep(0)

    return wait()


def test_cancelled_queued_parent_wakes_newly_ready_child():
    async def scenario():
        with TemporaryDirectory() as directory:
            instance, identity = service(Path(directory))
            instance.config["projects"]["p"].pop("root", None)
            entered = []
            release = asyncio.Event()

            async def execute(row):
                entered.append(row["id"])
                if row["id"] == "blocker":
                    await release.wait()
                return {"answer": "done"}

            instance.execute = execute
            # Pins one run per provider; the default for cloud providers is 2 (D14).
            instance.config["services"]["codex"]["max_concurrent"] = 1
            enqueue(instance, "blocker", "p", "codex")
            enqueue(instance, "parent", "p", "codex")
            enqueue(instance, "child", "p", "gemini", parent="parent")
            worker = asyncio.create_task(instance.worker())
            try:
                await spin()
                assert entered == ["blocker"]
                instance.cancel(identity, "parent")
                # D16: the cancelled turn's follow-up waits for "Run queued message".
                assert [row["id"] for row in instance.conversation_repository.ready()] == []
                instance.run_queued(identity, "child")
                assert [row["id"] for row in instance.conversation_repository.ready()] == ["child"]
                await spin()
                assert instance.conversation_repository.state("child")[0] == "completed"
            finally:
                release.set()
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
                instance.db.close()

    asyncio.run(scenario())


def test_retagged_work_item_waiter_wakes_when_conflict_is_removed():
    async def scenario():
        with TemporaryDirectory() as directory:
            instance, identity = service(Path(directory))
            instance.config["projects"]["p"].pop("root", None)
            entered = []
            release = asyncio.Event()

            async def execute(row):
                entered.append(row["id"])
                if row["id"] == "holder":
                    await release.wait()
                return {"answer": "done"}

            instance.execute = execute
            enqueue(instance, "holder", "p", "codex", work_item="ITEM1")
            enqueue(instance, "waiter", "p", "gemini", work_item="ITEM1")
            worker = asyncio.create_task(instance.worker())
            try:
                await spin()
                assert entered == ["holder"]
                instance.tag_work_item(identity, "waiter", "ITEM2")
                assert instance.write_ownership.acquire("probe", "p", "ITEM2", []) is None
                instance.write_ownership.release("probe")
                await spin()
                assert instance.conversation_repository.state("waiter")[0] == "completed"
            finally:
                release.set()
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
                instance.db.close()

    asyncio.run(scenario())


def test_native_approval_rejects_reply_after_published_deadline():
    class State:
        approval_session_owner = None

    class Request:
        state = State()
        path_params = {}

    async def scenario():
        with TemporaryDirectory() as directory:
            instance, identity = service(Path(directory))
            instance.config["approval_timeout_seconds"] = 0.01
            job = instance.submit(
                identity,
                dict(project_id="p", backend="codex", model="gpt-6-astra", prompt="wait"),
            )["job_id"]
            row = instance.job(identity, job)
            events = []
            approve = instance._approval_handler(
                SimpleNamespace(row=row, data={"model": "gpt-6-astra"}, backend="codex"),
                lambda kind, data: events.append((kind, data)),
                {},
                {},
            )
            waiter = asyncio.create_task(approve("command", {}))
            await asyncio.sleep(0)
            approval_id = events[0][1]["approval_id"]
            deadline = events[0][1]["expires_at"]
            Request.path_params = {"approval": approval_id}
            time.sleep(0.03)  # Delay timer callbacks while the wall-clock deadline passes.
            assert time.time() > deadline
            with (
                patch.object(
                    conversations, "require_approval_session", lambda *args, **kwargs: None
                ),
                patch.object(conversations, "body", AsyncMock(return_value={"approved": True})),
            ):
                try:
                    response = await conversations.approval(Request(), instance, identity)
                except APIError as error:
                    response = SimpleNamespace(status_code=error.status)
            decision = await waiter
            print(
                "NATIVE EXPIRY",
                {
                    "deadline": deadline,
                    "now": time.time(),
                    "status": response.status_code,
                    "decision": decision,
                    "events": [e[0] for e in events],
                },
            )
            try:
                assert response.status_code in (404, 409)
                assert decision.get("approved") is not True
            finally:
                instance.db.close()

    asyncio.run(scenario())


def test_work_item_pattern_has_a_process_deadline():
    import subprocess
    import sys

    script = """
from agent_service.work_items import invocation_reference
from agent_service.errors import APIError
try:
    invocation_reference({}, {"work_item_pattern": "(a+)+$"},
                         {"invocations": [{"args": "a" * 100000 + "!"}]})
except APIError as error:
    assert error.code == "invalid_work_item_pattern"
else:
    raise AssertionError("Pathological matching must fail closed")
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=3)
    assert result.returncode == 0, result.stderr.decode()


def test_project_models_advertise_available_maestro(tmp_path):
    from starlette.testclient import TestClient

    from agent_service.app import create_app

    settings = config(tmp_path)
    app = create_app(settings)
    try:
        client = TestClient(app, headers={"Authorization": "Bearer a"})
        assert client.get("/v1/models?project_id=p").json()["maestro"] is True
        app.state.service.config["maestro_enabled"] = False
        assert client.get("/v1/models?project_id=p").json()["maestro"] is False
    finally:
        app.state.service.db.close()


def test_work_item_timeout_reaps_child(monkeypatch):
    from agent_service import work_items

    children = []
    original = work_items.subprocess.Popen

    def started(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(work_items.subprocess, "Popen", started)
    with pytest.raises(APIError, match="invalid_work_item_pattern"):
        work_items.invocation_reference(
            {}, {"work_item_pattern": "(a+)+$"}, {"invocations": [{"args": "a" * 100000 + "!"}]}
        )
    assert len(children) == 1
    assert children[0].returncode is not None


@pytest.mark.parametrize(
    "args, expected",
    [("TASK-1 " * 10000, "TASK-1"), ("x" * 150000, None)],
    ids=["repeated-reference", "maximum-nonmatch"],
)
def test_work_item_large_input_has_bounded_results(args, expected):
    from agent_service.work_items import invocation_reference

    assert (
        invocation_reference(
            {}, {"work_item_pattern": r"TASK-\d+"}, {"invocations": [{"args": args}]}
        )
        == expected
    )
