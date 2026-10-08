"""Security regressions for file-backed orchestration previews; fake files only."""

import os
import signal
from contextlib import contextmanager

import pytest

from adapters.shared.orchestration_state import InstructionReader, safe_text


class DeadlineExceeded(Exception):
    pass


@contextmanager
def deadline(seconds):
    def expired(*_):
        raise DeadlineExceeded("bounded operation exceeded its deadline")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@pytest.mark.parametrize(
    "value",
    [
        r'run --token "first\"leaked-suffix" --mode safe',
        'curl -H "X-API-Key: first leaked-suffix"',
        "X-API-Key: first leaked-suffix",
    ],
)
def test_credential_redaction_covers_escaped_quotes_and_full_header_values(value):
    assert "leaked-suffix" not in safe_text(value)
    assert "first" not in safe_text(value)


def test_sanitizer_long_nonsecret_tokens_complete_with_linear_work():
    with deadline(2):
        for _ in range(20):
            assert safe_text("a" * 8000) == "a" * 8000


def test_instruction_preview_refuses_hardlinked_secret_files(tmp_path):
    private = tmp_path / "private.key"
    private.write_text("opaque-private-content")
    source = tmp_path / "CLAUDE.md"
    os.link(private, source)
    reader = InstructionReader(tmp_path, tmp_path)
    reader.document(source, "project")
    assert not reader.items
    assert reader.warnings


def test_instruction_preview_refuses_fifo_without_blocking(tmp_path):
    source = tmp_path / "CLAUDE.md"
    os.mkfifo(source)
    reader = InstructionReader(tmp_path, tmp_path)
    with deadline(2):
        reader.document(source, "project")
    assert not reader.items
    assert reader.warnings


def test_instruction_preview_never_creates_missing_parent_directories(tmp_path):
    parent = tmp_path / "missing" / "nested"
    reader = InstructionReader(tmp_path)
    reader.document(parent / "AGENTS.md", "user")
    assert not parent.exists()
    assert not reader.items


@pytest.mark.parametrize(
    "value", ['check --token "first"leaked-suffix', 'check --token first"leaked-suffix"']
)
def test_secret_shell_argument_redaction_includes_adjacent_quoted_segments(value):
    assert "first" not in safe_text(value)
    assert "leaked-suffix" not in safe_text(value)


def test_sanitizer_bounds_large_inputs_and_redacts_secrets_crossing_cutoff():
    with deadline(2):
        assert safe_text("a" * 1_000_000) == "a" * 8000
        result = safe_text('run --token "' + "private-fragment" * 100_000)
    assert "private-fragment" not in result
    assert "[REDACTED]" in result
