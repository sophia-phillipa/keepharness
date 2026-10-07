"""Provider state seam: errors, fingerprint and the atomic JSON writer (decisions D-038, D-039).

Fake homes only: every file lives under ``tmp_path``; nothing reads the owner's real
``~/.claude.json``, ``~/.claude`` or ``~/.codex``.
"""

import asyncio
import hashlib
import json
import logging
import os
import stat
from pathlib import Path

import pytest

from adapters.shared import provider_state
from adapters.shared.provider_state import (
    ProviderCommandError,
    ProviderStateAdapter,
    ProviderStateConflictError,
    ProviderStateSchemaError,
    ProviderStateUnsupportedError,
    ProviderStateValidationError,
    ProviderStateVersionError,
    ProviderVersionUnsupportedError,
    claude_json_backup_dir,
    fingerprint,
    write_json_atomic,
)
from agent_service.errors import HarnessError

SESSION_SECRET = "oauth-session-token-do-not-log"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(coroutine):
    return asyncio.run(coroutine)


def set_key(key: str, value):
    def change(document: dict) -> dict:
        document[key] = value
        return document

    return change


def claude_json(home: Path, text: str | None = None) -> Path:
    """A fake ``~/.claude.json`` with a session-like field, unknown keys and 2-space indent."""
    path = home / ".claude.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        text
        if text is not None
        else json.dumps(
            {
                "oauthAccount": {"accessToken": SESSION_SECRET},
                "projects": {"/work/p": {"disabledMcpServers": []}},
                "futureKey": {"nested": [1, 2.5, None, True, "café"]},
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def write(path: Path, change=None, *, expected: bytes | None = None, **options) -> str:
    expected = path.read_bytes() if expected is None else expected
    return run(write_json_atomic(path, change or set_key("added", 1), sha(expected), **options))


# --- errors and protocol ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "code", "status"),
    [
        (ProviderStateConflictError, "provider_state_conflict", 409),
        (ProviderStateUnsupportedError, "provider_state_write_unsupported", 422),
        (ProviderStateSchemaError, "provider_state_unreadable", 422),
        (ProviderStateVersionError, "provider_state_version_untested", 422),
        (ProviderStateValidationError, "provider_state_validation_failed", 422),
        (ProviderVersionUnsupportedError, "provider_version_unsupported", 422),
        (ProviderCommandError, "provider_command_failed", 502),
    ],
)
def test_errors_are_harness_errors_with_the_contract_code(error, code, status):
    raised = error("reason")

    assert isinstance(raised, HarnessError)
    assert (raised.code, raised.status, str(raised)) == (code, status, "reason")


def test_command_and_validation_errors_carry_their_details():
    assert ProviderCommandError("failed", exit_code=3).exit_code == 3
    assert ProviderStateValidationError("bad", errors=("x",)).errors == ("x",)


def test_the_adapter_protocol_lists_the_contract_methods():
    contract = {
        "read_state", "set_enabled", "watch_paths", "is_project_trusted", "trust_project",
        "approved_project_servers", "run_environment", "credential_isolation", "login_command",
        "login_status", "set_api_key",
    }

    assert contract <= set(dir(ProviderStateAdapter))
    namespace = {name: (lambda self, *a, **k: None) for name in contract}
    assert isinstance(type("Fake", (), namespace)(), ProviderStateAdapter)
    assert not isinstance(object(), ProviderStateAdapter)


def test_secret_str_hides_its_value():
    secret = provider_state.SecretStr("sk-very-secret")

    assert secret.get_secret_value() == "sk-very-secret"
    assert "sk-very-secret" not in repr(secret) + str(secret)


# --- backup folder ---------------------------------------------------------------------------


def test_backup_folder_lives_in_the_state_folder_never_in_claude_backups(tmp_path):
    state = tmp_path / "state"

    folder = claude_json_backup_dir(state)

    assert folder == state / "backups" / "claude-json"
    assert ".claude" not in folder.parts


# --- fingerprint -----------------------------------------------------------------------------


def test_fingerprint_is_stable_and_changes_with_a_file(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("a = 1\n")
    before = fingerprint([config])

    assert fingerprint([config]) == before
    config.write_text("a = 22\n")
    assert fingerprint([config]) != before


def test_fingerprint_changes_when_only_the_mtime_moves(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("a = 1\n")
    before = fingerprint([config])

    os.utime(config, ns=(1, 1))

    assert fingerprint([config]) != before


def test_fingerprint_marks_a_missing_file_and_notices_when_it_appears(tmp_path):
    config, other = tmp_path / "config.toml", tmp_path / "other.toml"
    other.write_text("x")
    missing = fingerprint([config, other])

    config.write_text("")

    assert fingerprint([config, other]) != missing
    assert fingerprint([config]) != fingerprint([tmp_path / "another.toml"])


def test_fingerprint_of_a_directory_follows_its_children(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "one").mkdir()
    (skills / "two").mkdir()
    os.utime(skills / "one", ns=(1, 1))
    os.utime(skills / "two", ns=(2, 2))
    before = fingerprint([skills])

    (skills / "one").rmdir()  # the older child goes: the newest mtime stays the same

    assert fingerprint([skills]) != before
    os.utime(skills / "two", ns=(3, 3))
    assert fingerprint([skills]) != before


def test_fingerprint_follows_a_symlink_to_the_real_file(tmp_path):
    real = tmp_path / "dotfiles" / "settings.json"
    real.parent.mkdir()
    real.write_text("{}")
    link = tmp_path / "settings.json"
    link.symlink_to(real)
    before = fingerprint([link])

    real.write_text('{"a": 1}')

    assert fingerprint([link]) != before


# --- write_json_atomic: content --------------------------------------------------------------


def test_untouched_content_keeps_its_bytes_order_and_indent(tmp_path):
    path = claude_json(tmp_path)
    original = path.read_text(encoding="utf-8")

    write(path, set_key("added", {"on": True}))

    expected = original.removesuffix("\n}\n") + ',\n  "added": {\n    "on": true\n  }\n}\n'
    assert path.read_text(encoding="utf-8") == expected


@pytest.mark.parametrize("indent", [2, 4, "\t", None])
def test_every_unknown_key_survives_with_the_detected_indent(tmp_path, indent):
    document = {"z": 1, "a": {"deep": [1, {"k": "v"}]}, "m": "café"}
    text = json.dumps(document, indent=indent, ensure_ascii=False) + "\n"
    path = claude_json(tmp_path, text)

    write(path, set_key("z", 2))

    assert path.read_text(encoding="utf-8") == json.dumps(
        {**document, "z": 2}, indent=indent, ensure_ascii=False
    ) + "\n"


def test_a_file_without_trailing_newline_and_compact_separators_keeps_both(tmp_path):
    path = claude_json(tmp_path, '{"a":1,"b":[1,2]}')

    write(path, set_key("a", 2))

    assert path.read_text() == '{"a":2,"b":[1,2]}'


def test_returns_the_sha256_of_the_new_bytes(tmp_path):
    path = claude_json(tmp_path)

    assert write(path) == sha(path.read_bytes())


def test_a_second_write_chains_on_the_returned_hash(tmp_path):
    path = claude_json(tmp_path)

    first = write(path, set_key("one", 1))
    second = run(write_json_atomic(path, set_key("two", 2), first))

    assert json.loads(path.read_text())["one"] == 1
    assert second == sha(path.read_bytes())


# --- write_json_atomic: target handling ------------------------------------------------------


def test_a_symlinked_target_stays_a_symlink_and_the_real_file_is_replaced(tmp_path):
    real = claude_json(tmp_path / "dotfiles")
    link = tmp_path / "home" / ".claude.json"
    link.parent.mkdir()
    link.symlink_to(real)
    inode = real.stat().st_ino

    run(write_json_atomic(link, set_key("added", 1), sha(real.read_bytes())))

    assert link.is_symlink()
    assert os.readlink(link) == str(real)
    assert json.loads(real.read_text())["added"] == 1
    assert real.stat().st_ino != inode  # replaced, not rewritten in place
    assert sorted(p.name for p in link.parent.iterdir()) == [".claude.json"]
    assert sorted(p.name for p in real.parent.iterdir()) == [".claude.json"]


def test_the_temp_file_is_created_beside_the_real_target(tmp_path, monkeypatch):
    real = claude_json(tmp_path / "dotfiles")
    link = tmp_path / "home" / ".claude.json"
    link.parent.mkdir()
    link.symlink_to(real)
    seen = []
    replace = os.replace

    def recording_replace(src, dst):
        seen.append((Path(src).parent, Path(dst)))
        replace(src, dst)

    monkeypatch.setattr(os, "replace", recording_replace)

    run(write_json_atomic(link, set_key("added", 1), sha(real.read_bytes())))

    assert seen == [(real.parent, real)]


def test_mode_is_kept(tmp_path):
    path = claude_json(tmp_path)
    path.chmod(0o640)

    write(path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_owner_and_group_are_restored_where_permitted(tmp_path, monkeypatch):
    path = claude_json(tmp_path)
    calls = []
    monkeypatch.setattr(os, "fchown", lambda fd, uid, gid: calls.append((uid, gid)))
    info = path.stat()

    write(path)

    assert calls == [(info.st_uid, info.st_gid)]


def test_a_refused_chown_does_not_block_the_write(tmp_path, monkeypatch):
    path = claude_json(tmp_path)

    def refuse(fd, uid, gid):
        raise PermissionError

    monkeypatch.setattr(os, "fchown", refuse)

    write(path)

    assert json.loads(path.read_text())["added"] == 1


def test_a_missing_file_is_never_created(tmp_path):
    path = tmp_path / ".claude.json"

    with pytest.raises(ProviderStateSchemaError):
        run(write_json_atomic(path, set_key("a", 1), sha(b"")))

    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", "\xff"])
def test_a_file_that_is_not_a_json_object_is_refused_untouched(tmp_path, text):
    path = claude_json(tmp_path, text)

    with pytest.raises(ProviderStateSchemaError):
        write(path)

    assert path.read_text() == text
    assert [p.name for p in tmp_path.iterdir()] == [".claude.json"]


def test_a_change_that_returns_no_object_is_refused_untouched(tmp_path):
    path = claude_json(tmp_path)
    before = path.read_bytes()

    with pytest.raises(ProviderStateSchemaError):
        write(path, lambda document: [])

    assert path.read_bytes() == before


# --- write_json_atomic: conflicts ------------------------------------------------------------


def test_a_stale_expected_hash_writes_nothing(tmp_path):
    path = claude_json(tmp_path)
    before = path.read_bytes()

    with pytest.raises(ProviderStateConflictError):
        run(write_json_atomic(path, set_key("a", 1), sha(b"something else")))

    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == [".claude.json"]


def test_bytes_changed_between_read_and_replace_raise_conflict_and_keep_the_other_write(tmp_path):
    path = claude_json(tmp_path)
    theirs = b'{"written": "by claude code"}\n'

    def change(document: dict) -> dict:
        path.write_bytes(theirs)  # the CLI rewrites the file after KeepHarness read it
        return {**document, "added": 1}

    with pytest.raises(ProviderStateConflictError):
        write(path, change)

    assert path.read_bytes() == theirs
    assert [p.name for p in tmp_path.iterdir()] == [".claude.json"]


def test_a_file_removed_before_the_replace_is_a_conflict_and_is_not_recreated(tmp_path):
    path = claude_json(tmp_path)

    def change(document: dict) -> dict:
        path.unlink()
        return document

    with pytest.raises(ProviderStateConflictError):
        write(path, change)

    assert list(tmp_path.iterdir()) == []


def test_two_writers_with_the_same_expected_hash_cannot_both_win(tmp_path):
    path = claude_json(tmp_path)
    expected = sha(path.read_bytes())

    async def scenario():
        return await asyncio.gather(
            write_json_atomic(path, set_key("first", 1), expected),
            write_json_atomic(path, set_key("second", 2), expected),
            return_exceptions=True,
        )

    results = run(scenario())

    assert sum(isinstance(r, ProviderStateConflictError) for r in results) == 1
    document = json.loads(path.read_text())
    assert ("first" in document) != ("second" in document)


def test_the_lock_is_one_per_real_path_and_serializes_writers(tmp_path):
    real = claude_json(tmp_path / "dotfiles")
    link = tmp_path / "link.json"
    link.symlink_to(real)

    async def scenario():
        lock = provider_state._lock_for(real.resolve())
        assert lock is provider_state._lock_for(Path(os.path.realpath(link)))
        assert lock is not provider_state._lock_for(tmp_path / "other.json")
        async with lock:
            writer = asyncio.create_task(
                write_json_atomic(link, set_key("added", 1), sha(real.read_bytes()))
            )
            await asyncio.sleep(0.05)
            assert not writer.done()
            assert "added" not in json.loads(real.read_text())
        await writer
        return json.loads(real.read_text())

    assert run(scenario())["added"] == 1


def test_a_cancelled_writer_releases_the_lock(tmp_path):
    path = claude_json(tmp_path)
    expected = sha(path.read_bytes())

    async def scenario():
        lock = provider_state._lock_for(path.resolve())
        async with lock:
            writer = asyncio.create_task(write_json_atomic(path, set_key("a", 1), expected))
            await asyncio.sleep(0.01)
            writer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await writer
        await write_json_atomic(path, set_key("b", 2), expected)

    run(scenario())

    assert json.loads(path.read_text())["b"] == 2


# --- write_json_atomic: failures leave nothing behind ----------------------------------------


@pytest.mark.parametrize("failing", ["fsync", "replace", "fchmod"])
def test_no_temp_file_is_left_when_a_step_fails(tmp_path, monkeypatch, failing):
    path = claude_json(tmp_path)
    before = path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, failing, fail)

    with pytest.raises(OSError):
        write(path)

    monkeypatch.undo()
    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == [".claude.json"]


def test_a_failing_change_leaves_the_file_and_the_folder_untouched(tmp_path):
    path = claude_json(tmp_path)
    before = path.read_bytes()

    def change(document: dict) -> dict:
        raise RuntimeError("bad change")

    with pytest.raises(RuntimeError):
        write(path, change)

    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == [".claude.json"]


# --- write_json_atomic: backup, validation and restore ---------------------------------------


def test_without_a_backup_folder_nothing_is_copied(tmp_path):
    path = claude_json(tmp_path / "home")

    write(path)

    assert [p.name for p in (tmp_path / "home").iterdir()] == [".claude.json"]


def test_the_pre_write_bytes_are_backed_up_owner_only(tmp_path):
    path = claude_json(tmp_path / "home")
    before = path.read_bytes()
    folder = claude_json_backup_dir(tmp_path / "state")

    write(path, backup_dir=folder)

    (backup,) = folder.iterdir()
    assert backup.read_bytes() == before
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700


def test_an_existing_loose_backup_folder_is_tightened_to_owner_only(tmp_path):
    path = claude_json(tmp_path / "home")
    folder = claude_json_backup_dir(tmp_path / "state")
    folder.mkdir(parents=True, mode=0o755)
    folder.chmod(0o755)

    write(path, backup_dir=folder)

    assert stat.S_IMODE(folder.stat().st_mode) == 0o700


def test_only_the_newest_three_backups_are_kept(tmp_path):
    path = claude_json(tmp_path / "home")
    folder = claude_json_backup_dir(tmp_path / "state")
    taken = []

    for index in range(5):
        taken.append(path.read_bytes())
        write(path, set_key("round", index), backup_dir=folder)

    kept = sorted(folder.iterdir())
    assert [p.read_bytes() for p in kept] == taken[-3:]
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in kept)


def test_rotation_leaves_files_it_did_not_write_alone(tmp_path):
    path = claude_json(tmp_path / "home")
    folder = claude_json_backup_dir(tmp_path / "state")
    folder.mkdir(parents=True)
    (folder / "notes.txt").write_text("mine")

    for index in range(5):
        write(path, set_key("round", index), backup_dir=folder)

    assert (folder / "notes.txt").read_text() == "mine"
    assert len(list(folder.glob("[0-9]*.json"))) == 3


def test_when_the_backup_fails_nothing_is_written(tmp_path):
    path = claude_json(tmp_path / "home")
    before = path.read_bytes()
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the folder should be")

    with pytest.raises(OSError):
        write(path, backup_dir=blocked / "claude-json")

    assert path.read_bytes() == before
    assert [p.name for p in path.parent.iterdir()] == [".claude.json"]


def broken_when_marked(data: bytes) -> list[str]:
    return ["marked"] if b'"broken"' in data else []


def test_a_new_validation_error_restores_the_previous_bytes(tmp_path):
    path = claude_json(tmp_path / "home")
    before = path.read_bytes()
    folder = claude_json_backup_dir(tmp_path / "state")

    with pytest.raises(ProviderStateValidationError) as caught:
        write(path, set_key("broken", True), backup_dir=folder, validate=broken_when_marked)

    assert caught.value.errors == ("marked",)
    assert path.read_bytes() == before
    assert [p.name for p in path.parent.iterdir()] == [".claude.json"]
    assert len(list(folder.iterdir())) == 1  # the backup stays as evidence


def test_the_previous_bytes_are_restored_even_without_a_backup_folder(tmp_path):
    path = claude_json(tmp_path)
    before = path.read_bytes()

    with pytest.raises(ProviderStateValidationError):
        write(path, set_key("broken", True), validate=broken_when_marked)

    assert path.read_bytes() == before


def test_an_error_the_file_already_had_does_not_block_the_write(tmp_path):
    path = claude_json(tmp_path, '{"broken": 1}\n')

    write(path, set_key("other", 2), validate=broken_when_marked)

    assert json.loads(path.read_text())["other"] == 2


def test_validation_that_passes_keeps_the_new_bytes(tmp_path):
    path = claude_json(tmp_path)

    write(path, validate=lambda data: [])

    assert json.loads(path.read_text())["added"] == 1


def test_validation_sees_the_new_bytes_and_the_old_bytes(tmp_path):
    path = claude_json(tmp_path)
    before = path.read_bytes()
    seen = []

    write(path, validate=lambda data: seen.append(data) or [])

    assert seen == [before, path.read_bytes()]


def test_no_restore_when_someone_else_wrote_after_us(tmp_path):
    path = claude_json(tmp_path)
    theirs = b'{"claude code": "rewrote the file"}\n'

    def validate(data: bytes) -> list[str]:
        if b'"broken"' not in data:
            return []
        path.write_bytes(theirs)  # the CLI writes between our replace and our validation
        return ["marked"]

    with pytest.raises(ProviderStateConflictError):
        write(path, set_key("broken", True), validate=validate)

    assert path.read_bytes() == theirs
    assert [p.name for p in tmp_path.iterdir()] == [".claude.json"]


# --- credential safety -----------------------------------------------------------------------


def test_logs_and_errors_never_carry_file_content(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    path = claude_json(tmp_path)
    folder = claude_json_backup_dir(tmp_path / "state")
    messages = []

    write(path, set_key("ok", 1), backup_dir=folder, validate=lambda data: [])
    for attempt in (
        lambda: run(write_json_atomic(path, set_key("a", 1), sha(b"stale"))),
        lambda: write(path, set_key("broken", True), validate=broken_when_marked),
        lambda: write(claude_json(tmp_path / "bad", "{" + SESSION_SECRET), set_key("a", 1)),
    ):
        with pytest.raises(HarnessError) as caught:
            attempt()
        messages.append(str(caught.value) + repr(caught.value.args))

    assert SESSION_SECRET not in caplog.text
    assert not any(SESSION_SECRET in message for message in messages)
