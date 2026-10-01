"""Project-scoped references survive migrations and conversation boundaries."""

import json

import pytest
from test_invocation_normalization import invocation_service

from agent_service.errors import APIError
from agent_service.persistence.db import baseline, connect, migrate


def test_work_item_migration_preserves_legacy_rows_and_is_idempotent(tmp_path):
    db = connect(tmp_path)
    baseline(db)
    db.execute("INSERT INTO jobs(id,project,payload) VALUES('old','p','{}')")
    db.commit()
    migrate(db)
    migrate(db)
    assert db.execute("SELECT work_item FROM jobs WHERE id='old'").fetchone()[0] is None
    assert "jobs_project_work_item" in {r[1] for r in db.execute("PRAGMA index_list(jobs)")}
    db.close()


def submit(service, identity, **extra):
    return service.submit(
        identity,
        dict(
            project_id="p",
            backend="codex",
            model="gpt-6-astra",
            effort="low",
            prompt="Hello",
            **extra,
        ),
    )["job_id"]


def test_pattern_assignment_from_invocation_and_cross_conversation(tmp_path, monkeypatch):
    service, identity = invocation_service(tmp_path, monkeypatch)
    service.config["projects"]["p"]["work_item_pattern"] = r"TASK-\d{4}"
    item = next(
        i
        for i in service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
        if i["name"] == "reviewer"
    )
    invocation = dict(
        kind="agent",
        resource_id=item["resource_id"],
        args="Review TASK-1234",
        order=0,
        mode="delegated",
    )
    first = submit(service, identity, invocations=[invocation])
    second = submit(service, identity, work_item="TASK-1234")
    assert service.job(identity, first)["work_item"] == "TASK-1234"
    assert service.job(identity, second)["work_item"] == "TASK-1234"
    assert service.conversation_id(service.job(identity, first)) != service.conversation_id(
        service.job(identity, second)
    )
    assert json.loads(service.job(identity, first)["payload"])["work_item"] == "TASK-1234"
    service.db.close()


def test_parent_reference_propagates_and_cannot_cross_project(tmp_path, monkeypatch):
    service, identity = invocation_service(tmp_path, monkeypatch)
    first = submit(service, identity, work_item="TASK-1234")
    child = submit(service, identity, parent_job_id=first)
    assert service.job(identity, child)["work_item"] == "TASK-1234"
    parent = service.job(identity, first)
    service.config["projects"]["other"] = {}
    identity[1]["projects"].append("other")
    with pytest.raises(APIError, match="invalid_parent_job"):
        service.resolve_work_item(identity, {"project_id": "other"}, parent=parent)
    service.db.close()


def test_only_enabled_trusted_invocation_catalog_pattern_applies(tmp_path, monkeypatch):
    service, identity = invocation_service(tmp_path, monkeypatch)
    service.config["projects"]["p"]["catalogs"] = ["demo"]
    service.config["catalogs"] = [dict(id="demo", trusted=True, work_item_pattern=r"CASE-\d+")]
    data = dict(
        project_id="p",
        invocations=[dict(resource_id="catalog/demo/commands/review.md", args="CASE-42")],
    )
    assert service.resolve_work_item(identity, data) == "CASE-42"
    service.config["catalogs"][0]["trusted"] = False
    assert service.resolve_work_item(identity, data) is None
    assert service.resolve_work_item(identity, dict(project_id="p", prompt="CASE-42")) is None
    service.db.close()


@pytest.mark.parametrize("value", ["", [], "line\nbreak", "x" * 129])
def test_manual_reference_validation(tmp_path, monkeypatch, value):
    service, identity = invocation_service(tmp_path, monkeypatch)
    with pytest.raises(APIError, match="invalid_work_item"):
        submit(service, identity, work_item=value)
    service.db.close()


def test_manual_tag_checks_owner_and_can_clear(tmp_path, monkeypatch):
    service, identity = invocation_service(tmp_path, monkeypatch)
    job = submit(service, identity)
    assert service.tag_work_item(identity, job, "TASK-1234")["work_item"] == "TASK-1234"
    service.config["clients"]["other"] = {"projects": ["p"]}
    with pytest.raises(APIError, match="job_owner_denied"):
        service.tag_work_item(("other", service.config["clients"]["other"]), job, "TASK-1234")
    assert service.tag_work_item(identity, job, None)["work_item"] is None
    service.db.close()


def test_control_preserves_project_and_catalog_patterns(tmp_path):
    import copy

    from control.runtime_config import base_config
    from control.server import Manager

    manager = Manager(tmp_path / "state")
    project = tmp_path / "project"
    catalog = tmp_path / "catalog"
    project.mkdir()
    catalog.mkdir()
    settings = copy.deepcopy(manager.settings)
    settings["catalogs"] = [
        dict(
            id="demo",
            root=str(catalog),
            kind="folder",
            trusted=True,
            namespace="demo",
            work_item_pattern=r"TASK-\d+",
        )
    ]
    settings["projects"] = [
        dict(id="p", root=str(project), catalogs=["demo"], work_item_pattern=r"CASE-\d+")
    ]
    validated = manager.validate(settings)
    runtime = base_config(validated, manager.state, 8094, "http://local/", {})
    assert runtime["projects"]["p"]["work_item_pattern"] == r"CASE-\d+"
    assert runtime["catalogs"][0]["work_item_pattern"] == r"TASK-\d+"
    settings["projects"][0]["work_item_pattern"] = "["
    with pytest.raises(ValueError, match="work.item pattern"):
        manager.validate(settings)


@pytest.mark.parametrize(
    "pattern,args,error",
    [
        ("[", "TASK-1", "invalid_work_item_pattern"),
        (r"TASK-\d+", "TASK-1 TASK-2", "ambiguous_work_item"),
    ],
)
def test_invalid_or_ambiguous_pattern_does_not_choose_arbitrary_reference(
    tmp_path, monkeypatch, pattern, args, error
):
    service, identity = invocation_service(tmp_path, monkeypatch)
    service.config["projects"]["p"]["work_item_pattern"] = pattern
    with pytest.raises(APIError, match=error):
        service.resolve_work_item(
            identity,
            dict(project_id="p", invocations=[dict(resource_id="project/review", args=args)]),
        )
    service.db.close()


def test_retry_keeps_idempotency_when_parent_reference_changes(tmp_path, monkeypatch):
    service, identity = invocation_service(tmp_path, monkeypatch)
    parent = submit(service, identity, work_item="TASK-1")
    request = dict(
        project_id="p",
        backend="codex",
        model="gpt-6-astra",
        effort="low",
        prompt="Continue",
        parent_job_id=parent,
    )
    first = service.submit(identity, request, idem="retry")
    service.tag_work_item(identity, parent, "TASK-2")
    assert service.submit(identity, request, idem="retry") == {
        "job_id": first["job_id"],
        "reused": True,
    }
    assert service.job(identity, first["job_id"])["work_item"] == "TASK-1"
    service.db.close()
