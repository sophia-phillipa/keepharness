import pytest

from adapters.claude.stream import Stream
from adapters.gemini.native import AcpStream
from agent_service.tool_metadata import (
    TARGET_LIMIT,
    command_name,
    event_metadata,
    item_target,
    tool_target,
)


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
            {
                "tool": "Bash",
                "tool_id": "call-2",
                "tool_call_id": "call-2",
                "command_name": "ls",
                "target": "/bin/ls -la /private",
            },
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


# C-03: a short display-only `target` (file path or command line) on tool events.
def test_tool_target_shows_the_command_without_the_shell_wrapper():
    assert (
        tool_target("commandExecution", {"command": "/bin/bash -lc 'sed -n 1,5p notes.txt'"})
        == "sed -n 1,5p notes.txt"
    )
    assert tool_target("Bash", {"command": "ls -la\n  /tmp"}) == "ls -la /tmp"
    assert tool_target("execute", {"command": ["git", "status", "--short"]}) == "git status --short"


def test_tool_target_masks_secrets_in_the_command():
    target = tool_target("Bash", {"command": "API_TOKEN=abc123 curl -H 'Authorization: Bearer xyz' u"})
    assert "abc123" not in target and "xyz" not in target
    assert target.startswith("API_TOKEN=[redacted] curl")


def test_tool_target_is_bounded_to_one_short_line():
    target = tool_target("Bash", {"command": "echo " + "x" * 500})
    assert len(target) == TARGET_LIMIT and target.endswith("…")


def test_tool_target_paths_are_relative_to_the_project_root():
    root = "/work/project"
    assert tool_target("read_file", {"path": "/work/project/facts/alpha.txt"}, root) == "facts/alpha.txt"
    assert tool_target("mcp__reader__list_dir", {"path": "/work/project"}, root) == "."
    assert tool_target("Read", {"file_path": "/etc/hosts"}, root) == "/etc/hosts"
    assert tool_target("Edit", {"file_path": "/work/project-evil/x"}, root) == "/work/project-evil/x"
    assert tool_target("read_file", {"path": "facts/alpha.txt"}, root) == "facts/alpha.txt"


def test_tool_target_search_prefers_the_pattern_and_file_changes_list_paths():
    assert tool_target("Grep", {"pattern": "TODO", "path": "src"}) == "TODO"
    assert tool_target("search_files", {"path": "docs"}) == "docs"
    changes = {"changes": [{"path": "/w/a.py"}, {"path": "/w/b.py"}]}
    assert tool_target("fileChange", changes, "/w") == "a.py, b.py"


def test_tool_target_is_absent_for_other_tools_or_bad_arguments():
    assert tool_target("webSearch", {"query": "cats"}) is None
    assert tool_target("Bash", None) is None
    assert tool_target("Read", {"file_path": 5}) is None
    assert tool_target(None, {"path": "x"}) is None


def test_codex_item_target_covers_commands_file_changes_and_mcp_calls():
    assert item_target({"type": "commandExecution", "command": "pwd"}) == "pwd"
    assert item_target({"type": "fileChange", "changes": [{"path": "/w/a.py"}]}, "/w") == "a.py"
    mcp = {"type": "mcpToolCall", "tool": "read_file", "arguments": {"path": "/w/a.txt"}}
    assert item_target(mcp, "/w") == "a.txt"
    assert item_target({"type": "webSearch"}) is None


def test_claude_tool_target_arrives_with_the_full_message_and_the_tool_end():
    events = []
    state = Stream(lambda kind, data: events.append((kind, data)), root="/w")
    state.consume(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "id": "r1", "name": "Read", "input": {}},
            },
        }
    )
    state.consume(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": "/w/facts/alpha.txt"}}
                ]
            },
        }
    )
    state.consume(
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "r1"}]}}
    )
    assert "target" not in events[0][1]
    assert events[1][1]["target"] == "facts/alpha.txt"


def test_claude_full_start_input_carries_the_command_target():
    events = []
    Stream(lambda kind, data: events.append(data)).consume(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "content_block": {
                    "type": "tool_use",
                    "id": "b1",
                    "name": "Bash",
                    "input": {"command": "sed -n 1,5p notes.txt"},
                },
            },
        }
    )
    assert events[0]["target"] == "sed -n 1,5p notes.txt"


def test_gemini_tool_target_uses_raw_input_and_locations_never_the_title():
    events = []
    stream = AcpStream(lambda kind, data: events.append(data), root="/w")
    stream.consume(
        {
            "update": {
                "sessionUpdate": "tool_call",
                "toolCallId": "g1",
                "kind": "read",
                "title": "Read secret.txt",
                "status": "in_progress",
                "locations": [{"path": "/w/facts/alpha.txt"}],
            }
        }
    )
    stream.consume(
        {
            "update": {
                "sessionUpdate": "tool_call",
                "toolCallId": "g2",
                "kind": "execute",
                "title": "ignored",
                "status": "in_progress",
                "rawInput": {"command": "ls -la"},
            }
        }
    )
    assert events[0]["target"] == "facts/alpha.txt"
    assert events[1]["target"] == "ls -la"


@pytest.mark.parametrize(
    ("command", "secret"),
    [
        ("git clone https://user:hunter2@example.test/r.git", "hunter2"),
        ("cli --token abc123 run", "abc123"),
        ("cli --password=abc123 run", "abc123"),
        ("cli --api-key abc123", "abc123"),
        ("mysql -u root -pabc123 db", "abc123"),
        ("curl -H 'Authorization: Basic YWJjOjEyMw==' u", "YWJjOjEyMw"),
        ("curl -H 'Authorization: token abc123' u", "abc123"),
        ("GITHUB_TOKEN='ghp abc' make", "abc"),
        ('DB_PASSWORD="two words" make', "words"),
        ("echo ghp_abcDEF123", "abcDEF123"),
        ("echo github_pat_11AAA_bbb", "11AAA"),
        ("echo xoxb-123-abc-DEF", "123-abc"),
        ("echo AKIAABCDEFGHIJKLMNOP", "ABCDEFGHIJKLMNOP"),
        ("aws configure set aws_secret_access_key abc123", "abc123"),
        ("AWS_SECRET_ACCESS_KEY=abc123 aws s3 ls", "abc123"),
        ("api_key=abc123 run", "abc123"),
    ],
)
def test_tool_target_masks_common_secret_shapes(command, secret):
    target = tool_target("Bash", {"command": command})
    assert secret not in target and "[redacted]" in target


def test_tool_target_masks_a_whole_quoted_value():
    assert tool_target("Bash", {"command": "GITHUB_TOKEN='ghp abc' make"}) == "GITHUB_TOKEN=[redacted] make"


@pytest.mark.parametrize(
    "command",
    ["MONKEY=banana run", "ls --sort-key=name", "KEYBOARD=us run", "TOKENIZERS_PARALLELISM=false run", "mysql -u root db"],
)
def test_tool_target_keeps_lookalike_names_visible(command):
    assert tool_target("Bash", {"command": command}) == command


def test_portable_history_does_not_forward_the_display_target_to_the_next_provider():
    import json
    import sqlite3

    from agent_service.conversation_context import portable_history

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, job TEXT, type TEXT, data TEXT)")
    start = {"tool": "Bash", "command_name": "ls", "target": "ls /secret/path"}
    db.execute("INSERT INTO events(job,type,data) VALUES('j','tool_start',?)", (json.dumps(start),))
    payload = {"_job_id": "j", "_state": "completed", "prompt": "hi"}
    evidence = portable_history(db, [(payload, {"answer": "ok"})])[0]["evidence"]
    assert evidence == [{"type": "tool_start", "data": {"tool": "Bash", "command_name": "ls"}}]
