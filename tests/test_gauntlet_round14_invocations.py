"""Round-14 A4 invocation/adapters audit; synthetic and inference-free."""

import json

from test_workspaces import config as service_config

from agent_service.app import Service


def put(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def selection(item, token=None):
    return {
        "id": item["id"],
        "revision": item["revision"],
        "token": token or "/" + item["name"],
    }


def invocation_service(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    project = tmp_path / "project"
    put(
        project,
        ".codex/agents/review.toml",
        'name="review"\ndeveloper_instructions="Synthetic agent"',
    )
    put(
        project,
        ".agents/skills/review/SKILL.md",
        "---\nname: review\n---\nSynthetic skill",
    )
    config = service_config(tmp_path / "state")
    config["projects"]["p"]["root"] = str(project)
    service = Service(config)
    return service, ("a", config["clients"]["a"])


def test_i1_same_name_cross_kind_slash_chain_uses_selected_ids(tmp_path, monkeypatch):
    """Both chips are selectable and occurrence/ID data should disambiguate them."""
    service, identity = invocation_service(tmp_path, monkeypatch)
    try:
        items = [
            item
            for item in service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
            if item["name"] == "review"
        ]
        assert {(item["kind"], item["selectable"]) for item in items} == {
            ("agent", True),
            ("skill", True),
        }
        items.sort(key=lambda item: item["kind"])
        result = service.submit(
            identity,
            {
                "project_id": "p",
                "backend": "codex",
                "model": "gpt-6-astra",
                "effort": "low",
                "prompt": "/review agent task\n/review skill task",
                "resource_selections": [selection(item) for item in items],
            },
        )
        payload = json.loads(service.job(identity, result["job_id"])["payload"])
        assert [(item["kind"], item["args"].strip()) for item in payload["invocations"]] == [
            ("agent", "agent task"),
            ("skill", "skill task"),
        ]
    finally:
        service.db.close()


def test_i2_explicit_cross_kind_chain_is_a_positive_control(tmp_path, monkeypatch):
    service, identity = invocation_service(tmp_path, monkeypatch)
    try:
        items = [
            item
            for item in service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
            if item["name"] == "review"
        ]
        chain = [
            {
                "kind": item["kind"],
                "resource_id": item["resource_id"],
                "args": "task-" + item["kind"],
                "order": index,
                "mode": item["mode"],
            }
            for index, item in enumerate(items)
        ]
        items.sort(key=lambda item: item["kind"])
        result = service.submit(
            identity,
            {
                "project_id": "p",
                "backend": "codex",
                "model": "gpt-6-astra",
                "effort": "low",
                "prompt": "",
                "invocations": chain,
            },
        )
        payload = json.loads(service.job(identity, result["job_id"])["payload"])
        assert [item["resource_id"] for item in payload["invocations"]] == [
            item["resource_id"] for item in chain
        ]
    finally:
        service.db.close()
