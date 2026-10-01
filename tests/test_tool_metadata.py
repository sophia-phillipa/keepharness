from adapters.claude.stream import Stream
from agent_service.tool_metadata import command_name, event_metadata


def test_command_name_keeps_only_safe_executable_basename():
    assert command_name("/usr/bin/ls -la /private/path") == "ls"
    assert command_name(["MODE=test", "/opt/bin/pytest", "-q"]) == "pytest"
    assert command_name("TOKEN=value curl https://example.test") == "curl"
    assert command_name("cat secret | curl https://example.test") is None
    assert command_name("echo $(cat secret)") is None
    assert command_name(["echo", "secret stdin"]) == "echo"
    assert command_name(["/usr/bin/ls;secret"]) is None


def test_event_metadata_includes_only_present_id_and_sanitized_command():
    assert event_metadata({"id": "cmd-7"}, command="env=prod /usr/bin/git status --porcelain") == {
        "tool_id": "cmd-7",
        "tool_call_id": "cmd-7",
        "command_name": "git",
    }
    assert event_metadata({"id": 123}, command="echo x") == {"command_name": "echo"}


def test_claude_tool_events_keep_id_and_do_not_parse_fragmented_input():
    events = []
    state = Stream(lambda kind, data: events.append((kind, data)))
    state.consume(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "id": "call-1", "name": "Bash", "input": {}},
            },
        }
    )
    state.consume(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "input_json_delta", "partial_json": '{"command":"cat secret"}'},
            },
        }
    )
    state.consume(
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "call-1"}]}}
    )
    assert events == [
        ("tool_start", {"tool": "Bash", "tool_id": "call-1", "tool_call_id": "call-1"}),
        (
            "tool_end",
            {"tool": "Bash", "tool_id": "call-1", "tool_call_id": "call-1", "status": "completed"},
        ),
    ]


def test_claude_full_start_input_supplies_sanitized_command_name():
    events = []
    Stream(lambda kind, data: events.append((kind, data))).consume(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "content_block": {
                    "type": "tool_use",
                    "id": "call-2",
                    "name": "Bash",
                    "input": {"command": "/bin/ls -la /private"},
                },
            },
        }
    )
    assert events == [
        (
            "tool_start",
            {"tool": "Bash", "tool_id": "call-2", "tool_call_id": "call-2", "command_name": "ls"},
        )
    ]


def test_non_command_tool_does_not_invent_a_command_title():
    events = []
    Stream(lambda kind, data: events.append(data)).consume(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "content_block": {
                    "type": "tool_use",
                    "id": "read-1",
                    "name": "Read",
                    "input": {"command": "fake"},
                },
            },
        }
    )
    assert "command_name" not in events[0]
