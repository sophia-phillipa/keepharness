from agent_service.checkpoints import Checkpoints


def test_changed_step_preserves_prefix_but_invalidates_downstream(tmp_path):
    plan = {"steps": [{"id": "one", "task": "one"}, {"id": "two", "task": "two"}]}
    store = Checkpoints(tmp_path, "run", plan, {"prompt": "request"})
    store.save(1, {"result": {"answer": "first"}}, [])
    store.save(2, {"result": {"answer": "second"}}, [{"answer": "first"}])
    plan["steps"][1]["task"] = "changed"
    changed = Checkpoints(tmp_path, "run", plan, {"prompt": "request"})
    assert changed.load(1, []) is not None
    assert changed.load(2, [{"answer": "first"}]) is None


def test_changed_inputs_or_dependency_or_output_cannot_reuse(tmp_path):
    plan = {"steps": [{"id": "one", "deps_revisions": {"guide": "v1"}}]}
    store = Checkpoints(tmp_path, "run", plan, {"workflow_inputs": {"x": 1}})
    store.save(1, {"result": {"answer": "first"}}, [])
    changed = Checkpoints(tmp_path, "run", plan, {"workflow_inputs": {"x": 2}})
    assert changed.load(1, []) is None
    plan["steps"][0]["deps_revisions"]["guide"] = "v2"
    assert Checkpoints(tmp_path, "run", plan, {"workflow_inputs": {"x": 1}}).load(1, []) is None
