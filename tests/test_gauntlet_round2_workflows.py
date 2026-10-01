"""Round-two admission and atomic save regressions; all paths are temporary."""

import asyncio
import json
import os
from unittest.mock import AsyncMock, patch

import pytest
from test_resources import cfg, put
from test_workflow_resume_rerun import setup_run
from test_workflow_schema import workflow

from agent_service import maestro, resources, workflows
from agent_service.errors import APIError
from agent_service.invocations import normalize_chips


@pytest.mark.parametrize("prompt", ["Explain:\n```js\n// comment\nconst n=1;\n```", "```diff\n@@ -1 +1 @@\n-old\n+new\n```", "What is 7 // 2 in Python?", "//www.example.invalid", "@@local.example"])
def test_plain_chat_accepts_code_unchanged(tmp_path, prompt):
    service, identity, _, data, _ = setup_run(tmp_path)
    try:
        result = service.submit(identity, {**data, "prompt": prompt})
        assert json.loads(service.job(identity, result["job_id"])["payload"])["prompt"] == prompt
    finally:
        service.db.close()


def test_fenced_token_cannot_steal_selected_invocation():
    items = [{"id": name, "kind": "agent"} for name in ("reviewer", "writer")]
    selections = [{"id": item["id"], "token": "/" + item["id"]} for item in items]
    example = "Explain this example:\n```\n/writer literal\n```\n"
    result = normalize_chips("/reviewer " + example + "/writer Write the summary", selections, items)
    assert [item.args for item in result] == [example, "Write the summary"]


@pytest.mark.parametrize("body", ["Review $ARGUMENTS", "Review {{args}}", "Review without substitution"])
def test_raw_command_arguments_are_not_shell(tmp_path, body):
    item = {"name": "check", "kind": "command", "_body": body}
    prompt = "/check What's changed?"
    expected = body.replace("$ARGUMENTS", "What's changed?").replace("{{args}}", "What's changed?")
    assert resources.prepare_prompt(prompt, [item]) == expected


def test_discovered_deep_resource_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    root = tmp_path / "project"
    put(root, ".agents/skills/" + "a" * 80 + "/" + "b" * 100 + "/SKILL.md", "---\nname: deep-check\n---\nCheck")
    config = cfg(root)
    item = resources.discover(config, "p", "codex")["items"][0]
    assert 200 < len(item["id"]) <= 1000 and item["selectable"]
    selections = [{"id": item["id"], "revision": item["revision"], "token": "/deep-check"}]
    data = {"project_id": "p", "backend": "codex", "prompt": "/deep-check test", "resource_selections": selections}
    resolved = resources.resolve(config, data)
    assert normalize_chips(data["prompt"], selections, resolved)[0].resource_id == item["id"]


def test_partial_workflow_write_never_publishes_and_retry_succeeds(tmp_path, monkeypatch):
    original = os.fdopen

    class BrokenWriter:
        def __init__(self, descriptor, *args, **kwargs):
            self.stream = original(descriptor, *args, **kwargs)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def write(self, value):
            self.stream.write(value[:17])
            self.stream.flush()
            raise OSError("synthetic disk failure")

    with monkeypatch.context() as context:
        context.setattr(os, "fdopen", BrokenWriter)
        with pytest.raises(OSError, match="synthetic"):
            workflows.save_chain_as_workflow({"root": str(tmp_path)}, workflow(), "saved", successful=True)
    assert not (tmp_path / "workflows/saved.json").exists()
    path = workflows.save_chain_as_workflow({"root": str(tmp_path)}, workflow(), "saved", successful=True)
    assert json.loads(path.read_text())["id"] == "saved"
    before = path.read_bytes()
    with pytest.raises(workflows.WorkflowError, match="workflow_already_exists"):
        workflows.save_chain_as_workflow({"root": str(tmp_path)}, workflow(), "saved", successful=True)
    assert path.read_bytes() == before


def test_save_workflow_respects_live_root_ownership(tmp_path):
    service, identity, row, data, plan = setup_run(tmp_path / "state")
    root = tmp_path / "project"
    root.mkdir()
    service.config["projects"]["p"]["root"] = str(root)
    try:
        with patch.object(service, "infer", AsyncMock(return_value={"answer": "done"})):
            result = asyncio.run(maestro.execute_plan(service, row, data, plan))
        with service.db:
            service.conversation_repository.set_result(row["id"], "completed", json.dumps(result))
        assert service.write_ownership.acquire("active-writer", "p", None, [root]) is None
        with pytest.raises(APIError, match="project_folder_busy"):
            service.save_workflow(identity, row["id"], "saved")
        assert not (root / "workflows/saved.json").exists()
        service.write_ownership.release("active-writer")
        assert service.save_workflow(identity, row["id"], "saved")["path"] == "workflows/saved.json"
        assert not service.write_ownership.leases
    finally:
        service.db.close()


def test_directory_sync_failure_rolls_back_published_workflow(tmp_path, monkeypatch):
    import stat
    original = os.fsync
    def fail_directory(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("synthetic directory sync failure")
        original(descriptor)
    with monkeypatch.context() as context:
        context.setattr(os, "fsync", fail_directory)
        with pytest.raises(OSError, match="synthetic"):
            workflows.save_chain_as_workflow({"root": str(tmp_path)}, workflow(), "saved", successful=True)
    assert not (tmp_path / "workflows/saved.json").exists()
    assert workflows.save_chain_as_workflow({"root": str(tmp_path)}, workflow(), "saved", successful=True).is_file()


def test_resume_retry_with_same_identity_creates_one_child(tmp_path):
    service, identity, row, data, plan = setup_run(tmp_path)
    try:
        with patch.object(service, "infer", AsyncMock(return_value={"answer": "done"})):
            result = asyncio.run(maestro.execute_plan(service, row, data, plan))
        with service.db:
            service.conversation_repository.set_result(row["id"], "failed", json.dumps(result))
        before = service.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        first = service.recover_workflow(identity, row["id"], {}, idem="stable-browser-recovery")
        second = service.recover_workflow(identity, row["id"], {}, idem="stable-browser-recovery")
        assert first["job_id"] == second["job_id"]
        assert second["reused"] is True
        assert service.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == before + 1
    finally:
        service.db.close()
