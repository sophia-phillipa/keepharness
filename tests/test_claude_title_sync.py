import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

from adapters.claude import backend, native


def test_native_session_receives_the_harness_conversation_title(tmp_path, monkeypatch):
    executable = tmp_path / "fake-claude"
    arguments = tmp_path / "arguments.json"
    executable.write_text(
        "#!" + sys.executable + "\n"
        "import json, sys\n"
        f"open({str(arguments)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        "json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'type':'result','subtype':'success','result':'ok','session_id':'claude-session'}), flush=True)\n"
    )
    executable.chmod(0o700)
    (tmp_path / "home").mkdir()
    monkeypatch.setattr(native, "configurations", lambda: {"claude": {}})
    monkeypatch.setattr(native, "inventory", lambda: {"claude": []})

    result = asyncio.run(
        native.run(
            {"binary": str(executable)},
            "hello",
            lambda *_: None,
            tmp_path,
            "sonnet",
            tmp_path / "home",
            {"read": True},
            [],
            lambda *_: asyncio.sleep(0),
            title="Tail Harness conversation  ",
        )
    )

    command = json.loads(arguments.read_text())
    assert command[command.index("--name") + 1] == "Tail Harness conversation  "
    assert result["thread_id"] == "claude-session"


def test_scoped_contract_has_no_persisted_claude_session():
    source = native.__file__.replace("native.py", "scoped.py")
    assert "--no-session-persistence" in open(source, encoding="utf-8").read()


def test_backend_forwards_the_canonical_title_to_native(tmp_path, monkeypatch):
    workspace = SimpleNamespace(
        prompt="hello",
        cwd=tmp_path,
        home=tmp_path,
        permissions={},
        images=[],
        roots=[tmp_path],
    )
    run = AsyncMock(return_value={"answer": "ok"})
    monkeypatch.setattr(backend, "prepare_workspace", lambda *_: workspace)
    monkeypatch.setattr(backend.native, "run", run)

    asyncio.run(
        backend.run_native(
            {"binary": "fixture"},
            "hello",
            lambda *_: None,
            {"_conversation_title": "Canonical title"},
            "sonnet",
            "configured",
            tmp_path,
            AsyncMock(),
        )
    )

    assert run.await_args.args[-1] == "Canonical title"
