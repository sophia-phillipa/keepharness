"""Provider diagnostics are useful, bounded and never contain authentication data."""

import asyncio
import json
import sys
from unittest.mock import AsyncMock

import pytest

from adapters.codex.rpc import connection


@pytest.mark.parametrize("secret", ["x", "abcdefg", "abcdefgh"])
def test_stderr_value_redaction_requires_eight_characters(secret, monkeypatch):
    from adapters.shared.process import StderrCapture

    monkeypatch.setattr("adapters.shared.process.os.environ", {})
    capture = StderrCapture({"API_TOKEN": secret})
    capture.buffer.extend(f"diagnostic {secret} suffix\ntoken={secret}\n".encode())
    expected = "[redacted]" if len(secret) >= 8 else secret
    assert capture.text() == f"diagnostic {expected} suffix\ntoken=[redacted]\n"


@pytest.mark.parametrize("field", ["nonce", "NONCE", "enrollment_nonce"])
def test_stderr_redacts_nonce_fields(field):
    from adapters.shared.process import StderrCapture

    capture = StderrCapture({})
    capture.buffer.extend(f'{field}="short"\nuseful diagnostic\n'.encode())
    assert capture.text() == f"{field}=[redacted]\nuseful diagnostic\n"


def test_codex_stderr_flood_is_drained_bounded_and_redacted(monkeypatch):
    monkeypatch.setenv("KEEPHARNESS_API_KEY", "harness-fixture-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "provider-fixture-secret")
    program = """
import json, sys
line = sys.stdin.readline()
sys.stderr.write('x' * 100000 + '\\n')
sys.stderr.write('Bearer arbitrary-secret Cookie: harness_token=cookie-secret\\n')
sys.stderr.write('OPENAI_API_KEY=provider-fixture-secret\\n')
sys.stderr.write('harness-fixture-secret\\n')
sys.stderr.write('useful final diagnostic\\n')
sys.stderr.flush()
print(json.dumps({'id': json.loads(line)['id'], 'result': {}}), flush=True)
sys.stdin.readline()
sys.stdin.readline()
"""
    events = []

    async def scenario():
        with pytest.raises(RuntimeError, match="fixture_failure") as caught:
            async with connection(
                [sys.executable, "-c", program], event=lambda *event: events.append(event)
            ):
                raise RuntimeError("fixture_failure")
        text = caught.value.error_detail
        assert "useful final diagnostic" in text
        assert len(text.encode()) <= 65536
        assert all(
            value not in text
            for value in (
                "arbitrary-secret",
                "cookie-secret",
                "provider-fixture-secret",
                "harness-fixture-secret",
            )
        )

    asyncio.run(scenario())
    assert events[-1][0] == "provider_stderr"
    assert "useful final diagnostic" in json.dumps(events)


def test_provider_environment_strips_harness_authority_and_host_logins():
    from adapters.shared.process import child_environment

    source = {
        "PATH": "/usr/bin",
        "HOME": "/temporary-home",
        "OPENAI_API_KEY": "provider-a",
        "ANTHROPIC_API_KEY": "provider-b",
        "CLAUDE_CODE_OAUTH_TOKEN": "provider-c",
        "KEEPHARNESS_API_KEY": "authority-a",
        "LOCAL_AGENT_TOKEN": "authority-b",
        "harness_token": "authority-c",
        "HTTP_COOKIE": "authority-d",
        "KEEPHARNESS_AGENT_CONFIG": "/secret/runtime.json",
        "HARNESS_SESSION_COOKIE": "authority-e",
        "ADMIN_TOKEN": "authority-f",
        # The prefix from before the KeepHarness rename still carries harness authority.
        "TAIL_HARNESS_TOKEN": "authority-g",
    }
    clean = child_environment(source)
    # Provider logins live in the harness-owned homes, not in host variables (D02, SEC RC-09).
    assert clean == {key: source[key] for key in ("PATH", "HOME")}
    assert len(source) == 13


def test_deepseek_keeps_only_its_explicit_inference_credential(monkeypatch):
    from adapters.shared.process import child_environment

    monkeypatch.setenv("KEEPHARNESS_API_KEY", "host-authority")
    assert "KEEPHARNESS_API_KEY" not in child_environment(provider="deepseek")
    assert child_environment(
        {"KEEPHARNESS_API_KEY": "inference-key", "HARNESS_SESSION": "human"}, provider="deepseek"
    ) == {"KEEPHARNESS_API_KEY": "inference-key"}


@pytest.mark.parametrize("native", [False, True])
def test_claude_spawn_scrubs_inheritance_and_reports_stderr(native, tmp_path, monkeypatch):
    from adapters.claude import native as claude_native
    from adapters.claude.stream import stream

    monkeypatch.setenv("HARNESS_SESSION", "human-cookie")
    monkeypatch.setenv("KEEPHARNESS_TOKEN", "human-token")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "provider-oauth")
    monkeypatch.setenv("HOME", str(tmp_path))
    program = """
import json, os, sys
assert 'HARNESS_SESSION' not in os.environ
assert 'KEEPHARNESS_TOKEN' not in os.environ
assert 'CLAUDE_CODE_OAUTH_TOKEN' not in os.environ
sys.stdin.readline()
sys.stderr.write('harness_session=human-cookie\\n')
sys.stderr.write('enrollment_nonce=enrollment-secret\\n')
sys.stderr.write('provider-oauth\\n')
sys.stderr.write('useful Claude diagnostic\\n')
sys.stderr.flush()
print(json.dumps({'type':'result','subtype':'error','is_error':True}), flush=True)
"""
    events = []
    command = [sys.executable, "-c", program]
    monkeypatch.setattr(claude_native, "build_command", lambda *args: command)

    async def scenario():
        with pytest.raises(Exception, match="claude_execution_failed") as caught:
            if native:
                await claude_native.run(
                    {"binary": sys.executable},
                    "prompt",
                    lambda *args: events.append(args),
                    tmp_path,
                    "fake",
                    tmp_path,
                    {},
                    AsyncMock(),
                )
            else:
                await stream(command, "prompt\n", lambda *args: events.append(args), "fake")
        detail = caught.value.error_detail
        assert "useful Claude diagnostic" in detail
        assert all(
            secret not in detail
            for secret in ("human-cookie", "enrollment-secret", "provider-oauth")
        )
        assert events[-1][0] == "provider_stderr"

    asyncio.run(scenario())


def test_stderr_redaction_covers_chunk_boundary_and_truncated_credential(monkeypatch):
    from adapters.shared.process import STDERR_LIMIT, StderrCapture

    async def scenario():
        reader = asyncio.StreamReader()
        capture = StderrCapture({})
        reader.feed_data(b"Cookie: harness_ses")
        reader.feed_data(b"sion=unknown-cookie; harness_token=unknown-token\n")
        reader.feed_data(b"Authorization: Bearer " + b"z" * (STDERR_LIMIT + 200))
        reader.feed_data(b"\nuseful final line\n")
        reader.feed_eof()
        await capture.drain(reader)
        text = capture.text()
        assert len(capture.buffer) <= STDERR_LIMIT
        assert text == "useful final line\n"

    asyncio.run(scenario())


def test_gemini_failure_reports_redacted_stderr(tmp_path, monkeypatch):
    from adapters.gemini import native
    from agent_service.tools import ToolError

    program = """
import json, sys
request = json.loads(sys.stdin.readline())
sys.stderr.write('harness_session=gemini-human-secret\\nGemini diagnostic\\n')
sys.stderr.flush()
print(json.dumps({'id':request['id'],'result':{'agentCapabilities':{}}}), flush=True)
"""
    monkeypatch.setattr(native, "prepare", lambda *args: ([sys.executable, "-c", program], {}))
    events = []

    async def scenario():
        with pytest.raises(ToolError, match="gemini_acp_unavailable") as caught:
            await native.run(
                {},
                "hello",
                lambda *args: events.append(args),
                tmp_path,
                "auto",
                tmp_path,
                {},
                "ask",
            )
        assert "Gemini diagnostic" in caught.value.error_detail
        assert "gemini-human-secret" not in caught.value.error_detail
        assert events[-1][0] == "provider_stderr"

    asyncio.run(scenario())


def test_stderr_keeps_a_large_nonsecret_line_within_the_cap():
    from adapters.shared.process import StderrCapture

    async def scenario():
        reader = asyncio.StreamReader()
        capture = StderrCapture({})
        reader.feed_data(b"x" * 60000 + b"\n")
        reader.feed_eof()
        await capture.drain(reader)
        assert capture.text() == "x" * 60000 + "\n"

    asyncio.run(scenario())


def test_cleanup_reaps_a_child_with_unread_stdout():
    from adapters.shared.process import process_diagnostics

    async def scenario():
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import sys,time; sys.stdout.buffer.write(b'x'*100000); sys.stdout.flush(); time.sleep(30)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            limit=1024,
        )
        await asyncio.sleep(0.05)

        async def finish():
            async with process_diagnostics(process, "fixture"):
                pass

        try:
            await asyncio.wait_for(finish(), 0.4)
        finally:
            # Recover the fixture even if the lifecycle under test fails.
            await process.stdout.read()
            await process.wait()
        assert process.returncode is not None

    asyncio.run(scenario())


@pytest.mark.parametrize("header", ["Authorization", "Proxy-Authorization"])
@pytest.mark.parametrize(
    "value",
    ["Basic dXNlcjpwYXNzd29yZA==", 'Digest username="unknown-user", response="unknown-response"'],
)
def test_stderr_masks_complete_authorization_headers(header, value):
    from adapters.shared.process import StderrCapture

    async def scenario():
        reader = asyncio.StreamReader()
        capture = StderrCapture({})
        reader.feed_data((header + ": " + value[:5]).encode())
        reader.feed_data((value[5:] + "\nuseful diagnostic\n").encode())
        reader.feed_eof()
        await capture.drain(reader)
        assert capture.text() == header + ": [redacted]\nuseful diagnostic\n"

    asyncio.run(scenario())
