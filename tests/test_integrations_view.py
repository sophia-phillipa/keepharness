"""``GET /v1/integrations``: installed, allowed, effective and recently used connectors and plugins.

Provider CLIs are never run: the profiles live in a throwaway HOME and the usage evidence is
seeded straight into the harness database.
"""

import hashlib
import json
import logging
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from agent_service import integrations_view
from agent_service.app import create_app
from agent_service.persistence.repositories import MessageRepository

SECRET = "SECRET-TOKEN-123"
DAY = 86400
NATIVE = "Availability and approvals follow the native CLI configuration and selected access mode."
ISOLATED = "Isolated conversations use no host connectors or plugins."
NOT_ALLOWED = "This provider does not use host connectors or plugins; Codex and Claude Code use their own native configuration."
GEMINI_READ_ONLY = "Read-only access turns connectors off for Gemini."


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A HOME whose provider profiles carry credentials in every field the view must not echo."""
    root = tmp_path / "home"
    (root / ".codex").mkdir(parents=True)
    (root / ".claude").mkdir()
    (root / ".gemini").mkdir()
    (root / ".codex/config.toml").write_text(
        f"""
[mcp_servers.github]
url = "https://api.example.test/mcp?key={SECRET}"
bearer_token_env_var = "GITHUB_TOKEN"
http_headers = {{ Authorization = "Bearer {SECRET}" }}

[mcp_servers.files]
command = "npx"
args = ["-y", "files-server", "--token", "{SECRET}"]
env = {{ API_KEY = "{SECRET}" }}

[mcp_servers.harness_effects_jira]
command = "internal-effects"

[plugins."notes@market"]
enabled = true
"""
    )
    (root / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "type": "http",
                        "url": f"https://user:{SECRET}@api.example.test/mcp",
                        "headers": {"Authorization": f"Bearer {SECRET}"},
                    },
                    "files": {
                        "command": "npx",
                        "args": ["-y", "files-server", "--token", SECRET],
                        "env": {"API_KEY": SECRET},
                    },
                    "harness_effects": {"command": "internal-effects"},
                }
            }
        )
    )
    (root / ".claude/settings.json").write_text(
        json.dumps({"enabledPlugins": {"notes@market": True, "docs@market": True}})
    )
    (root / ".gemini/settings.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "search": {
                        "httpUrl": f"https://search.example.test/mcp?token={SECRET}",
                        "headers": {"Authorization": f"Bearer {SECRET}"},
                    }
                }
            }
        )
    )
    monkeypatch.setenv("HOME", str(root))
    return root


def service_entry(models, **permissions):
    return {
        "enabled": True,
        "models": list(models),
        "projects": ["p"],
        "permissions": {"read": True, **permissions},
    }


@pytest.fixture
def settings(tmp_path):
    return {
        "state_dir": str(tmp_path / "state"),
        "origins": [],
        "projects": {"p": {}, "q": {}},
        "clients": {
            "local": {"sha256": hashlib.sha256(b"a").hexdigest(), "projects": ["p"]},
            "b": {"sha256": hashlib.sha256(b"b").hexdigest(), "projects": ["p", "q"]},
        },
        "services": {
            "codex": service_entry(["m"]),
            "claude": service_entry(["m"]),
            "gemini": service_entry(["m"], internet=True),
            "local": service_entry(["m"]),
        },
        "codex": {"unrestricted": True},
        "claude": {"unrestricted": True},
    }


@pytest.fixture
def client(home, settings, tmp_path):
    (tmp_path / "state").mkdir()
    with TestClient(create_app(settings), headers={"Authorization": "Bearer a"}) as test_client:
        yield test_client


def view(client, **query):
    query = {"project_id": "p", "backend": "codex", "model": "m", **query}
    return client.get("/v1/integrations", params=query)


def items_by_id(response):
    assert response.status_code == 200, response.text
    return {item["id"]: item for item in response.json()["items"]}


def seed(client, job, tools, *, owner="local", project="p", backend="claude", when=None):
    """One finished job with a ``tool_start`` event per entry of ``tools``."""
    service = client.app.state.service
    when = time.time() - 60 if when is None else when
    service.conversation_repository.insert(
        job,
        project,
        owner,
        "completed",
        when,
        json.dumps({"backend": backend, "project_id": project}),
        None,
        None,
        None,
    )
    for offset, tool in enumerate(tools):
        service.message_repository.add_event(
            job, when + offset, "tool_start", json.dumps({"tool": tool})
        )
    service.db.commit()
    return when


# -- access and validation, mirroring /v1/resources --------------------------------------------


def test_requires_authentication(client):
    client.headers.pop("Authorization")
    assert view(client).status_code == 401


def test_project_must_be_accessible_to_the_caller(client):
    response = view(client, project_id="q")
    assert (response.status_code, response.json()["code"]) == (403, "project_denied")


def test_service_must_be_enabled_for_the_project(client, settings):
    settings["services"]["codex"]["projects"] = []
    response = view(client)
    assert (response.status_code, response.json()["code"]) == (403, "service_project_denied")


def test_model_must_be_allowed(client):
    response = view(client, model="other")
    assert (response.status_code, response.json()["code"]) == (403, "model_denied")


@pytest.mark.parametrize(
    "query,code",
    [
        ({"backend": "gemini", "execution_mode": "scoped"}, "execution_mode_unsupported"),
        ({"execution_mode": "bogus"}, "execution_mode_unsupported"),
        ({"access_mode": "bogus"}, "invalid_access_mode"),
    ],
)
def test_modes_are_validated(client, query, code):
    response = view(client, **query)
    assert (response.status_code, response.json()["code"]) == (422, code)


def test_response_is_not_cached_and_read_only(client):
    assert view(client).headers["cache-control"] == "no-store"
    assert client.post("/v1/integrations").status_code == 405


# -- inventory exposure -------------------------------------------------------------------------


def test_contract_shape_for_a_native_codex_route(client):
    body = view(client).json()
    assert body == {
        "backend": "codex",
        "execution_mode": "native",
        "access_mode": "ask",
        "effective_note": NATIVE,
        "window_days": 30,
        "warnings": [],
        "other_tools": [],
        "elsewhere": [],
        "items": [
            {
                "id": "mcp:files",
                "kind": "mcp",
                "name": "files",
                "transport": "stdio",
                "status": "configured",
                "allowed": None,
                "effective": None,
                "reason": NATIVE,
                "used": {"count": 0, "last_used": None, "tools": []},
            },
            {
                "id": "mcp:github",
                "kind": "mcp",
                "name": "github",
                "transport": "http",
                "status": "configured",
                "allowed": None,
                "effective": None,
                "reason": NATIVE,
                "used": {"count": 0, "last_used": None, "tools": []},
            },
            {
                "id": "plugin:notes@market",
                "kind": "plugin",
                "name": "notes@market",
                "transport": None,
                "status": "installed",
                "allowed": None,
                "effective": None,
                "reason": NATIVE,
                "used": {"count": 0, "last_used": None, "tools": []},
            },
        ],
    }


@pytest.mark.parametrize("backend", ["codex", "claude", "gemini", "local"])
@pytest.mark.parametrize("access_mode", ["ask", "full", "read_only"])
def test_profile_secrets_are_never_returned(client, backend, access_mode):
    seed(client, "j1", ["mcp__github__create_issue"])
    response = view(client, backend=backend, access_mode=access_mode)
    assert response.status_code == 200, response.text
    for leaked in (SECRET, "api.example.test", "npx", "files-server", "GITHUB_TOKEN", "Bearer"):
        assert leaked not in response.text
    for item in response.json()["items"]:
        assert set(item) == {
            "id",
            "kind",
            "name",
            "transport",
            "status",
            "allowed",
            "effective",
            "reason",
            "used",
        }


def test_internal_effect_servers_are_hidden(client):
    codex = items_by_id(view(client))
    claude = items_by_id(view(client, backend="claude"))
    assert not [name for name in {**codex, **claude} if "harness_effects" in name]


def test_claude_inventory_has_connectors_then_plugins_sorted_by_name(client):
    ids = [item["id"] for item in view(client, backend="claude").json()["items"]]
    assert ids == ["mcp:files", "mcp:github", "plugin:docs@market", "plugin:notes@market"]


# -- allowed / effective / reason matrix --------------------------------------------------------


def effective_state(response):
    return {
        item_id: (item["allowed"], item["effective"], item["reason"])
        for item_id, item in items_by_id(response).items()
    }


def test_codex_native_defers_availability_to_the_cli(client):
    assert effective_state(view(client)) == {
        "mcp:files": (None, None, NATIVE),
        "mcp:github": (None, None, NATIVE),
        "plugin:notes@market": (None, None, NATIVE),
    }


@pytest.mark.parametrize("backend", ["claude", "codex"])
@pytest.mark.parametrize("mode", ["ask", "auto", "full", "read_only"])
@pytest.mark.parametrize("unrestricted,shell", [(True, True), (False, True), (True, False)])
def test_native_approvals_follow_cli_settings(client, settings, backend, mode, unrestricted, shell):
    settings[backend]["unrestricted"] = unrestricted
    settings["services"][backend]["permissions"]["shell"] = shell
    body = view(client, backend=backend, access_mode=mode).json()
    assert body["effective_note"] == NATIVE
    assert effective_state(view(client, backend=backend, access_mode=mode))["mcp:github"] == (
        None,
        None,
        NATIVE,
    )


@pytest.mark.parametrize("access_mode", ["ask", "auto", "full", "read_only"])
def test_deepseek_sees_no_host_connectors_in_its_own_home(client, settings, access_mode):
    settings["services"]["deepseek"] = service_entry(["m"])
    body = view(client, backend="deepseek", access_mode=access_mode).json()
    assert body["items"] == []
    assert body["warnings"] == [integrations_view.DEEPSEEK_OWN_HOME]


def test_deepseek_ignores_a_plugin_catalog_kept_in_the_settings(client, settings):
    settings["services"]["deepseek"] = service_entry(["m"])
    settings.setdefault("deepseek", {})["plugin_inventory"] = ["plugin:extra@market"]
    assert view(client, backend="deepseek").json()["items"] == []


def test_gemini_items_are_never_allowed_by_the_harness(client):
    assert effective_state(view(client, backend="gemini")) == {
        "mcp:search": (False, False, NOT_ALLOWED)
    }


def test_gemini_read_only_says_connectors_are_off(client):
    assert effective_state(view(client, backend="gemini", access_mode="read_only")) == {
        "mcp:search": (False, False, GEMINI_READ_ONLY)
    }


def test_local_backend_never_uses_host_connectors(client):
    body = view(client, backend="local").json()
    assert body["execution_mode"] == "scoped"
    assert body["effective_note"] == ISOLATED
    assert {item["effective"] for item in body["items"]} == {False}
    assert {item["reason"] for item in body["items"]} == {ISOLATED}


@pytest.mark.parametrize("backend", ["codex", "claude"])
def test_retired_cloud_scoped_integration_view_is_rejected(client, backend):
    response = view(client, backend=backend, execution_mode="scoped", access_mode="full")
    assert response.status_code == 422
    assert response.json()["code"] == "execution_mode_unsupported"


# -- usage evidence -----------------------------------------------------------------------------


def test_claude_mcp_tools_are_attributed_to_their_connector(client):
    when = seed(
        client,
        "j1",
        [
            "mcp__github__create_issue",
            "mcp__github__create_issue",
            "mcp__github__list_issues",
            "mcp__files__read",
            "Read",
            "Bash",
            "Bash",
        ],
    )
    body = view(client, backend="claude").json()
    used = {item["id"]: item["used"] for item in body["items"]}
    assert used["mcp:github"] == {
        "count": 3,
        "last_used": when + 2,
        "tools": ["create_issue", "list_issues"],
    }
    assert used["mcp:files"] == {"count": 1, "last_used": when + 3, "tools": ["read"]}
    assert used["plugin:notes@market"]["count"] == 0
    assert body["other_tools"] == [
        {"name": "Bash", "count": 2, "last_used": when + 6},
        {"name": "Read", "count": 1, "last_used": when + 4},
    ]


def test_connector_server_names_are_matched_the_way_claude_spells_them(client, home):
    config = json.loads((home / ".claude.json").read_text())
    config["mcpServers"]["my.server"] = {"command": "x"}
    (home / ".claude.json").write_text(json.dumps(config))
    seed(client, "j1", ["mcp__my_server__ping"])
    used = {item["id"]: item["used"] for item in view(client, backend="claude").json()["items"]}
    assert used["mcp:my.server"]["tools"] == ["ping"]


def test_at_most_five_tool_names_per_connector(client):
    tools = [f"mcp__github__tool_{index}" for index in range(7)]
    seed(client, "j1", [*tools, "mcp__github__tool_6"])
    github = items_by_id(view(client, backend="claude"))["mcp:github"]["used"]
    assert github["count"] == 8
    assert github["tools"] == ["tool_6", "tool_0", "tool_1", "tool_2", "tool_3"]


def test_events_outside_the_window_are_ignored(client):
    now = time.time()
    seed(client, "old", ["mcp__github__create_issue", "Read"], when=now - 31 * DAY)
    seed(client, "edge", ["mcp__github__list_issues"], when=now - 29 * DAY)
    body = view(client, backend="claude").json()
    github = {item["id"]: item for item in body["items"]}["mcp:github"]["used"]
    assert github["count"] == 1 and github["tools"] == ["list_issues"]
    assert body["other_tools"] == []


def test_other_projects_owners_and_providers_are_excluded(client):
    seed(client, "mine", ["mcp__github__create_issue"])
    seed(client, "elsewhere", ["mcp__github__create_issue", "Read"], project="q", owner="b")
    seed(client, "colleague", ["mcp__github__create_issue", "Read"], owner="b")
    seed(client, "other-engine", ["mcp__github__create_issue", "exec_command"], backend="codex")
    body = view(client, backend="claude").json()
    github = {item["id"]: item for item in body["items"]}["mcp:github"]["used"]
    assert github["count"] == 1
    assert body["other_tools"] == []


def test_codex_tool_names_carry_no_server_so_they_stay_unattributed(client):
    seed(client, "j1", ["exec_command", "exec_command", "apply_patch"], backend="codex")
    body = view(client, backend="codex").json()
    assert {item["used"]["count"] for item in body["items"]} == {0}
    assert [(tool["name"], tool["count"]) for tool in body["other_tools"]] == [
        ("exec_command", 2),
        ("apply_patch", 1),
    ]


def test_unknown_servers_are_listed_as_other_tools_and_internal_ones_hidden(client):
    seed(client, "j1", ["mcp__ghost__haunt", "mcp__harness_effects__prepare"])
    body = view(client, backend="claude").json()
    assert [tool["name"] for tool in body["other_tools"]] == ["mcp__ghost__haunt"]


def test_other_tools_keep_the_twenty_most_used(client):
    tools = [f"tool_{index:02d}" for index in range(25) for _ in range(index + 1)]
    seed(client, "j1", tools)
    names = [tool["name"] for tool in view(client, backend="claude").json()["other_tools"]]
    assert len(names) == 20
    assert names[0] == "tool_24" and names[-1] == "tool_05"


def test_tool_names_from_the_provider_are_bounded(client):
    seed(client, "j1", ["x" * 300, "mcp__github__" + "y" * 300])
    body = view(client, backend="claude").json()
    assert [len(tool["name"]) for tool in body["other_tools"]] == [120]
    github = {item["id"]: item for item in body["items"]}["mcp:github"]["used"]
    assert [len(name) for name in github["tools"]] == [120]


def test_malformed_tool_events_are_skipped(client):
    service = client.app.state.service
    seed(client, "j1", ["Read"])
    for data in ('{"tool": 7}', '{"tool": ""}', '{"other": 1}', "[]"):
        service.message_repository.add_event("j1", time.time(), "tool_start", data)
    service.db.commit()
    body = view(client, backend="claude").json()
    assert [tool["name"] for tool in body["other_tools"]] == ["Read"]


def test_jobs_created_before_the_window_and_its_margin_are_not_read(client):
    service = client.app.state.service
    now = time.time()
    since = now - integrations_view.WINDOW_DAYS * DAY
    seed(client, "ancient", [], when=since - 5 * DAY)
    seed(client, "overnight", [], when=since - 3600)
    for job in ("ancient", "overnight"):
        service.message_repository.add_event(
            job, now - DAY, "tool_start", json.dumps({"tool": "Read"})
        )
    service.db.commit()
    rows = service.message_repository.tool_usage("local", "p", "claude", since, 10)
    assert [(row["tool"], row["uses"]) for row in rows] == [("Read", 1)]


def test_usage_is_read_through_the_indexes(client):
    service = client.app.state.service
    plan = service.db.execute(
        "EXPLAIN QUERY PLAN " + MessageRepository.TOOL_USAGE, (0.0, "local", "p", "claude", 0.0, 10)
    ).fetchall()
    details = " ".join(row[3] for row in plan)
    assert "SCAN" not in details
    assert "events_job_terminal" in details


# -- failure handling ---------------------------------------------------------------------------


def test_unreadable_inventory_returns_warnings_not_an_error(client, monkeypatch, caplog):
    def broken():
        raise AttributeError(SECRET)

    monkeypatch.setattr(integrations_view, "inventory", broken)
    with caplog.at_level(logging.WARNING):
        response = view(client)
    body = response.json()
    assert response.status_code == 200, response.text
    assert body["items"] == []
    assert body["warnings"] == ["Could not read the connector and plugin inventory."]
    assert SECRET not in response.text + caplog.text


def test_hand_edited_profile_with_the_wrong_shape_is_not_a_server_error(client, home):
    (home / ".claude.json").write_text(json.dumps({"mcpServers": 5}))
    response = view(client, backend="claude")
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert response.json()["warnings"]


def test_missing_profiles_are_an_empty_inventory_without_warnings(client, home):
    for path in (".codex/config.toml", ".claude.json", ".claude/settings.json"):
        (home / path).unlink()
    body = view(client, backend="claude").json()
    assert (body["items"], body["warnings"]) == ([], [])


# -- tools connected on another provider ---------------------------------------------------------


@pytest.mark.parametrize(
    "item,key",
    [
        ({"id": "mcp:GitHub", "name": "GitHub"}, "github"),
        ({"id": "plugin:github@openai-curated", "name": "github@openai-curated"}, "github"),
        ({"id": "mcp:my_tool name", "name": "my_tool name"}, "my-tool-name"),
        ({"id": "plugin:Docs@market@x", "name": None}, "docs"),
        ({"id": "mcp:files"}, "files"),
        ({"name": "mcp:search"}, "search"),
        ({}, ""),
    ],
)
def test_family_key_normalises_the_connector_name(item, key):
    assert integrations_view.family_key(item) == key


def test_plugin_directory_javascript_family_cases_share_the_python_contract():
    fixture = json.loads((Path(__file__).parent / "fixtures" / "plugin-directory.json").read_text())
    for case in fixture["family_cases"]:
        assert integrations_view.family_key(case["item"]) == case["key"]


@pytest.fixture
def github_on_codex(home):
    """GitHub is a plugin on Codex and an installed MCP server on Claude Code."""
    with (home / ".codex/config.toml").open("a") as profile:
        profile.write('\n[plugins."github@openai-curated"]\nenabled = true\n')


@pytest.mark.parametrize("backend", ["claude", "codex", "gemini", "deepseek", "local"])
def test_nothing_is_reported_as_connected_elsewhere(client, settings, github_on_codex, backend):
    settings["services"].setdefault("deepseek", service_entry(["m"]))
    response = view(client, backend=backend)
    assert response.status_code == 200, response.text
    assert response.json()["elsewhere"] == []


def test_the_inventory_is_read_once_per_view(client, github_on_codex, monkeypatch):
    real = integrations_view.inventory
    calls = []
    monkeypatch.setattr(integrations_view, "inventory", lambda: calls.append(1) or real())
    assert view(client, backend="claude").status_code == 200
    assert len(calls) == 1


@pytest.mark.parametrize("backend", ["codex", "claude"])
@pytest.mark.parametrize("mode", ["ask", "auto", "full", "read_only"])
def test_native_inventory_does_not_invent_cli_availability(client, backend, mode):
    body = view(client, backend=backend, access_mode=mode).json()
    assert body["items"]
    assert body["warnings"] == []
    assert body["effective_note"] == NATIVE
    assert all(
        item["allowed"] is None and item["effective"] is None and item["reason"] == NATIVE
        for item in body["items"]
    )
    assert body["elsewhere"] == []


@pytest.mark.parametrize("backend", ["codex", "claude"])
def test_native_disabled_plugins_are_not_claimed_available(client, settings, home, backend):
    if backend == "codex":
        with (home / ".codex/config.toml").open("a") as profile:
            profile.write('\n[plugins."disabled@openai-remote"]\nenabled = false\n')
    else:
        (home / ".claude/settings.json").write_text(
            json.dumps({"enabledPlugins": {"disabled@market": False}})
        )
    settings[backend]["plugin_inventory"] = ["plugin:stale@market"]
    entries = items_by_id(view(client, backend=backend, access_mode="read_only"))
    assert "plugin:stale@market" not in entries
    disabled = next(item for item in entries.values() if item["name"].startswith("disabled@"))
    assert (disabled["allowed"], disabled["effective"], disabled["reason"]) == (None, None, NATIVE)
