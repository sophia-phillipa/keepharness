import asyncio
import hashlib
import json
from unittest.mock import patch

import httpx
import pytest

from adapters.claude.stream import Stream
from agent_service.app import create_app


@pytest.mark.parametrize("channel", ["answer_delta", "reasoning_delta"])
@pytest.mark.parametrize("interleaved", [False, True])
@pytest.mark.parametrize("syntax", ["bearer", "registered"])
def test_interleaved_authority_redaction(tmp_path, channel, interleaved, syntax):
    async def scenario():
        secret = "SYNTHETIC-AUTHORITY-R11-SECRET-481"
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
                "claude": {
                    "enabled": True,
                    "models": ["haiku"],
                    "projects": ["p"],
                    "permissions": {"delegate": True},
                }
            },
            "claude_models": {"haiku": ["configured"]},
            "claude": {"binary": "synthetic"},
        }
        app = create_app(cfg)
        service = app.state.service
        if syntax == "registered":
            service.vault.set("fixture", {"password": secret})
        identity = ("alice", cfg["clients"]["alice"])
        job = service.submit(
            identity,
            {
                "project_id": "p",
                "backend": "claude",
                "model": "haiku",
                "prompt": "Synthetic diagnostics",
                "execution_mode": "native",
            },
        )["job_id"]
        row = service.job(identity, job)

        async def provider(settings, prompt, progress, *args):
            stream = Stream(progress)

            def emit(text, parent=None):
                delta = (
                    {"type": "text_delta", "text": text}
                    if channel == "answer_delta"
                    else {"type": "thinking_delta", "thinking": text}
                )
                stream.consume(
                    {
                        "type": "stream_event",
                        "parent_tool_use_id": parent,
                        "event": {"type": "content_block_delta", "delta": delta},
                    }
                )

            text = ("Authorization: Bearer " if syntax == "bearer" else "") + secret + "\n"
            for split in range(1, len(text)):
                emit(text[:split])
                if interleaved:
                    emit("Public child update. ", "synthetic-child")
                    emit("Other child update. ", "second-child")
                emit(text[split:])
            emit("Safe child trailer", "synthetic-child")
            return {"answer": "Synthetic complete"}

        try:
            with patch("adapters.run_native", side_effect=provider):
                result = await service.infer(row, json.loads(row["payload"]))
            service.finish(job, "completed", result)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost",
                headers={"Authorization": "Bearer " + secret},
            ) as client:
                event_pages = []
                after = 0
                while True:
                    events = await client.get(
                        "/v1/jobs/" + job + "/events?format=json&after=" + str(after)
                    )
                    assert events.status_code == 200
                    event_pages.append(events.text)
                    if not events.json()["has_more"]:
                        break
                    after = events.json()["next_after"]
                event_text = "".join(event_pages)
                spans = await client.get("/v1/jobs/" + job + "/spans?include_content=true")
            assert events.status_code == spans.status_code == 200
            durable = json.dumps(
                [
                    dict(r)
                    for r in service.db.execute(
                        "SELECT data FROM events WHERE job=? AND type=? ORDER BY id", (job, channel)
                    )
                ]
            )
            facts = {
                "channel": channel,
                "interleaved": interleaved,
                "events_leak": secret in event_text,
                "spans_leak": secret in spans.text,
                "sqlite_leak": secret in durable,
            }
            print(json.dumps(facts))
            assert (
                secret not in event_text and secret not in spans.text and secret not in durable
            ), facts
            root_output = "".join(
                json.loads(r["data"]).get("text", "")
                for r in service.db.execute(
                    "SELECT data FROM events WHERE job=? AND type=? ORDER BY id", (job, channel)
                )
                if not json.loads(r["data"]).get("parent_tool_use_id")
            )
            prefix = "Authorization: Bearer " if syntax == "bearer" else ""
            assert root_output == (prefix + "[redacted]\n") * (len(prefix + secret + "\n") - 1)
            assert "Safe child trailer" in event_text
            assert "synthetic-child" in event_text
        finally:
            await service.effects.close()
            service.db.close()

    asyncio.run(scenario())


def test_source_limit_retains_unfinished_redaction_state(tmp_path):
    from agent_service.secret_vault import SecretStream, SecretVault

    vault = SecretVault(tmp_path / "vault")
    vault.set("fixture", {"password": "synthetic-credential"})
    stream = SecretStream()
    for index in range(256):
        stream.feed(("answer", index), "Bearer " if index % 2 else "synthetic-")
    assert stream.feed(("answer", "overflow"), "private value") == "[redacted]"
    assert stream.feed(("answer", 0), "credential\n") == "[redacted]\n"
    assert stream.feed(("answer", 1), "private\n") == "[redacted]\n"
    assert (
        max(map(len, (stream.channels, stream.pending, stream.authority, stream.vault_pending)))
        <= 256
    )
