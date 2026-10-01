"""Manifest runtime settings reach the real adapter boundary without inference."""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_invocation_normalization import invocation_service

from adapters.shared.process import child_environment
from adapters.shared.workspace import prepare_workspace
from agent_service.catalog_manifest import load_manifest, materialize_runtime
from agent_service.services.queue_worker import ownership_roots
from control.product import PRODUCT


def test_catalog_dispatch_cwd_environment_snapshot_and_lease(tmp_path, monkeypatch):
    service, identity = invocation_service(tmp_path, monkeypatch)
    catalog = tmp_path / "catalog"
    (catalog / "commands").mkdir(parents=True)
    (catalog / "commands/task.md").write_text("Run synthetic task.")
    (catalog / "context.md").write_text("Synthetic context.")
    (catalog / "prepare.sh").write_text("#!/bin/sh\ntouch hook-ran\n")
    (catalog / "prepare.sh").chmod(0o700)
    (catalog / "harness.catalog.json").write_text(
        json.dumps(
            {
                "version": 1,
                "cwd": ".",
                "context": ["context.md"],
                "writable_state": ["memory"],
                "allowed_hooks": ["prepare.sh"],
            }
        )
    )
    service.config.update(catalogs=[{"id": "demo", "root": str(catalog), "trusted": True}])
    service.config["projects"]["p"]["catalogs"] = ["demo"]
    service.config["codex"] = {"binary": "synthetic"}
    service.config["services"]["codex"]["permissions"]["hooks"] = True
    runtime = materialize_runtime(catalog, load_manifest(catalog), service.root, "demo")
    item = next(
        item
        for item in service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"]
        if item["name"] == "task"
    )
    job = service.submit(
        identity,
        {
            "project_id": "p",
            "backend": "codex",
            "model": "gpt-6-astra",
            "prompt": "Run task",
            "invocations": [
                {
                    "kind": "command",
                    "resource_id": item["resource_id"],
                    "args": "",
                    "order": 0,
                    "mode": "inline",
                }
            ],
        },
    )["job_id"]
    service.conversation_repository.set_running(job)
    row = service.job(identity, job)
    assert catalog in [Path(value).resolve() for value in ownership_roots(service, row) if value]

    async def provider(config, prompt, progress, project, model, effort, session, backend, approve):
        assert (catalog / "hook-ran").is_file()
        assert project["permissions"]["hooks"] is False
        workspace = prepare_workspace(project, prompt, session)
        assert workspace.cwd == catalog
        assert runtime["writable_roots"][0] in workspace.roots
        assert "Synthetic context." in prompt
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import json,os; print(json.dumps([os.getcwd(),os.environ["
            + repr(PRODUCT.env_prefix + "_CATALOG_STATE_DEMO")
            + "]]))",
            cwd=workspace.cwd,
            env=child_environment(),
            stdout=asyncio.subprocess.PIPE,
        )
        output, _ = await process.communicate()
        assert json.loads(output) == [str(catalog), str(service.root / "catalog_runtime/demo")]
        return {"answer": "done"}

    async def scenario():
        with (
            patch("adapters.run_native", side_effect=provider),
            patch.object(service, "quota", AsyncMock(return_value=None)),
        ):
            assert (await service.infer(row, json.loads(row["payload"])))["answer"] == "done"
        snapshots = [
            json.loads(event["data"])
            for event in service.message_repository.all_events(job)
            if event["type"] == "catalog_snapshot"
        ]
        assert snapshots[0]["catalogs"] == [
            {"catalog_id": "demo", "catalog_commit": None, "catalog_dirty": None, "pinned": False}
        ]
        service.db.close()

    asyncio.run(scenario())
