"""Qualification script for the dsh ACP profile (KeepHarness #51), run against the offline fixture."""

import asyncio
import contextlib
import tempfile
from pathlib import Path
from typing import Any

import pytest

from agent_service.errors import ToolError
from agent_service.secret_vault import redact_secrets
from scripts import qualify_dsh

FIXTURE = Path(__file__).parent / "fixtures" / "fake-dsh-acp" / "dsh"
FAKE_KEY = "sk-fake-qualify-0123456789abcdef"
CRASH_ENV = {"FAKE_DSH_CRASH_MID_STREAM": "1"}
CRASH_FAIL_IDS = ("P02", "P04", "P05", "P07")
PASSING_PROBE_IDS = tuple(f"P{number:02d}" for number in range(1, 9))
UNVERIFIED_IDS = tuple(f"U{number:02d}" for number in range(1, 10))  # one per D-043 UNVERIFIED line
ROW_IDS = (*UNVERIFIED_IDS, *(f"P{number:02d}" for number in range(1, 10)))
NEEDS_TURN_REASON = "needs a real model turn (no API key in the disposable profile)"
MISSING_KEY_ERROR = (
    "gemini_execution_failed: Internal error: turn failed: llm-deepseek: no API key for provider "
    'route "deepseek-official"; store DEEPSEEK_API_KEY through the credentials service '
    "(the web Models page writes it), or export DEEPSEEK_API_KEY in the launching environment"
)
AUTH_ERROR = "gemini_execution_failed: Internal error: turn failed: llm-deepseek: 401 Unauthorized"


def rows_by_id(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in result["rows"]}


@pytest.fixture(scope="module")
def clean_run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    scratch = tmp_path_factory.mktemp("clean-run")
    return qualify_dsh.qualify(str(FIXTURE), scratch)


def test_clean_run_reports_every_row_in_table_order(clean_run: dict[str, Any]) -> None:
    assert [row["id"] for row in clean_run["rows"]] == list(ROW_IDS)


@pytest.mark.parametrize("probe_id", PASSING_PROBE_IDS)
def test_clean_run_protocol_probes_pass(clean_run: dict[str, Any], probe_id: str) -> None:
    assert rows_by_id(clean_run)[probe_id]["status"] == "PASS"


def test_clean_run_leaves_pinned_version_unverified(clean_run: dict[str, Any]) -> None:
    assert rows_by_id(clean_run)["P09"]["status"] == "UNVERIFIED"


def test_clean_run_leaves_every_unverified_item_unverified(
    clean_run: dict[str, Any],
) -> None:
    rows = rows_by_id(clean_run)
    assert {rows[row_id]["status"] for row_id in UNVERIFIED_IDS} == {"UNVERIFIED"}


def test_usage_row_stays_unverified_when_the_target_emits_no_usage(
    clean_run: dict[str, Any],
) -> None:
    row = rows_by_id(clean_run)["U05"]
    assert row["status"] == "UNVERIFIED"
    assert "usage not emitted by the probe target" in row["reason"]


def test_usage_reason_records_an_observed_event_verbatim() -> None:
    reason = qualify_dsh.usage_reason([{"used": 10, "size": 100}])
    assert '{"used": 10, "size": 100}' in reason


def test_crash_mid_stream_fails_streaming_and_resume_probes(tmp_path: Path) -> None:
    rows = rows_by_id(qualify_dsh.qualify(str(FIXTURE), tmp_path, child_env=CRASH_ENV))
    for probe_id in CRASH_FAIL_IDS:
        assert rows[probe_id]["status"] == "FAIL", probe_id
        assert rows[probe_id]["reason"], probe_id
    assert rows["P03"]["status"] == "PASS"


def test_report_has_no_sandbox_home_or_key_and_removes_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    report = tmp_path / "report.md"

    code = qualify_dsh.main(
        [
            "--dsh",
            str(FIXTURE),
            "--out",
            str(report),
            "--child-env",
            "FAKE_DSH_CRASH_MID_STREAM=1",
            f"DEEPSEEK_API_KEY={FAKE_KEY}",
        ]
    )

    text = report.read_text(encoding="utf-8")
    assert code == 1
    assert str(scratch) not in text
    assert str(Path.home()) not in text
    assert FAKE_KEY not in text
    assert list(scratch.iterdir()) == []


def test_transcript_never_loads_a_session_and_resumes_one(
    clean_run: dict[str, Any],
) -> None:
    methods = {message.get("method") for message in clean_run["transcript"]}
    assert "session/load" not in methods
    assert "session/resume" in methods


def test_sanitize_replaces_sandbox_home_and_keys(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    text = (
        f"cwd {sandbox}/state home {Path.home()}/.cache "
        f"key sk-live0123456789abcdefghij and {FAKE_KEY}"
    )

    cleaned = qualify_dsh.sanitize(text, sandbox)

    assert "<sandbox>/state" in cleaned
    assert "<HOME>/.cache" in cleaned
    assert "[redacted-key]" in cleaned
    assert "sk-live0123456789" not in cleaned
    assert FAKE_KEY not in cleaned
    assert str(sandbox) not in cleaned


def test_sanitize_applies_shared_secret_redaction(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    assert qualify_dsh.sanitize(f"{sandbox}/x", sandbox) == redact_secrets("<sandbox>/x")


def test_qualify_without_dsh_leaves_every_row_unverified(tmp_path: Path) -> None:
    result = qualify_dsh.qualify(None, tmp_path)

    assert [row["id"] for row in result["rows"]] == list(ROW_IDS)
    assert {row["status"] for row in result["rows"]} == {"UNVERIFIED"}
    assert list(tmp_path.iterdir()) == []


def test_main_returns_one_in_crash_mode_and_writes_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    report = tmp_path / "report.md"

    code = qualify_dsh.main(
        [
            "--dsh",
            str(FIXTURE),
            "--out",
            str(report),
            "--child-env",
            "FAKE_DSH_CRASH_MID_STREAM=1",
        ]
    )

    assert code == 1
    assert report.read_text(encoding="utf-8").strip()


class FakeRpc:
    """Answers each ACP method from a canned reply; an exception value is raised instead."""

    def __init__(self, replies: dict[str, Any]) -> None:
        self.replies = replies

    async def call(self, method: str, _params: dict[str, Any]) -> Any:
        reply = self.replies.get(method, {})
        if isinstance(reply, BaseException):
            raise reply
        return reply


def fake_connect(replies: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    @contextlib.asynccontextmanager
    async def connect(_run: Any, _extra_env: Any, *_args: Any, **_kwargs: Any) -> Any:
        yield FakeRpc(replies)

    monkeypatch.setattr(qualify_dsh, "connect", connect)


def fake_run(tmp_path: Path) -> qualify_dsh.Run:
    return qualify_dsh.Run(
        dsh=tmp_path / "dsh", env={}, workspace=tmp_path, transcript=[], usage=[]
    )


def test_handshake_accepts_an_absent_load_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_connect({"initialize": {"protocolVersion": 1, "agentCapabilities": {}}}, monkeypatch)
    verdict = asyncio.run(qualify_dsh.probe_handshake(fake_run(tmp_path)))
    assert verdict.status == "PASS"


def test_handshake_fails_when_load_session_is_advertised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replies = {"initialize": {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}}}
    fake_connect(replies, monkeypatch)
    verdict = asyncio.run(qualify_dsh.probe_handshake(fake_run(tmp_path)))
    assert verdict.status == "FAIL"


def test_turn_without_api_key_is_unverified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replies = {
        "session/new": {"sessionId": "s1"},
        "session/prompt": ToolError(MISSING_KEY_ERROR),
    }
    fake_connect(replies, monkeypatch)
    verdict = asyncio.run(qualify_dsh.bounded(qualify_dsh.probe_turn, fake_run(tmp_path)))
    assert verdict == qualify_dsh.Verdict("UNVERIFIED", NEEDS_TURN_REASON)


def test_turn_auth_error_is_unverified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replies = {"session/new": {"sessionId": "s1"}, "session/prompt": ToolError(AUTH_ERROR)}
    fake_connect(replies, monkeypatch)
    verdict = asyncio.run(qualify_dsh.bounded(qualify_dsh.probe_turn, fake_run(tmp_path)))
    assert verdict == qualify_dsh.Verdict("UNVERIFIED", NEEDS_TURN_REASON)


def test_turn_failure_that_is_not_about_the_key_stays_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replies = {
        "session/new": {"sessionId": "s1"},
        "session/prompt": ToolError("gemini_execution_failed: turn stream dropped"),
    }
    fake_connect(replies, monkeypatch)
    verdict = asyncio.run(qualify_dsh.bounded(qualify_dsh.probe_turn, fake_run(tmp_path)))
    assert verdict.status == "FAIL"
    assert verdict.reason == "gemini_execution_failed: turn stream dropped"


def test_session_not_resumable_reply_stays_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_connect({"session/resume": ToolError("session is not resumable")}, monkeypatch)
    verdict = asyncio.run(
        qualify_dsh.bounded(qualify_dsh.probe_unknown_session, fake_run(tmp_path))
    )
    assert verdict.status == "FAIL"


def test_pinned_version_with_an_unverified_turn_does_not_blame_the_version() -> None:
    protocol = {
        "P01": qualify_dsh.Verdict("PASS"),
        "P02": qualify_dsh.Verdict("UNVERIFIED", NEEDS_TURN_REASON),
    }
    verdict = qualify_dsh.version_verdict(qualify_dsh.PINNED_VERSION, protocol)
    assert verdict.status == "UNVERIFIED"
    assert "not the pinned one" not in verdict.reason
