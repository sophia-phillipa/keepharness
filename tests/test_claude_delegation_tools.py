"""The CLI owns its tool catalog; the harness can request subagent stream forwarding."""

import pytest

from adapters.claude.native import build_command


@pytest.mark.parametrize("delegate", [False, True])
def test_native_tools_are_not_filtered_by_delegate_grant(tmp_path, monkeypatch, delegate):
    monkeypatch.setattr("control.integrations.configurations", lambda: {"claude": {}})
    monkeypatch.setattr("control.integrations.inventory", lambda: {"claude": []})
    command = build_command(
        {"binary": "claude"}, "haiku", tmp_path, {"read": True, "delegate": delegate}, "ask", []
    )
    assert "--tools" not in command
    assert ("--forward-subagent-text" in command) is delegate


def test_child_stream_events_retain_parent_correlation():
    from adapters.claude.stream import Stream

    events = []
    stream = Stream(lambda *event: events.append(event))
    stream.consume(
        {
            "type": "stream_event",
            "parent_tool_use_id": "task-1",
            "event": {
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "name": "Read", "id": "child-read"},
            },
        }
    )
    stream.consume(
        {
            "type": "stream_event",
            "parent_tool_use_id": "task-1",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "child-only"},
            },
        }
    )
    assert all(data["parent_tool_use_id"] == "task-1" for _, data in events)
    assert stream.answer == ""
