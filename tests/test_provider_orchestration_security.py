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


@pytest.mark.parametrize("option", ['"--token"', "'--token'", '"--api-key"'])
def test_quoted_credential_option_redacts_value(option):
    assert "private-value" not in safe_text(f'check {option} "private-value"')


REDACTION_CASES = [
    pytest.param(
        'check --label "token" --token SECRET-ONE',
        ("SECRET-ONE",),
        ("check", '"token"', "--token"),
        id="quoted-value-named-token",
    ),
    pytest.param(
        'check --label "--token" --api-key SECRET-TWO',
        ("SECRET-TWO",),
        ("check", "--label", "--api-key"),
        id="quoted-flag-as-value",
    ),
    pytest.param(
        'check "--token" "SECRET-THREE"',
        ("SECRET-THREE",),
        ("check",),
        id="quoted-flag-then-quoted-secret",
    ),
    pytest.param(
        'check --token "first\\"leaked-suffix"',
        ("first", "leaked-suffix"),
        ("check", "--token"),
        id="escaped-quote-inside-value",
    ),
    pytest.param(
        "check --token=SECRET-FOUR --mode safe",
        ("SECRET-FOUR",),
        ("--token=", "--mode", "safe"),
        id="equals-form",
    ),
    pytest.param(
        "curl -H 'Authorization: Bearer s3cret-five'",
        ("s3cret-five",),
        ("curl", "-H", "Authorization:"),
        id="bearer-header",
    ),
    pytest.param(
        'curl -H "X-API-Key: first second"',
        ("first", "second"),
        ("curl", "-H", "X-API-Key:"),
        id="multi-word-api-key-header",
    ),
    pytest.param(
        'curl -H "Cookie: session=private-cookie"',
        ("private-cookie",),
        ("curl", "-H"),
        id="cookie-header",
    ),
    pytest.param(
        "curl -H 'Authorization: Custom private-auth'",
        ("private-auth",),
        ("curl", "-H"),
        id="custom-authorization-header",
    ),
    pytest.param(
        'check --token "first leaked-suffix',
        ("first", "leaked-suffix"),
        ("check", "--token"),
        id="unbalanced-quote-after-credential",
    ),
    pytest.param(
        'echo "oops --token private-value',
        ("private-value",),
        ("echo",),
        id="unbalanced-quote-before-credential",
    ),
    pytest.param(
        "plain words " * 22_000,
        (),
        ("plain",),
        id="256-kib-nonsecret-input",
    ),
]


@pytest.mark.parametrize(("value", "secrets", "kept"), REDACTION_CASES)
def test_shell_words_redact_every_secret_and_keep_plain_text(value, secrets, kept):
    with deadline(2):
        result = safe_text(value)
    for secret in secrets:
        assert secret not in result
    for text in kept:
        assert text in result


def test_nested_instruction_import_allows_safe_parent_traversal(tmp_path):
    project = tmp_path / "project"
    (project / "docs").mkdir(parents=True)
    (project / "CLAUDE.md").write_text("@docs/more.md")
    (project / "docs/more.md").write_text("@../shared.md")
    (project / "shared.md").write_text("Shared instructions")
    reader = InstructionReader(tmp_path, project)
    reader.document(project / "CLAUDE.md", "project", imports=True)
    assert any(item.details.get("preview") == "Shared instructions" for item in reader.items)
    assert project / "shared.md" in reader.paths


@pytest.mark.parametrize("linked", [False, True])
def test_nested_instruction_import_rejects_escape_and_symlink(tmp_path, linked):
    project = tmp_path / "project"
    (project / "docs").mkdir(parents=True)
    (tmp_path / "private.md").write_text("private-content")
    if linked:
        (project / "linked").symlink_to(project / "docs", target_is_directory=True)
        target = "../linked/../shared.md"
        (project / "shared.md").write_text("private-content")
    else:
        target = "../../private.md"
    (project / "CLAUDE.md").write_text("@docs/more.md")
    (project / "docs/more.md").write_text("@" + target)
    reader = InstructionReader(tmp_path, project)
    reader.document(project / "CLAUDE.md", "project", imports=True)
    assert all(item.details.get("preview") != "private-content" for item in reader.items)
    assert reader.warnings
