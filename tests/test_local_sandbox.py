"""Exercise real filesystem isolation with harmless Python, never an AI CLI."""

import asyncio
import json
import os
import shutil
import subprocess
from unittest.mock import patch

import pytest

from adapters.codex import rpc
from adapters.local import backend
from adapters.local.sandbox import wrap
from agent_service.tools import ToolError
from control.product import PRODUCT

KEY_NAME = PRODUCT.env_prefix + "_LOCAL_KEY"
SECRET = "sk-network-" + "k" * 24


@pytest.mark.parametrize("writable", [False, True])
def test_authorized_root_outside_symlink_credentials_and_write_boundary(tmp_path, writable):
    if not shutil.which("bwrap"):
        pytest.skip("bubblewrap is not installed")
    root = tmp_path / "project"
    root.mkdir()
    (root / "inside.txt").write_text("allowed")
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("must remain outside")
    (root / "escape.txt").symlink_to(outside)
    session = tmp_path / "session"
    session.mkdir()
    script = """import os,json,pathlib
root=pathlib.Path(%r)
def readable(path):
 try:pathlib.Path(path).read_text();return True
 except OSError:return False
try:(root/'written.txt').write_text('fixture');write=True
except OSError:write=False
print(json.dumps({'inside':readable(root/'inside.txt'),'outside':readable(%r),'symlink':readable(root/'escape.txt'),'write':write,'home':os.environ['HOME'],'foreign_env':os.environ.get('UNRELATED_SECRET'),'local_key':os.environ.get('KEEPHARNESS_LOCAL_KEY')}))
""" % (str(root), str(outside))
    command = wrap(
        ["/usr/bin/python3", "-c", script],
        session,
        root,
        {"root": str(root), "permissions": {"read": True, "write": writable}},
        {"KEEPHARNESS_LOCAL_KEY": "fixture-local", "UNRELATED_SECRET": "must-not-pass"},
    )
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=10, pass_fds=command.descriptors
        )
    finally:
        command.close()
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["inside"] and not value["outside"] and not value["symlink"]
    assert value["write"] is writable
    assert value["home"] == str(session / "agent-home")
    assert value["foreign_env"] is None and value["local_key"] == "fixture-local"


def test_missing_bwrap_is_fail_closed(tmp_path):
    with patch("adapters.local.sandbox.shutil.which", return_value=None):
        with pytest.raises(ToolError, match="local_filesystem_isolation_unavailable"):
            wrap(["/usr/bin/python3"], tmp_path, tmp_path, {})


def test_project_internal_runtime_secrets_are_hidden(tmp_path):
    if not shutil.which("bwrap"):
        pytest.skip("bubblewrap is not installed")
    root = tmp_path / "project"
    root.mkdir()
    for name in ("config", "migration-backup"):
        folder = root / "local_ai" / name
        folder.mkdir(parents=True)
        (folder / "private.txt").write_text("fixture-private")
    (root / "source.py").write_text("public source")
    session = tmp_path / "session"
    session.mkdir()
    script = (
        "import pathlib,json; p=pathlib.Path(%r); print(json.dumps({'source':(p/'source.py').read_text(),'private':[str(x) for x in (p/'local_ai').rglob('private.txt')]}))"
        % str(root)
    )
    with patch.dict("os.environ", {"KEEPHARNESS_ROOT": str(root)}):
        command = wrap(
            ["/usr/bin/python3", "-c", script],
            session,
            root,
            {"root": str(root), "permissions": {"read": True, "write": True}},
        )
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"source": "public source", "private": []}
    assert (root / "local_ai/config/private.txt").read_text() == "fixture-private"


@pytest.fixture
def linux_bwrap():
    with (
        patch("adapters.local.sandbox.sys.platform", "linux"),
        patch("adapters.local.sandbox.shutil.which", return_value="/fixture/bwrap"),
    ):
        yield


def wrapped(tmp_path, environment):
    binary = tmp_path / "fixture-cli"
    binary.touch()
    return wrap([str(binary)], tmp_path, tmp_path, {"permissions": {}}, environment)


def test_the_network_key_never_appears_in_the_command_line(tmp_path, linux_bwrap):
    command = wrapped(tmp_path, {KEY_NAME: SECRET, "UNRELATED": "must-not-pass"})
    try:
        assert not any(SECRET in part or "UNRELATED" in part for part in command)
        assert KEY_NAME not in command and "--args" in command
        (descriptor,) = command.descriptors
        assert command[command.index("--args") + 1] == str(descriptor)
        # bwrap parses the arguments it finds on that descriptor, not the process list.
        payload = os.pread(descriptor, 4096, 0).split(b"\0")
        assert payload == [b"--setenv", KEY_NAME.encode(), SECRET.encode(), b""]
    finally:
        command.close()


@pytest.mark.parametrize("environment", [None, {}, {"OTHER": "value"}, {KEY_NAME: ""}])
def test_no_descriptor_is_opened_without_a_key(tmp_path, linux_bwrap, environment):
    command = wrapped(tmp_path, environment)
    assert command.descriptors == () and "--args" not in command
    command.close()


def test_a_key_with_a_nul_byte_cannot_smuggle_sandbox_options(tmp_path, linux_bwrap):
    with pytest.raises(ValueError, match="NUL"):
        wrapped(tmp_path, {KEY_NAME: "x\0--bind\0/\0/"})


def test_closing_the_command_releases_the_descriptor_once(tmp_path, linux_bwrap):
    command = wrapped(tmp_path, {KEY_NAME: SECRET})
    (descriptor,) = command.descriptors
    command.close()
    command.close()
    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_the_key_reaches_the_sandboxed_process_without_touching_argv(tmp_path):
    if not shutil.which("bwrap"):
        pytest.skip("bubblewrap is not installed")
    script = f"import os; print(os.environ.get({KEY_NAME!r}))"
    command = wrap(
        ["/usr/bin/python3", "-c", script],
        tmp_path,
        tmp_path,
        {"permissions": {}},
        {KEY_NAME: SECRET},
    )
    try:
        assert not any(SECRET in part for part in command)
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=10, pass_fds=command.descriptors
        )
    finally:
        command.close()
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == SECRET


def local_project(tmp_path):
    key = tmp_path / "server.key"
    key.write_text(SECRET + "\n")
    binary = tmp_path / "codex"
    binary.touch()
    config = {
        "binary": str(binary),
        "local_provider": "ollama",
        "local_models": {"net": {"url": "http://box.ts.net:8080", "key_file": str(key)}},
    }
    return config, {"permissions": {}}


def test_the_backend_hands_the_descriptor_to_the_spawn_and_closes_it_afterwards(
    tmp_path, linux_bwrap
):
    config, project = local_project(tmp_path)
    seen = {}

    async def run_turn(
        config, event, project, model, effort, session_dir, approve, workspace, runtime, provider
    ):
        seen["runtime"] = runtime
        (descriptor,) = runtime.pass_fds
        seen["payload"] = os.pread(descriptor, 4096, 0)
        return {"answer": "ok"}

    with patch.object(backend, "run_turn", run_turn):
        result = asyncio.run(
            backend.run_native(config, "hi", None, project, "net", "low", tmp_path / "s", None)
        )
    assert result == {"answer": "ok"}
    runtime = seen["runtime"]
    assert SECRET.encode() in seen["payload"]
    assert not any(SECRET in part for part in runtime.command)
    with pytest.raises(OSError):
        os.fstat(runtime.pass_fds[0])


def test_the_backend_closes_the_descriptor_when_the_turn_fails(tmp_path, linux_bwrap):
    config, project = local_project(tmp_path)
    seen = {}

    async def run_turn(
        config, event, project, model, effort, session_dir, approve, workspace, runtime, provider
    ):
        seen["descriptors"] = runtime.pass_fds
        raise RuntimeError("provider crashed")

    with patch.object(backend, "run_turn", run_turn), pytest.raises(RuntimeError):
        asyncio.run(
            backend.run_native(config, "hi", None, project, "net", "low", tmp_path / "s", None)
        )
    with pytest.raises(OSError):
        os.fstat(seen["descriptors"][0])


def test_the_rpc_connection_forwards_the_descriptors_to_the_child(tmp_path):
    seen = {}

    async def spawn(*command, **options):
        seen.update(options)
        raise RuntimeError("stop before the handshake")

    async def attempt():
        async with rpc.connection(["/fixture/bwrap"], pass_fds=(7,)):
            pass

    with patch("asyncio.create_subprocess_exec", spawn), pytest.raises(RuntimeError):
        asyncio.run(attempt())
    assert seen["pass_fds"] == (7,)
