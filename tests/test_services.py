"""The service layer keeps the historical Service surface while delegating."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from test_workspaces import config, single_owner_config

from agent_service import app
from agent_service.config import runtime_job_affected, validate_runtime_config
from agent_service.services import queue_worker
from agent_service.services.conversation_service import ConversationService
from control import runtime_config
from control.server import Manager


def test_service_alias_and_project_folder_sets_are_shared(tmp_path):
    assert app.Service is ConversationService
    service = ConversationService(config(tmp_path))
    try:
        projects = service.project_service
        assert service.deleted_project_folders is projects.deleted_project_folders
        assert service.deleting_project_folders is projects.deleting_project_folders
        assert service.config is projects.config
        service.deleting_project_folders.add("p")
        assert "p" in projects.deleting_project_folders
    finally:
        service.db.close()


def test_queue_methods_delegate_to_the_queue_worker(tmp_path):
    service = ConversationService(config(tmp_path))
    try:
        with patch.object(queue_worker, "next_job", return_value="row") as next_job:
            assert service.next_job() == "row"
        next_job.assert_called_once_with(service)
        with patch.object(queue_worker, "cancel", return_value={"ok": True}) as cancel:
            assert service.cancel(("a", {}), "job") == {"ok": True}
        cancel.assert_called_once_with(service, ("a", {}), "job")
    finally:
        service.db.close()


def test_runtime_config_helpers_are_pure_functions(tmp_path):
    current = single_owner_config(tmp_path)
    assert validate_runtime_config(current)
    assert not validate_runtime_config({**current, "services": []})
    row = {"id": "j", "owner": "local", "project": "p", "state": "queued", "payload": "{}"}
    assert runtime_job_affected(current, {}, row, {**current, "projects": {}})


def test_runtime_config_holds_only_the_owner(tmp_path):
    current = single_owner_config(tmp_path)
    assert validate_runtime_config({**current, "tailscale_logins": {"me@example.test": "local"}})
    # An old config with a per-login client would otherwise be granted owner rights.
    old_client = {"sha256": "0" * 64, "projects": ["p"]}
    assert not validate_runtime_config(
        {**current, "clients": {**current["clients"], "tailnet-0123abcd": old_client}}
    )
    assert not validate_runtime_config({**current, "clients": {"a": old_client}})
    assert not validate_runtime_config({**current, "tailscale_logins": {"me@example.test": "a"}})
    assert not validate_runtime_config({**current, "tailscale_logins": ["me@example.test"]})


def test_a_control_built_config_validates(tmp_path):
    current = single_owner_config(tmp_path)
    current["clients"] = {}
    runtime_config.build_clients(
        current, {"logins": ["me@example.test", "you@example.test"]}, tmp_path, {}
    )
    assert set(current["clients"]) == {"local"}
    assert validate_runtime_config(current)


@pytest.mark.parametrize("local_access", [True, False])
def test_every_config_the_control_writes_validates_at_startup(tmp_path, local_access):
    # First run (nothing enabled, no previous runtime); the control only binds loopback, so a
    # local_access-off config is the same output with the flag cleared.
    manager = Manager(tmp_path)
    manager.inventory = {"network": {"hostname": None}, "services": [], "binaries": {}}
    cfg = asyncio.run(manager.build_runtime_config(manager.settings, allow_empty=True))
    assert cfg["local_access"] is True
    cfg["local_access"] = local_access
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(cfg))
    assert app.read_startup_config(path) == cfg


def test_startup_refuses_a_config_naming_another_client(tmp_path):
    current = single_owner_config(tmp_path)
    current["clients"]["tailnet-0123abcd"] = {"sha256": "0" * 64, "projects": ["p"]}
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(current))
    with pytest.raises(SystemExit, match="runtime_config_invalid"):
        app.read_startup_config(path)
    path.write_text("[]")
    with pytest.raises(SystemExit, match="runtime_config_invalid"):
        app.read_startup_config(path)


def test_a_queued_job_of_an_unknown_owner_is_not_run(tmp_path):
    async def scenario():
        service = ConversationService(single_owner_config(tmp_path))
        try:
            service.db.execute(
                "INSERT INTO jobs(id,project,owner,state,created,payload,result,idem,digest)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                ("orphan", "p", "tailnet-0123abcd", "queued", 1, json.dumps(payload), None, None, "o"),
            )
            service.db.commit()
            service.execute = AsyncMock(return_value={"answer": "must not run"})
            worker = asyncio.create_task(service.worker())
            for _ in range(200):
                if service.conversation_repository.state("orphan")[0] != "queued":
                    break
                await asyncio.sleep(0.005)
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            state = service.conversation_repository.state("orphan")[0]
            result = json.loads(service.conversation_repository.get("orphan")["result"])
            return state, result["error"], service.execute.await_count
        finally:
            service.db.close()

    payload = {"backend": "codex", "model": "gpt-6-astra", "prompt": "orphan", "project_id": "p"}
    assert asyncio.run(scenario()) == ("failed", "owner_unknown", 0)
