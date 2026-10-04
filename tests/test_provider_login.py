"""Browser renewal chooses CLI credentials only after successful login."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from adapters.claude.auth import cli_login_environment
from control.operations import Operations, strip_ansi
from control.server import Manager, create_app
from tests.owner_session import sign_in


def test_login_environment_does_not_modify_parent(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "stale-fixture")
    monkeypatch.setenv("KEEP_TEST_VARIABLE", "kept")
    env = cli_login_environment()
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert env["KEEP_TEST_VARIABLE"] == "kept"
    assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "stale-fixture"


@pytest.mark.parametrize("exit_code", [0, 1])
def test_login_applies_credential_preference_only_on_success(tmp_path, monkeypatch, exit_code):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "stale-fixture")
    manager = Manager(tmp_path)
    manager._write_runtime({"claude": {"binary": "/fixture"}, "other": "preserved"})

    async def exercise():
        operations = Operations()
        job = operations.launch(
            [
                sys.executable,
                "-c",
                f'import os,sys; assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ; sys.exit({exit_code})',
            ],
            env=cli_login_environment(),
            on_success=manager.claude_login_completed,
        )
        await asyncio.gather(*operations.tasks)
        assert job["state"] == ("completed" if exit_code == 0 else "failed")
        assert (tmp_path / "claude-cli-login").exists() == (exit_code == 0)
        runtime = manager._previous_runtime()
        assert runtime["claude"].get("use_cli_login", False) == (exit_code == 0)
        assert runtime["other"] == "preserved"
        assert manager.auth.get("claude") is (True if exit_code == 0 else None)

    asyncio.run(exercise())


def test_cancelled_login_keeps_existing_credentials(tmp_path):
    async def exercise():
        manager = Manager(tmp_path)
        operations = Operations()
        job = operations.launch(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            on_success=manager.claude_login_completed,
        )
        await asyncio.sleep(0.05)
        operations.cancel(job["id"])
        await asyncio.gather(*operations.tasks, return_exceptions=True)
        assert job["state"] == "cancelled"
        assert not (tmp_path / "claude-cli-login").exists()

    asyncio.run(exercise())


def test_login_endpoint_deduplicates_and_requires_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "stale-fixture")
    inventory = {"services": [], "binaries": {"claude": "/fixture/claude"}, "network": {}}

    def launch(operations, args, timeout=300, **kwargs):
        kwargs["timeout"] = timeout
        assert args == ["/fixture/claude", "auth", "login"]
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in kwargs["env"]
        assert callable(kwargs["on_success"])
        assert kwargs["interactive"] is True
        assert kwargs["timeout"] == 900
        job = {"id": "login", "state": "running", "output": ""}
        operations.jobs["login"] = job
        return job

    with (
        patch("control.discovery.scan", AsyncMock(return_value=inventory)),
        patch.object(Operations, "launch", launch),
    ):
        with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8094") as client:
            assert (
                client.post("/api/provider-login", json={"provider": "claude"}).status_code == 401
            )
            sign_in(client).get("/")
            assert (
                client.post("/api/provider-login", json={"provider": "claude"}).status_code == 400
            )
            headers = {"X-Harness-Admin": "1"}
            one = client.post("/api/provider-login", json={"provider": "claude"}, headers=headers)
            two = client.post("/api/provider-login", json={"provider": "claude"}, headers=headers)
            assert one.status_code == two.status_code == 200
            assert one.json()["id"] == two.json()["id"]


def test_renewed_account_check_ignores_inherited_token(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "stale-fixture")
    manager = Manager(tmp_path)
    manager.inventory = {"services": [{"id": "claude", "found": True, "binary": "/fixture"}]}
    asyncio.run(manager.claude_login_completed())
    with (
        patch(
            "control.discovery.command", AsyncMock(return_value=(0, '{"loggedIn":true}'))
        ) as command,
        patch(
            "adapters.claude.account.metadata",
            AsyncMock(return_value={"models": [{"value": "sonnet"}]}),
        ),
    ):
        assert asyncio.run(manager.check("claude"))["authenticated"]
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in command.call_args.kwargs["env"]


@pytest.mark.parametrize("use_cli_login", [False, True])
def test_native_run_uses_the_selected_auth_source(tmp_path, monkeypatch, use_cli_login):
    from adapters.claude import native

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "stale-fixture")
    executable = tmp_path / "claude-fixture"
    executable.write_text(
        "#!" + sys.executable + "\nimport sys,json,os\n"
        "json.loads(sys.stdin.readline())\n"
        'print(json.dumps({"type":"result","subtype":"success","result":"env" if "CLAUDE_CODE_OAUTH_TOKEN" in os.environ else "cli"}),flush=True)\n'
    )
    executable.chmod(0o700)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(native, "configurations", lambda: {"claude": {}})
    monkeypatch.setattr(native, "inventory", lambda: {"claude": []})
    result = asyncio.run(
        native.run(
            {"binary": str(executable), "use_cli_login": use_cli_login},
            "fixture",
            lambda *_: None,
            tmp_path,
            "sonnet",
            home,
            {},
            [],
            AsyncMock(),
        )
    )
    # The terminal's login token never reaches a run: KeepHarness signs in to its own home (D02).
    assert result["answer"] == "cli"


def test_pending_claude_login_does_not_block_native_harness_startup(tmp_path):
    manager = Manager(tmp_path)
    manager.settings["services"]["claude"].update(enabled=True, models=["sonnet"])
    manager.inventory = {
        "network": {},
        "binaries": {},
        "services": [
            {
                "id": "claude",
                "binary": sys.executable,
                "auth_file": str(tmp_path / "missing-credentials"),
            }
        ],
    }
    with patch.object(
        manager,
        "check",
        AsyncMock(return_value={"authenticated": False, "models": {"sonnet": ["configured"]}}),
    ):
        runtime = asyncio.run(manager.build_runtime_config(manager.settings))
    assert runtime["services"]["claude"]["enabled"]
    assert runtime["services"]["claude"]["mode"] == "native"
    assert runtime["claude_models"] == {"sonnet": ["configured"]}


CODE = "4ukTm3Jk-fixture#XYiy_fixture-code"


def test_interactive_login_receives_one_pasted_code_line(tmp_path):
    async def exercise():
        operations = Operations()
        job = operations.launch(
            [
                sys.executable,
                "-c",
                f"import sys; sys.exit(0 if sys.stdin.readline().strip() == {CODE!r} else 3)",
            ],
            interactive=True,
        )
        await asyncio.sleep(0.05)
        assert job["accepts_input"] is True
        await operations.send_input(job["id"], CODE)
        await asyncio.gather(*operations.tasks)
        assert job["state"] == "completed"
        assert job["accepts_input"] is False
        assert CODE not in job["output"]

    asyncio.run(exercise())


ECHO_SCRIPT = """
import sys, time
line = sys.stdin.readline().strip()
print("received", line, flush=True)
half = len(line) // 2
sys.stdout.write("again " + line[:half])
sys.stdout.flush()
time.sleep(0.3)
sys.stdout.write(line[half:] + "\\n")
sys.stdout.flush()
"""


def test_a_login_code_the_cli_echoes_is_removed_from_the_job_output():
    async def exercise():
        operations = Operations()
        job = operations.launch([sys.executable, "-c", ECHO_SCRIPT], interactive=True)
        await asyncio.sleep(0.05)
        await operations.send_input(job["id"], CODE)
        await asyncio.gather(*operations.tasks)
        assert job["state"] == "completed"
        assert "received [redacted]" in job["output"] and "again" in job["output"]
        assert CODE not in job["output"]
        # The code lives in memory only for the redaction, and only while the job runs.
        assert CODE not in repr(vars(operations)) and not operations.codes

    asyncio.run(exercise())


def test_non_interactive_operation_rejects_input(tmp_path):
    async def exercise():
        operations = Operations()
        job = operations.launch([sys.executable, "-c", "import time; time.sleep(30)"])
        await asyncio.sleep(0.05)
        assert not job.get("accepts_input")
        with pytest.raises(ValueError):
            await operations.send_input(job["id"], CODE)
        operations.cancel(job["id"])
        await asyncio.gather(*operations.tasks, return_exceptions=True)

    asyncio.run(exercise())


def test_login_code_endpoint_validates_and_never_records_the_code(tmp_path):
    inventory = {"services": [], "binaries": {"claude": "/fixture/claude"}, "network": {}}
    sent = []

    async def send_input(operations, jid, text):
        sent.append((jid, text))

    with (
        patch("control.discovery.scan", AsyncMock(return_value=inventory)),
        patch.object(Operations, "send_input", send_input),
    ):
        with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8094") as client:
            sign_in(client).get("/")
            headers = {"X-Harness-Admin": "1"}
            path = "/api/provider-login-code"
            assert client.post(path, json={"id": "login", "code": CODE}).status_code == 400
            client.app.state.manager.operations.jobs["login"] = {
                "id": "login",
                "state": "running",
                "output": "",
                "kind": "provider-login",
                "provider": "claude",
                "accepts_input": True,
            }
            for bad in ("", "has space", "line\nbreak", "x" * 600, 42):
                response = client.post(path, json={"id": "login", "code": bad}, headers=headers)
                assert response.status_code == 400, bad
            assert client.post(path, json={"id": "other", "code": CODE}, headers=headers).status_code == 400
            ok = client.post(path, json={"id": "login", "code": CODE}, headers=headers)
            assert ok.status_code == 200 and ok.json() == {"sent": True}
            assert sent == [("login", CODE)]
    assert CODE not in "".join(path.read_text() for path in tmp_path.rglob("*") if path.is_file())


@pytest.mark.parametrize(
    "sequence",
    ["\x1b[94m", "\x1b]8;;https://x.test\x07", "\x1b]8;;https://x.test\x1b\\", "\x1b(B", "\x1b7", "\x1b8"],
)
def test_login_output_strips_terminal_sequences_at_every_read_boundary(sequence):
    for boundary in range(len(sequence) + 1):
        first, held = strip_ansi("before" + sequence[:boundary])
        second, held = strip_ansi(held + sequence[boundary:] + "after")
        assert first + second == "beforeafter"
        assert held == ""


def test_login_output_has_no_ansi_escape_codes_even_when_split_between_reads(tmp_path):
    """Codex colours its URL and one-time code; the raw codes broke the link and the code (PRD-R2-1)."""
    script = (
        "import sys,time\n"
        "w=lambda s:(sys.stdout.write(s),sys.stdout.flush(),time.sleep(0.15))\n"
        "w('Open \\x1b[94mhttps://auth.openai.com/codex/device\\x1b[0m\\n')\n"
        "w('code \\x1b[9')\n"  # an escape sequence cut in two reads
        "w('4mABCD-12345\\x1b[0')\n"
        "w('m\\n\\x1b]8;;https://x.test\\x07link\\x1b]8;;\\x07 done\\n')\n"
    )

    async def exercise():
        operations = Operations()
        job = operations.launch([sys.executable, "-c", script])
        await asyncio.gather(*operations.tasks)
        return job

    job = asyncio.run(exercise())
    assert job["state"] == "completed"
    assert "\x1b" not in job["output"]
    assert "Open https://auth.openai.com/codex/device\n" in job["output"]
    assert "code ABCD-12345\n" in job["output"]
    assert "link done" in job["output"]


def test_an_unterminated_osc_sequence_does_not_hold_back_later_output():
    text, held = strip_ansi("before\x1b]0;" + "x" * 3000)
    assert held == "" and text.startswith("before0;x")  # released as text, not held forever
    text, held = strip_ansi("\x1b]8;;https://x.test")  # a short one is still waited for
    assert (text, held) == ("", "\x1b]8;;https://x.test")


def test_sign_in_output_after_an_unterminated_osc_is_kept_when_the_cli_exits(tmp_path):
    script = "import sys\nsys.stdout.write('\\x1b]0;title\\nOpen https://x.test code ABCD-1234\\n')\n"

    async def exercise():
        operations = Operations()
        job = operations.launch([sys.executable, "-c", script])
        await asyncio.gather(*operations.tasks)
        return job

    job = asyncio.run(exercise())
    assert job["state"] == "completed"
    assert "Open https://x.test code ABCD-1234" in job["output"] and "\x1b" not in job["output"]


@pytest.mark.parametrize(
    ("display", "expected"),
    [
        ({"DISPLAY": ":0"}, ["/fixture/codex", "login"]),
        ({"WAYLAND_DISPLAY": "wayland-0"}, ["/fixture/codex", "login"]),
        ({}, ["/fixture/codex", "login", "--device-auth"]),
    ],
)
def test_codex_signs_in_in_the_browser_locally_and_by_device_code_when_headless(
    tmp_path, monkeypatch, display, expected
):
    """D20: the browser callback where a browser can open, device auth for a headless host."""
    monkeypatch.setattr(sys, "platform", "linux")
    for name in ("DISPLAY", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in display.items():
        monkeypatch.setenv(name, value)
    inventory = {"services": [], "binaries": {"codex": "/fixture/codex"}, "network": {}}
    seen = []

    def launch(operations, args, timeout=300, **kwargs):
        seen.append(args)
        job = {"id": "login", "state": "running", "output": ""}
        operations.jobs["login"] = job
        return job

    with (
        patch("control.discovery.scan", AsyncMock(return_value=inventory)),
        patch.object(Operations, "launch", launch),
    ):
        with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8094") as client:
            sign_in(client).get("/")
            response = client.post(
                "/api/provider-login", json={"provider": "codex"}, headers={"X-Harness-Admin": "1"}
            )
    assert response.status_code == 200
    assert seen == [expected]
