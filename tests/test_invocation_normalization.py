import json

from agent_service import maestro
from agent_service.invocations import normalize_chips


def test_chips_normalize_in_prompt_order_with_verbatim_args():
    items = [{"id": "b", "resource_id": "catalog/demo/commands/build.md", "kind": "command", "name": "build"}, {"id": "a", "resource_id": "project/agents/reviewer.md", "kind": "agent", "name": "reviewer"}]
    refs = [{"id": "a", "token": "/reviewer"}, {"id": "b", "token": "/build"}]
    result = normalize_chips('/build  "x y"\n/reviewer check  ', refs, items)
    assert [i.kind for i in result] == ["command", "agent"]
    assert result[0].args == ' "x y"\n'
    assert result[1].args == 'check  '
    assert [i.order for i in result] == [0, 1]
    assert result[1].mode == "delegated"


def test_legacy_planner_remains_accepted_and_canonical():
    step = {"role": "reviewer", "backend": "codex", "model": "m", "effort": "low", "task": "  review\n", "reason": "verify"}
    plan = maestro.validate_plan(json.dumps({"steps": [step]}), [{"backend": "codex", "model": "m", "efforts": ["low"]}])
    assert plan["steps"][0]["role"] == "reviewer"
    assert plan["steps"][0]["invocation"]["args"] == "  review\n"
    assert plan["steps"][0]["invocation"]["resource_id"] == "builtin/roles/reviewer"
