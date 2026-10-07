"""Provider state seam: errors, fingerprint and the atomic JSON writer (decisions D-038, D-039).

Fake homes only: every file lives under ``tmp_path``; nothing reads the owner's real
``~/.claude.json``, ``~/.claude`` or ``~/.codex``.
"""

import hashlib
import json
import logging
import os
import stat
import threading
from pathlib import Path

import pytest

from adapters.shared import provider_state
from adapters.shared.provider_state import (
    MISSING_FILE,
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
    return write_json_atomic(path, change or set_key("added", 1), sha(expected), **options)


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


def test_fingerprint_of_a_directory_notices_its_own_mtime(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "one").mkdir()
    os.utime(skills, ns=(1, 1))
    before = fingerprint([skills])

    os.utime(skills, ns=(2, 2))  # the directory itself changed, its children did not

    assert fingerprint([skills]) != before


def test_fingerprint_of_a_directory_follows_a_symlinked_child(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    target = tmp_path / "elsewhere"
    target.write_text("x")
    (skills / "linked").symlink_to(target)
    os.utime(skills, ns=(1, 1))
    os.utime(target, ns=(1, 1))
    before = fingerprint([skills])

    os.utime(target, ns=(5, 5))

    assert fingerprint([skills]) != before


def test_fingerprint_survives_a_symlink_loop(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "loop").symlink_to(skills / "loop")
    (skills / "real").write_text("x")
    looped = tmp_path / "self"
    looped.symlink_to(looped)

    before = fingerprint([skills, looped])
    (skills / "real").write_text("changed")
    os.utime(skills / "real", ns=(9, 9))

    assert fingerprint([skills, looped]) != before


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
    second = write_json_atomic(path, set_key("two", 2), first)

    assert json.loads(path.read_text())["one"] == 1
    assert second == sha(path.read_bytes())


# --- write_json_atomic: target handling ------------------------------------------------------


def test_a_symlinked_target_stays_a_symlink_and_the_real_file_is_replaced(tmp_path):
    real = claude_json(tmp_path / "dotfiles")
    link = tmp_path / "home" / ".claude.json"
    link.parent.mkdir()
    link.symlink_to(real)
    inode = real.stat().st_ino

    write_json_atomic(link, set_key("added", 1), sha(real.read_bytes()))

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

    write_json_atomic(link, set_key("added", 1), sha(real.read_bytes()))

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


def test_a_missing_file_is_a_conflict_and_is_never_created(tmp_path):
    path = tmp_path / ".claude.json"

    with pytest.raises(ProviderStateConflictError):
        write_json_atomic(path, set_key("a", 1), sha(b""))

    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("raw", [b"{not json", b"[1, 2]", b"\xff"])
def test_a_file_that_is_not_a_json_object_is_refused_untouched(tmp_path, raw):
    path = claude_json(tmp_path)
    path.write_bytes(raw)  # b"\xff" is not UTF-8 at all

    with pytest.raises(ProviderStateSchemaError):
        write(path)

    assert path.read_bytes() == raw
    assert [p.name for p in tmp_path.iterdir()] == [".claude.json"]


def test_a_change_that_returns_no_object_is_refused_untouched(tmp_path):
    path = claude_json(tmp_path)
    before = path.read_bytes()

    with pytest.raises(ProviderStateSchemaError):
        write(path, lambda document: [])

    assert path.read_bytes() == before


# --- write_json_atomic: create mode ----------------------------------------------------------


def test_create_mode_makes_the_file_owner_only_with_the_cli_style_layout(tmp_path):
    path = tmp_path / "settings.local.json"

    digest = write_json_atomic(path, set_key("added", 1), MISSING_FILE)

    assert path.read_text() == '{\n  "added": 1\n}\n'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert digest == sha(path.read_bytes())
    assert [p.name for p in tmp_path.iterdir()] == ["settings.local.json"]


def test_create_mode_refuses_a_file_that_already_exists(tmp_path):
    path = tmp_path / "settings.local.json"
    path.write_text('{"mine": true}')

    with pytest.raises(ProviderStateConflictError):
        write_json_atomic(path, set_key("added", 1), MISSING_FILE)

    assert path.read_text() == '{"mine": true}'
    assert [p.name for p in tmp_path.iterdir()] == ["settings.local.json"]


def test_a_file_that_appears_before_the_placement_is_a_conflict_and_is_kept(tmp_path, monkeypatch):
    path = tmp_path / "settings.local.json"
    real_link = os.link

    def appear_then_link(source, target, *args, **kwargs):
        Path(target).write_text('{"theirs": 1}')
        return real_link(source, target, *args, **kwargs)

    monkeypatch.setattr(os, "link", appear_then_link)

    with pytest.raises(ProviderStateConflictError):
        write_json_atomic(path, set_key("added", 1), MISSING_FILE)

    monkeypatch.undo()
    assert path.read_text() == '{"theirs": 1}'
    assert [p.name for p in tmp_path.iterdir()] == ["settings.local.json"]


def test_create_mode_never_creates_the_parent_folder(tmp_path):
    path = tmp_path / ".claude" / "settings.local.json"

    with pytest.raises(ProviderStateUnsupportedError):
        write_json_atomic(path, set_key("added", 1), MISSING_FILE)

    assert list(tmp_path.iterdir()) == []


def test_create_mode_still_validates_and_writes_nothing_when_refused(tmp_path):
    path = tmp_path / "settings.local.json"

    with pytest.raises(ProviderStateValidationError):
        write_json_atomic(path, set_key("broken", True), MISSING_FILE, validate=broken_when_marked)

    assert list(tmp_path.iterdir()) == []


def test_create_mode_takes_no_backup_and_a_failing_step_leaves_nothing(tmp_path, monkeypatch):
    path = tmp_path / "settings.local.json"
    folder = tmp_path / "backups"
    write_json_atomic(path, set_key("a", 1), MISSING_FILE, backup_dir=folder)
    assert not folder.exists()
    path.unlink()

    def fail(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError):
        write_json_atomic(path, set_key("a", 1), MISSING_FILE)
    monkeypatch.undo()

    assert list(tmp_path.iterdir()) == []


def test_create_mode_through_a_dangling_symlink_keeps_the_link(tmp_path):
    real = tmp_path / "real.json"
    link = tmp_path / "link.json"
    link.symlink_to(real)

    write_json_atomic(link, set_key("a", 1), MISSING_FILE)

    assert link.is_symlink() and json.loads(real.read_text()) == {"a": 1}


# --- write_json_atomic: conflicts ------------------------------------------------------------


def test_a_stale_expected_hash_writes_nothing(tmp_path):
    path = claude_json(tmp_path)
    before = path.read_bytes()

    with pytest.raises(ProviderStateConflictError):
        write_json_atomic(path, set_key("a", 1), sha(b"something else"))

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


def race(first_path: Path, second_path: Path, expected: str) -> tuple[dict, dict]:
    """Start the first writer, hold it inside its change, then start the second; both outcomes."""
    entered, release = threading.Event(), threading.Event()
    outcomes: dict[str, object] = {}

    def holding(document: dict) -> dict:
        entered.set()
        assert release.wait(5)
        return {**document, "first": 1}

    def attempt(name: str, path: Path, change) -> None:
        try:
            outcomes[name] = write_json_atomic(path, change, expected)
        except Exception as error:  # noqa: BLE001 - the test reads the outcome
            outcomes[name] = error

    first = threading.Thread(target=attempt, args=("first", first_path, holding))
    second = threading.Thread(target=attempt, args=("second", second_path, set_key("second", 2)))
    first.start()
    assert entered.wait(5)
    second.start()
    second.join(0.2)
    blocked = second.is_alive()  # without the lock it would already be done
    release.set()
    first.join(5)
    second.join(5)
    assert blocked, "the second writer ran while the first held the file"
    return outcomes["first"], outcomes["second"]


def test_two_writers_with_the_same_expected_hash_cannot_both_win(tmp_path):
    path = claude_json(tmp_path)
    expected = sha(path.read_bytes())

    first, second = race(path, path, expected)

    assert first == sha(path.read_bytes())
    assert isinstance(second, ProviderStateConflictError)
    document = json.loads(path.read_text())
    assert "first" in document and "second" not in document


def test_two_threads_through_two_symlinks_serialize_and_the_stale_one_conflicts(tmp_path):
    real = claude_json(tmp_path / "dotfiles")
    one, two = tmp_path / "one.json", tmp_path / "two.json"
    one.symlink_to(real)
    two.symlink_to(real)

    first, second = race(one, two, sha(real.read_bytes()))

    assert first == sha(real.read_bytes())
    assert isinstance(second, ProviderStateConflictError)
    assert "second" not in json.loads(real.read_text())
    assert [p.name for p in real.parent.iterdir()] == [".claude.json"]


def test_the_lock_is_one_per_real_path(tmp_path):
    real = claude_json(tmp_path / "dotfiles")
    link = tmp_path / "link.json"
    link.symlink_to(real)

    lock = provider_state._lock_for(real.resolve())

    assert lock is provider_state._lock_for(Path(os.path.realpath(link)))
    assert lock is not provider_state._lock_for(tmp_path / "other.json")


def test_a_failing_change_releases_the_lock(tmp_path):
    path = claude_json(tmp_path)
    expected = sha(path.read_bytes())

    def boom(document: dict) -> dict:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        write_json_atomic(path, boom, expected)

    write_json_atomic(path, set_key("b", 2), expected)
    assert json.loads(path.read_text())["b"] == 2


def test_a_lone_surrogate_is_written_back_as_an_escape(tmp_path):
    path = claude_json(tmp_path, '{\n  "label": "\\ud83d broken",\n  "ok": "caf\u00e9"\n}\n')

    write(path)

    raw = path.read_bytes()
    assert b"\\ud83d broken" in raw
    assert "café".encode() in raw
    assert json.loads(raw)["label"] == "\ud83d broken"


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


# --- write_json_atomic: backup and validation -----------------------------------------------


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


def test_a_clock_that_went_backwards_never_deletes_the_copy_just_taken(tmp_path):
    path = claude_json(tmp_path / "home")
    folder = claude_json_backup_dir(tmp_path / "state")
    folder.mkdir(parents=True)
    future = [folder / f"{99999999999999999990 + n:020d}.json" for n in range(3)]
    for old in future:
        old.write_bytes(b"{}")
    before = path.read_bytes()

    write(path, backup_dir=folder)

    kept = sorted(folder.iterdir())
    assert len(kept) == 3
    assert kept[-1].read_bytes() == before  # the new copy is the newest by name
    assert future[0] not in kept  # the oldest of the future-dated copies went instead


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


def test_a_new_validation_error_refuses_the_write_and_changes_nothing(tmp_path):
    path = claude_json(tmp_path / "home")
    before = path.read_bytes()
    folder = claude_json_backup_dir(tmp_path / "state")

    with pytest.raises(ProviderStateValidationError) as caught:
        write(path, set_key("broken", True), backup_dir=folder, validate=broken_when_marked)

    assert caught.value.errors == ("marked",)
    assert path.read_bytes() == before
    assert [p.name for p in path.parent.iterdir()] == [".claude.json"]  # no temp left
    assert not folder.exists()  # nothing was written, so nothing was copied


def test_validation_runs_before_the_replace(tmp_path, monkeypatch):
    path = claude_json(tmp_path)
    replaced = []
    monkeypatch.setattr(os, "replace", lambda *args: replaced.append(args))

    with pytest.raises(ProviderStateValidationError):
        write(path, set_key("broken", True), validate=broken_when_marked)

    assert replaced == []


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


# --- credential safety -----------------------------------------------------------------------


def test_logs_and_errors_never_carry_file_content(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    path = claude_json(tmp_path)
    folder = claude_json_backup_dir(tmp_path / "state")
    messages = []

    write(path, set_key("ok", 1), backup_dir=folder, validate=lambda data: [])
    for attempt in (
        lambda: write_json_atomic(path, set_key("a", 1), sha(b"stale")),
        lambda: write(path, set_key("broken", True), validate=broken_when_marked),
        lambda: write(claude_json(tmp_path / "bad", "{" + SESSION_SECRET), set_key("a", 1)),
    ):
        with pytest.raises(HarnessError) as caught:
            attempt()
        messages.append(str(caught.value) + repr(caught.value.args))

    assert SESSION_SECRET not in caplog.text
    assert not any(SESSION_SECRET in message for message in messages)
