# ruff: noqa: F401, F811
"""Trust changes compensate native writes; stale owner-disabled approvals refuse."""

import asyncio
import json
import tomllib

import pytest

from adapters.shared.provider_state import ProviderStateVersionError
from tests.test_admin_provider_state import HEADERS, app, claude_dir, client, codex_home, project
from tests.test_provider_state_codex import seed
from tests.test_provider_trust import setup_claude


def test_owner_disabled_approval_is_rechecked_before_write(
    client, app, codex_home, claude_dir, project
):
    seed(codex_home)
    claude = setup_claude(app, claude_dir, project)
    claude.trust_project(project)
    # The UI's prior read showed an enabled server; the CLI disables it meanwhile.
    assert (
        client.get(
            "/api/provider-state", params={"provider": "claude", "project_id": "p"}
        ).status_code
        == 200
    )
    path = claude._claude_json()
    state = json.loads(path.read_text())
    state["projects"][str(project)]["disabledMcpServers"] = ["marker"]
    path.write_text(json.dumps(state))
    approval_path = project / ".claude/settings.local.json"
    response = client.post(
        "/api/provider-state/mcp-approvals",
        headers=HEADERS,
        json={
            "provider": "claude",
            "project_id": "p",
            "expected_project_root": str(project),
            "server": "marker",
            "approved": True,
        },
    )
    assert response.status_code == 409, response.text
    assert "disabled by owner" in response.json()["message"].lower()
    assert not approval_path.exists()


@pytest.mark.parametrize("initial", [None, "trusted", "untrusted"])
@pytest.mark.parametrize("failing_provider", ["codex", "claude"])
def test_failed_trust_restores_both_native_stores(
    app, codex_home, claude_dir, project, monkeypatch, initial, failing_provider
):
    seed(codex_home)
    claude = setup_claude(app, claude_dir, project)
    service = app.state.manager.provider_state
    if initial is not None:
        service.adapters["codex"].trust_project(project, trusted=initial == "trusted")
        claude.trust_project(project, trusted=initial == "trusted")
    else:
        state = json.loads(claude._claude_json().read_text())
        del state["projects"][str(project)]["hasTrustDialogAccepted"]
        claude._claude_json().write_text(json.dumps(state))
    paths = [codex_home / "config.toml", claude._claude_json()]
    before = [path.read_bytes() for path in paths]
    asyncio.run(service.read("codex", "p"))
    seen = (service.state / "provider-state-seen.json").read_bytes()
    adapter = service.adapters[failing_provider]
    real = adapter.trust_project

    def write_then_fail(root, **kwargs):
        real(root, **kwargs)
        raise ProviderStateVersionError("Injected failure after native write")

    monkeypatch.setattr(adapter, "trust_project", write_then_fail)
    result = asyncio.run(service.security_write("codex", "p", trusted=initial != "trusted"))
    assert result.status_code == 422
    assert tomllib.loads(paths[0].read_text()) == tomllib.loads(before[0].decode())
    assert paths[1].read_bytes() == before[1]
    assert (service.state / "provider-state-seen.json").read_bytes() == seen
    service.cache.clear()
    assert asyncio.run(service.read("codex", "p"))["external_changes"] == []


def test_explicit_child_revocation_wins_over_inherited_parent_layer(
    app, codex_home, claude_dir, project, monkeypatch
):
    import adapters.codex.state as codex_state

    seed(codex_home)
    claude = setup_claude(app, claude_dir, project)
    service = app.state.manager.provider_state
    codex = service.adapters["codex"]
    codex.trust_project(project.parent)
    claude.trust_project(project)
    real = codex_state._ask

    async def inherited_read(binary, requests, **kwargs):
        result, failures = await real(binary, requests, **kwargs)
        if "config" in result:
            # Native 0.157.1 shape: no child .codex, ancestor layer remains enabled,
            # but the user config carries an explicit child untrusted key.
            result["config"]["layers"].insert(
                0,
                {
                    "name": {"type": "project", "dotCodexFolder": str(project.parent / ".codex")},
                    "version": "sha256:fixture-parent",
                    "config": {},
                },
            )
        return result, failures

    monkeypatch.setattr(codex_state, "_ask", inherited_read)
    assert codex._is_project_trusted(project)
    response = asyncio.run(service.security_write("codex", "p", trusted=False))
    assert isinstance(response, dict), response.body
    assert response["trust"] == {
        "trusted": False,
        "required": True,
        "inherited_from": str(project.parent),
    }
    assert not claude._is_project_trusted(project)
    assert not codex._is_project_trusted(project)
    assert (
        tomllib.loads((codex_home / "config.toml").read_text())["projects"][str(project.parent)][
            "trust_level"
        ]
        == "trusted"
    )


@pytest.mark.parametrize("external_provider", ["codex", "claude"])
def test_failed_rollback_preserves_concurrent_edit_and_reports_partial_state(
    app, codex_home, claude_dir, project, monkeypatch, external_provider
):
    seed(codex_home)
    claude = setup_claude(app, claude_dir, project)
    service = app.state.manager.provider_state
    before_claude = claude._claude_json().read_bytes()
    before_codex = tomllib.loads((codex_home / "config.toml").read_text())
    real = claude.trust_project
    external = []

    def write_and_conflict(root, **kwargs):
        real(root, **kwargs)
        path = codex_home / "config.toml" if external_provider == "codex" else claude._claude_json()
        if external_provider == "codex":
            path.write_text(path.read_text() + '\n[external]\nkeep="concurrent"\n')
        else:
            document = json.loads(path.read_text())
            document["external"] = "concurrent"
            path.write_text(json.dumps(document))
        external.append(path.read_bytes())
        raise ProviderStateVersionError("Injected second-provider confirmation failure")

    monkeypatch.setattr(claude, "trust_project", write_and_conflict)
    response = asyncio.run(service.security_write("codex", "p"))
    body = json.loads(response.body)
    assert response.status_code == 409
    assert body["error"] == "provider_trust_rollback_incomplete"
    assert "rollback could not finish" in body["message"]
    if external_provider == "codex":
        assert (codex_home / "config.toml").read_bytes() == external[0]
        assert claude._claude_json().read_bytes() == before_claude
    else:
        assert claude._claude_json().read_bytes() == external[0]
        assert tomllib.loads((codex_home / "config.toml").read_text()) == before_codex
    assert not list((service.state / "provider-state-writes").glob("*.json"))


def test_failed_trust_restores_missing_claude_file(
    app, codex_home, claude_dir, project, monkeypatch
):
    seed(codex_home)
    claude = setup_claude(app, claude_dir, project)
    claude._claude_json().unlink()
    before = tomllib.loads((codex_home / "config.toml").read_text())
    service = app.state.manager.provider_state
    real = claude.trust_project

    def write_then_fail(root, **kwargs):
        real(root, **kwargs)
        raise ProviderStateVersionError("Injected confirmation failure")

    monkeypatch.setattr(claude, "trust_project", write_then_fail)
    assert asyncio.run(service.security_write("codex", "p")).status_code == 422
    assert not claude._claude_json().exists()
    assert tomllib.loads((codex_home / "config.toml").read_text()) == before


def test_cancelled_inflight_native_write_finishes_then_rolls_back(
    app, codex_home, claude_dir, project, monkeypatch
):
    import threading

    seed(codex_home)
    claude = setup_claude(app, claude_dir, project)
    service = app.state.manager.provider_state
    before = tomllib.loads((codex_home / "config.toml").read_text())
    started, release, done = threading.Event(), threading.Event(), threading.Event()
    real = service.adapters["codex"].trust_project

    def delayed(root, **kwargs):
        started.set()
        assert release.wait(5)
        try:
            real(root, **kwargs)
        finally:
            done.set()

    monkeypatch.setattr(service.adapters["codex"], "trust_project", delayed)

    async def scenario():
        task = asyncio.create_task(service.security_write("codex", "p"))
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(done.wait, 5)

    asyncio.run(scenario())
    assert tomllib.loads((codex_home / "config.toml").read_text()) == before
    assert not claude._is_project_trusted(project)


@pytest.mark.parametrize("inherited", [False, True])
def test_real_codex_child_transaction_uses_native_versions(
    app, codex_home, claude_dir, project, monkeypatch, inherited
):
    import os
    import subprocess

    import adapters.codex.state as codex_state

    native = os.environ.get("KEEPHARNESS_NATIVE_CODEX")
    if not native:
        pytest.skip("Set KEEPHARNESS_NATIVE_CODEX to exercise the installed CLI with fake homes")
    (codex_home / "config.toml").write_text("")
    claude = setup_claude(app, claude_dir, project)
    service = app.state.manager.provider_state
    codex = service.adapters["codex"]
    original_which = codex_state.shutil.which
    monkeypatch.setattr(
        codex_state.shutil,
        "which",
        lambda name: native if name == "codex" else original_which(name),
    )
    if inherited:
        (project.parent / ".codex").mkdir()
        (project.parent / ".codex/config.toml").write_text('model = "gpt-5.4"\n')
        subprocess.run(
            ["git", "init", "-q", str(project.parent)],
            env=codex._environment(),
            check=True,
            timeout=10,
        )
        codex.trust_project(project.parent)
        assert codex.project_trust_details(project)["inherited_from"] == str(project.parent)
    claude.trust_project(project)
    before = tomllib.loads((codex_home / "config.toml").read_text())
    original_claude = claude.trust_project

    def fail_after_claude(root, **kwargs):
        original_claude(root, **kwargs)
        raise ProviderStateVersionError("Injected Claude confirmation failure")

    monkeypatch.setattr(claude, "trust_project", fail_after_claude)
    response = asyncio.run(service.security_write("codex", "p", trusted=False))
    assert response.status_code == 422, response.body
    assert tomllib.loads((codex_home / "config.toml").read_text()) == before
    assert claude._is_project_trusted(project)
    monkeypatch.setattr(claude, "trust_project", original_claude)
    response = asyncio.run(service.security_write("codex", "p", trusted=False))
    assert isinstance(response, dict), response.body
    expected = {"trusted": False, "required": True}
    if inherited:
        expected["inherited_from"] = str(project.parent)
    assert response["trust"] == expected
    assert not codex._is_project_trusted(project)
    assert not claude._is_project_trusted(project)


def test_failed_revoke_preserves_existing_receipt_and_seen_map(
    app, codex_home, claude_dir, project, monkeypatch
):
    from tests.test_provider_trust_review import fixture_service

    service, claude = fixture_service(app, codex_home, claude_dir, project)
    assert isinstance(asyncio.run(service.security_write("codex", "p")), dict)
    receipts = {
        path: path.read_bytes() for path in (service.state / "provider-state-writes").glob("*.json")
    }
    assert receipts
    seen = (service.state / "provider-state-seen.json").read_bytes()
    real = claude.trust_project

    def fail(root, **kwargs):
        real(root, **kwargs)
        raise ProviderStateVersionError("Injected failure after native write")

    monkeypatch.setattr(claude, "trust_project", fail)
    assert asyncio.run(service.security_write("codex", "p", trusted=False)).status_code == 422
    assert {path: path.read_bytes() for path in receipts} == receipts
    assert (service.state / "provider-state-seen.json").read_bytes() == seen
    assert asyncio.run(service.read("codex", "p"))["external_changes"] == []


@pytest.mark.parametrize("missing", [True, False])
def test_revoke_requires_explicit_native_confirmation_before_commit(
    app, codex_home, claude_dir, project, monkeypatch, missing
):
    import adapters.codex.state as codex_state

    seed(codex_home)
    claude = setup_claude(app, claude_dir, project)
    service = app.state.manager.provider_state
    codex = service.adapters["codex"]
    codex.trust_project(project)
    claude.trust_project(project)
    before_codex = tomllib.loads((codex_home / "config.toml").read_text())
    before_claude = claude._claude_json().read_bytes()
    read, write = codex_state._ask, codex_state._write
    written = False

    async def write_then_lose_confirmation(*args, **kwargs):
        nonlocal written
        result = await write(*args, **kwargs)
        written = True
        return result

    async def failed_confirmation(binary, requests, **kwargs):
        if written and len(requests) == 1 and requests[0][1] == "config/read":
            return ({}, {"config": "unavailable"}) if missing else ({"config": {}}, {})
        return await read(binary, requests, **kwargs)

    monkeypatch.setattr(codex_state, "_write", write_then_lose_confirmation)
    monkeypatch.setattr(codex_state, "_ask", failed_confirmation)
    response = asyncio.run(service.security_write("codex", "p", trusted=False))
    assert not isinstance(response, dict), "Missing confirmation must never commit revocation"
    assert response.status_code == 422
    assert tomllib.loads((codex_home / "config.toml").read_text()) == before_codex
    assert claude._claude_json().read_bytes() == before_claude
    assert not list((service.state / "provider-state-writes").glob("*.json"))


@pytest.mark.parametrize("writer_fails", [False, True])
def test_repeated_cancellation_waits_for_native_writer_and_restores_state(
    app, codex_home, claude_dir, project, monkeypatch, writer_fails
):
    import threading

    seed(codex_home)
    claude = setup_claude(app, claude_dir, project)
    service = app.state.manager.provider_state
    before_codex = tomllib.loads((codex_home / "config.toml").read_text())
    before_claude = claude._claude_json().read_bytes()
    started, release, done = threading.Event(), threading.Event(), threading.Event()
    real = service.adapters["codex"].trust_project

    def suspended_writer(root, **kwargs):
        started.set()
        assert release.wait(10)
        try:
            real(root, **kwargs)
            if writer_fails:
                raise ProviderStateVersionError("Injected failure after the cancelled write")
        finally:
            done.set()

    monkeypatch.setattr(service.adapters["codex"], "trust_project", suspended_writer)

    async def scenario():
        task = asyncio.create_task(service.security_write("codex", "p"))
        try:
            assert await asyncio.to_thread(started.wait, 10)
            for _ in range(2):
                task.cancel()
                for _ in range(3):
                    await asyncio.sleep(0)
            assert not task.done(), "Repeated cancellation must still wait for the native writer"
            assert all(service.locks[name].locked() for name in ("codex", "claude"))
        finally:
            release.set()
            result = await asyncio.gather(task, return_exceptions=True)
            assert await asyncio.to_thread(done.wait, 10)
        assert isinstance(result[0], asyncio.CancelledError)
        assert all(not service.locks[name].locked() for name in ("codex", "claude"))

    asyncio.run(scenario())
    assert tomllib.loads((codex_home / "config.toml").read_text()) == before_codex
    assert claude._claude_json().read_bytes() == before_claude


def test_repeated_cancellation_waits_for_every_reverse_compensation(
    app, codex_home, claude_dir, project, monkeypatch
):
    import threading

    from adapters.codex.state import _TrustRollback
    from adapters.shared.provider_state import TrustWriteRollback

    seed(codex_home)
    claude = setup_claude(app, claude_dir, project)
    service = app.state.manager.provider_state
    before_codex = tomllib.loads((codex_home / "config.toml").read_text())
    before_claude = claude._claude_json().read_bytes()
    started, release, done = threading.Event(), threading.Event(), threading.Event()
    completed = []
    write = claude.trust_project
    restore_claude, restore_codex = TrustWriteRollback.restore, _TrustRollback.restore

    def fail_after_claude(root, **kwargs):
        write(root, **kwargs)
        raise ProviderStateVersionError("Injected confirmation failure")

    def suspended_undo(undo):
        started.set()
        assert release.wait(10)
        try:
            restore_claude(undo)
            completed.append("claude")
        finally:
            done.set()

    def second_undo(undo):
        restore_codex(undo)
        completed.append("codex")

    monkeypatch.setattr(claude, "trust_project", fail_after_claude)
    monkeypatch.setattr(TrustWriteRollback, "restore", suspended_undo)
    monkeypatch.setattr(_TrustRollback, "restore", second_undo)

    async def scenario():
        task = asyncio.create_task(service.security_write("codex", "p"))
        try:
            assert await asyncio.to_thread(started.wait, 10)
            for _ in range(3):
                task.cancel()
                for _ in range(3):
                    await asyncio.sleep(0)
            assert not task.done(), "Cancellation must not detach the reverse compensation"
            assert all(service.locks[name].locked() for name in ("codex", "claude"))
        finally:
            release.set()
            result = await asyncio.gather(task, return_exceptions=True)
            assert await asyncio.to_thread(done.wait, 10)
        assert isinstance(result[0], asyncio.CancelledError)
        assert completed == ["claude", "codex"]
        assert all(not service.locks[name].locked() for name in ("codex", "claude"))

    asyncio.run(scenario())
    assert tomllib.loads((codex_home / "config.toml").read_text()) == before_codex
    assert claude._claude_json().read_bytes() == before_claude
