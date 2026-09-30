import json

from agent_service import maestro
from agent_service.invocations import normalize_chips


def test_chips_normalize_in_prompt_order_with_verbatim_args():
    items = [
        {
            "id": "b",
            "resource_id": "catalog/demo/commands/build.md",
            "kind": "command",
            "name": "build",
        },
        {
            "id": "a",
            "resource_id": "project/agents/reviewer.md",
            "kind": "agent",
            "name": "reviewer",
        },
    ]
    refs = [{"id": "a", "token": "/reviewer"}, {"id": "b", "token": "/build"}]
    result = normalize_chips('/build  "x y"\n/reviewer check  ', refs, items)
    assert [i.kind for i in result] == ["command", "agent"]
    assert result[0].args == ' "x y"\n'
    assert result[1].args == "check  "
    assert [i.order for i in result] == [0, 1]
    assert result[1].mode == "delegated"


def test_legacy_planner_remains_accepted_and_canonical():
    step = {
        "role": "reviewer",
        "backend": "codex",
        "model": "m",
        "effort": "low",
        "task": "  review\n",
        "reason": "verify",
    }
    plan = maestro.validate_plan(
        json.dumps({"steps": [step]}), [{"backend": "codex", "model": "m", "efforts": ["low"]}]
    )
    assert plan["steps"][0]["role"] == "reviewer"
    assert plan["steps"][0]["invocation"]["args"] == "  review\n"
    assert plan["steps"][0]["invocation"]["resource_id"] == "builtin/roles/reviewer"


def invocation_service(tmp_path, monkeypatch):
    from test_workspaces import config

    from agent_service.app import Service

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    root = tmp_path / "project"
    agents = root / ".codex/agents"
    agents.mkdir(parents=True)
    for name, mode in (
        ("reviewer", "delegated"),
        ("writer", "delegated"),
        ("discussion", "conversational"),
    ):
        (agents / (name + ".toml")).write_text(
            f'name="{name}"\ndescription="Synthetic role"\ndeveloper_instructions="Help"\nmode="{mode}"'
        )
    configuration = config(tmp_path / "state")
    configuration["projects"]["p"]["root"] = str(root)
    service = Service(configuration)
    return service, ("a", configuration["clients"]["a"])


def test_service_normalizes_chips_rejects_conversational_chain(tmp_path, monkeypatch):
    import pytest

    from agent_service.errors import APIError

    service, identity = invocation_service(tmp_path, monkeypatch)
    items = service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
    refs = [
        {"id": item["id"], "revision": item["revision"], "token": "/" + item["name"]}
        for item in items
        if item["name"] in ("discussion", "reviewer")
    ]
    with pytest.raises(APIError, match="conversational_chain_unsupported"):
        service.submit(
            identity,
            {
                "project_id": "p",
                "backend": "codex",
                "model": "gpt-6-astra",
                "effort": "low",
                "prompt": "/discussion hello /reviewer check",
                "resource_selections": refs,
            },
        )
    service.db.close()


def test_canonical_request_retains_arguments_and_persona_until_released(tmp_path, monkeypatch):
    service, identity = invocation_service(tmp_path, monkeypatch)
    item = next(
        item
        for item in service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
        if item["name"] == "discussion"
    )
    first = service.submit(
        identity,
        {
            "project_id": "p",
            "backend": "codex",
            "model": "gpt-6-astra",
            "effort": "low",
            "invocations": [
                {
                    "kind": "agent",
                    "resource_id": item["resource_id"],
                    "args": "  hello\n",
                    "order": 0,
                    "mode": "conversational",
                }
            ],
        },
    )
    payload = json.loads(service.job(identity, first["job_id"])["payload"])
    assert payload["invocations"][0]["args"] == "  hello\n"
    service.db.execute("UPDATE jobs SET state='completed' WHERE id=?", (first["job_id"],))
    service.db.commit()
    second = service.submit(
        identity,
        {
            "project_id": "p",
            "backend": "codex",
            "model": "gpt-6-astra",
            "effort": "low",
            "parent_job_id": first["job_id"],
            "prompt": "continue",
        },
    )
    payload = json.loads(service.job(identity, second["job_id"])["payload"])
    assert payload["invocations"][0]["mode"] == "conversational"
    service.db.execute("UPDATE jobs SET state='completed' WHERE id=?", (second["job_id"],))
    service.db.commit()
    third = service.submit(
        identity,
        {
            "project_id": "p",
            "backend": "codex",
            "model": "gpt-6-astra",
            "effort": "low",
            "parent_job_id": second["job_id"],
            "prompt": "ordinary chat",
            "release_persona": True,
        },
    )
    assert not json.loads(service.job(identity, third["job_id"])["payload"]).get("invocations")
    service.db.close()


def test_inline_expansion_preserves_verbatim_arguments():
    from agent_service.resources import prepare_prompt

    item = {"kind": "command", "name": "inspect", "_body": "ARGS=[$ARGUMENTS]"}
    assert prepare_prompt('/inspect  "two words"  ', [item]) == 'ARGS=[ "two words"  ]'


def test_explicit_invocation_cannot_disagree_with_chip_arguments(tmp_path, monkeypatch):
    import pytest

    from agent_service.errors import APIError

    service, identity = invocation_service(tmp_path, monkeypatch)
    item = next(
        item
        for item in service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
        if item["name"] == "reviewer"
    )
    with pytest.raises(APIError, match="invocation_selection_mismatch"):
        service.submit(
            identity,
            {
                "project_id": "p",
                "backend": "codex",
                "model": "gpt-6-astra",
                "effort": "low",
                "prompt": "/reviewer actual",
                "resource_selections": [
                    {"id": item["id"], "revision": item["revision"], "token": "/reviewer"}
                ],
                "invocations": [
                    {
                        "kind": "agent",
                        "resource_id": item["resource_id"],
                        "args": "different",
                        "mode": "delegated",
                        "order": 0,
                    }
                ],
            },
        )
    service.db.close()


def test_conversational_resource_supplies_body_without_delegation():
    from agent_service.resources import prepare_prompt

    prompt = prepare_prompt(
        "/discussion hello",
        [
            {
                "name": "discussion",
                "kind": "agent",
                "mode": "conversational",
                "source": "catalog/agents/discussion.md",
                "_body": "Synthetic persona instructions.",
            }
        ],
    )
    assert "Synthetic persona instructions." in prompt
    assert "Delegate" not in prompt
