"""Round-three current authority, publication identity and output sanitation."""

import asyncio
import copy
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from test_catalog_pin import git
from test_catalog_pin import repository as repository
from test_effect_executor import prepared, request
from test_workspaces import config

from agent_service import catalog_pin as pins
from agent_service.app import create_app
from agent_service.approval_sessions import consume_enrollment, issue_enrollment
from agent_service.secret_vault import SecretStream


@pytest.mark.parametrize("kind", ["tag", "gate", "publication", "native"])
def test_held_request_obeys_current_project_grants(make_harness_config, kind):
    async def scenario():
        app, effect = await prepared(make_harness_config)
        service = app.state.service
        service.config["projects"]["p"] = {}
        service.config["clients"]["local"]["projects"].append("p")
        service.db.execute("UPDATE jobs SET project='p',state='completed' WHERE id='job'")
        token = consume_enrollment(service.config, issue_enrollment(service.config, "local"))
        waiter = None
        gate_id = effect["gate_id"]
        if kind == "gate":
            waiter = asyncio.create_task(
                service.gates.ask(
                    "job",
                    {"question": "Proceed?", "options": [{"id": "approve", "label": "Approve"}]},
                    lambda *_: None,
                )
            )
            await asyncio.sleep(0)
            gate_id = next(key for key in service.gates.progress if key != effect["gate_id"])
        elif kind == "native":
            gate_id = "native-held"
            service.approvals[gate_id] = ("job", asyncio.get_running_loop().create_future())
        path = "/v1/jobs/job/work-item" if kind == "tag" else "/v1/approvals/" + gate_id
        payload = (
            {"work_item": "AFTER-REVOCATION"}
            if kind == "tag"
            else {"choice": "approve", "approved": True}
        )
        reading, release = asyncio.Event(), asyncio.Event()

        async def body():
            reading.set()
            await release.wait()
            yield json.dumps(payload).encode()

        service.effects.driver.create = AsyncMock(return_value=("done", {}))
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 42000)),
                base_url="http://127.0.0.1:18095",
                cookies={"harness_session": token},
            ) as client:
                held = asyncio.create_task(
                    client.request("PATCH" if kind == "tag" else "POST", path, content=body())
                )
                await asyncio.wait_for(reading.wait(), 2)
                candidate = copy.deepcopy(service.config)
                candidate["clients"]["local"]["projects"] = ["sem-projeto"]
                await service.apply_runtime_config(candidate)
                assert (await client.get("/v1/jobs/job")).status_code == 403
                release.set()
                response = await held
                assert response.status_code == 403, response.text
                assert service.conversation_repository.get("job")["work_item"] != "AFTER-REVOCATION"
                assert service.gates.repository.get(effect["gate_id"])["state"] == "pending"
                service.effects.driver.create.assert_not_called()
        finally:
            if waiter:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
            await service.effects.close()
            service.db.close()

    asyncio.run(scenario())


def test_dispatch_rechecks_project_grant_after_gate(make_harness_config):
    async def scenario():
        app, effect = await prepared(make_harness_config)
        service = app.state.service
        service.gates.repository.resolve(effect["gate_id"], "approve", "local", time.time())
        service.config["clients"]["local"]["projects"] = []
        service.effects.driver.create = AsyncMock(return_value=("done", {}))
        try:
            assert (await service.effects.execute(effect["effect_id"]))["status"] == "invalidated"
            service.effects.driver.create.assert_not_called()
        finally:
            await service.effects.close()
            service.db.close()

    asyncio.run(scenario())


def test_idna_equivalent_publication_dispatches_once(make_harness_config):
    async def scenario():
        app, _ = await prepared(make_harness_config)
        service = app.state.service
        service.db.execute("UPDATE effects SET status='invalidated'")
        original = service.config["effect_integrations"][0]
        original["endpoint"] = "https://bücher.invalid"
        service.config["effect_integrations"].append(
            {**original, "integration": "alias", "endpoint": "https://xn--bcher-kva.invalid"}
        )
        first = await service.effects.prepare("job", request())
        second = await service.effects.prepare("job", {**request(), "integration": "alias"})
        posts = []

        def handler(req):
            posts.append(str(req.url))
            return httpx.Response(201, json={"key": "TEST-1"})

        client = httpx.AsyncClient
        try:
            with patch(
                "agent_service.jira_effects.httpx.AsyncClient",
                side_effect=lambda **kw: client(**kw, transport=httpx.MockTransport(handler)),
            ):
                statuses = []
                for effect in (first, second):
                    service.gates.repository.resolve(
                        effect["gate_id"], "approve", "local", time.time()
                    )
                    statuses.append((await service.effects.execute(effect["effect_id"]))["status"])
            assert statuses == ["done", "invalidated"]
            assert len(posts) == 1
        finally:
            await service.effects.close()
            service.db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["harness_session=", 'NONCE: "'])
def test_authority_stream_redaction_at_every_boundary(field):
    text = field + "synthetic-session-credential"
    for boundary in range(1, len(text)):
        stream = SecretStream()
        result = (
            stream.feed("answer", text[:boundary])
            + stream.feed("answer", text[boundary:])
            + stream.feed("answer", " end")
        )
        assert "synthetic-session-credential" not in result, boundary
        assert result.endswith(" end")


@pytest.mark.parametrize("channel", ["answer_delta", "reasoning_delta"])
def test_stream_authority_is_safe_in_events_and_spans(tmp_path, channel):
    async def scenario():
        cfg = config(tmp_path / "state")
        project = tmp_path / "project"
        project.mkdir()
        cfg["projects"]["p"]["root"] = str(project)
        cfg["codex"] = {"binary": "synthetic"}
        app = create_app(cfg)
        service = app.state.service
        identity = ("a", cfg["clients"]["a"])
        token = consume_enrollment(cfg, issue_enrollment(cfg, "a"))
        job = service.submit(
            identity,
            {
                "project_id": "p",
                "backend": "codex",
                "model": "gpt-6-astra",
                "prompt": "Synthetic diagnostics",
                "execution_mode": "native",
            },
        )["job_id"]
        row = service.job(identity, job)

        async def provider(settings, prompt, progress, *args):
            for part in ["harness_sess", "ion=", token[:12], token[12:], " end"]:
                progress(channel, {"text": part})
            return {"answer": "harness_session=" + token}

        try:
            with (
                patch("adapters.run_native", side_effect=provider),
                patch.object(service, "quota", AsyncMock(return_value=None)),
            ):
                result = await service.infer(row, json.loads(row["payload"]))
            service.finish(job, "completed", result)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost",
                headers={"Authorization": "Bearer a"},
            ) as client:
                events = await client.get("/v1/jobs/" + job + "/events?format=json")
                spans = await client.get("/v1/jobs/" + job + "/spans?include_content=true")
            assert events.status_code == spans.status_code == 200
            text = "".join(
                e["data"].get("text", "") for e in events.json()["events"] if e["type"] == channel
            )
            assert token not in text
            assert token not in spans.text
        finally:
            await service.effects.close()
            service.db.close()

    asyncio.run(scenario())


def test_public_projections_redact_without_changing_approved_artifact(make_harness_config):
    async def scenario():
        app, _ = await prepared(make_harness_config)
        service = app.state.service
        payload = request()
        payload["artifact"]["fields"]["summary"] = "Diagnostic synthetic-secret"
        # Legacy rows still receive public projection sanitation; new admission rejects them.
        effect = await service.effects.prepare("job", request())
        service.db.execute(
            "UPDATE effects SET artifact=? WHERE effect_id=?",
            (json.dumps(payload["artifact"]), effect["effect_id"]),
        )
        gate = service.gates.repository.get(effect["gate_id"])
        spec = json.loads(gate["spec"])
        spec["artifact"] = payload["artifact"]
        service.db.execute(
            "UPDATE gates SET spec=? WHERE gate_id=?", (json.dumps(spec), effect["gate_id"])
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 42000)),
                base_url="http://127.0.0.1:18095",
            ) as client:
                for path in ["/v1/jobs/job/effects", "/v1/conversations/job"]:
                    response = await client.get(path)
                    assert response.status_code == 200
                    assert "synthetic-secret" not in response.text
            stored = service.effects.get(effect["effect_id"])
            assert stored["artifact"] == payload["artifact"]
            assert stored["artifact_digest"] == effect["artifact_digest"]
        finally:
            await service.effects.close()
            service.db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("relative", ["commands/unreviewed.local.md", "dependency.local.md"])
def test_pin_rejects_ignored_uncommitted_content(repository, tmp_path, relative):
    catalog, _ = repository
    root = Path(catalog["root"])
    (root / ".gitignore").write_text("*.local.md\n")
    git(root, "add", ".gitignore")
    git(root, "commit", "-qm", "Ignore local resources")
    state = tmp_path / "state"
    pin = pins.pin_catalog(catalog, state, "HEAD", owner=True)
    tree = Path(pin["root"])
    (tree / relative).parent.chmod(0o755)
    (tree / relative).write_text("Uncommitted synthetic instruction")
    assert git(tree, "status", "--porcelain") == ""
    with pytest.raises(pins.CatalogPinError, match="modified"):
        pins.effective_catalogs(
            {"state_dir": str(state), "catalogs": [catalog]},
            {"catalogs": ["demo"], "catalog_pins": {"demo": pin}},
        )


@pytest.mark.parametrize("operation", ["title", "upload"])
def test_mutations_after_stream_revalidate_and_clean_up(tmp_path, operation):
    async def scenario():
        cfg = config(tmp_path)
        app = create_app(cfg)
        service = app.state.service
        with service.db:
            service.conversation_repository.insert(
                "job", "p", "a", "completed", 1, "{}", None, "job", "job"
            )
        reading, release = asyncio.Event(), asyncio.Event()

        async def body():
            reading.set()
            await release.wait()
            yield b'{"title":"AFTER-REVOCATION"}' if operation == "title" else b"Synthetic upload"

        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost",
                headers={"Authorization": "Bearer a", "x-filename": "fixture.txt"},
            ) as client:
                task = asyncio.create_task(
                    client.request(
                        "PATCH" if operation == "title" else "POST",
                        "/v1/conversations/job"
                        if operation == "title"
                        else "/v1/files?project_id=p",
                        content=body(),
                    )
                )
                await asyncio.wait_for(reading.wait(), 2)
                candidate = copy.deepcopy(cfg)
                candidate["clients"]["a"]["projects"] = []
                await service.apply_runtime_config(candidate)
                release.set()
                response = await task
                assert response.status_code == 403, response.text
                assert service.conversation_repository.titles().get("job") != "AFTER-REVOCATION"
                assert service.db.execute("SELECT count(*) FROM files").fetchone()[0] == 0
                assert not list((tmp_path / "files" / "p").glob("*"))
        finally:
            service.db.close()

    asyncio.run(scenario())


def test_authority_stream_pending_is_bounded_and_emits_terminal_redaction():
    stream = SecretStream()
    chunks = []
    for char in "nonce=" + "X" * 20000:
        chunks.append(stream.feed("answer", char))
        assert len(stream.pending.get("answer", "")) <= 32
    assert "".join(chunks) == "nonce=[redacted]"
    assert stream.feed("answer", "; ordinary text") == "; ordinary text"


def test_sensitive_effect_is_rejected_before_durable_preparation(make_harness_config):
    from agent_service.errors import APIError

    async def scenario():
        app, _ = await prepared(make_harness_config)
        service = app.state.service
        payload = request()
        payload["artifact"]["fields"]["summary"] = "Diagnostic synthetic-secret"
        try:
            before = service.db.execute("SELECT count(*) FROM effects").fetchone()[0]
            with pytest.raises(APIError, match="effect_sensitive_content"):
                await service.effects.prepare("job", payload)
            assert service.db.execute("SELECT count(*) FROM effects").fetchone()[0] == before
        finally:
            await service.effects.close()
            service.db.close()

    asyncio.run(scenario())


def test_legacy_publication_content_is_not_replayed_after_rotation(
    make_harness_config, monkeypatch
):
    import weakref

    from agent_service import secret_vault

    async def scenario():
        app, effect = await prepared(make_harness_config)
        service = app.state.service
        artifact = {"fields": {"summary": "historical-synthetic-credential"}}
        spec = json.loads(service.gates.repository.get(effect["gate_id"])["spec"])
        spec.update(artifact=artifact, artifact_preview=json.dumps(artifact))
        # A legacy record has no admission-time public snapshot.
        columns = {r[1] for r in service.db.execute("PRAGMA table_info(effects)")}
        service.db.execute(
            "UPDATE effects SET artifact=? WHERE effect_id=?",
            (json.dumps(artifact), effect["effect_id"]),
        )
        if "public_content" in columns:
            service.db.execute(
                "UPDATE effects SET public_content=NULL WHERE effect_id=?", (effect["effect_id"],)
            )
        service.db.execute(
            "UPDATE gates SET spec=? WHERE gate_id=?", (json.dumps(spec), effect["gate_id"])
        )
        service.effects.credentials.set(
            "synthetic", {"email": "fixture@example.invalid", "token": "rotated-synthetic-token"}
        )
        # Clear process-only values: public safety cannot depend on old credentials in memory.
        monkeypatch.setattr(secret_vault, "_known_secrets", weakref.WeakKeyDictionary())
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 42000)),
                base_url="http://127.0.0.1:18095",
            ) as client:
                for path in ["/v1/jobs/job/effects", "/v1/conversations/job"]:
                    response = await client.get(path)
                    assert response.status_code == 200
                    assert "historical-synthetic-credential" not in response.text
        finally:
            await service.effects.close()
            service.db.close()

    asyncio.run(scenario())


def test_receipt_sanitation_survives_real_process_restart(make_harness_config):
    import subprocess
    import sys

    async def scenario():
        app, effect = await prepared(make_harness_config)
        service = app.state.service
        service.effects._status(
            effect["effect_id"], "done", receipt={"issue_key": "synthetic-secret"}
        )
        service.effects.credentials.set(
            "synthetic", {"email": "fixture@example.invalid", "token": "rotated-token"}
        )
        cfg = service.config
        await service.effects.close()
        service.db.close()
        code = "from agent_service.app import create_app; from agent_service.routes.conversations import gate_records; import json,sys; s=create_app(json.loads(sys.argv[1])).state.service; print(json.dumps([s.effects.public(sys.argv[2]),gate_records(s,'job')])); s.db.close()"
        result = subprocess.run(
            [sys.executable, "-c", code, json.dumps(cfg), effect["effect_id"]],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "synthetic-secret" not in result.stdout
        assert "[redacted]" in result.stdout

    asyncio.run(scenario())
