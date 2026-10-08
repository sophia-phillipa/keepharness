# ruff: noqa: F401, F811
"""Trust and MCP writes must still address the project shown in the owner's UI."""

import pytest
from starlette.testclient import TestClient

from control.provider_state import ProviderStateService
from tests.test_admin_provider_state import (
    HEADERS, app, claude_dir, client, codex_home, get, project,
)
from tests.test_provider_state_codex import seed
from tests.test_provider_trust import setup_claude


@pytest.mark.parametrize("surface", ["admin", "harness"])
@pytest.mark.parametrize("action", ["trust", "mcp-approvals"])
@pytest.mark.parametrize("context", ["missing", "wrong", "rebound", "current", "alias"])
def test_security_write_requires_rendered_project_context(
    app, client, codex_home, claude_dir, project, tmp_path, monkeypatch,
    surface, action, context,
):
    seed(codex_home)
    setup_claude(app, claude_dir, project)
    manager = app.state.manager
    harness = None
    if surface == "admin":
        target, prefix, headers = client, "/api", HEADERS
        rebind = lambda root: manager.settings["projects"][0].update(root=str(root))
    else:
        from agent_service.app import create_app
        from tests.test_api_security import owner_config, owner_cookie

        cfg = owner_config(tmp_path, projects={"p": {"root": str(project)}, "sem-projeto": {}})
        cfg["clients"]["local"]["projects"] = ["p", "sem-projeto"]
        harness = create_app(cfg)
        service = harness.state.service
        service.provider_state = ProviderStateService(
            manager.state,
            lambda: [{"id": key, **value} for key, value in service.config["projects"].items()],
            manager.provider_state.adapters,
            track_notices=False,
        )
        target = TestClient(harness, base_url="http://127.0.0.1:8095", client=("127.0.0.1", 42000))
        target.cookies.update(owner_cookie(cfg))
        prefix, headers = "/v1", {}
        rebind = lambda root: service.config["projects"]["p"].update(root=str(root))
    try:
        if context == "alias":
            alias = tmp_path / "project-alias"
            alias.symlink_to(project, target_is_directory=True)
            rebind(alias)
        response = target.get(prefix + "/provider-state", params={"provider": "claude", "project_id": "p"})
        assert response.status_code == 200, response.text
        rendered_root = response.json()["snapshot"]["project_root"]
        writes = []
        for adapter in manager.provider_state.adapters.values():
            for name in ("trust_project", "set_project_server_approval"):
                if not hasattr(adapter, name):
                    continue
                original = getattr(adapter, name)

                def record(*args, original=original, **kwargs):
                    writes.append(args[0])
                    return original(*args, **kwargs)

                monkeypatch.setattr(adapter, name, record)
        body = {"provider": "claude", "project_id": "p"}
        if context != "missing":
            body["expected_project_root"] = rendered_root if context != "wrong" else str(tmp_path)
        if context == "rebound":
            other = tmp_path / "other"
            other.mkdir()
            (other / ".mcp.json").write_bytes((project / ".mcp.json").read_bytes())
            rebind(other)
        if action == "mcp-approvals":
            body.update(server="marker", approved=True)
        response = target.post(prefix + "/provider-state/" + action, headers=headers, json=body)
        expected = 400 if context == "missing" else 200 if context in ("current", "alias") else 409
        assert response.status_code == expected, response.text
        if context in ("current", "alias"):
            assert writes and all(root == project for root in writes)
        else:
            assert writes == [], "a stale or absent context must fail before any CLI writer"
        if context == "rebound":
            fresh = target.get(prefix + "/provider-state", params={"provider": "claude", "project_id": "p"})
            assert fresh.status_code == 200, fresh.text
            assert fresh.json()["snapshot"]["project_root"] == str(other)
            body["expected_project_root"] = fresh.json()["snapshot"]["project_root"]
            retried = target.post(prefix + "/provider-state/" + action, headers=headers, json=body)
            assert retried.status_code == 200, retried.text
            assert writes and all(root == other for root in writes)
    finally:
        if harness is not None:
            target.close()
            harness.state.service.db.close()
