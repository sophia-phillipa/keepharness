# ruff: noqa: F401, F811
"""The run-start fingerprint stat (issue #43): ``run_start_check`` and the queue worker hook.

Fake homes only. The harness side stats the watched paths and dates an outside change; the control
side reuses that date. It never parses a CLI file and never raises into a run.
"""

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest

import control.provider_state as provider_state
from adapters.codex.state import CodexStateAdapter
from agent_service.services import queue_worker
from control.provider_state import run_start_check
from tests.test_admin_provider_state import (
    app,
    claude_dir,
    client,
    codex_home,
    get,
    project,
)
from tests.test_provider_state_codex import GITHUB, seed
from tests.test_provider_state_notices import (
    changes,
    clocked,
    edit_codex,
    later,
    now,
    seen_path,
)

KEY = "codex|sem-projeto"
FIRST, SECOND = 1_700_000_000.0, 1_700_000_999.0


def stamp_of(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def runs_path(app):
    return app.state.manager.state / "provider-state-runs.json"


def runs(app):
    return json.loads(runs_path(app).read_text())["entries"]


def check(app):
    run_start_check(app.state.manager.state, "codex", "sem-projeto", None)


def change_codex(codex_home):
    edit_codex(codex_home, "enabled = false  # turned off", "enabled = true  # turned on")


def test_run_start_records_detected_at_only_on_change(client, app, now, codex_home, monkeypatch):
    get(client)  # baseline
    clock = iter([FIRST, SECOND])
    monkeypatch.setattr(provider_state.time, "time", lambda: next(clock))
    check(app)
    assert not runs_path(app).exists()  # same stat as the baseline
    change_codex(codex_home)
    check(app)
    first = runs(app)[KEY]
    assert first["detected_at"] == stamp_of(FIRST) and first["stat"]
    check(app)  # same fingerprint again: not rewritten with a later date
    assert runs(app)[KEY] == first


def test_run_start_never_parses(client, app, now, codex_home, monkeypatch):
    get(client)
    change_codex(codex_home)
    (codex_home / "config.toml").write_text("not [valid toml\n")

    def forbidden(*args, **kwargs):
        raise AssertionError("run_start_check must only stat")

    monkeypatch.setattr(CodexStateAdapter, "read_state", forbidden)
    monkeypatch.setattr(CodexStateAdapter, "_read_documents", forbidden, raising=False)
    check(app)
    assert runs(app)[KEY]["detected_at"]


def test_run_start_without_baseline_writes_nothing(app, codex_home):
    seed(codex_home)
    check(app)
    assert not runs_path(app).exists()


def test_run_start_errors_never_raise(client, app, now, codex_home, monkeypatch):
    get(client)
    change_codex(codex_home)
    state = app.state.manager.state
    run_start_check(state / "missing" / "dir", "codex", "sem-projeto", None)  # no seen file
    seen_path(app).chmod(0)  # unreadable seen map
    run_start_check(state, "codex", "sem-projeto", None)
    seen_path(app).chmod(0o600)

    def refuse(*args, **kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(provider_state.ControlStateRepository, "_replace", refuse)
    run_start_check(state, "codex", "sem-projeto", None)  # the write fails
    run_start_check(state, "unknown", "sem-projeto", None)
    assert not runs_path(app).exists()


def test_control_uses_run_detected_at(client, app, now, codex_home, monkeypatch):
    get(client)
    change_codex(codex_home)
    with monkeypatch.context() as patch:
        patch.setattr(provider_state.time, "time", lambda: FIRST)
        check(app)
    later(now)  # the control's own clock is far from FIRST
    (notice,) = changes(client)
    assert notice["detected_at"] == stamp_of(FIRST)
    assert notice["item_id"] == GITHUB


def test_run_job_calls_hook_once_per_5s(monkeypatch, tmp_path):
    calls = []
    tick = [1000.0]
    monkeypatch.setattr(queue_worker, "run_start_check", lambda *args: calls.append(args))
    monkeypatch.setattr(queue_worker, "time", SimpleNamespace(monotonic=lambda: tick[0]))
    monkeypatch.setattr(queue_worker, "_run_checks", {})
    service = SimpleNamespace(
        config={"control_state_dir": str(tmp_path), "projects": {"p": {"root": str(tmp_path)}}}
    )

    def run(provider, project):
        asyncio.run(
            queue_worker.provider_state_run_check(
                service, {"project": project}, {"backend": provider}
            )
        )

    run("codex", "sem-projeto")
    tick[0] += 4.9
    run("codex", "sem-projeto")  # coalesced
    run("claude", "sem-projeto")  # another provider
    run("codex", "p")  # another project
    assert len(calls) == 3
    assert calls[2][2:] == ("p", tmp_path)
    tick[0] += 0.2
    run("codex", "sem-projeto")
    assert len(calls) == 4
    run("deepseek", "sem-projeto")
    assert len(calls) == 5
    assert calls[-1][1] == "deepseek"
    run("dsh", "sem-projeto")  # not a supported provider
    assert len(calls) == 5


def test_concurrent_run_starts_keep_every_key(client, app, now, codex_home, project, monkeypatch):
    get(client)
    get(client, project_id="p")
    change_codex(codex_home)
    barrier = threading.Barrier(2, timeout=0.5)
    real = provider_state._write_entries

    def meet_then_write(path, entries):
        try:
            barrier.wait()  # both have read the runs file, unless a lock holds one back
        except threading.BrokenBarrierError:
            pass
        real(path, entries)

    monkeypatch.setattr(provider_state, "_write_entries", meet_then_write)
    state = app.state.manager.state
    threads = [
        threading.Thread(target=run_start_check, args=(state, "codex", "sem-projeto", None)),
        threading.Thread(target=run_start_check, args=(state, "codex", "p", project)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert set(runs(app)) == {KEY, "codex|p"}
