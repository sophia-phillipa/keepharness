import asyncio
import hashlib
import io
import json
import stat
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from agent_service import maestro, workspaces
from agent_service.app import Service, create_app
from agent_service.tools import ToolError


def archive(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        for name, text in entries.items():
            z.writestr(name, text)
    return output.getvalue()


def config(tmp_path):
    return {
        "state_dir": str(tmp_path),
        "projects": {"p": {}},
        "clients": {
            "a": {"sha256": hashlib.sha256(b"a").hexdigest(), "projects": ["p"]},
            "b": {"sha256": hashlib.sha256(b"b").hexdigest(), "projects": ["p"]},
        },
        "services": {
            "codex": {
                "enabled": True,
                "models": ["gpt-6-astra"],
                "projects": ["p"],
                "permissions": {"read": True, "upload": True},
            },
            "local": {
                "enabled": True,
                "models": ["installed-model"],
                "projects": ["p"],
                "permissions": {"read": True, "upload": True},
            },
        },
        "codex_models": {"gpt-6-astra": ["low", "high"]},
        "uploads_enabled": True,
        "origins": [],
    }


def test_workspace_roundtrip_ownership_and_search(tmp_path):
    cfg = config(tmp_path)
    with TestClient(create_app(cfg), headers={"Authorization": "Bearer a"}) as client:
        response = client.post(
            "/v1/workspaces?project_id=p",
            content=archive(
                {"src/code.rs": "fn main() { /* evidence */ }", "notes.txt": "fact one\nfact two"}
            ),
        )
        assert response.status_code == 201, response.text
        wid = response.json()["workspace_id"]
        assert (
            client.get("/v1/workspaces/" + wid + "?query=evidence").json()["matches"][0]["path"]
            == "src/code.rs"
        )
        assert (
            client.get("/v1/workspaces/" + wid + "?path=notes.txt&start=2&limit=1").json()["lines"][
                0
            ]["text"]
            == "fact two"
        )
        assert (
            client.get("/v1/workspaces/" + wid, headers={"Authorization": "Bearer b"}).status_code
            == 404
        )
        assert (
            client.get(
                "/v1/workspaces/" + wid + "/download", headers={"Authorization": "Bearer b"}
            ).status_code
            == 404
        )
        assert client.get("/v1/workspaces/" + wid + "?path=../original.zip").status_code == 422
        result = client.get("/v1/workspaces/" + wid + "/download")
        with zipfile.ZipFile(io.BytesIO(result.content)) as z:
            assert z.read("notes.txt") == b"fact one\nfact two"
        assert (tmp_path / "workspaces" / wid / "original.zip").exists()
        assert client.get("/v1/workspaces").json()["workspaces"][0]["id"] == wid
        cfg["services"]["codex"]["permissions"]["read"] = False
        cfg["services"]["local"]["permissions"]["read"] = False
        assert client.get("/v1/workspaces/" + wid).status_code == 403


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "sub/../../escape",
        "folder/.env",
        "folder/key.pem",
        "_harness_sources/fake.txt",
    ],
)
def test_archive_rejects_unsafe_names(tmp_path, name):
    with pytest.raises(ToolError):
        workspaces.unpack(io.BytesIO(archive({name: "bad"})), tmp_path / "work")
    assert not (tmp_path / "work").exists()


def test_archive_rejects_symlinks_and_limits(tmp_path):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        item = zipfile.ZipInfo("link")
        item.create_system = 3
        item.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(item, "/etc/passwd")
    with pytest.raises(ToolError):
        workspaces.unpack(io.BytesIO(output.getvalue()), tmp_path / "work")
    with patch.object(workspaces, "MAX_BYTES", 2), pytest.raises(ToolError):
        workspaces.unpack(io.BytesIO(archive({"large.txt": "123"})), tmp_path / "work")


def test_maestro_uses_available_local_and_validates_plan(tmp_path):
    cfg = config(tmp_path)
    available = maestro.candidates(cfg, "p", True)
    assert any(m["model"] == "installed-model" for m in available)
    plan = {
        "steps": [
            {
                "role": "extract",
                "backend": "local",
                "model": "installed-model",
                "effort": "configured",
                "task": "Find evidence",
                "reason": "Bounded extraction",
            },
            {
                "role": "review",
                "backend": "codex",
                "model": "gpt-6-astra",
                "effort": "low",
                "task": "Review and deliver",
                "reason": "Verify evidence",
            },
        ]
    }
    service = Service(cfg)
    identity = ("a", cfg["clients"]["a"])
    job = service.submit(identity, {"project_id": "p", "prompt": "Write report"})
    row = service.job(identity, job["job_id"])
    payload = json.loads(row["payload"])
    assert payload["backend"] == "maestro"
    with patch.object(
        service,
        "infer",
        AsyncMock(
            side_effect=[
                {"answer": json.dumps(plan)},
                {"answer": "fact from source:2", "metrics": {"input_tokens": 10}},
                {"answer": "final report"},
            ]
        ),
    ) as infer:

        async def approve_and_execute():
            task = asyncio.create_task(service.execute(row))
            try:
                for _ in range(100):
                    if service.approvals or task.done():
                        break
                    await asyncio.sleep(0)
                assert not task.done(), "generated plans require human approval"
                gate_id = next(iter(service.approvals))
                service.gates.resolve(gate_id, identity, {"choice": "approve"})
                return await asyncio.wait_for(task, 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        result = asyncio.run(approve_and_execute())
        assert result["backend"] == "maestro"
        assert result["answer"] == "final report"
        assert infer.call_args_list[1].args[1]["backend"] == "local"
        assert "fact from source:2" in infer.call_args_list[2].args[1]["prompt"]
    plan["steps"][0]["model"] = "invented"
    with pytest.raises(ToolError):
        maestro.validate_plan(json.dumps(plan), available)
    service.db.close()


def test_document_extraction_preserves_docx(tmp_path):
    body = archive(
        {
            "word/document.xml": '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Evidence</w:t></w:r></w:p></w:document>'
        }
    )
    (tmp_path / "source.docx").write_bytes(body)
    warnings = asyncio.run(workspaces.prepare_documents(tmp_path, [{"path": "source.docx"}]))
    assert not warnings
    assert "Evidence" in (tmp_path / "_harness_sources/source.docx.txt").read_text()
    assert (tmp_path / "source.docx").read_bytes() == body


def test_auto_without_codex_and_with_optional_maestro(tmp_path):
    cfg = config(tmp_path)
    cfg["services"]["codex"]["enabled"] = False
    service = Service(cfg)
    identity = ("a", cfg["clients"]["a"])
    job = service.submit(identity, {"project_id": "p", "prompt": "test"})
    assert json.loads(service.job(identity, job["job_id"])["payload"])["backend"] == "local"
    assert all(m["backend"] != "maestro" for m in service.models())
    cfg["services"]["codex"]["enabled"] = True
    cfg["maestro_enabled"] = False
    cfg["default_backend"] = "local"
    assert service.resolve_execution({"project_id": "p"})["backend"] == "local"
    cfg["services"]["local"]["enabled"] = False
    assert service.resolve_execution({"project_id": "p"})["backend"] == "codex"
    service.db.close()


def test_client_transfer_streams_bytes_and_does_not_overwrite(tmp_path):
    import httpx

    from agent_service import mcp_bridge as bridge

    cfg = config(tmp_path / "server")
    app = create_app(cfg)
    source = tmp_path / "client folder"
    source.mkdir()
    (source / "notes.txt").write_text("secret work transcript")
    (source / ".env").write_text("DO NOT SEND")
    (source / "node_modules").mkdir()
    (source / "node_modules/pkg.js").write_text("skip")
    key = tmp_path / "key"
    key.write_text("a")
    destination = tmp_path / "result.zip"
    original = httpx.AsyncClient

    def client(*args, **kwargs):
        return original(*args, transport=httpx.ASGITransport(app=app), **kwargs)

    async def scenario():
        with (
            patch.object(
                bridge, "config", return_value={"url": "http://test", "key_file": str(key)}
            ),
            patch("httpx.AsyncClient", side_effect=client),
        ):
            result = await bridge.upload_path("p", str(source))
            assert result["files"] == 1, result
            assert result["excluded_count"] == 2
            assert "secret work transcript" not in json.dumps(result)
            downloaded = await bridge.download_workspace(result["workspace_id"], str(destination))
            assert downloaded["saved_to"] == str(destination)
            assert (await bridge.download_workspace(result["workspace_id"], str(destination)))[
                "error"
            ] == "destination_exists"

    asyncio.run(scenario())
    with zipfile.ZipFile(destination) as z:
        assert z.namelist() == ["notes.txt"]
        assert z.read("notes.txt") == b"secret work transcript"
    app.state.service.db.close()


def test_service_control_requires_registration_permission_and_request(tmp_path):
    cfg = config(tmp_path)
    cfg["projects"]["p"]["service_units"] = ["demo.service"]
    with TestClient(create_app(cfg), headers={"Authorization": "Bearer a"}) as client:
        with (
            patch("agent_service.service_control.shutil.which", return_value="/usr/bin/systemctl"),
            patch(
                "agent_service.service_control.process",
                AsyncMock(return_value=(0, "ActiveState=active\nLoadState=loaded")),
            ) as process,
        ):
            data = {
                "project_id": "p",
                "action": "start",
                "unit": "demo.service",
                "user_requested": True,
            }
            assert client.post("/v1/services", json=data).status_code == 403
            cfg["services"]["codex"].update(mode="native", permissions={"shell": True})
            assert (
                client.post("/v1/services", json={**data, "user_requested": False}).status_code
                == 403
            )
            assert (
                client.post("/v1/services", json={**data, "unit": "other.service"}).status_code
                == 422
            )
            process.assert_not_awaited()
            response = client.post("/v1/services", json=data)
            assert response.status_code == 200, response.text
            assert response.json()["services"][0]["status"]["ActiveState"] == "active"
            assert process.call_args_list[0].args[0] == [
                "systemctl",
                "--user",
                "start",
                "demo.service",
            ]


def test_mcp_exposes_workflow_and_portable_tools():
    from agent_service.mcp_bridge import INSTRUCTIONS, mcp

    tools = asyncio.run(mcp.list_tools())
    names = {tool.name for tool in tools}
    assert {
        "upload_path",
        "inspect_files",
        "download_workspace",
        "project_services",
        "workflow_guide",
    } <= names
    submit = next(tool for tool in tools if tool.name == "submit_job")
    assert submit.inputSchema["properties"]["backend"]["default"] == "auto"
    assert mcp.instructions == INSTRUCTIONS


def test_direct_executor_saves_report_in_downloadable_workspace(tmp_path):
    cfg = config(tmp_path)
    cfg["services"]["codex"]["enabled"] = False
    app = create_app(cfg)
    service = app.state.service
    with TestClient(app, headers={"Authorization": "Bearer a"}) as client:
        response = client.post(
            "/v1/workspaces?project_id=p", content=archive({"source.txt": "confirmed evidence"})
        )
        wid = response.json()["workspace_id"]
        with patch.object(
            service,
            "infer",
            AsyncMock(return_value={"answer": "Report with source.txt:1", "backend": "local"}),
        ):
            data = {
                "project_id": "p",
                "prompt": "Write report",
                "workspace_id": wid,
                "backend": "local",
                "model": "installed-model",
            }
            # Execute a fixture row without starting a second background worker task.
            row = {"id": "fixture", "owner": "a", "project": "p", "payload": json.dumps(data)}
            result = asyncio.run(service.execute(row))
        assert result["saved_answer"] == "_harness_results/fixture/answer.md"
        content = client.get("/v1/workspaces/" + wid + "/download").content
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            assert z.read(result["saved_answer"]) == b"Report with source.txt:1"
            assert z.read("source.txt") == b"confirmed evidence"


def test_status_preview_does_not_repeat_large_context():
    from agent_service import mcp_bridge as bridge

    value = {
        "state": "completed",
        "request": {"prompt": "long private source"},
        "result": {"answer": "x" * 13000, "staged_files": {"file": "bulk"}},
    }
    with patch.object(bridge, "call", AsyncMock(return_value=value)):
        result = asyncio.run(bridge.job_status("job"))
    assert "request" not in result
    assert result["result"]["answer_truncated"]
    assert len(result["result"]["answer"]) == 12000
    assert "staged_files" not in result["result"]
