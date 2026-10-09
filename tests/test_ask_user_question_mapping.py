"""Claude questions are answered through validated typed option gates."""

import asyncio
import json
from copy import deepcopy

import pytest

from adapters.claude.native import answer_questions
from agent_service.tools import ToolError


def test_multiple_questions_updated_input_and_multiselect():
    request = {
        "questions": [
            {
                "question": "Color?",
                "options": [{"label": "Blue"}, {"label": "Red"}],
                "multiSelect": False,
            },
            {
                "question": "Fruit?",
                "options": [{"label": "Apple"}, {"label": "Pear"}],
                "multiSelect": True,
            },
        ]
    }
    original = deepcopy(request)

    async def scenario():
        received = []

        async def approve(kind, data):
            assert kind == "gate"
            received.append(data)
            return {"approved": True, "choice": ["0", "1"] if data["multi_select"] else "1"}

        response = await answer_questions(request, approve)
        assert response == {**request, "answers": {"Color?": "Red", "Fruit?": "Apple, Pear"}}
        assert request == original
        assert received[0]["options"][0] == {"id": "0", "label": "Blue", "description": ""}

    asyncio.run(scenario())


def test_denial_returns_no_updated_input():
    async def scenario():
        async def deny(*_):
            return {"approved": False}

        assert (
            await answer_questions(
                {"questions": [{"question": "Color?", "options": [{"label": "Blue"}]}]}, deny
            )
            is None
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "inputs",
    [
        {},
        {"questions": []},
        {"questions": [{"question": "?", "options": []}]},
        {"questions": [{"question": "?", "options": [{"label": "X"}, {"label": "X"}]}]},
    ],
)
def test_malformed_questions_fail_closed(inputs):
    async def approve(*_):
        raise AssertionError("Malformed input must not ask a human")

    with pytest.raises(ToolError, match="claude_invalid_question"):
        asyncio.run(answer_questions(inputs, approve))


def test_stdio_returns_updated_input_answers(tmp_path, monkeypatch):
    import json
    import sys

    from adapters.claude.native import run

    executable = tmp_path / "fake_claude.py"
    captured = tmp_path / "reply.json"
    script = """import json, sys
json.loads(sys.stdin.readline())
print(json.dumps({"type":"control_request", "request_id":"question-1", "request":{"subtype":"can_use_tool", "tool_name":"AskUserQuestion", "input":{"questions":[{"question":"Color?", "options":[{"label":"Blue"},{"label":"Red"}], "multiSelect":False}]}}}), flush=True)
reply = json.loads(sys.stdin.readline())
open(CAPTURE, "w").write(json.dumps(reply))
print(json.dumps({"type":"result", "subtype":"success", "result":"done"}), flush=True)
"""
    executable.write_text(
        "#!" + sys.executable + "\n" + script.replace("CAPTURE", repr(str(captured)))
    )
    executable.chmod(0o700)
    monkeypatch.setattr("control.integrations.configurations", lambda: {"claude": {}})
    monkeypatch.setattr("control.integrations.inventory", lambda: {"claude": []})

    async def approve(kind, gate):
        assert kind == "gate"
        return {"approved": True, "choice": "1"}

    asyncio.run(
        run(
            {"binary": str(executable)},
            "ask",
            lambda *_: None,
            tmp_path,
            "haiku",
            tmp_path,
            {},
            [],
            approve,
        )
    )
    response = json.loads(captured.read_text())["response"]
    assert response["request_id"] == "question-1"
    assert response["response"]["behavior"] == "allow"
    assert response["response"]["updatedInput"]["answers"] == {"Color?": "Red"}


class CapturedStdin:
    def __init__(self):
        self.messages = []

    def write(self, data):
        self.messages.append(json.loads(data))

    async def drain(self):
        pass


def codex_result(method, reply):
    """What the Codex adapter writes back for one interactive request and decision."""
    from types import SimpleNamespace

    from adapters.codex.native import respond_to_interaction

    rpc = SimpleNamespace(process=SimpleNamespace(stdin=CapturedStdin()))

    async def decide(kind, params):
        return reply

    asyncio.run(
        respond_to_interaction(
            rpc, {"id": 7, "method": method, "params": {}}, decide, {}, {}, True, False
        )
    )
    return rpc.process.stdin.messages[0]["result"]


TYPED = {"q1": {"answers": ["typed text"]}}


@pytest.mark.parametrize("approved", [True, False])
def test_codex_user_input_sends_typed_answers_only_when_approved(approved):
    result = codex_result("item/tool/requestUserInput", {"approved": approved, "answers": TYPED})
    assert result == {"answers": TYPED if approved else {}}


@pytest.mark.parametrize("approved", [True, False])
def test_codex_elicitation_sends_content_only_when_accepted(approved):
    result = codex_result("mcpServer/elicitation/request", {"approved": approved, "answers": TYPED})
    assert result == (
        {"action": "accept", "content": TYPED}
        if approved
        else {"action": "decline", "content": None}
    )
