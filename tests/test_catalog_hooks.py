import asyncio
import json
from pathlib import Path

import pytest

from agent_service.catalog_hooks import run_hooks
from agent_service.catalog_manifest import hooks_digest
from agent_service.errors import APIError
from agent_service.secret_vault import SecretVault, execution_environment


def executable(path, body):
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o700)
    return str(path)


def trusted(root, *names):
    (root / "harness.catalog.json").write_text(
        json.dumps({"version": 1, "allowed_hooks": list(names)})
    )
    return [{"root": str(root), "hooks_sha256": hooks_digest(root)}]


def test_hooks_require_grant_and_run_only_allowlisted_scripts(tmp_path):
    allowed = executable(tmp_path / "allowed", 'printf "$DEMO_TOKEN"; touch allowed-marker\n')
    executable(tmp_path / "unrelated", "touch unrelated-marker\n")
    store = SecretVault(tmp_path / "vault")
    store.set("demo", {"token": "fake-hook-value"})
    runtime = {
        "allowed_hooks": [allowed],
        "cwd": str(tmp_path),
        "hook_catalogs": trusted(tmp_path, "allowed"),
    }
    events = []

    async def scenario():
        with execution_environment({"DEMO_TOKEN": "fake-hook-value"}):
            await run_hooks(runtime, False, lambda kind, value: events.append(value))
            assert not (tmp_path / "allowed-marker").exists()
            await run_hooks(runtime, True, lambda kind, value: events.append(value))

    asyncio.run(scenario())
    assert (tmp_path / "allowed-marker").is_file()
    assert not (tmp_path / "unrelated-marker").exists()
    assert events[-1]["stdout"] == "[redacted]"
    assert "fake-hook-value" not in str(events)


def test_hook_failure_timeout_and_output_limit_stop_execution(tmp_path):
    async def scenario(body, code, timeout=2):
        hook = executable(tmp_path / "hook", body)
        with pytest.raises(APIError, match=code):
            await run_hooks(
                {
                    "allowed_hooks": [hook],
                    "cwd": str(tmp_path),
                    "hook_catalogs": trusted(tmp_path, "hook"),
                },
                True,
                lambda *_: None,
                timeout=timeout,
                output_limit=100,
            )

    asyncio.run(scenario("exit 3\n", "catalog_hook_failed"))
    asyncio.run(scenario("sleep 30\n", "catalog_hook_timeout", timeout=0.02))
    asyncio.run(scenario('printf "%200s" x\n', "catalog_hook_output_limit"))


def test_hook_outside_the_verified_set_is_skipped_not_run_unhashed(tmp_path):
    hook = executable(tmp_path / "loose", "touch ran\n")
    events = []
    asyncio.run(
        run_hooks(
            {"allowed_hooks": [hook], "cwd": str(tmp_path)},
            True,
            lambda kind, value: events.append(value),
        )
    )
    assert not (tmp_path / "ran").exists()
    assert events == [{"outcome": "skipped", "reason": "hooks_not_trusted", "hook": hook}]


def test_pin_suppresses_catalog_hooks(tmp_path):
    import json

    from test_catalog_pin import git

    from agent_service.catalog_manifest import runtime_for_project
    from agent_service.catalog_pin import pin_catalog

    root = tmp_path / "catalog"
    root.mkdir()
    hook = executable(root / "hook", "touch forbidden\n")
    (root / "harness.catalog.json").write_text(
        json.dumps({"version": 1, "allowed_hooks": ["hook"]})
    )
    git(root, "init", "-q")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Fixture")
    git(root, "add", ".")
    git(root, "commit", "-qm", "fixture")
    catalog = {"id": "demo", "root": str(root), "trusted": True, "kind": "git"}
    state = tmp_path / "state"
    pin = pin_catalog(catalog, state, "HEAD", owner=True)
    runtime = runtime_for_project(
        {
            "state_dir": str(state),
            "catalogs": [catalog],
            "projects": {"p": {"catalogs": ["demo"], "catalog_pins": {"demo": pin}}},
        },
        "p",
    )
    assert runtime["allowed_hooks"] == []
    asyncio.run(run_hooks(runtime, True, lambda *_: None))
    assert not Path(hook).parent.joinpath("forbidden").exists()
