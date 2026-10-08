import asyncio
import copy
import json

import pytest
from starlette.testclient import TestClient
from test_workspaces import single_owner_config as config

from agent_service.app import APIError, Service, create_app
from control.manager import Manager


def payload(backend="codex", model="codex-test"):
    return {"backend": backend, "model": model, "prompt": "fixture", "project_id": "p"}


def queued(service, ident, data):
    service.db.execute(
        "INSERT INTO jobs(id,project,owner,state,created,payload,result,idem,digest) VALUES(?,?,?,?,?,?,?,?,?)",
        (ident, "p", "local", "queued", 1, json.dumps(data), None, None, ident),
    )
    service.db.commit()


def test_reload_cancels_only_removed_model_and_keeps_registered_projects(tmp_path):
    cfg = config(tmp_path)
    cfg["project_registration"] = True
    cfg["services"]["stable"] = {
        "enabled": True,
        "models": ["stable-model"],
        "projects": ["p"],
        "permissions": {"read": True},
    }
    service = Service(copy.deepcopy(cfg))
    try:
        registered = tmp_path.parent / (tmp_path.name + "-registered")
        registered.mkdir()
        project = service.add_project({"root": str(registered), "label": "Registered"})
        queued(service, "removed", payload())
        queued(service, "kept", payload("stable", "stable-model"))
        service.db.execute(
            "INSERT INTO jobs(id,project,owner,state,created,payload,result,idem,digest) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "registered",
                project,
                "local",
                "queued",
                1,
                json.dumps({**payload("stable", "stable-model"), "project_id": project}),
                None,
                None,
                "registered",
            ),
        )
        service.db.commit()
        candidate = copy.deepcopy(cfg)
        candidate["config_revision"] = "revision-two"
        candidate["services"]["codex"]["models"] = []
        candidate["codex_models"] = {}

        asyncio.run(service.apply_runtime_config(candidate))

        assert service.job(("local", service.config["clients"]["local"]), "removed")["state"] == "cancelled"
        assert service.job(("local", service.config["clients"]["local"]), "kept")["state"] == "queued"
        assert service.job(("local", service.config["clients"]["local"]), "registered")["state"] == "queued"
        assert service.config["projects"][project]["root"] == str(registered)
        assert service.config["config_revision"] == "revision-two"
    finally:
        service.db.close()


def test_reload_rejects_malformed_and_immutable_changes(tmp_path):
    cfg = config(tmp_path)
    service = Service(copy.deepcopy(cfg))
    try:
        changed = copy.deepcopy(cfg)
        changed["state_dir"] = str(tmp_path / "other")
        with pytest.raises(APIError, match="runtime_immutable_changed"):
            asyncio.run(service.apply_runtime_config(changed))
        assert service.config["state_dir"] == cfg["state_dir"]
    finally:
        service.db.close()


def test_reload_keeps_last_config_for_malformed_nested_service(tmp_path):
    cfg = config(tmp_path)
    service = Service(copy.deepcopy(cfg))
    try:
        malformed = copy.deepcopy(cfg)
        malformed["services"]["codex"]["models"] = {"not": "a list"}
        with pytest.raises(APIError, match="runtime_config_invalid"):
            asyncio.run(service.apply_runtime_config(malformed))
        assert service.config["services"]["codex"]["models"] == cfg["services"]["codex"]["models"]
    finally:
        service.db.close()


def test_reload_revokes_client_and_only_affected_local_runtime(tmp_path):
    cfg = config(tmp_path)
    cfg["services"]["local"] = {
        "enabled": True,
        "models": ["one", "two"],
        "projects": ["p"],
        "permissions": {"read": True},
    }
    cfg["local"] = {
        "model_roots": {"one": ["/one"], "two": ["/two"]},
        "local_models": {"one": {"endpoint": "first"}, "two": {"endpoint": "second"}},
    }
    service = Service(copy.deepcopy(cfg))
    try:
        queued(service, "one", payload("local", "one"))
        queued(service, "two", payload("local", "two"))
        candidate = copy.deepcopy(cfg)
        candidate["local"]["local_models"]["one"] = {"endpoint": "replacement"}
        asyncio.run(service.apply_runtime_config(candidate))
        identity = ("local", service.config["clients"]["local"])
        assert service.job(identity, "one")["state"] == "cancelled"
        assert service.job(identity, "two")["state"] == "queued"

        candidate = copy.deepcopy(service.config)
        candidate["clients"]["local"]["projects"] = []
        asyncio.run(service.apply_runtime_config(candidate))
        assert (
            service.db.execute("SELECT state FROM jobs WHERE id='two'").fetchone()[0] == "cancelled"
        )
        assert service.config["clients"]["local"]["projects"] == [], (
            "reload must not restore revoked grants"
        )
    finally:
        service.db.close()


def test_reload_cancels_active_job_when_its_permissions_are_revoked(tmp_path):
    cfg = config(tmp_path)
    cfg["services"]["codex"]["models"] = ["codex-test"]
    service = Service(copy.deepcopy(cfg))

    async def scenario():
        queued(service, "active", payload())
        with service.db:
            service.db.execute("UPDATE jobs SET state='running' WHERE id='active'")
        service.active = "active"
        service.task = asyncio.create_task(asyncio.sleep(10))
        candidate = copy.deepcopy(cfg)
        candidate["services"]["codex"]["permissions"]["read"] = False
        await service.apply_runtime_config(candidate)
        assert service.task.cancelled() is False
        with pytest.raises(asyncio.CancelledError):
            await service.task
        assert service.cancellation_reasons["active"] == "configuration_changed"

    try:
        asyncio.run(scenario())
    finally:
        service.db.close()


def test_reload_cancels_active_job_when_its_model_is_removed(tmp_path):
    cfg = config(tmp_path)
    cfg["services"]["codex"]["models"] = ["codex-test"]
    service = Service(copy.deepcopy(cfg))

    async def scenario():
        queued(service, "active-removed", payload())
        with service.db:
            service.db.execute("UPDATE jobs SET state='running' WHERE id='active-removed'")
        service.active = "active-removed"
        service.task = asyncio.create_task(asyncio.sleep(10))
        candidate = copy.deepcopy(cfg)
        candidate["services"]["codex"]["models"] = []
        await service.apply_runtime_config(candidate)
        with pytest.raises(asyncio.CancelledError):
            await service.task
        assert service.cancellation_reasons["active-removed"] == "model_removed"

    try:
        asyncio.run(scenario())
    finally:
        service.db.close()


def test_reload_cancels_active_maestro_when_its_resolved_local_model_is_removed(
    tmp_path,
):
    cfg = config(tmp_path)
    cfg["services"]["local"] = {
        "enabled": True,
        "models": ["one"],
        "projects": ["p"],
        "permissions": {"read": True},
    }
    service = Service(copy.deepcopy(cfg))

    async def scenario():
        queued(service, "maestro-active", payload("maestro", "auto"))
        with service.db:
            service.db.execute("UPDATE jobs SET state='running' WHERE id='maestro-active'")
        service.active = "maestro-active"
        service.active_executors["maestro-active"] = ("local", "one")
        service.task = asyncio.create_task(asyncio.sleep(10))
        candidate = copy.deepcopy(cfg)
        candidate["services"]["local"]["models"] = []
        await service.apply_runtime_config(candidate)
        with pytest.raises(asyncio.CancelledError):
            await service.task
        assert service.cancellation_reasons["maestro-active"] == "model_removed"

    try:
        asyncio.run(scenario())
    finally:
        service.db.close()


def test_watcher_applies_atomic_runtime_file_and_reports_invalid_config(tmp_path):
    cfg = config(tmp_path)
    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps(cfg))
    app = create_app(copy.deepcopy(cfg), runtime_path=runtime)
    with TestClient(app, headers={"Authorization": "Bearer a"}) as client:
        candidate = copy.deepcopy(cfg)
        candidate["config_revision"] = "watched-revision"
        replacement = runtime.with_suffix(".new")
        replacement.write_text(json.dumps(candidate))
        replacement.replace(runtime)
        for _ in range(30):
            if client.get("/v1/version").json().get("config_revision") == "watched-revision":
                break
            import time

            time.sleep(0.03)
        assert client.get("/v1/version").json()["config_revision"] == "watched-revision"
        malformed = copy.deepcopy(candidate)
        malformed["services"]["codex"]["models"] = {"invalid": True}
        runtime.write_text(json.dumps(malformed))
        for _ in range(30):
            if client.get("/v1/version").json().get("config_reload_error"):
                break
            import time

            time.sleep(0.03)
        assert client.get("/v1/version").json()["config_reload_error"]
        assert app.state.service.config["config_revision"] == "watched-revision"


def test_claude_login_revision_keeps_queued_and_running_claude_jobs(tmp_path):
    cfg = config(tmp_path)
    cfg["services"]["claude"] = {
        "enabled": True,
        "models": ["claude-test"],
        "projects": ["p"],
        "permissions": {"read": True},
    }
    cfg["claude"] = {"binary": "/fixture", "use_cli_login": True}
    service = Service(copy.deepcopy(cfg))

    async def scenario():
        queued(service, "running-claude", payload("claude", "claude-test"))
        queued(service, "queued-claude", payload("claude", "claude-test"))
        with service.db:
            service.db.execute("UPDATE jobs SET state='running' WHERE id='running-claude'")
        service.active = "running-claude"
        service.task = asyncio.create_task(asyncio.sleep(10))
        # What the admin panel writes when a Claude login completes.
        manager = Manager(tmp_path / "control")
        manager._write_runtime({"claude": {"binary": "/fixture"}, "provider_revisions": {}})
        await manager.claude_login_completed()
        runtime = manager._previous_runtime()
        candidate = copy.deepcopy(cfg)
        candidate["config_revision"] = "after-login"
        candidate["provider_revisions"] = runtime["provider_revisions"]
        candidate["account_revisions"] = runtime["account_revisions"]
        await service.apply_runtime_config(candidate)
        assert not service.task.done()
        service.task.cancel()
        assert service.config["account_revisions"] == runtime["account_revisions"]
        assert service.cancellation_reasons == {}
        rows = service.db.execute("SELECT id,state FROM jobs")
        states = {row["id"]: row["state"] for row in rows}
        assert states == {"running-claude": "running", "queued-claude": "queued"}
        events = service.db.execute("SELECT type FROM events WHERE type='configuration_changed'")
        assert events.fetchall() == []

    try:
        asyncio.run(scenario())
    finally:
        service.db.close()
