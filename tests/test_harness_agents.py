"""Harness-owned agents: storage, HTTP API, resource discovery, `@@id` resolution and personas."""

import hashlib
import json
import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from agent_service import harness_agents, resources
from agent_service.app import Service, create_app
from agent_service.catalog import catalog
from agent_service.errors import APIError
from agent_service.persistence.harness_agent_repository import HarnessAgentRepository

MISSING = object()
PROVIDERS = {
    "codex": ["gpt-6-astra", "gpt-6-lite"],
    "claude": ["sonnet"],
    "deepseek": ["deepseek-chat"],
    "gemini": ["gemini-pro"],
    "local": ["installed-model"],
}
EFFORT = {
    "codex": "low",
    "claude": "configured",
    "deepseek": "low",
    "gemini": "configured",
    "local": "configured",
}


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    state, control = tmp_path / "state", tmp_path / "control"
    state.mkdir()
    control.mkdir()
    return {
        "state_dir": str(state),
        "control_state_dir": str(control),
        "origins": [],
        "projects": {"p": {}},
        "clients": {
            "a": {"sha256": hashlib.sha256(b"a").hexdigest(), "projects": ["p"]},
            "local": {"sha256": hashlib.sha256(b"local").hexdigest(), "projects": ["p"]},
        },
        "services": {
            name: {
                "enabled": True,
                "mode": "native",
                "models": list(models),
                "projects": ["p"],
                "permissions": {"read": True},
            }
            for name, models in PROVIDERS.items()
        },
        "codex_models": {"gpt-6-astra": ["low", "high"], "gpt-6-lite": ["low"]},
        "deepseek_models": {"deepseek-chat": ["low", "high"]},
    }


@pytest.fixture
def folder(config):
    return Path(config["control_state_dir"]) / "harness-agents"


@pytest.fixture
def api(config):
    """The local browser client: the only one allowed to write agents."""
    with TestClient(create_app(config), headers={"Authorization": "Bearer local"}) as client:
        yield client


@pytest.fixture
def remote(config):
    """An authenticated client that is not the local browser (a VPN or tailnet device)."""
    with TestClient(create_app(config), headers={"Authorization": "Bearer a"}) as client:
        yield client


def agent(**overrides):
    value = {
        "name": "code-reviewer",
        "purpose": "Reviews diffs for correctness.",
        "instructions": "Read the diff and report real bugs only.",
        "tasks": ["Find bugs", "Suggest fixes"],
        "target_output": "A short, ranked review.",
        "backend": "codex",
        "model": "gpt-6-astra",
        "effort": "low",
    }
    value.update(overrides)
    return {key: item for key, item in value.items() if item is not MISSING}


def selection(item, token=None):
    return {"id": item["id"], "revision": item["revision"], "token": token or "@@" + item["name"]}


def harness_item(config, name="code-reviewer", backend="codex", **kwargs):
    found = resources.discover(config, "p", backend, private=True, **kwargs)["items"]
    return next(item for item in found if item["name"] == name and item["scope"] == "harness")


# --------------------------------------------------------------------------- HTTP: create / read


def test_create_returns_agent_and_persists_a_private_file(api, config, folder):
    response = api.post("/v1/harness-agents", json=agent())
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["id"] == created["name"] == "code-reviewer"
    assert created["tasks"] == ["Find bugs", "Suggest fixes"]
    assert created["available"] is True and created["unavailable_reason"] == ""
    assert created["created_at"] and created["updated_at"]
    path = folder / "code-reviewer.json"
    text = path.read_text(encoding="utf-8")
    assert created["revision"] == hashlib.sha256(text.encode()).hexdigest()
    stored = json.loads(text)
    assert stored["id"] == "code-reviewer" and stored["instructions"].startswith("Read the diff")
    assert "revision" not in stored and "available" not in stored
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert [entry.name for entry in folder.iterdir()] == ["code-reviewer.json"]


def test_state_dir_is_used_when_there_is_no_control_state_dir(config, tmp_path):
    del config["control_state_dir"]
    created = harness_agents.create_agent(config, agent())
    assert (tmp_path / "state" / "harness-agents" / "code-reviewer.json").is_file()
    assert created["id"] == "code-reviewer"


def test_list_is_sorted_by_name_with_revision_and_availability(api):
    for name in ("zeta-agent", "alpha-agent"):
        assert api.post("/v1/harness-agents", json=agent(name=name)).status_code == 201
    listed = api.get("/v1/harness-agents")
    assert listed.status_code == 200
    assert listed.headers["Cache-Control"] == "no-store"
    agents = listed.json()["agents"]
    assert [item["name"] for item in agents] == ["alpha-agent", "zeta-agent"]
    assert all(len(item["revision"]) == 64 and item["available"] for item in agents)


def test_list_marks_agents_whose_route_is_no_longer_offered(api, config):
    api.post("/v1/harness-agents", json=agent())
    config["services"]["codex"]["models"].remove("gpt-6-astra")
    listed = api.get("/v1/harness-agents").json()["agents"][0]
    assert listed["available"] is False
    assert "gpt-6-astra" in listed["unavailable_reason"]
    config["services"]["codex"]["models"].append("gpt-6-astra")
    config["codex_models"]["gpt-6-astra"] = ["high"]
    listed = api.get("/v1/harness-agents").json()["agents"][0]
    assert listed["available"] is False and "low" in listed["unavailable_reason"]
    config["services"]["codex"]["enabled"] = False
    assert api.get("/v1/harness-agents").json()["agents"][0]["available"] is False


def test_requests_without_credentials_are_rejected(api):
    api.headers.pop("Authorization")
    for method, path in (
        ("GET", "/v1/harness-agents"),
        ("POST", "/v1/harness-agents"),
        ("PUT", "/v1/harness-agents/code-reviewer"),
        ("DELETE", "/v1/harness-agents/code-reviewer"),
    ):
        response = api.request(method, path, json={})
        assert response.status_code == 401, (method, response.text)
        assert response.json()["code"] == "authentication_required"


def test_only_the_local_client_writes_agents(api, remote, folder):
    created = api.post("/v1/harness-agents", json=agent()).json()
    before = (folder / "code-reviewer.json").read_text()
    attempts = (
        ("POST", "/v1/harness-agents", agent(name="other-agent")),
        (
            "PUT",
            "/v1/harness-agents/code-reviewer",
            {**agent(purpose="Hijacked"), "revision": created["revision"]},
        ),
        ("DELETE", "/v1/harness-agents/code-reviewer", {"revision": created["revision"]}),
    )
    for method, path, body in attempts:
        response = remote.request(method, path, json=body)
        assert response.status_code == 403, (method, response.text)
        assert response.json()["code"] == "harness_agent_local_only"
    assert (folder / "code-reviewer.json").read_text() == before
    assert [entry.name for entry in folder.iterdir()] == ["code-reviewer.json"]


def test_the_local_check_runs_before_the_body_is_read(remote):
    response = remote.post("/v1/harness-agents", content=b"not json")
    assert response.status_code == 403 and response.json()["code"] == "harness_agent_local_only"


def test_every_authenticated_client_lists_and_uses_harness_agents(api, remote):
    api.post("/v1/harness-agents", json=agent())
    listed = remote.get("/v1/harness-agents")
    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()["agents"]] == ["code-reviewer"]


def test_a_header_never_makes_a_client_local(remote):
    spoofed = {"X-Forwarded-For": "127.0.0.1", "X-Harness-Client": "local", "Host": "localhost"}
    response = remote.post("/v1/harness-agents", json=agent(), headers=spoofed)
    assert response.status_code == 403 and response.json()["code"] == "harness_agent_local_only"
    assert remote.get("/v1/harness-agents").json() == {"agents": []}


def test_the_local_browser_is_recognized_without_a_token(config, folder):
    config["local_access"] = True
    local = TestClient(create_app(config), base_url="http://localhost", client=("127.0.0.1", 50000))
    with local:
        assert local.post("/v1/harness-agents", json=agent()).status_code == 201
        proxied = local.post(
            "/v1/harness-agents",
            json=agent(name="other-agent"),
            headers={"X-Forwarded-For": "100.64.0.9"},
        )
        assert proxied.status_code == 401
    assert [entry.name for entry in folder.iterdir()] == ["code-reviewer.json"]


# --------------------------------------------------------------------------- HTTP: validation

INVALID_FIELDS = [
    ({"name": "A"}, "name"),
    ({"name": "a"}, "name"),
    ({"name": "-ab"}, "name"),
    ({"name": "a_b"}, "name"),
    ({"name": "a b"}, "name"),
    ({"name": "x" * 49}, "name"),
    ({"name": 5}, "name"),
    ({"name": MISSING}, "name"),
    ({"id": "other-agent"}, "id"),
    ({"purpose": ""}, "purpose"),
    ({"purpose": "   "}, "purpose"),
    ({"purpose": "x" * 301}, "purpose"),
    ({"purpose": 5}, "purpose"),
    ({"purpose": MISSING}, "purpose"),
    ({"purpose": "bad\x00text"}, "purpose"),
    ({"instructions": ""}, "instructions"),
    ({"instructions": "x" * 20001}, "instructions"),
    ({"instructions": None}, "instructions"),
    ({"instructions": MISSING}, "instructions"),
    ({"instructions": "\U0001f600" * 20000}, "instructions"),
    ({"tasks": "not a list"}, "tasks"),
    ({"tasks": [""]}, "tasks"),
    ({"tasks": ["x" * 201]}, "tasks"),
    ({"tasks": ["task"] * 13}, "tasks"),
    ({"tasks": [5]}, "tasks"),
    ({"target_output": "x" * 501}, "target_output"),
    ({"target_output": 5}, "target_output"),
    ({"backend": "nope"}, "backend"),
    ({"backend": ""}, "backend"),
    ({"backend": 5}, "backend"),
    ({"backend": MISSING}, "backend"),
    ({"model": "ghost-model"}, "model"),
    ({"model": MISSING}, "model"),
    ({"effort": "extreme"}, "effort"),
    ({"effort": "configured"}, "effort"),
    ({"effort": MISSING}, "effort"),
    ({"color": "red"}, "color"),
]


@pytest.mark.parametrize("overrides,field", INVALID_FIELDS)
def test_create_rejects_invalid_fields_naming_the_field(api, folder, overrides, field):
    response = api.post("/v1/harness-agents", json=agent(**overrides))
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["code"] == "harness_agent_invalid" and body["field"] == field
    assert not folder.exists() or not list(folder.iterdir())


def test_a_backend_that_is_not_enabled_is_invalid(api, config):
    config["services"]["claude"]["enabled"] = False
    response = api.post(
        "/v1/harness-agents", json=agent(backend="claude", model="sonnet", effort="configured")
    )
    assert response.status_code == 400
    assert response.json()["field"] == "backend"


def test_optional_fields_default_to_empty(api):
    created = api.post(
        "/v1/harness-agents", json=agent(tasks=MISSING, target_output=MISSING)
    ).json()
    assert created["tasks"] == [] and created["target_output"] == ""


def test_text_is_trimmed_before_it_is_measured(api):
    created = api.post(
        "/v1/harness-agents", json=agent(purpose="  padded  ", tasks=["  one  "])
    ).json()
    assert created["purpose"] == "padded" and created["tasks"] == ["one"]


def test_read_only_keys_from_a_listing_are_ignored_on_input(api):
    body = agent(
        available=True, unavailable_reason="", created_at="x", updated_at="y", revision="r"
    )
    assert api.post("/v1/harness-agents", json=body).status_code == 201


def test_duplicate_name_and_agent_limit(api, config):
    assert api.post("/v1/harness-agents", json=agent()).status_code == 201
    duplicate = api.post("/v1/harness-agents", json=agent())
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "harness_agent_exists"
    for number in range(harness_agents.MAX_AGENTS - 1):
        harness_agents.create_agent(config, agent(name="agent-%03d" % number))
    full = api.post("/v1/harness-agents", json=agent(name="one-too-many"))
    assert full.status_code == 409 and full.json()["code"] == "harness_agent_limit"
    # An existing name is reported as such even when the folder is full.
    assert api.post("/v1/harness-agents", json=agent()).json()["code"] == "harness_agent_exists"


def test_concurrent_creates_of_one_name_yield_a_single_agent(config):
    barrier = threading.Barrier(8)

    def attempt(_):
        barrier.wait()
        try:
            harness_agents.create_agent(config, agent())
            return "created"
        except APIError as error:
            return error.code

    with ThreadPoolExecutor(8) as pool:
        outcomes = list(pool.map(attempt, range(8)))
    assert outcomes.count("created") == 1
    assert set(outcomes) == {"created", "harness_agent_exists"}


# --------------------------------------------------------------------------- HTTP: replace / delete


def test_put_replaces_editable_fields_and_keeps_identity(api, folder):
    created = api.post("/v1/harness-agents", json=agent()).json()
    body = agent(
        purpose="New purpose",
        tasks=[],
        backend="local",
        model="installed-model",
        effort="configured",
    )
    body["revision"] = created["revision"]
    response = api.put("/v1/harness-agents/code-reviewer", json=body)
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["purpose"] == "New purpose" and updated["tasks"] == []
    assert updated["backend"] == "local"
    assert updated["created_at"] == created["created_at"]
    assert updated["revision"] != created["revision"]
    assert (
        updated["revision"]
        == hashlib.sha256((folder / "code-reviewer.json").read_bytes()).hexdigest()
    )
    assert api.get("/v1/harness-agents").json()["agents"][0]["revision"] == updated["revision"]
    assert [entry.name for entry in folder.iterdir()] == ["code-reviewer.json"]


def test_put_with_a_stale_revision_changes_nothing(api, folder):
    created = api.post("/v1/harness-agents", json=agent()).json()
    first = {**agent(purpose="First edit"), "revision": created["revision"]}
    assert api.put("/v1/harness-agents/code-reviewer", json=first).status_code == 200
    before = (folder / "code-reviewer.json").read_text()
    stale = {**agent(purpose="Second edit"), "revision": created["revision"]}
    response = api.put("/v1/harness-agents/code-reviewer", json=stale)
    assert response.status_code == 409 and response.json()["code"] == "harness_agent_changed"
    assert (folder / "code-reviewer.json").read_text() == before


def test_put_cannot_rename_an_agent(api):
    created = api.post("/v1/harness-agents", json=agent()).json()
    renamed = {**agent(name="other-name"), "revision": created["revision"]}
    response = api.put("/v1/harness-agents/code-reviewer", json=renamed)
    assert response.status_code == 400 and response.json()["field"] == "name"
    same = {**agent(id="code-reviewer"), "revision": created["revision"]}
    assert api.put("/v1/harness-agents/code-reviewer", json=same).status_code == 200


@pytest.mark.parametrize("revision", [MISSING, 5, ""])
def test_put_and_delete_require_a_revision(api, revision):
    api.post("/v1/harness-agents", json=agent())
    body = {
        key: value
        for key, value in {**agent(), "revision": revision}.items()
        if value is not MISSING
    }
    put = api.put("/v1/harness-agents/code-reviewer", json=body)
    assert put.status_code == 400 and put.json()["field"] == "revision"
    delete = api.request(
        "DELETE",
        "/v1/harness-agents/code-reviewer",
        json={} if revision is MISSING else {"revision": revision},
    )
    assert delete.status_code == 400 and delete.json()["field"] == "revision"


@pytest.mark.parametrize(
    "overrides,field", [item for item in INVALID_FIELDS if item[1] not in ("name", "id")]
)
def test_put_validates_like_create(api, overrides, field):
    created = api.post("/v1/harness-agents", json=agent()).json()
    body = {**agent(**overrides), "revision": created["revision"]}
    response = api.put("/v1/harness-agents/code-reviewer", json=body)
    assert response.status_code == 400 and response.json()["field"] == field


@pytest.mark.parametrize(
    "path", ["/v1/harness-agents/ghost-agent", "/v1/harness-agents/Bad_Id", "/v1/harness-agents/x"]
)
def test_unknown_or_malformed_ids_are_not_found(api, path):
    put = api.put(path, json={**agent(name=MISSING), "revision": "r"})
    delete = api.request("DELETE", path, json={"revision": "r"})
    for response in (put, delete):
        assert response.status_code == 404 and response.json()["code"] == "harness_agent_not_found"


def test_delete_removes_the_file_after_a_revision_check(api, folder):
    created = api.post("/v1/harness-agents", json=agent()).json()
    stale = api.request("DELETE", "/v1/harness-agents/code-reviewer", json={"revision": "0" * 64})
    assert stale.status_code == 409 and stale.json()["code"] == "harness_agent_changed"
    assert (folder / "code-reviewer.json").exists()
    deleted = api.request(
        "DELETE", "/v1/harness-agents/code-reviewer", json={"revision": created["revision"]}
    )
    assert deleted.status_code == 200 and deleted.json() == {"deleted": True}
    assert not (folder / "code-reviewer.json").exists()
    assert api.get("/v1/harness-agents").json() == {"agents": []}


# --------------------------------------------------------------------------- storage safety


def test_a_symlinked_folder_is_refused(api, config, folder, tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    folder.symlink_to(target, target_is_directory=True)
    for response in (api.post("/v1/harness-agents", json=agent()), api.get("/v1/harness-agents")):
        assert response.status_code == 500
        assert response.json()["code"] == "harness_agent_storage_unsafe"
    assert list(target.iterdir()) == []
    found = resources.discover(config, "p", "codex", execution_mode="native")
    assert not [item for item in found["items"] if item["scope"] == "harness"]
    assert any("Harness agents" in warning for warning in found["warnings"])


def test_a_symlinked_agent_file_is_refused_and_never_written_through(api, folder, tmp_path):
    created = api.post("/v1/harness-agents", json=agent()).json()
    outside = tmp_path / "outside.json"
    outside.write_text((folder / "code-reviewer.json").read_text())
    (folder / "code-reviewer.json").unlink()
    (folder / "code-reviewer.json").symlink_to(outside)
    assert api.get("/v1/harness-agents").json() == {"agents": []}
    body = {**agent(purpose="Changed"), "revision": created["revision"]}
    for response in (
        api.put("/v1/harness-agents/code-reviewer", json=body),
        api.request(
            "DELETE", "/v1/harness-agents/code-reviewer", json={"revision": created["revision"]}
        ),
    ):
        assert (
            response.status_code == 500
            and response.json()["code"] == "harness_agent_storage_unsafe"
        )
    assert api.post("/v1/harness-agents", json=agent()).json()["code"] == "harness_agent_exists"
    assert json.loads(outside.read_text())["purpose"] == "Reviews diffs for correctness."


def test_unreadable_files_are_skipped_not_fatal(api, config, folder):
    api.post("/v1/harness-agents", json=agent())
    valid = json.loads((folder / "code-reviewer.json").read_text())
    (folder / "broken-json.json").write_text("{not json")
    (folder / "wrong-id.json").write_text(json.dumps({**valid, "id": "other", "name": "other"}))
    (folder / "too-large.json").write_text(
        json.dumps({**valid, "id": "too-large", "name": "too-large", "padding": "x" * 70000})
    )
    (folder / "bad-field.json").write_text(
        json.dumps({**valid, "id": "bad-field", "name": "bad-field", "tasks": "no"})
    )
    (folder / "directory-entry.json").mkdir()
    (folder / "notes.txt").write_text("ignored")
    assert [item["name"] for item in api.get("/v1/harness-agents").json()["agents"]] == [
        "code-reviewer"
    ]
    found = resources.discover(config, "p", "codex", execution_mode="native")
    assert [item["name"] for item in found["items"] if item["scope"] == "harness"] == [
        "code-reviewer"
    ]
    assert sum("Could not read the Harness agent" in warning for warning in found["warnings"]) == 5


def test_an_existing_folder_is_tightened_to_owner_only(config, folder):
    folder.mkdir(mode=0o755)
    os.chmod(folder, 0o755)
    harness_agents.create_agent(config, agent())
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700


def test_a_failed_write_leaves_neither_a_file_nor_a_temp_file(folder, monkeypatch):
    repository = HarnessAgentRepository(folder)

    def broken(_descriptor):
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", broken)
        with pytest.raises(OSError, match="disk full"):
            repository.create("code-reviewer", "{}\n")
    assert list(folder.iterdir()) == []


def test_replace_is_atomic_and_cleans_up_when_it_fails(folder, monkeypatch):
    repository = HarnessAgentRepository(folder)
    repository.create("code-reviewer", '{"v": 1}\n')

    def broken(*_args, **_kwargs):
        raise OSError("rename failed")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", broken)
        with pytest.raises(OSError, match="rename failed"):
            repository.replace("code-reviewer", '{"v": 2}\n')
    assert (folder / "code-reviewer.json").read_text() == '{"v": 1}\n'
    assert [entry.name for entry in folder.iterdir()] == ["code-reviewer.json"]


# --------------------------------------------------------------------------- discovery


@pytest.mark.parametrize("backend", sorted(PROVIDERS))
def test_every_backend_lists_harness_agents_as_selectable_conversational_agents(config, backend):
    harness_agents.create_agent(config, agent())
    harness_agents.create_agent(
        config,
        agent(
            name="local-helper",
            backend=backend,
            model=PROVIDERS[backend][0],
            effort=EFFORT[backend],
        ),
    )
    found = resources.discover(config, "p", backend, execution_mode="native", private=True)
    items = {item["name"]: item for item in found["items"] if item["scope"] == "harness"}
    assert set(items) == {"code-reviewer", "local-helper"}
    item = items["local-helper"]
    assert item["id"] == item["resource_id"] == "harness/agents/local-helper"
    assert (item["kind"], item["origin"], item["group"]) == ("agent", "harness", "Your agents")
    assert (item["mode"], item["argument_hint"], item["namespace"]) == ("conversational", "", "")
    assert item["description"] == "Reviews diffs for correctness."
    assert (item["backend"], item["model"], item["effort"]) == (
        backend,
        PROVIDERS[backend][0],
        EFFORT[backend],
    )
    assert item["selectable"] is True and item["unavailable_reason"] == ""
    assert "Runs on " + PROVIDERS[backend][0] in item["preflight_hint"]
    assert len(item["revision"]) == 64 and item["native_command"] is False
    assert item["_body"].startswith('You are the agent "local-helper".')


def test_harness_agents_are_listed_even_without_native_mode(config):
    harness_agents.create_agent(config, agent())
    found = resources.discover(config, "p", "codex", execution_mode="scoped")
    assert [item["name"] for item in found["items"] if item["scope"] == "harness"] == [
        "code-reviewer"
    ]
    assert "Native resources require a native-mode execution." in found["warnings"]


def test_private_fields_only_appear_when_asked_for(config):
    harness_agents.create_agent(config, agent())
    public = resources.discover(config, "p", "codex")["items"][0]
    assert not [key for key in public if key.startswith("_")]


def test_an_agent_whose_model_is_gone_is_not_selectable(config):
    harness_agents.create_agent(config, agent())
    config["services"]["codex"]["models"].remove("gpt-6-astra")
    item = harness_item(config)
    assert item["selectable"] is False
    assert "gpt-6-astra" in item["unavailable_reason"]
    assert (
        item["preflight_hint"]
        and item["preflight_hint"]
        != "Ready to invoke with the current provider and execution mode."
    )


def test_availability_follows_the_project_the_agent_is_listed_for(config):
    harness_agents.create_agent(config, agent())
    config["projects"]["q"] = {}
    assert harness_item(config)["selectable"] is True
    found = resources.discover(config, "q", "codex", execution_mode="native")
    item = next(item for item in found["items"] if item["scope"] == "harness")
    assert item["selectable"] is False


def test_native_and_harness_agents_with_one_name_both_stay(config, tmp_path):
    root = tmp_path / "project"
    (root / ".codex/agents").mkdir(parents=True)
    (root / ".codex/agents/code-reviewer.toml").write_text(
        'name="code-reviewer"\ndescription="Native"\ndeveloper_instructions="Help"'
    )
    config["projects"]["p"]["root"] = str(root)
    harness_agents.create_agent(config, agent())
    items = resources.discover(config, "p", "codex", execution_mode="native")["items"]
    pair = [item for item in items if item["name"] == "code-reviewer"]
    assert sorted(item["scope"] for item in pair) == ["harness", "project"]
    assert len({item["id"] for item in pair}) == 2


def test_plan_dependency_lookups_skip_harness_agents(config):
    harness_agents.create_agent(config, agent())
    found = resources.discover(
        config, "p", "codex", execution_mode="native", include_workflows=False
    )
    assert not [item for item in found["items"] if item["scope"] == "harness"]


def test_project_catalog_lists_each_harness_agent_once_in_items_only(config):
    harness_agents.create_agent(config, agent())
    result = catalog(config, config["projects"]["p"], "p")
    assert [item["resource_id"] for item in result["items"] if item["scope"] == "harness"] == [
        "harness/agents/code-reviewer"
    ]
    assert "code-reviewer" not in [entry["name"] for entry in result["agents"]]


def test_configs_without_any_state_directory_list_no_harness_agents(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = {"projects": {"p": {}}, "services": {"codex": {"mode": "native"}}}
    assert resources.discover(config, "p", "codex")["items"] == []


def test_persona_is_composed_from_the_fields():
    record = agent(tasks=["Find bugs", "Suggest fixes"])
    assert harness_agents.compose_persona(record) == (
        'You are the agent "code-reviewer". Purpose: Reviews diffs for correctness.\n'
        "Follow these instructions:\n"
        "Read the diff and report real bugs only.\n"
        "Tasks you handle:\n"
        "- Find bugs\n"
        "- Suggest fixes\n"
        "Target output: A short, ranked review."
    )
    bare = harness_agents.compose_persona(agent(tasks=[], target_output=""))
    assert "Tasks you handle" not in bare and "Target output" not in bare
    assert bare.endswith("Read the diff and report real bugs only.")


# --------------------------------------------------------------------------- resolve / prepare_prompt


def resolve(config, prompt, selections, **extra):
    return resources.resolve(
        config,
        {
            "project_id": "p",
            "backend": "codex",
            "model": "gpt-6-astra",
            "execution_mode": "native",
            "prompt": prompt,
            "resource_selections": selections,
            **extra,
        },
    )


def test_at_at_id_resolves_and_the_persona_is_prepared(config):
    harness_agents.create_agent(config, agent())
    item = harness_item(config)
    selected = resolve(config, "@@code-reviewer check this", [selection(item)])
    assert [value["resource_id"] for value in selected] == ["harness/agents/code-reviewer"]
    assert selected[0]["_token"] == "@@code-reviewer"
    prompt = resources.prepare_prompt("@@code-reviewer check this", selected)
    assert prompt.startswith("@@code-reviewer check this")
    assert 'Adopt the conversational agent "code-reviewer"' in prompt
    assert item["_body"] in prompt
    assert str(config["control_state_dir"]) not in prompt


def test_the_persona_note_points_at_no_path_a_model_could_open(config):
    harness_agents.create_agent(config, agent())
    item = harness_item(config)
    selected = resolve(config, "@@code-reviewer check this", [selection(item)])
    prompt = resources.prepare_prompt("@@code-reviewer check this", selected)
    assert "harness/agents" not in prompt and ".json" not in prompt
    assert item["source"] not in prompt
    assert "defined by the user in KeepHarness" in prompt
    assert prompt.index("defined by the user in KeepHarness") < prompt.index(item["_body"])


@pytest.mark.parametrize("token", ["/code-reviewer", "@code-reviewer"])
def test_harness_agents_only_answer_to_the_reserved_token(config, token):
    harness_agents.create_agent(config, agent())
    item = harness_item(config)
    with pytest.raises(resources.ResourceError, match="resource_selection_missing"):
        resolve(config, token + " check", [selection(item, token)])


def test_a_changed_or_unavailable_harness_agent_is_rejected(config):
    created = harness_agents.create_agent(config, agent())
    item = harness_item(config)
    harness_agents.replace_agent(
        config, "code-reviewer", {**agent(purpose="Edited"), "revision": created["revision"]}
    )
    with pytest.raises(resources.ResourceError, match="resource_changed"):
        resolve(config, "@@code-reviewer go", [selection(item)])
    fresh = harness_item(config)
    config["services"]["codex"]["models"].remove("gpt-6-astra")
    with pytest.raises(resources.ResourceError, match="resource_unavailable"):
        resolve(config, "@@code-reviewer go", [selection(fresh)])


def test_unknown_or_unselected_reserved_tokens_stay_unavailable(config):
    harness_agents.create_agent(config, agent())
    item = harness_item(config)
    for prompt, selections in (
        ("@@code-reviewer go", []),
        ("@@ghost go", []),
        ("@@code-reviewer and @@ghost go", [selection(item)]),
        ("//code-reviewer go", []),
        ("//code-reviewer @@code-reviewer go", [selection(item)]),
    ):
        with pytest.raises(resources.ResourceError, match="harness_resources_unavailable"):
            resolve(config, prompt, selections)


def test_a_harness_token_cannot_ride_along_with_a_native_selection(config, tmp_path):
    root = tmp_path / "project"
    (root / ".codex/agents").mkdir(parents=True)
    (root / ".codex/agents/helper.toml").write_text('name="helper"\ndeveloper_instructions="Help"')
    config["projects"]["p"]["root"] = str(root)
    harness_agents.create_agent(config, agent())
    native = next(
        item
        for item in resources.discover(config, "p", "codex", private=True, execution_mode="native")[
            "items"
        ]
        if item["name"] == "helper"
    )
    with pytest.raises(resources.ResourceError, match="harness_resources_unavailable"):
        resolve(config, "/helper @@code-reviewer go", [selection(native, "/helper")])


def test_reserved_markers_inside_code_stay_literal(config):
    harness_agents.create_agent(config, agent())
    item = harness_item(config)
    prompt = "@@code-reviewer explain:\n```\n@@ghost\n//ghost\n```\n"
    assert resolve(config, prompt, [selection(item)])[0]["name"] == "code-reviewer"


# --------------------------------------------------------------------------- service: gate and persona


@pytest.fixture
def service(config):
    service = Service(config)
    yield service
    service.db.close()


def submit(service, **data):
    identity = ("a", service.config["clients"]["a"])
    base = {"project_id": "p", "backend": "codex", "model": "gpt-6-astra", "effort": "low"}
    return service.submit(identity, {**base, **data}), identity


def test_service_catalog_offers_harness_agents_and_the_composer_route_gate_holds(service, config):
    harness_agents.create_agent(config, agent())
    identity = ("a", config["clients"]["a"])
    items = service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
    item = next(item for item in items if item["scope"] == "harness")
    chip = [selection(item)]
    ok, _ = submit(service, prompt="@@code-reviewer review", resource_selections=chip)
    assert ok["job_id"]
    for route, code in (
        (
            {"backend": "local", "model": "installed-model", "effort": "configured"},
            "invocation_backend_mismatch",
        ),
        ({"model": "gpt-6-lite"}, "invocation_model_or_effort_mismatch"),
        ({"effort": "high"}, "invocation_model_or_effort_mismatch"),
    ):
        with pytest.raises(APIError, match=code):
            submit(service, prompt="@@code-reviewer review", resource_selections=chip, **route)


def test_follow_up_turns_keep_the_harness_persona_until_released(service, config):
    harness_agents.create_agent(config, agent())
    identity = ("a", config["clients"]["a"])
    item = next(
        item
        for item in service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
        if item["scope"] == "harness"
    )
    first, _ = submit(
        service,
        invocations=[
            {
                "kind": "agent",
                "resource_id": item["resource_id"],
                "args": " hello",
                "order": 0,
                "mode": "conversational",
            }
        ],
    )
    payload = json.loads(service.job(identity, first["job_id"])["payload"])
    assert payload["resource_selections"][0]["token"] == "@@code-reviewer"
    assert payload["prompt"].startswith("@@code-reviewer")
    service.db.execute("UPDATE jobs SET state='completed' WHERE id=?", (first["job_id"],))
    service.db.commit()
    second, _ = submit(service, parent_job_id=first["job_id"], prompt="continue")
    payload = json.loads(service.job(identity, second["job_id"])["payload"])
    assert payload["invocations"][0]["mode"] == "conversational"
    assert payload["invocations"][0]["resource_id"] == "harness/agents/code-reviewer"
    assert payload["prompt"].startswith("@@code-reviewer continue")
    selected = service.selected_resources(payload)
    assert harness_item(config)["_body"] in resources.prepare_prompt(payload["prompt"], selected)
    service.db.execute("UPDATE jobs SET state='completed' WHERE id=?", (second["job_id"],))
    service.db.commit()
    third, _ = submit(
        service, parent_job_id=second["job_id"], prompt="plain chat", release_persona=True
    )
    assert not json.loads(service.job(identity, third["job_id"])["payload"]).get("invocations")


def test_a_persona_started_before_the_rename_keeps_answering(service, config):
    # Jobs stored before 0.15.0 select the agent as "tail/agents/<id>".
    harness_agents.create_agent(config, agent())
    identity = ("a", config["clients"]["a"])
    item = harness_item(config)
    first, _ = submit(service, prompt="@@code-reviewer hi", resource_selections=[selection(item)])
    row = service.db.execute("SELECT payload FROM jobs WHERE id=?", (first["job_id"],)).fetchone()
    legacy = row[0].replace("harness/agents/", "tail/agents/")
    service.db.execute(
        "UPDATE jobs SET state='completed', payload=? WHERE id=?", (legacy, first["job_id"])
    )
    service.db.commit()
    second, _ = submit(service, parent_job_id=first["job_id"], prompt="continue")
    payload = json.loads(service.job(identity, second["job_id"])["payload"])
    assert payload["resource_selections"][0]["id"] == "harness/agents/code-reviewer"
    assert payload["invocations"][0]["resource_id"] == "harness/agents/code-reviewer"
    assert payload["prompt"].startswith("@@code-reviewer continue")


def test_the_agents_folder_from_before_the_rename_moves_on_startup(config, folder):
    harness_agents.create_agent(config, agent())
    legacy = folder.with_name("tail-agents")
    folder.rename(legacy)
    Service(config).db.close()
    assert not legacy.exists()
    assert [item["name"] for item in harness_agents.list_agents(config)] == ["code-reviewer"]


def test_the_old_agents_folder_is_never_merged_into_the_new_one(config, folder):
    harness_agents.create_agent(config, agent())
    legacy = folder.with_name("tail-agents")
    legacy.mkdir(mode=0o700)
    (legacy / "old-agent.json").write_text("{}")
    Service(config).db.close()
    assert (legacy / "old-agent.json").exists()
    assert [entry.name for entry in folder.iterdir()] == ["code-reviewer.json"]


def test_editing_the_agent_between_turns_is_reported_not_silently_applied(service, config):
    created = harness_agents.create_agent(config, agent())
    identity = ("a", config["clients"]["a"])
    item = next(
        item
        for item in service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
        if item["scope"] == "harness"
    )
    first, _ = submit(service, prompt="@@code-reviewer hi", resource_selections=[selection(item)])
    service.db.execute("UPDATE jobs SET state='completed' WHERE id=?", (first["job_id"],))
    service.db.commit()
    harness_agents.replace_agent(
        config, "code-reviewer", {**agent(purpose="Edited"), "revision": created["revision"]}
    )
    with pytest.raises(APIError, match="resource_changed"):
        submit(service, parent_job_id=first["job_id"], prompt="continue")


def test_harness_agents_skip_the_read_gate_but_file_resources_do_not(service, config):
    # A Harness persona is harness-kept text; "read" guards project and catalog files.
    config["services"]["codex"]["permissions"] = {"read": False}
    harness_agents.create_agent(config, agent())
    identity = ("a", config["clients"]["a"])
    item = next(
        item
        for item in service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
        if item["scope"] == "harness"
    )
    ok, _ = submit(service, prompt="@@code-reviewer review", resource_selections=[selection(item)])
    assert ok["job_id"]
    native = {"id": "project/p/.codex/agents/x.toml", "revision": "r", "token": "/x"}
    with pytest.raises(APIError, match="resource_read_denied"):
        submit(service, prompt="/x review", resource_selections=[native])


MALFORMED_SELECTIONS = [
    "abc",
    {"id": "x"},
    5,
    True,
    [5],
    [None],
    ["abc"],
    [[]],
    [{"id": "harness/agents/x"}, 5],
]


@pytest.mark.parametrize("selections", MALFORMED_SELECTIONS)
def test_malformed_selections_are_a_422_not_a_server_error(service, selections):
    with pytest.raises(APIError) as refused:
        submit(service, prompt="hello", resource_selections=selections)
    assert (refused.value.code, refused.value.status) == ("invalid_resource_selections", 422)


@pytest.mark.parametrize("selections", MALFORMED_SELECTIONS[:4])
def test_the_http_layer_answers_malformed_selections_with_a_422(remote, selections):
    body = {
        "project_id": "p",
        "backend": "codex",
        "model": "gpt-6-astra",
        "effort": "low",
        "prompt": "hello",
        "resource_selections": selections,
    }
    response = remote.post("/v1/jobs", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "invalid_resource_selections"


def test_a_malformed_selection_still_needs_the_read_permission(service, config):
    config["services"]["codex"]["permissions"] = {"read": False}
    for selections in ("abc", [5]):
        with pytest.raises(APIError, match="resource_read_denied"):
            submit(service, prompt="hello", resource_selections=selections)
