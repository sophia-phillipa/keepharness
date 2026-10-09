"""Review regressions for Codex diagnostics, rule trust and trust write isolation."""

import asyncio
import json

import pytest

from adapters.codex.state import CodexStateAdapter
from agent_service.errors import APIError
from control.provider_state import ProviderStateService


def test_changed_snapshot_is_not_hidden_by_equal_file_stats(tmp_path):
    """A source may change between its content read and its final stat."""
    from dataclasses import replace
    from types import SimpleNamespace

    from adapters.shared.provider_state import StateItem, StateSnapshot

    adapter = SimpleNamespace(watch_paths=lambda _: (), source_identity_paths=lambda _: ())
    service = ProviderStateService(tmp_path, lambda: [], adapters={"codex": adapter})
    item = StateItem("hook:one", "hook", "Hook", "user", False, "hooks.json", False)
    first = StateSnapshot("codex", "codex", None, (item,), "before", "fake")
    second = replace(first, items=(replace(item, enabled=True),), fingerprint="after")
    service._record("codex", "sem-projeto", "same-stat", first)
    service._record("codex", "sem-projeto", "same-stat", second)
    assert service._pending("codex", "sem-projeto")[0]["change"] == "changed"


def fake_codex(monkeypatch, result):
    async def ask(*args, **kwargs):
        return result, {}

    monkeypatch.setattr("adapters.codex.state._ask", ask)
    monkeypatch.setattr("adapters.codex.state._cli_version", lambda *_: "0.157.1")
    adapter = CodexStateAdapter()
    monkeypatch.setattr(adapter, "_binary", lambda: "fake")
    return adapter


@pytest.mark.parametrize("trust", ["untrusted", "trusted", None])
def test_codex_project_instructions_follow_native_trust(
    tmp_path, isolated_provider_homes, monkeypatch, trust
):
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("# Project instructions")
    (isolated_provider_homes / ".codex/AGENTS.md").write_text("# User instructions")
    config = {"projects": {str(project): {"trust_level": trust}}} if trust else {}
    adapter = fake_codex(monkeypatch, {"config": {"config": config, "layers": []}})
    rules = [item for item in adapter.read_state(project).items if item.kind == "instructions"]
    local = next(item for item in rules if item.scope == "project")
    assert local.enabled is (trust == "trusted")
    if trust != "trusted":
        assert local.details["status"] == "pending project trust"
    assert next(item for item in rules if item.scope == "user").enabled


def test_codex_hook_discovery_diagnostics_are_visible_and_redacted(tmp_path, monkeypatch):
    adapter = fake_codex(
        monkeypatch,
        {
            "config": {"layers": []},
            "hooks": {
                "data": [
                    {
                        "cwd": str(tmp_path),
                        "hooks": [],
                        "errors": [
                            {"path": "hooks.json", "message": "Bad handler --token hidden-error"}
                        ],
                        "warnings": ["Deprecated hook password=hidden-warning"],
                    }
                ]
            },
        },
    )
    warnings = "\n".join(adapter.read_state(tmp_path).warnings)
    assert "Bad handler" in warnings and "hooks.json" in warnings
    assert "Deprecated hook" in warnings
    assert "hidden-error" not in warnings and "hidden-warning" not in warnings


@pytest.mark.parametrize("trusted", [True, False])
def test_deepseek_trust_rejected_before_touching_other_providers(tmp_path, monkeypatch, trusted):
    service = ProviderStateService(tmp_path / "state", lambda: [{"id": "p", "root": str(tmp_path)}])
    touched = []

    def adapter(name):
        touched.append(name)
        raise AssertionError("An unsupported trust request must not reach any adapter")

    monkeypatch.setattr(service, "_adapter", adapter)
    with pytest.raises(APIError) as refused:
        asyncio.run(service.security_write("deepseek", "p", trusted=trusted))
    assert touched == []
    assert (refused.value.code, refused.value.status) == ("invalid_request", 400)
    assert not service.cache and not service.locks


def test_codex_hook_without_a_key_gets_a_location_id_not_a_hash_of_the_command(
    tmp_path, isolated_provider_homes, monkeypatch
):
    import hashlib

    hook = {
        "eventName": "preToolUse",
        "command": "check weakpw1",
        "sourcePath": str(tmp_path / "hooks.json"),
        "currentHash": "sha256:" + "ab" * 32,
    }
    adapter = fake_codex(
        monkeypatch, {"config": {"layers": []}, "hooks": {"data": [{"hooks": [hook]}]}}
    )
    (row,) = [x for x in adapter.read_state(tmp_path).items if x.kind == "hook"]
    location = f"{hook['sourcePath']}:preToolUse:0"
    assert row.id == "hook:" + hashlib.sha256(location.encode()).hexdigest()[:20]
    assert hook["currentHash"] not in json.dumps(row.details)
