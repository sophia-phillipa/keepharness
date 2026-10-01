import asyncio
import json
import os
import sys

from adapters.shared.process import StderrCapture, child_environment
from agent_service.log_config import redact
from agent_service.secret_vault import SecretVault, execution_environment, redact_secrets


def test_registered_values_redacted_on_nested_surfaces(tmp_path):
    store = SecretVault(tmp_path / "vault")
    store.set("demo", {"password": "fake-private-value"})
    assert "fake-private-value" not in json.dumps(
        redact_secrets({"answer": ["fake-private-value"]})
    )
    assert "fake-private-value" not in redact("exception fake-private-value")
    capture = StderrCapture()
    capture.buffer.extend(b"opaque fake-private-value")
    assert "fake-private-value" not in capture.text()


def test_environment_isolated_between_concurrent_subprocesses():
    async def child(value):
        with execution_environment({"SYNTHETIC_CREDENTIAL": value}):
            await asyncio.sleep(0)
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                'import os; print(os.environ["SYNTHETIC_CREDENTIAL"])',
                env=child_environment(),
                stdout=asyncio.subprocess.PIPE,
            )
            output, _ = await process.communicate()
            return output.decode().strip()

    async def scenario():
        assert await asyncio.gather(child("fake-one"), child("fake-two")) == [
            "fake-one",
            "fake-two",
        ]

    asyncio.run(scenario())
    assert "SYNTHETIC_CREDENTIAL" not in os.environ
    assert "SYNTHETIC_CREDENTIAL" not in child_environment()


def test_short_secrets_redact_keys_values_and_split_stream(tmp_path):
    from agent_service.secret_vault import SecretStream

    store = SecretVault(tmp_path / "vault")
    store.set("demo", {"short": "x", "token": "fake-private-value"})
    assert redact_secrets({"x": "sample", "answer": "x"}) == {
        "[redacted]": "sample",
        "answer": "[redacted]",
    }
    assert redact_secrets("embedded x credential") == "embedded [redacted] credential"
    stream = SecretStream()
    assert stream.feed("answer", "start fake-pri") == "start "
    assert stream.feed("answer", "vate-value end") == "[redacted] end"


def test_synthetic_identity_inference_key_is_explicit_only(monkeypatch):
    from dataclasses import replace

    from adapters.shared import process

    monkeypatch.setattr(process, "PRODUCT", replace(process.PRODUCT, env_prefix="SYNTHETIC"))
    monkeypatch.setenv("SYNTHETIC_API_KEY", "host-private-value")
    assert "SYNTHETIC_API_KEY" not in process.child_environment(provider="deepseek")
    assert process.child_environment(
        {"SYNTHETIC_API_KEY": "explicit-private-value"}, provider="deepseek"
    ) == {"SYNTHETIC_API_KEY": "explicit-private-value"}


def test_mediated_names_are_removed_from_inherited_environment(monkeypatch):
    monkeypatch.setenv("DEMO_TOKEN", "host-private-value")
    with execution_environment({"DEMO_TOKEN": "conflicting-value"}, blocked=["DEMO_TOKEN"]):
        assert "DEMO_TOKEN" not in child_environment()
    assert os.environ["DEMO_TOKEN"] == "host-private-value"
