"""A failed settings or runtime write (disk full) leaves no temporary file (P5-27, F-10)."""

import errno
from pathlib import Path
from unittest.mock import patch

import pytest

from control.manager import Manager

REAL_WRITE_TEXT = Path.write_text


def disk_full(path, *args, **kwargs):
    if path.suffix == ".tmp":
        REAL_WRITE_TEXT(path, "{partial")
        raise OSError(errno.ENOSPC, "No space left on device")
    return REAL_WRITE_TEXT(path, *args, **kwargs)


@pytest.mark.parametrize(
    ("name", "write"),
    [
        ("settings", lambda manager: manager.save(manager.settings)),
        ("runtime", lambda manager: manager._write_runtime({"port": 8095})),
    ],
)
def test_disk_full_keeps_the_previous_file_and_removes_the_temporary(tmp_path, name, write):
    manager = Manager(tmp_path)
    write(manager)
    target = tmp_path / (name + ".json")
    before = target.read_bytes()
    with patch("pathlib.Path.write_text", disk_full), pytest.raises(OSError) as raised:
        write(manager)
    assert raised.value.errno == errno.ENOSPC
    assert target.read_bytes() == before
    assert not (tmp_path / (name + ".tmp")).exists()
