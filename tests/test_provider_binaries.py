"""Provider binaries: the isolated-mode ELF gate, npm Codex wrappers and a CLI removed
while the harness runs (P5-30, P5-31, F-01, F-10)."""

import asyncio
import json
import sys
from unittest.mock import patch

import pytest

from agent_service.app import Service
from control import runtime_config
from tests.test_shared_projects import config

ELF = b"\x7fELF\x02\x01\x01\x00fixture"


def build(provider, binary, mode, tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    cfg = {"services": {provider: {}}}
    spec = {"mode": mode, "models": ["m"], "integrations": []}
    checked = {"authenticated": True, "models": {"m": ["low"]}}
    info = {"binary": str(binary), "auth_file": str(auth)}
    runtime_config.build_cli_provider(cfg, provider, spec, checked, info, tmp_path)
    return cfg[provider]["binary"]


def test_isolated_mode_rejects_a_script_wrapper_and_native_mode_accepts_it(tmp_path):
    script = tmp_path / "claude"
    script.write_text('#!/bin/sh\nexec node cli.js "$@"\n')
    script.chmod(0o755)
    with pytest.raises(ValueError) as raised:
        build("claude", script, "scoped", tmp_path)
    assert str(raised.value) == (
        "Use the native Linux binary for claude; shell/npm wrappers are not mounted in the sandbox."
    )
    assert build("claude", script, "native", tmp_path) == str(script)


def npm_codex(prefix, with_native=True):
    """The layout ``npm install -g @openai/codex`` creates, with a fake native binary."""
    package = prefix / "lib/node_modules/@openai/codex"
    (package / "bin").mkdir(parents=True)
    wrapper = package / "bin/codex.js"
    wrapper.write_text("#!/usr/bin/env node\n")
    wrapper.chmod(0o755)
    (prefix / "bin").mkdir()
    (prefix / "bin/codex").symlink_to(wrapper)
    native = (
        package / "node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex"
    )
    if with_native:
        native.parent.mkdir(parents=True)
        native.write_bytes(ELF)
        native.chmod(0o755)
    return prefix / "bin/codex", native


@pytest.mark.parametrize("mode", ["scoped", "native"])
def test_npm_codex_wrapper_resolves_to_its_native_binary(tmp_path, mode):
    command, native = npm_codex(tmp_path / "prefix")
    with (
        patch("control.runtime_config.platform.system", return_value="Linux"),
        patch("control.runtime_config.platform.machine", return_value="x86_64"),
    ):
        assert build("codex", command, mode, tmp_path) == str(native)


def test_npm_codex_wrapper_without_its_platform_package_is_still_rejected(tmp_path):
    command, _ = npm_codex(tmp_path / "prefix", with_native=False)
    with (
        patch("control.runtime_config.platform.system", return_value="Linux"),
        patch("control.runtime_config.platform.machine", return_value="x86_64"),
        pytest.raises(ValueError, match="native Linux binary for codex"),
    ):
        build("codex", command, "scoped", tmp_path)


def test_provider_cli_removed_while_running_fails_with_a_stable_code(tmp_path):
    binary = tmp_path / "claude"
    binary.write_text("#!" + sys.executable + "\n")
    binary.chmod(0o755)

    async def exercise():
        cfg = config(tmp_path)
        cfg["services"]["claude"].update(models=["sonnet"], mode="native")
        cfg["claude_models"] = {"sonnet": ["configured"]}
        cfg["claude"] = {"binary": str(binary)}
        service = Service(cfg)
        identity = ("a", service.config["clients"]["a"])
        request = {
            "project_id": "sem-projeto",
            "backend": "claude",
            "model": "sonnet",
            "effort": "configured",
            "prompt": "hello",
        }
        job = service.submit(identity, request)["job_id"]
        binary.unlink()
        worker = asyncio.create_task(service.worker())
        try:
            async with asyncio.timeout(5):
                while service.job(identity, job)["state"] in ("queued", "running"):
                    await asyncio.sleep(0.01)
            row = service.job(identity, job)
            assert row["state"] == "failed"
            assert json.loads(row["result"])["error"] == "cli_missing"
            # The worker survives and keeps serving the queue.
            follow_up = service.submit(identity, {**request, "prompt": "again"})["job_id"]
            async with asyncio.timeout(5):
                while service.job(identity, follow_up)["state"] in ("queued", "running"):
                    await asyncio.sleep(0.01)
            assert json.loads(service.job(identity, follow_up)["result"])["error"] == "cli_missing"
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            service.db.close()

    asyncio.run(exercise())
