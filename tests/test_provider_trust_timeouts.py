# ruff: noqa: F401, F811
"""Native trust deadlines stop processes and refuse late file mutation."""

import asyncio
import json
import os
import signal
import threading
import time
import tomllib
from pathlib import Path

import pytest

from tests.test_admin_provider_state import app, claude_dir, codex_home, project
from tests.test_provider_state_codex import seed
from tests.test_provider_trust import setup_claude


def configure_deadline(app, codex_home, claude_dir, project, monkeypatch):
    import control.provider_state as state

    seed(codex_home)
    claude = setup_claude(app, claude_dir, project)
    monkeypatch.setattr(state, "SECURITY_WRITE_SECONDS", 0.5, raising=False)
    return app.state.manager.provider_state, claude


async def bounded_request(service, release):
    task = asyncio.create_task(service.security_write("codex", "p"))
    expired = False
    try:
        result = await asyncio.wait_for(asyncio.shield(task), 2)
    except TimeoutError:
        expired = True
    finally:
        if expired:
            release()
        result = await task
    assert not expired, "Native trust operation must finish within its deadline and cleanup budget"
    assert all(not service.locks[name].locked() for name in ("codex", "claude"))
    assert not service.cache
    return result


def test_claude_real_file_lock_times_out_without_late_write(
    app, codex_home, claude_dir, project, monkeypatch
):
    from adapters.shared.provider_state import _lock_for

    service, claude = configure_deadline(app, codex_home, claude_dir, project, monkeypatch)
    before = claude._claude_json().read_bytes()
    lock = _lock_for(claude._claude_json().resolve())
    lock.acquire()
    try:
        response = asyncio.run(
            bounded_request(service, lambda: lock.release() if lock.locked() else None)
        )
        assert response.status_code == 504, response.body
        assert json.loads(response.body)["error"] == "provider_state_timeout"
        assert lock.locked(), "The service must time out before this external lock is released"
        assert claude._claude_json().read_bytes() == before
    finally:
        if lock.locked():
            lock.release()
    time.sleep(0.1)
    assert claude._claude_json().read_bytes() == before
    assert not service.adapters["codex"]._is_project_trusted(project)
    assert asyncio.run(service.read("codex", "p"))["trust"]["trusted"] is False


@pytest.mark.parametrize("during_rollback", [False, True])
@pytest.mark.parametrize("landed", [False, True, "external", "reply"])
def test_codex_hung_writer_is_killed_reaped_and_cannot_write_late(
    app, codex_home, claude_dir, project, monkeypatch, tmp_path, landed, during_rollback
):
    import adapters.codex.state as codex_state

    service, claude = configure_deadline(app, codex_home, claude_dir, project, monkeypatch)
    source = Path("tests/fixtures/fake-codex/codex").read_text()
    shim = tmp_path / "hanging-codex"
    injection = """
_normal_write = config_batch_write
def config_batch_write(params):
    flag = home() / "hang-once"
    if not flag.exists():
        return _normal_write(params)
    flag.unlink()
    if (home() / "land-before-hang").exists():
        _normal_write(params)
    if (home() / "external-edit").exists():
        with (home() / "config.toml").open("a") as stream:
            stream.write('\\n[external]\\nkeep=true\\n')
    import signal, subprocess
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child = subprocess.Popen([sys.executable, "-c", "import time;from pathlib import Path;time.sleep(0.9);Path(" + repr(str(home() / "late-write")) + ").write_text('late')"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (home() / "writer-pids").write_text(str(os.getpid()) + " " + str(child.pid))
    if (home() / "reply-before-hang").exists():
        (home() / "hang-after-reply").touch()
        return {"status": "ok", "version": "irrelevant"}
    while True:
        time.sleep(1)

"""
    source = source.replace("HANDLERS = {", injection + "HANDLERS = {")
    source = source.replace(
        "        print(json.dumps(reply), flush=True)",
        "        print(json.dumps(reply), flush=True)\n"
        "        if method == 'config/batchWrite' and (home() / 'hang-after-reply').exists():\n"
        "            (home() / 'hang-after-reply').unlink()\n"
        "            while True: time.sleep(1)",
    )
    shim.write_text(source)
    shim.chmod(0o700)
    real_which = codex_state.shutil.which
    monkeypatch.setattr(
        codex_state.shutil, "which", lambda name: str(shim) if name == "codex" else real_which(name)
    )
    if during_rollback:
        from adapters.shared.provider_state import ProviderStateVersionError

        write_claude = claude.trust_project

        def fail_before_native_undo(root, **kwargs):
            write_claude(root, **kwargs)
            (codex_home / "hang-once").touch()
            raise ProviderStateVersionError("Injected second-provider failure")

        monkeypatch.setattr(claude, "trust_project", fail_before_native_undo)
    else:
        (codex_home / "hang-once").touch()
    if landed:
        (codex_home / "land-before-hang").touch()
    if landed == "external":
        (codex_home / "external-edit").touch()
    if landed == "reply":
        (codex_home / "reply-before-hang").touch()
    before = tomllib.loads((codex_home / "config.toml").read_text())
    claude_before = claude._claude_json().read_bytes()

    def cleanup():
        pids = codex_home / "writer-pids"
        if pids.exists():
            try:
                os.killpg(int(pids.read_text().split()[0]), signal.SIGKILL)
            except ProcessLookupError:
                pass

    try:
        response = asyncio.run(bounded_request(service, cleanup))
        incomplete = during_rollback or landed == "external"
        assert response.status_code == (409 if incomplete else 504), response.body
        assert json.loads(response.body)["error"] == (
            "provider_trust_rollback_incomplete"
            if incomplete
            else "provider_state_timeout"
        )
        writer, child = map(int, (codex_home / "writer-pids").read_text().split())
        with pytest.raises(ProcessLookupError):
            os.kill(writer, 0)
        child_state = Path(f"/proc/{child}/stat")
        assert not child_state.exists() or child_state.read_text().split()[2] == "Z"
        time.sleep(1)
        assert not (codex_home / "late-write").exists()
        if landed == "external":
            assert tomllib.loads((codex_home / "config.toml").read_text())["external"] == {
                "keep": True
            }
        elif not (during_rollback and not landed):
            assert tomllib.loads((codex_home / "config.toml").read_text()) == before
        assert claude._claude_json().read_bytes() == claude_before
        assert asyncio.run(service.read("codex", "p"))["trust"]["trusted"] is (
            (during_rollback and not landed) or (not during_rollback and landed == "external")
        )
    finally:
        cleanup()


def test_claude_deadline_expiring_before_swap_never_changes_target(
    app, codex_home, claude_dir, project, monkeypatch
):
    import adapters.shared.provider_state as state

    service, claude = configure_deadline(app, codex_home, claude_dir, project, monkeypatch)
    before = claude._claude_json().read_bytes()
    swap = state._swap

    def delayed_swap(*args, **kwargs):
        time.sleep(0.6)
        return swap(*args, **kwargs)

    monkeypatch.setattr(state, "_swap", delayed_swap)
    response = asyncio.run(bounded_request(service, lambda: None))
    assert response.status_code == 504, response.body
    assert claude._claude_json().read_bytes() == before
    assert not service.adapters["codex"]._is_project_trusted(project)


def test_rollback_real_lock_deadline_does_not_skip_other_provider(
    app, codex_home, claude_dir, project, monkeypatch
):
    from adapters.shared.provider_state import ProviderStateVersionError, _lock_for

    service, claude = configure_deadline(app, codex_home, claude_dir, project, monkeypatch)
    lock = _lock_for(claude._claude_json().resolve())
    write = claude.trust_project

    def write_then_block_rollback(root, **kwargs):
        write(root, **kwargs)
        lock.acquire()
        raise ProviderStateVersionError("Injected failure with a blocked rollback lock")

    monkeypatch.setattr(claude, "trust_project", write_then_block_rollback)
    try:
        response = asyncio.run(
            bounded_request(service, lambda: lock.release() if lock.locked() else None)
        )
        assert response.status_code == 409, response.body
        assert json.loads(response.body)["error"] == "provider_trust_rollback_incomplete"
        assert lock.locked()
        assert not service.adapters["codex"]._is_project_trusted(project)
        assert claude._is_project_trusted(project)
    finally:
        if lock.locked():
            lock.release()
    time.sleep(0.1)
    assert claude._is_project_trusted(project), "No detached undo may run after the response"
    assert asyncio.run(service.read("codex", "p"))["trust"]["trusted"] is True
