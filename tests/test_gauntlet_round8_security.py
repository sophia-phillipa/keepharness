import asyncio
import hashlib
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from test_approval_authority import pending_approval
from test_effect_executor import configure_effects, request

from agent_service.app import create_app
from agent_service.approval_sessions import consume_enrollment, issue_enrollment
from agent_service.log_config import redact


@pytest.fixture
def app(tmp_path):
    cfg = {
        "state_dir": str(tmp_path),
        "origins": ["http://testserver"],
        "projects": {"p": {}},
        "clients": {"alice": {"sha256": hashlib.sha256(b"alice").hexdigest(), "projects": ["p"]}},
        "services": {
            "codex": {
                "enabled": True,
                "models": ["gpt-6-astra"],
                "projects": ["p"],
                "permissions": {},
            }
        },
        "codex_models": {"gpt-6-astra": ["low"]},
    }
    app = create_app(cfg)
    s = app.state.service
    with s.db:
        s.conversation_repository.insert(
            "job", "p", "alice", "completed", 1, '{"prompt":"synthetic"}', None, "job", "job"
        )
    yield app
    s.db.close()


@pytest.mark.parametrize(
    "route,payload",
    [
        ("/v1/jobs/job/work-item", {"work_item": "AFTER-LOGOUT"}),
        ("/v1/conversations/job", {"title": "AFTER-LOGOUT"}),
        (
            "/v1/jobs",
            {
                "project_id": "p",
                "backend": "codex",
                "model": "gpt-6-astra",
                "prompt": "AFTER-LOGOUT",
            },
        ),
    ],
)
@pytest.mark.parametrize("revocation", ["logout", "token_rotation", "valid"])
def test_pending_mutation_revalidates_authentication(app, route, payload, revocation):
    async def scenario():
        s = app.state.service
        token = consume_enrollment(s.config, issue_enrollment(s.config, "alice"))
        headers = (
            {"Cookie": "harness_session=" + token}
            if revocation == "logout"
            else {"Authorization": "Bearer alice"}
        )
        read, release = asyncio.Event(), asyncio.Event()

        async def body():
            read.set()
            await release.wait()
            yield json.dumps(payload).encode()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=headers
        ) as client:
            task = asyncio.create_task(
                client.request("POST" if route == "/v1/jobs" else "PATCH", route, content=body())
            )
            await asyncio.wait_for(read.wait(), 2)
            if revocation == "logout":
                result = await client.post("/v1/logout")
                assert result.status_code == 200
            elif revocation == "token_rotation":
                s.config["clients"]["alice"]["sha256"] = hashlib.sha256(b"rotated").hexdigest()
            fresh = await client.get("/v1/jobs/job", headers=headers)
            release.set()
            response = await task
            row = s.conversation_repository.get("job")
            titles = s.conversation_repository.titles()
            print(
                json.dumps(
                    {
                        "case": revocation,
                        "route": route,
                        "fresh_status": fresh.status_code,
                        "held_status": response.status_code,
                        "work_item": row["work_item"],
                        "title": titles.get("job"),
                        "job_count": s.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
                    }
                )
            )
            if revocation == "valid":
                assert fresh.status_code == 200
                assert response.status_code == (202 if route == "/v1/jobs" else 200)
                return
            assert fresh.status_code == 401
            assert response.status_code in (401, 403), response.text
            assert row["work_item"] is None and titles.get("job") is None
            assert s.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "syntax", ["harness_token={secret}", "Authorization: Bearer {secret}", '{"token":"{secret}"}']
)
@pytest.mark.parametrize("channel", ["answer_delta", "reasoning_delta"])
def test_api_credentials_redacted_before_event_persistence(tmp_path, syntax, channel):
    async def scenario():
        secret = "SYNTHETIC-BEARER-CREDENTIAL-481"
        project = tmp_path / "project"
        project.mkdir()
        cfg = {
            "state_dir": str(tmp_path / "state"),
            "origins": [],
            "projects": {"p": {"root": str(project)}},
            "clients": {
                "alice": {"sha256": hashlib.sha256(secret.encode()).hexdigest(), "projects": ["p"]}
            },
            "services": {
                "codex": {
                    "enabled": True,
                    "models": ["gpt-6-astra"],
                    "projects": ["p"],
                    "permissions": {},
                }
            },
            "codex_models": {"gpt-6-astra": ["low"]},
            "codex": {"binary": "synthetic"},
        }
        app = create_app(cfg)
        s = app.state.service
        identity = ("alice", cfg["clients"]["alice"])
        job = s.submit(
            identity,
            {
                "project_id": "p",
                "backend": "codex",
                "model": "gpt-6-astra",
                "prompt": "Synthetic diagnostics",
                "execution_mode": "native",
            },
        )["job_id"]
        row = s.job(identity, job)
        text = syntax.replace("{secret}", secret)

        async def provider(settings, prompt, progress, *args):
            for fragment in [text[:5], text[5:20], text[20:]]:
                progress(channel, {"text": fragment})
            return {"answer": text}

        try:
            with (
                patch("adapters.run_native", side_effect=provider),
                patch.object(s, "quota", AsyncMock(return_value=None)),
            ):
                result = await s.infer(row, json.loads(row["payload"]))
            s.finish(job, "completed", result)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost",
                headers={"Authorization": "Bearer " + secret},
            ) as client:
                events = await client.get("/v1/jobs/" + job + "/events?format=json")
                spans = await client.get("/v1/jobs/" + job + "/spans?include_content=true")
            assert events.status_code == spans.status_code == 200
            emitted = "".join(
                e["data"].get("text", "") for e in events.json()["events"] if e["type"] == channel
            )
            durable = "".join(
                json.loads(r[0]).get("text", "")
                for r in s.db.execute(
                    "SELECT data FROM events WHERE job=? AND type=? ORDER BY id", (job, channel)
                )
            )
            print(
                json.dumps(
                    {
                        "syntax": syntax,
                        "channel": channel,
                        "logs_hide_secret": secret not in redact(text),
                        "events_leak": secret in emitted,
                        "spans_leak": secret in spans.text,
                        "durable_leak": secret in durable,
                        "result_leak": secret in json.dumps(result),
                        "stored_result_leak": secret
                        in s.conversation_repository.get(job)["result"],
                    }
                )
            )
            assert secret not in emitted and secret not in durable and secret not in spans.text
            assert secret not in s.conversation_repository.get(job)["result"]
        finally:
            await s.effects.close()
            s.db.close()

    asyncio.run(scenario())


def test_failed_effect_gate_publication_cleans_pending(make_harness_config, monkeypatch):
    async def scenario():
        app = create_app(configure_effects(make_harness_config(approval_timeout_seconds=0.01)))
        pending_approval(app, "local")
        service = app.state.service
        baseline_approvals = set(service.approvals)
        service.effects.credentials.set(
            "synthetic", {"email": "fixture@example.invalid", "token": "synthetic-secret"}
        )
        original = service.event

        def event(job, kind, data):
            if kind == "gate_required":
                raise OSError("synthetic transient event persistence failure")
            return original(job, kind, data)

        monkeypatch.setattr(service, "event", event)
        try:
            with pytest.raises(OSError):
                await service.effects.prepare("job", request())
            await asyncio.sleep(0.03)
            effects = service.effects.for_job("job")
            print(
                "PREPARE_FAILURE_STATE",
                json.dumps(
                    {
                        "pending": list(set(service.approvals) - baseline_approvals),
                        "tasks": list(service.effects.tasks),
                        "effect_states": [e["status"] for e in effects],
                        "gate_states": [
                            g["state"] for g in service.gates.repository.for_job("job")
                        ],
                    }
                ),
            )
            assert set(service.approvals) == baseline_approvals, (
                "No expiry task owns leaked approval after failed prepare"
            )
            assert all(e["status"] != "prepared" for e in effects)
            assert not service.effects.tasks
            assert all(
                service.gates.repository.get(e["gate_id"])["state"] != "pending" for e in effects
            )
        finally:
            monkeypatch.setattr(service, "event", original)
            await service.effects.close()
            service.db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "syntax", ["harness_token={secret}", "Authorization: Bearer {secret}", '{"token":"{secret}"}']
)
def test_authentication_stream_all_boundaries(syntax):
    from agent_service.secret_vault import SecretStream, redact_secrets

    secret = "SYNTHETIC-CREDENTIAL"
    text = syntax.replace("{secret}", secret) + " end"
    for split in range(len(text) + 1):
        stream = SecretStream()
        result = stream.feed("answer", text[:split]) + stream.feed("answer", text[split:])
        assert secret not in result, (split, result)
        assert "[redacted]" in result
    stream = SecretStream()
    assert secret not in "".join(stream.feed("reasoning", char) for char in text)
    assert secret not in json.dumps(
        redact_secrets({"nested": [{"token": secret}, {"Authorization": "Bearer " + secret}]})
    )
