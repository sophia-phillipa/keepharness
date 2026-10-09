import asyncio
import hashlib

from adapters.codex.backend import run_native as run_codex
from agent_service.invocations import normalize_chips
from agent_service.resources import unfenced


def test_nested_list_blockquote_fence_is_literal():
    prompt = "- > ```text\n  > /review example\n  > ```\n\n/review actual"
    items = [
        {
            "id": "review",
            "resource_id": "project/p/.codex/agents/review.toml",
            "kind": "agent",
            "mode": "delegated",
        }
    ]
    selections = [{"id": "review", "token": "/review"}]
    masked = unfenced(prompt, preserve_offsets=True)
    invocation = normalize_chips(prompt, selections, items)[0]
    print({"masked": masked, "args": invocation.args})
    assert "/review example" not in masked
    assert invocation.args == "actual"


def test_codex_conversational_agent_is_not_registered_for_delegation(tmp_path, monkeypatch):
    source = tmp_path / "discussion.toml"
    text = (
        'name = "discussion"\n'
        'mode = "conversational"\n'
        'developer_instructions = "Discuss in the main thread."\n'
    )
    source.write_text(text)
    item = {
        "kind": "agent",
        "name": "discussion",
        "mode": "conversational",
        "resource_id": "project/p/.codex/agents/discussion.toml",
        "revision": hashlib.sha256(text.encode()).hexdigest(),
        "_text": text,
        "_body": "Discuss in the main thread.",
        "source": str(source),
    }

    events = []

    async def capture(
        config, event, project, model, effort, session_dir, approve, workspace, runtime, provider
    ):
        agent_options = [value for value in runtime.command if value.startswith("agents.")]
        print({"agent_options": agent_options})
        assert agent_options == []
        return {"answer": "done"}

    monkeypatch.setattr("adapters.codex.backend.run_turn", capture)
    monkeypatch.setattr("adapters.codex.state._cli_version", lambda *args: "0.157.0")
    asyncio.run(
        run_codex(
            {"binary": "codex"},
            "task",
            lambda *event: events.append(event),
            {
                "root": str(tmp_path),
                "permissions": {"read": True},
                "_resources": [item],
            },
            "gpt-6-astra",
            "high",
            tmp_path / "session",
            None,
        )
    )

    assert not events
