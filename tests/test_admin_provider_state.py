"""Admin routes for the CLI state (issue #21): ``GET`` and ``POST /api/provider-state``.

Fake homes only (the autouse ``isolated_provider_homes`` fixture) and the fake ``codex`` and
``claude`` CLIs first on PATH; the Claude adapter gets a temporary managed-settings folder.
"""

import asyncio
import json
import logging
import os
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from adapters.claude.state import ClaudeStateAdapter
from adapters.codex.state import CodexStateAdapter
from adapters.shared.provider_state import (
    ProviderCommandError,
    ProviderStateSchemaError,
    ProviderStateUnsupportedError,
    ProviderStateValidationError,
)
from control.provider_state import COALESCE_SECONDS, ProviderStateService, error_response
from control.server import create_app
from tests.owner_session import sign_in
from tests.test_provider_state_codex import DOCS, GITHUB, calls, seed, writes

FIXTURES = Path(__file__).parent / "fixtures"
HEADERS = {"X-Harness-Admin": "1"}
SECRET = "oauth-secret-value-9876"
CLAUDE_PLUGIN = "plugin:x@y"


@pytest.fixture
def codex_home(isolated_provider_homes, monkeypatch):
    monkeypatch.setenv(
        "PATH",
        f"{FIXTURES / 'fake-codex'}{os.pathsep}{FIXTURES / 'fake-claude'}{os.pathsep}{os.environ['PATH']}",
    )
    return isolated_provider_homes / ".codex"


@pytest.fixture
def claude_dir(isolated_provider_homes, codex_home):
    return isolated_provider_homes / ".claude"


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    return folder


@pytest.fixture
def app(tmp_path, codex_home, project):
    app = create_app(tmp_path / "state")
    manager = app.state.manager
    manager.settings["projects"] = [{"id": "p", "root": str(project)}]
    manager.provider_state.adapters = {
        "codex": CodexStateAdapter(),
        "claude": ClaudeStateAdapter(manager.state, tmp_path / "managed"),
    }
    return app


@pytest.fixture
def client(app):
    client = TestClient(app, base_url="http://127.0.0.1:8094")
    sign_in(client).get("/")
    return client


def get(client, provider="codex", project_id="sem-projeto"):
    return client.get(
        "/api/provider-state", params={"provider": provider, "project_id": project_id}
    )


def post(client, **fields):
    body = {
        "provider": "codex",
        "project_id": "sem-projeto",
        "item_id": GITHUB,
        "scope": "user",
        "enabled": True,
        "fingerprint": "unused",
        **fields,
    }
    return client.post("/api/provider-state", headers=HEADERS, json=body)


def fresh_fingerprint(client, provider="codex"):
    return get(client, provider).json()["snapshot"]["fingerprint"]


def fresh_fingerprint_for(client, project_id, provider="codex"):
    return get(client, provider, project_id).json()["snapshot"]["fingerprint"]


def row(body, item_id):
    return next(item for item in body["snapshot"]["items"] if item["id"] == item_id)


def test_get_codex_snapshot_shape(client, codex_home):
    seed(codex_home)
    response = get(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"snapshot", "external_changes"} and body["external_changes"] == []
    snapshot = body["snapshot"]
    assert (snapshot["provider"], snapshot["engine"]) == ("codex", "codex")
    assert snapshot["fingerprint"] and snapshot["cli_version"] and snapshot["warnings"] == []
    github = row(body, GITHUB)
    assert set(github) == {
        "id",
        "kind",
        "name",
        "scope",
        "enabled",
        "source",
        "writable",
        "reason",
        "affects",
    }
    assert (github["kind"], github["scope"], github["enabled"]) == ("plugin", "user", False)
    assert isinstance(github["affects"], list)


def test_get_claude_snapshot_shape(client, claude_dir):
    (claude_dir / "settings.json").write_text(json.dumps({"enabledPlugins": {"x@y": True}}))
    body = get(client, "claude").json()
    assert (body["snapshot"]["provider"], body["snapshot"]["engine"]) == ("claude", "claude")
    item = row(body, CLAUDE_PLUGIN)
    assert (item["enabled"], item["scope"], item["writable"]) == (True, "user", True)
    assert isinstance(item["affects"], list) and body["external_changes"] == []


@pytest.mark.parametrize("provider", ["deepseek", "x", ""])
def test_get_unknown_provider_404(client, provider):
    response = get(client, provider)
    assert (response.status_code, response.json()) == (404, {"error": "provider_unknown"})


def test_unknown_project_404(client):
    assert get(client, project_id="nope").json() == {"error": "project_unknown"}
    assert get(client, project_id="nope").status_code == 404
    assert post(client, project_id="nope").status_code == 404


def test_get_sem_projeto_has_null_root(client, codex_home, project):
    seed(codex_home)
    assert get(client).json()["snapshot"]["project_root"] is None
    assert get(client, project_id="p").json()["snapshot"]["project_root"] == str(project)


def test_post_ok_returns_fresh_snapshot(client, codex_home):
    seed(codex_home)
    response = post(client, enabled=True, fingerprint=fresh_fingerprint(client))
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"snapshot"} and row(body, GITHUB)["enabled"] is True
    (write,) = writes(codex_home)
    assert write["params"]["edits"][0]["keyPath"] == "plugins.github@openai-curated.enabled"
    assert get(client).json()["snapshot"]["fingerprint"] == body["snapshot"]["fingerprint"]


def test_post_conflict_returns_409_with_fresh_snapshot(client, codex_home):
    seed(codex_home)
    stale = fresh_fingerprint(client)
    assert post(client, item_id=DOCS, enabled=False, fingerprint=stale).status_code == 200
    response = post(client, enabled=True, fingerprint=stale)
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "provider_state_conflict" and body["external_changes"] == []
    assert body["snapshot"]["fingerprint"] != stale
    assert row(body, DOCS)["enabled"] is False and row(body, GITHUB)["enabled"] is False
    assert len(writes(codex_home)) == 1  # the stale write never reached the CLI


def test_post_unsupported_422(client, codex_home):
    seed(codex_home)
    response = post(
        client, item_id="skill:/home/evil/SKILL.md", fingerprint=fresh_fingerprint(client)
    )
    body = response.json()
    assert response.status_code == 422 and body["error"] == "provider_state_write_unsupported"
    assert 0 < len(body["message"]) <= 300 and set(body) == {"error", "message"}
    assert writes(codex_home) == []


class Failing(CodexStateAdapter):
    """The real adapter for reads; ``set_enabled`` raises what the test hands in."""

    def __init__(self, error):
        self.error = error

    def set_enabled(self, *args, **kwargs):
        raise self.error


def test_post_validation_failed_422(client, app):
    error = ProviderStateValidationError("x" * 500, errors=("/secret/path is invalid",))
    app.state.manager.provider_state.adapters["codex"] = Failing(error)
    response = post(client)
    body = response.json()
    assert response.status_code == 422 and body["error"] == "provider_state_validation_failed"
    assert body["message"] == "x" * 300 and set(body) == {"error", "message"}
    assert "/secret/path" not in response.text


def test_post_version_untested_422(client, claude_dir):
    (claude_dir / "skills" / "deploy").mkdir(parents=True)
    (claude_dir / "skills" / "deploy" / "SKILL.md").write_text("---\nname: deploy\n---\nBody.\n")
    (claude_dir / "fake-claude-version").write_text("2.2.0")
    fingerprint = fresh_fingerprint(client, "claude")
    response = post(
        client, provider="claude", item_id="skill:deploy", enabled=False, fingerprint=fingerprint
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "provider_state_version_untested"


def test_post_unreadable_422(client, app):
    app.state.manager.provider_state.adapters["codex"] = Failing(
        ProviderStateSchemaError("the codex CLI is not on PATH")
    )
    response = post(client)
    assert response.status_code == 422
    assert response.json() == {
        "error": "provider_state_unreadable",
        "message": "the codex CLI is not on PATH",
    }


def test_get_unreadable_422(client, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    response = get(client)
    assert response.status_code == 422
    assert response.json()["error"] == "provider_state_unreadable"


def test_post_command_failed_502_redacted(client, claude_dir, caplog):
    (claude_dir / ".claude.json").write_text(
        json.dumps(
            {"oauthAccount": {"emailAddress": SECRET}, "mcpServers": {"s": {"env": {"T": SECRET}}}}
        )
    )
    (claude_dir / "fake-claude-fail").write_text("")
    fingerprint = fresh_fingerprint(client, "claude")
    with caplog.at_level(logging.DEBUG):
        response = post(client, provider="claude", item_id=CLAUDE_PLUGIN, fingerprint=fingerprint)
    body = response.json()
    assert response.status_code == 502
    assert set(body) == {"error", "provider_message"} and body["error"] == "provider_command_failed"
    assert body["provider_message"] and len(body["provider_message"]) <= 300
    home = str(claude_dir.parent)
    for leaked in (SECRET, home):
        assert leaked not in response.text
        assert leaked not in caplog.text
    assert body["provider_message"] not in caplog.text
    assert "provider_command_failed" in caplog.text


def test_post_requires_admin_header(client, codex_home):
    seed(codex_home)
    response = client.post("/api/provider-state", json={"provider": "codex"})
    assert response.status_code == 400 and writes(codex_home) == []


def test_routes_stay_behind_the_admin_guard(app, codex_home):
    seed(codex_home)
    anonymous = TestClient(app, base_url="http://127.0.0.1:8094")
    assert anonymous.get("/api/provider-state?provider=codex").status_code == 401
    foreign = TestClient(app, base_url="http://evil.example:8094")
    assert foreign.get("/api/provider-state?provider=codex").status_code == 403
    assert calls(codex_home) == []


@pytest.mark.parametrize(
    "change",
    [
        {"item_id": 5},
        {"item_id": ""},
        {"item_id": "x" * 301},
        {"scope": "everywhere"},
        {"scope": None},
        {"enabled": "yes"},
        {"enabled": 1},
        {"fingerprint": ""},
        {"fingerprint": "x" * 201},
        {"fingerprint": 7},
        {"project_id": 3},
        {"project_id": None},
    ],
)
def test_post_invalid_fields_400(client, codex_home, change):
    seed(codex_home)
    response = post(client, **change)
    assert (response.status_code, response.json()) == (400, {"error": "invalid_request"})
    assert writes(codex_home) == []


def test_post_missing_field_400(client):
    body = {"provider": "codex", "project_id": "sem-projeto", "item_id": GITHUB}
    response = client.post("/api/provider-state", headers=HEADERS, json=body)
    assert response.status_code == 400


def test_post_command_failed_502_hides_secrets_from_stderr(client, claude_dir):
    (claude_dir / "fake-claude-fail").write_text(
        "denied for Bearer abc123SECRETvalue with sk-ant-api03-abcdefghijklmnopqrstuv"
        f" in {claude_dir}/settings.json"
    )
    fingerprint = fresh_fingerprint(client, "claude")
    response = post(client, provider="claude", item_id=CLAUDE_PLUGIN, fingerprint=fingerprint)
    assert response.status_code == 502 and response.json()["provider_message"]
    for leaked in ("abc123SECRETvalue", "sk-ant-api03", str(claude_dir)):
        assert leaked not in response.text


def test_error_response_strips_paths_from_provider_message():
    exc = ProviderCommandError("codex failed at /home/sophia/.codex/config.toml (~/x, ./y)")
    message = json.loads(error_response(exc).body)["provider_message"]
    assert "/home" not in message and "config.toml" not in message and "<path>" in message


def test_post_checks_provider_and_project_before_the_fields(client):
    assert post(client, provider="deepseek", item_id=5).status_code == 404
    assert post(client, project_id="nope", enabled="yes").status_code == 404
    assert post(client, project_id=3).status_code == 400


def test_write_in_a_project_drops_the_providers_other_cache_keys(client, app, codex_home):
    seed(codex_home)
    clocked(app)
    get(client)  # caches (codex, sem-projeto)
    before = len(sessions(codex_home))
    post(client, project_id="p", fingerprint=fresh_fingerprint_for(client, "p"))
    after_write = len(sessions(codex_home))
    get(client)
    assert len(sessions(codex_home)) > after_write >= before  # sem-projeto was read again


def test_failed_write_drops_the_providers_cache(client, app, codex_home):
    seed(codex_home)
    clocked(app)
    service = app.state.manager.provider_state
    get(client)
    assert ("codex", "sem-projeto") in service.cache
    service.adapters["codex"] = Failing(ProviderStateUnsupportedError("no"))
    assert post(client).status_code == 422
    assert not [key for key in service.cache if key[0] == "codex"]


def clocked(app):
    now = [100.0]
    service = ProviderStateService(
        app.state.manager.state,
        lambda: app.state.manager.settings["projects"],
        app.state.manager.provider_state.adapters,
        clock=lambda: now[0],
    )
    app.state.manager.provider_state = service
    return now


def sessions(home):
    return [c for c in calls(home) if c["method"] == "initialize"]


def test_get_coalesces_within_5s(client, app, codex_home):
    seed(codex_home)
    now = clocked(app)
    first = get(client).json()
    assert len(sessions(codex_home)) == 1
    now[0] += COALESCE_SECONDS - 0.1
    assert get(client).json() == first and len(sessions(codex_home)) == 1
    now[0] += 0.2
    get(client)
    assert len(sessions(codex_home)) == 2


def test_post_refreshes_cache(client, app, codex_home):
    seed(codex_home)
    clocked(app)
    stale = fresh_fingerprint(client)
    written = post(client, enabled=True, fingerprint=stale).json()
    sessions_after_write = len(sessions(codex_home))
    again = get(client).json()
    assert again == {**again, "snapshot": written["snapshot"]}
    assert again["snapshot"]["fingerprint"] != stale
    assert len(sessions(codex_home)) == sessions_after_write  # served from the cache


def test_adapter_calls_run_off_loop(client, app):
    seen = []

    def where():
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return "off-loop"
        return "on-loop"

    class Spy(CodexStateAdapter):
        def __init__(self):
            pass

        def read_state(self, project_root):
            seen.append(where())
            raise ProviderStateSchemaError("stop here")

        def set_enabled(self, *args, **kwargs):
            seen.append(where())
            raise ProviderStateUnsupportedError("stop here")

    app.state.manager.provider_state.adapters["codex"] = Spy()
    assert get(client).status_code == 422 and post(client).status_code == 422
    assert seen == ["off-loop"] * 2
