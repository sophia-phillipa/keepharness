"""Contract tests for GET/PUT /v1/project-files/content, the project file editor backend.

Written before the implementation (D-055 P4a). Every test is expected to fail until the route and
the ``agent_service.workspaces`` editor helpers exist; none of them may fail on a fixture error.
"""

import errno
import hashlib
import os
import stat
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient
from test_workspaces import config, single_owner_config

from agent_service import workspaces
from agent_service.app import create_app
from agent_service.approval_sessions import SESSION_COOKIE, consume_enrollment, issue_enrollment

ROUTE = "/v1/project-files/content"
MIB = 1024 * 1024
CREDENTIAL_FILES = [".git-credentials", ".npmrc", "id_ed25519"]
SECRET = b"fixture-only credential\n"


def sha_etag(data: bytes) -> str:
    """The ETag contract: SHA-256 of the bytes, hex, in quotes."""
    return f'"{hashlib.sha256(data).hexdigest()}"'


def query(path="notes.md", root_id="root"):
    return {"project_id": "p", "root_id": root_id, "path": path}


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["code"] == code


def signed_in(cfg):
    """Cookie headers for an approval session enrolled for the local owner, as Settings does."""
    token = consume_enrollment(cfg, issue_enrollment(cfg, "local"))
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


def get(client, path="notes.md", *, cookie=None, root_id="root"):
    return client.get(ROUTE, params=query(path, root_id), headers=cookie or {})


def put(client, text, *, etag=None, cookie=None, path="notes.md"):
    headers = dict(cookie or {})
    if etag is not None:
        headers["If-Match"] = etag
    return client.put(ROUTE, params=query(path), json={"text": text}, headers=headers)


@pytest.fixture
def root(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    return project


@pytest.fixture
def outside(tmp_path):
    folder = tmp_path / "outside"
    folder.mkdir()
    return folder


@pytest.fixture
def owner_cfg(tmp_path, root):
    cfg = single_owner_config(tmp_path)
    cfg["projects"]["p"]["root"] = str(root)
    return cfg


@pytest.fixture
def owner(owner_cfg):
    with TestClient(create_app(owner_cfg), headers={"Authorization": "Bearer a"}) as client:
        yield client


@pytest.fixture
def guest(tmp_path, root):
    cfg = config(tmp_path)
    cfg["projects"]["p"]["root"] = str(root)
    with TestClient(create_app(cfg), headers={"Authorization": "Bearer b"}) as client:
        yield client


@pytest.fixture
def links(root, outside):
    (outside / "notes.md").write_bytes(b"outside\n")
    (root / "link.md").symlink_to(outside / "notes.md")
    (root / "linked").symlink_to(outside, target_is_directory=True)


# Read: GET


def test_get_returns_text_etag_size_and_eol_for_an_lf_file(owner, owner_cfg, root):
    data = b"alpha\nbeta\n"
    (root / "notes.md").write_bytes(data)
    response = get(owner, cookie=signed_in(owner_cfg))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"] == "alpha\nbeta\n"
    assert body["etag"] == sha_etag(data)
    assert response.headers["ETag"] == sha_etag(data)
    assert body["size"] == len(data)
    assert body["eol"] == "lf"
    assert body["writable"] is True


@pytest.mark.parametrize(
    ("data", "text", "eol"),
    [
        (b"a\nb\n", "a\nb\n", "lf"),
        (b"a\r\nb\r\n", "a\nb\n", "crlf"),
        (b"a\r\nb\n", "a\nb\n", "mixed"),
    ],
    ids=["lf", "crlf", "mixed"],
)
def test_get_reports_eol_and_returns_lf_text(owner, owner_cfg, root, data, text, eol):
    (root / "notes.md").write_bytes(data)
    response = get(owner, cookie=signed_in(owner_cfg))
    assert response.status_code == 200, response.text
    assert response.json()["eol"] == eol
    assert response.json()["text"] == text


def test_get_without_approval_session_is_read_only(owner, root):
    (root / "notes.md").write_bytes(b"alpha\n")
    response = get(owner)
    assert response.status_code == 200, response.text
    assert response.json()["writable"] is False


def test_mixed_eol_is_readable_but_not_writable(owner, owner_cfg, root):
    (root / "notes.md").write_bytes(b"a\r\nb\n")
    response = get(owner, cookie=signed_in(owner_cfg))
    assert response.status_code == 200, response.text
    assert response.json()["eol"] == "mixed"
    assert response.json()["writable"] is False


def test_get_accepts_a_file_of_exactly_one_mib(owner, owner_cfg, root):
    (root / "notes.md").write_bytes(b"a" * MIB)
    response = get(owner, cookie=signed_in(owner_cfg))
    assert response.status_code == 200, response.text
    assert response.json()["size"] == MIB


def test_get_refuses_a_file_above_one_mib(owner, owner_cfg, root):
    (root / "notes.md").write_bytes(b"a" * (MIB + 1))
    response = get(owner, cookie=signed_in(owner_cfg))
    assert_error(response, 413, "file_too_large")


@pytest.mark.parametrize(
    "data", [b"\x89PNG\r\n\x1a\n\xff\xfe\x00\x00", b"caf\xe9\n"], ids=["binary", "latin-1"]
)
def test_get_refuses_non_text(owner, owner_cfg, root, data):
    (root / "notes.md").write_bytes(data)
    response = get(owner, cookie=signed_in(owner_cfg))
    assert_error(response, 415, "file_not_text")


def test_get_missing_file_is_path_not_authorized(owner, owner_cfg):
    response = get(owner, "missing.md", cookie=signed_in(owner_cfg))
    assert_error(response, 403, "path_not_authorized")


def test_get_unknown_root_is_refused(owner, owner_cfg, root):
    (root / "notes.md").write_bytes(b"v1\n")
    response = get(owner, root_id="other", cookie=signed_in(owner_cfg))
    assert_error(response, 403, "project_root_denied")


# Local owner gate and guest refusal


@pytest.mark.parametrize("path", ["notes.md", "missing.md"])
def test_guest_get_is_refused_before_any_read(guest, root, path):
    (root / "notes.md").write_bytes(b"private draft\n")
    response = get(guest, path)
    assert_error(response, 403, "editor_local_only")
    assert b"private draft" not in response.content


@pytest.mark.parametrize("path", ["notes.md", "missing.md"])
def test_guest_put_is_refused_before_precondition_and_write(guest, root, path):
    (root / "notes.md").write_bytes(b"private draft\n")
    response = put(guest, "guest edit\n", path=path)
    assert_error(response, 403, "editor_local_only")
    assert (root / "notes.md").read_bytes() == b"private draft\n"
    assert not (root / "missing.md").exists()


# Path guard, symlinks and credential names


@pytest.mark.parametrize("path", ["../outside/notes.md", "/etc/passwd", "notes\x00.md"])
def test_get_refuses_paths_outside_the_root(owner, owner_cfg, outside, path):
    (outside / "notes.md").write_bytes(b"outside\n")
    response = get(owner, path, cookie=signed_in(owner_cfg))
    assert_error(response, 403, "path_not_authorized")


@pytest.mark.parametrize("path", ["../outside/notes.md", "/etc/passwd", "notes\x00.md"])
def test_put_refuses_paths_outside_the_root(owner, owner_cfg, outside, path):
    (outside / "notes.md").write_bytes(b"outside\n")
    response = put(
        owner, "pwned\n", etag=sha_etag(b"outside\n"), cookie=signed_in(owner_cfg), path=path
    )
    assert_error(response, 403, "path_not_authorized")
    assert (outside / "notes.md").read_bytes() == b"outside\n"


@pytest.mark.parametrize("path", ["link.md", "linked/notes.md"])
def test_get_refuses_symlinked_file_and_parent(owner, owner_cfg, links, path):
    response = get(owner, path, cookie=signed_in(owner_cfg))
    assert_error(response, 403, "symlink_denied")


@pytest.mark.parametrize("path", ["link.md", "linked/notes.md"])
def test_put_refuses_symlinked_file_and_parent(owner, owner_cfg, links, outside, path):
    response = put(
        owner, "pwned\n", etag=sha_etag(b"outside\n"), cookie=signed_in(owner_cfg), path=path
    )
    assert_error(response, 403, "symlink_denied")
    assert (outside / "notes.md").read_bytes() == b"outside\n"


@pytest.mark.parametrize("name", CREDENTIAL_FILES)
def test_get_refuses_credential_files(owner, owner_cfg, root, name):
    (root / name).write_bytes(SECRET)
    response = get(owner, name, cookie=signed_in(owner_cfg))
    assert_error(response, 403, "path_not_authorized")
    assert b"fixture-only" not in response.content


@pytest.mark.parametrize("name", CREDENTIAL_FILES)
def test_put_refuses_credential_files(owner, owner_cfg, root, name):
    (root / name).write_bytes(SECRET)
    response = put(
        owner, "overwritten\n", etag=sha_etag(SECRET), cookie=signed_in(owner_cfg), path=name
    )
    assert_error(response, 403, "path_not_authorized")
    assert (root / name).read_bytes() == SECRET


def test_put_never_creates_a_missing_file(owner, owner_cfg, root):
    response = put(
        owner, "new\n", etag=sha_etag(b""), cookie=signed_in(owner_cfg), path="missing.md"
    )
    assert_error(response, 403, "path_not_authorized")
    assert not (root / "missing.md").exists()


# Non-text, size and encoding


@pytest.mark.parametrize(
    "data", [b"\x89PNG\r\n\x1a\n\xff\xfe\x00\x00", b"caf\xe9\n"], ids=["binary", "latin-1"]
)
def test_put_refuses_non_text_and_leaves_file_unchanged(owner, owner_cfg, root, data):
    (root / "notes.md").write_bytes(data)
    response = put(owner, "text\n", etag=sha_etag(data), cookie=signed_in(owner_cfg))
    assert_error(response, 415, "file_not_text")
    assert (root / "notes.md").read_bytes() == data


def test_put_refuses_text_above_one_mib_and_leaves_file_unchanged(owner, owner_cfg, root):
    original = b"small\n"
    (root / "notes.md").write_bytes(original)
    response = put(owner, "a" * (MIB + 1), etag=sha_etag(original), cookie=signed_in(owner_cfg))
    assert_error(response, 413, "file_too_large")
    assert (root / "notes.md").read_bytes() == original


@pytest.mark.parametrize("value", [123, None, ["a"]], ids=["int", "null", "list"])
def test_put_rejects_non_string_text(owner, owner_cfg, root, value):
    original = b"v1\n"
    (root / "notes.md").write_bytes(original)
    response = put(owner, value, etag=sha_etag(original), cookie=signed_in(owner_cfg))
    assert_error(response, 422, "invalid_text")
    assert (root / "notes.md").read_bytes() == original


# Write: PUT preconditions, approval and ETag


def test_put_writes_text_and_returns_new_etag_and_size(owner, owner_cfg, root):
    (root / "notes.md").write_bytes(b"old\n")
    response = put(owner, "new text\n", etag=sha_etag(b"old\n"), cookie=signed_in(owner_cfg))
    assert response.status_code == 200, response.text
    assert response.json() == {"etag": sha_etag(b"new text\n"), "size": 9}
    assert (root / "notes.md").read_bytes() == b"new text\n"


def test_put_without_if_match_is_428_and_writes_nothing(owner, owner_cfg, root):
    original = b"v1\n"
    (root / "notes.md").write_bytes(original)
    response = put(owner, "v2\n", cookie=signed_in(owner_cfg))
    assert_error(response, 428, "precondition_required")
    assert (root / "notes.md").read_bytes() == original


def test_put_with_stale_etag_is_412_and_reports_current_etag(owner, owner_cfg, root):
    (root / "notes.md").write_bytes(b"v1\n")
    changed = b"v2 from another writer\n"
    (root / "notes.md").write_bytes(changed)
    response = put(owner, "mine\n", etag=sha_etag(b"v1\n"), cookie=signed_in(owner_cfg))
    assert_error(response, 412, "file_changed")
    assert response.headers["ETag"] == sha_etag(changed)
    assert (root / "notes.md").read_bytes() == changed


def test_second_writer_with_the_same_etag_is_412(owner, owner_cfg, root):
    original = b"v1\n"
    (root / "notes.md").write_bytes(original)
    cookie = signed_in(owner_cfg)
    first = put(owner, "first\n", etag=sha_etag(original), cookie=cookie)
    assert first.status_code == 200, first.text
    second = put(owner, "second\n", etag=sha_etag(original), cookie=cookie)
    assert_error(second, 412, "file_changed")
    assert second.headers["ETag"] == sha_etag(b"first\n")
    assert (root / "notes.md").read_bytes() == b"first\n"


def test_put_without_approval_session_is_403_and_writes_nothing(owner, root):
    original = b"v1\n"
    (root / "notes.md").write_bytes(original)
    response = put(owner, "v2\n", etag=sha_etag(original))
    assert_error(response, 403, "approval_session_required")
    assert (root / "notes.md").read_bytes() == original


def test_put_on_mixed_eol_is_refused_as_not_writable(owner, owner_cfg, root):
    original = b"a\r\nb\n"
    (root / "notes.md").write_bytes(original)
    response = put(owner, "a\nc\n", etag=sha_etag(original), cookie=signed_in(owner_cfg))
    assert_error(response, 403, "file_not_writable")
    assert (root / "notes.md").read_bytes() == original


# Line endings on save


def test_crlf_file_saved_unchanged_leaves_bytes_identical(owner, owner_cfg, root):
    original = b"a\r\nb\r\n"
    (root / "notes.md").write_bytes(original)
    cookie = signed_in(owner_cfg)
    read = get(owner, cookie=cookie)
    assert read.status_code == 200, read.text
    response = put(owner, read.json()["text"], etag=read.json()["etag"], cookie=cookie)
    assert response.status_code == 200, response.text
    assert response.json()["etag"] == sha_etag(original)
    assert (root / "notes.md").read_bytes() == original


def test_crlf_file_keeps_crlf_when_saved_with_lf_text(owner, owner_cfg, root):
    original = b"a\r\nb\r\n"
    (root / "notes.md").write_bytes(original)
    response = put(owner, "a\nB\n", etag=sha_etag(original), cookie=signed_in(owner_cfg))
    assert response.status_code == 200, response.text
    assert (root / "notes.md").read_bytes() == b"a\r\nB\r\n"


# Atomic write: mode, temp files and the parent-swap race


def test_put_keeps_file_mode(owner, owner_cfg, root):
    original = b"v1\n"
    (root / "notes.md").write_bytes(original)
    os.chmod(root / "notes.md", 0o640)
    response = put(owner, "v2\n", etag=sha_etag(original), cookie=signed_in(owner_cfg))
    assert response.status_code == 200, response.text
    assert stat.S_IMODE(os.stat(root / "notes.md").st_mode) == 0o640


def test_put_leaves_no_temp_file_behind(owner, owner_cfg, root):
    original = b"v1\n"
    (root / "notes.md").write_bytes(original)
    before = sorted(os.listdir(root))
    response = put(owner, "v2\n", etag=sha_etag(original), cookie=signed_in(owner_cfg))
    assert response.status_code == 200, response.text
    assert sorted(os.listdir(root)) == before


def test_symlinked_parent_swapped_before_write_is_refused(owner, owner_cfg, root, outside):
    (outside / "notes.md").write_bytes(b"outside original\n")
    (root / "sub").mkdir()
    (root / "sub" / "notes.md").write_bytes(b"inside original\n")
    real_open_parent_dir = workspaces.open_parent_dir
    swaps = []

    def swap_then_open(*args, **kwargs):
        # Another process replaces the folder with a symlink after the path check ran.
        (root / "sub").rename(root / "sub-moved")
        (root / "sub").symlink_to(outside, target_is_directory=True)
        swaps.append(True)
        return real_open_parent_dir(*args, **kwargs)

    cookie = signed_in(owner_cfg)
    with patch.object(workspaces, "open_parent_dir", side_effect=swap_then_open):
        response = put(
            owner,
            "changed\n",
            etag=sha_etag(b"inside original\n"),
            cookie=cookie,
            path="sub/notes.md",
        )
    assert response.status_code == 403, response.text
    assert swaps == [True]
    assert response.json()["code"] in {"symlink_denied", "path_not_authorized"}
    assert (outside / "notes.md").read_bytes() == b"outside original\n"
    assert (root / "sub-moved" / "notes.md").read_bytes() == b"inside original\n"


# Review hardening: identity re-check, hardlinks, chown and fsync, OS errors


def test_identity_revalidated_as_non_local_is_refused_before_any_read(owner, root):
    original = b"v1\n"
    (root / "notes.md").write_bytes(original)
    with patch("agent_service.routes.files.require_current_read", return_value=("guest", "g")):
        assert_error(get(owner), 403, "editor_local_only")
        assert_error(put(owner, "v2\n", etag=sha_etag(original)), 403, "editor_local_only")
    assert (root / "notes.md").read_bytes() == original


def test_hardlinked_file_is_not_writable_and_stays_unchanged(owner, owner_cfg, root):
    original = b"v1\n"
    (root / "notes.md").write_bytes(original)
    os.link(root / "notes.md", root / "alias.md")
    response = put(owner, "v2\n", etag=sha_etag(original), cookie=signed_in(owner_cfg))
    assert_error(response, 403, "file_not_writable")
    assert (root / "notes.md").read_bytes() == original
    assert (root / "alias.md").read_bytes() == original


def test_refused_chown_still_saves_and_keeps_mode(owner, owner_cfg, root):
    original = b"v1\n"
    (root / "notes.md").write_bytes(original)
    os.chmod(root / "notes.md", 0o640)
    with patch.object(workspaces.os, "fchown", side_effect=PermissionError(1, "denied")):
        response = put(owner, "v2\n", etag=sha_etag(original), cookie=signed_in(owner_cfg))
    assert response.status_code == 200, response.text
    assert (root / "notes.md").read_bytes() == b"v2\n"
    assert stat.S_IMODE(os.stat(root / "notes.md").st_mode) == 0o640


def test_folder_fsync_failure_after_rename_keeps_the_save(owner, owner_cfg, root):
    original = b"v1\n"
    (root / "notes.md").write_bytes(original)
    real_fsync = os.fsync
    calls = []

    def fsync_folder_fails(fd):
        calls.append(fd)
        if len(calls) == 2:  # the temp file is synced first, the folder after the rename
            raise OSError(errno.EIO, "fsync failed")
        real_fsync(fd)

    with patch.object(workspaces.os, "fsync", side_effect=fsync_folder_fails):
        response = put(owner, "v2\n", etag=sha_etag(original), cookie=signed_in(owner_cfg))
    assert response.status_code == 200, response.text
    assert (root / "notes.md").read_bytes() == b"v2\n"


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (PermissionError(errno.EACCES, "denied"), 403, "file_not_writable"),
        (OSError(errno.EPERM, "not permitted"), 403, "file_not_writable"),
        (OSError(errno.EROFS, "read-only"), 403, "file_not_writable"),
        (OSError(errno.ENOSPC, "no space"), 507, "storage_full"),
        (OSError(errno.EIO, "io error"), 500, "file_unavailable"),
    ],
)
def test_os_errors_during_save_map_to_api_codes_without_the_path(
    owner, owner_cfg, root, caplog, error, status, code
):
    original = b"v1\n"
    (root / "notes.md").write_bytes(original)
    with patch.object(workspaces, "replace_project_file", side_effect=error):
        response = put(owner, "v2\n", etag=sha_etag(original), cookie=signed_in(owner_cfg))
    assert_error(response, status, code)
    assert str(root) not in response.text
    assert (root / "notes.md").read_bytes() == original
    assert f"editor io errno={error.errno}" in caplog.text
    assert str(root) not in caplog.text
