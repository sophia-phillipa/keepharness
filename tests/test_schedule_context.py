"""Scheduled tasks that carry a Harness agent and Space pages, read as they are at run time (D41)."""

# Fixtures imported from schedule_fixtures are re-declared as test arguments.
# ruff: noqa: F811

import pytest
from schedule_fixtures import (  # noqa: F401
    VALID,
    clock,
    config,
    idle_worker,
    local,
)
from test_scheduler import add, current, identity, jobs, service, tick  # noqa: F401

from agent_service import harness_agents, pages, schedules
from agent_service.errors import APIError

AGENT = {
    "name": "release-checker",
    "purpose": "Checks a release",
    "instructions": "Check the release notes.",
    "tasks": [],
    "target_output": "",
    "backend": "codex",
    "model": "gpt-6-astra",
    "effort": "low",
}
DUE = local(2026, 10, 3, 9) + 1


def make_agent(config, **fields):
    return harness_agents.create_agent(config, {**AGENT, **fields})


def make_page(config, title="Brief", body="Ship on Friday."):
    return pages.create_page(config, "a", "p", {"title": title, "body": body})


def invalid_field(config, **overrides):
    with pytest.raises(APIError) as caught:
        add(config, **overrides)
    return caught.value.code, caught.value.field


def test_a_typed_agent_marker_needs_the_agent_to_be_picked(config):
    make_agent(config)
    assert invalid_field(config, prompt="@@release-checker go") == (
        "schedule_agent_unselected",
        "prompt",
    )
    assert invalid_field(config, prompt="@@other go", agent="release-checker") == (
        "schedule_agent_unselected",
        "prompt",
    )
    assert invalid_field(config, prompt="//release-checker go")[0] == "schedule_agent_unselected"
    created = add(config, prompt="@@release-checker go", agent="release-checker")
    assert created["agent"] == "release-checker"
    # A marker inside a code fence is plain text.
    assert add(config, prompt="Explain:\n```\n@@whatever\n```")["agent"] is None


def test_the_picked_agent_must_exist_and_be_a_name(config):
    assert invalid_field(config, agent="missing-agent") == ("schedule_invalid", "agent")
    assert invalid_field(config, agent="Not A Name") == ("schedule_invalid", "agent")
    assert add(config, agent="")["agent"] is None


def test_a_paused_schedule_can_still_be_renamed_after_its_agent_is_deleted(config):
    make_agent(config)
    created = add(config, agent="release-checker")
    stored = harness_agents.repository(config)
    stored.delete("release-checker")
    changed = schedules.replace_schedule(
        config,
        "a",
        created["id"],
        {"revision": created["revision"], "enabled": False, "title": "Renamed"},
    )
    assert (changed["title"], changed["agent"]) == ("Renamed", "release-checker")


def test_a_run_selects_the_agents_current_revision(config, service, clock):
    make_agent(config)
    created = add(config, agent="release-checker")
    tick(service, DUE)
    (job,) = jobs(service)
    selection = job["payload"]["resource_selections"][0]
    first = selection["revision"]
    assert selection["id"] == "harness/agents/release-checker"
    assert job["payload"]["prompt"] == "@@release-checker " + VALID["prompt"]
    service.finish(job["id"], "completed", {"answer": "done"})
    harness_agents.replace_agent(
        config,
        "release-checker",
        {
            **AGENT,
            "purpose": "Checks a release twice",
            "revision": next(a for a in harness_agents.list_agents(config))["revision"],
        },
    )
    tick(service, local(2026, 10, 4, 9) + 1)
    second = jobs(service)[1]["payload"]["resource_selections"][0]["revision"]
    assert second != first
    assert current(config, created)["last_run"]["state"] == "submitted"


def test_a_deleted_agent_is_a_failed_run_with_a_safe_code(config, service, clock):
    make_agent(config)
    created = add(config, agent="release-checker")
    harness_agents.repository(config).delete("release-checker")
    tick(service, DUE)
    assert jobs(service) == []
    last = current(config, created)["last_run"]
    assert (last["state"], last["error"]) == ("failed", "schedule_agent_missing")


def test_page_ids_are_validated(config):
    page = make_page(config)
    assert invalid_field(config, page_ids="x") == ("schedule_invalid", "page_ids")
    assert invalid_field(config, page_ids=["nope"]) == ("schedule_invalid", "page_ids")
    assert invalid_field(config, page_ids=[page["id"], page["id"]])[1] == "page_ids"
    assert invalid_field(config, page_ids=[page["id"]] * 6)[1] == "page_ids"
    assert add(config, page_ids=[page["id"]])["page_ids"] == [page["id"]]
    assert add(config)["page_ids"] == []


def test_a_run_carries_the_current_text_of_each_page(config, service, clock):
    page = make_page(config, body="Ship on Friday.")
    created = add(config, page_ids=[page["id"]])
    pages.replace_page(
        config,
        "a",
        "p",
        page["id"],
        {"revision": page["revision"], "title": "Brief", "body": "Ship on Monday."},
    )
    tick(service, DUE)
    (job,) = jobs(service)
    prompt = job["payload"]["prompt"]
    assert prompt.startswith(VALID["prompt"] + '\n\nPage "Brief":\n```markdown\n')
    assert "Ship on Monday." in prompt and "Friday" not in prompt
    assert "resource_selections" not in job["payload"]
    assert current(config, created)["last_run"]["state"] == "submitted"


def test_markers_and_fences_inside_a_page_stay_plain_text(config, service, clock):
    body = "Notes\n@@ghost do it\n//ghost\n````\n```\n@@inner\n```\n````"
    page = make_page(config, title="@@ghost", body=body)
    add(config, page_ids=[page["id"]])
    tick(service, DUE)
    (job,) = jobs(service)
    assert job["state"] == "queued" and body in job["payload"]["prompt"]


def test_a_deleted_page_is_a_failed_run_with_a_safe_code(config, service, clock):
    page = make_page(config)
    created = add(config, page_ids=[page["id"]])
    pages.delete_page(config, "a", "p", page["id"], {"revision": page["revision"]})
    tick(service, DUE)
    assert jobs(service) == []
    last = current(config, created)["last_run"]
    assert (last["state"], last["error"]) == ("failed", "schedule_page_missing")
