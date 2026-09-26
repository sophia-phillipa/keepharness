"""Conversations saved before execution modes existed keep working after an upgrade (F-07).

0.5.0 stored no ``execution_mode`` and forced every service to ``mode: native``; its auto
conversations were saved with ``backend: maestro``, which is not a configured service.
"""

import json

import pytest
from test_workspaces import config

from agent_service.app import Service


def legacy_root(service, backend):
    payload = {"project_id": "p", "backend": backend, "model": "auto", "prompt": "old"}
    with service.db:
        service.db.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)",
            ("legacy", "p", "a", "completed", 1, json.dumps(payload), "{}", None, "legacy"),
        )
    return service.job(("a", service.config["clients"]["a"]), "legacy")


@pytest.mark.parametrize(
    ("backend", "service_spec", "expected"),
    [
        ("maestro", None, "native"),
        ("gemini", {"enabled": True, "models": ["auto"], "projects": ["p"]}, "native"),
        (
            "codex",
            {"enabled": True, "models": ["auto"], "projects": ["p"], "mode": "native"},
            "native",
        ),
        (
            "local",
            {"enabled": True, "models": ["auto"], "projects": ["p"], "mode": "native"},
            "scoped",
        ),
    ],
)
def test_legacy_conversation_falls_back_to_a_mode_its_backend_supports(
    tmp_path, backend, service_spec, expected
):
    cfg = config(tmp_path)
    if service_spec:
        cfg["services"][backend] = service_spec
    service = Service(cfg)
    try:
        identity = ("a", service.config["clients"]["a"])
        row = legacy_root(service, backend)
        assert service.conversation_execution_mode(row) == expected
        bound = service.bind_execution_mode(
            identity, {"project_id": "p", "backend": backend, "parent_job_id": "legacy"}
        )
        assert bound["execution_mode"] == expected
    finally:
        service.db.close()
