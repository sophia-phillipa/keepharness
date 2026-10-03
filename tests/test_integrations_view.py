"""``GET /v1/integrations``: installed, allowed, effective and recently used connectors and plugins.

Provider CLIs are never run: the profiles live in a throwaway HOME and the usage evidence is
seeded straight into the harness database.
"""

import hashlib
import json
import logging
import time

import pytest
from starlette.testclient import TestClient

from agent_service import integrations_view
from agent_service.app import create_app
from agent_service.persistence.repositories import MessageRepository

SECRET = "SECRET-TOKEN-123"
DAY = 86400
ISOLATED = "Isolated conversations use no host connectors or plugins."
NOT_ALLOWED = "Not allowed for this provider. Change it in Settings › System › Providers."
REMOTE_PLUGIN = "Remote ChatGPT plugins bring their tools as Codex apps, which harness runs turn off."
GEMINI_READ_ONLY = "Read-only access turns connectors off for Gemini."
GEMINI_INTERNET = "Gemini connectors need the internet permission."
ASKS = "Each connector call asks for your approval."
NO_ASK = "Connector calls run without asking (full access)."


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


def service_entry(models, integrations=(), **permissions):
    return {
        "enabled": True,
        "models": list(models),
        "projects": ["p"],
        "integrations": list(integrations),
        "permissions": {"read": True, **permissions},
    }


@pytest.fixture
def settings(tmp_path):
    return {
        "state_dir": str(tmp_path / "state"),
        "origins": [],
        "projects": {"p": {}, "q": {}},
        "clients": {
            "a": {"sha256": hashlib.sha256(b"a").hexdigest(), "projects": ["p"]},
            "b": {"sha256": hashlib.sha256(b"b").hexdigest(), "projects": ["p", "q"]},
        },
        "services": {
            "codex": service_entry(["m"], ["mcp:github", "plugin:notes@market"]),
            "claude": service_entry(["m"], ["mcp:github", "plugin:notes@market"]),
            "gemini": service_entry(["m"], ["mcp:search"], internet=True),
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


def seed(client, job, tools, *, owner="a", project="p", backend="claude", when=None):
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


def test_maestro_resolves_to_its_coordinator(client, settings):
    settings["maestro_coordinator"] = {"backend": "claude", "model": "m"}
    body = view(client, backend="maestro", model="auto", access_mode="ask").json()
    assert (body["backend"], body["execution_mode"]) == ("claude", "native")
    assert body["effective_note"] == ASKS


def test_maestro_with_a_local_coordinator_is_isolated(client, settings):
    settings["maestro_coordinator"] = {"backend": "local", "model": "m"}
    body = view(client, backend="maestro", model="auto").json()
    assert (body["backend"], body["execution_mode"]) == ("local", "scoped")
    assert body["effective_note"] == ISOLATED


# -- inventory exposure -------------------------------------------------------------------------


def test_contract_shape_for_a_native_codex_route(client):
    body = view(client).json()
    assert body == {
        "backend": "codex",
        "execution_mode": "native",
        "access_mode": "ask",
        "effective_note": "",
        "window_days": 30,
        "warnings": [],
        "other_tools": [],
        "items": [
            {
                "id": "mcp:files",
                "kind": "mcp",
                "name": "files",
                "transport": "stdio",
                "status": "configured",
                "allowed": False,
                "effective": False,
                "reason": NOT_ALLOWED,
                "used": {"count": 0, "last_used": None, "tools": []},
            },
            {
                "id": "mcp:github",
                "kind": "mcp",
                "name": "github",
                "transport": "http",
                "status": "configured",
                "allowed": True,
                "effective": True,
                "reason": "",
                "used": {"count": 0, "last_used": None, "tools": []},
            },
            {
                "id": "plugin:notes@market",
                "kind": "plugin",
                "name": "notes@market",
                "transport": None,
                "status": "installed",
                "allowed": True,
                "effective": True,
                "reason": "",
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


def test_installed_plugin_catalog_replaces_the_profile_plugins(client, settings):
    settings["codex"]["plugin_inventory"] = ["plugin:notes@market", "plugin:extra@market"]
    items = items_by_id(view(client))
    assert items["plugin:extra@market"]["status"] == "installed"
    assert items["plugin:extra@market"]["allowed"] is False
    assert items["plugin:notes@market"]["allowed"] is True


def test_remote_chatgpt_plugins_are_not_effective_in_codex_runs(client, settings):
    # Harness Codex runs set features.apps=false; remote ChatGPT plugins bring their
    # tools as apps, so a real run never sees them (checked live 2026-10-03).
    settings["codex"]["plugin_inventory"] = ["plugin:github@openai-curated-remote", "plugin:notes@market"]
    settings["services"]["codex"]["integrations"] = [
        "plugin:github@openai-curated-remote",
        "plugin:notes@market",
    ]
    items = items_by_id(view(client))
    remote = items["plugin:github@openai-curated-remote"]
    assert remote["allowed"] is True and remote["effective"] is False
    assert remote["reason"] == REMOTE_PLUGIN
    assert items["plugin:notes@market"]["effective"] is True
    claude = items_by_id(view(client, backend="claude"))
    assert all(item["reason"] != REMOTE_PLUGIN for item in claude.values())


# -- allowed / effective / reason matrix --------------------------------------------------------


def effective_state(response):
    return {
        item_id: (item["allowed"], item["effective"], item["reason"])
        for item_id, item in items_by_id(response).items()
    }


def test_codex_native_enables_exactly_the_allowed_items(client):
    assert effective_state(view(client)) == {
        "mcp:files": (False, False, NOT_ALLOWED),
        "mcp:github": (True, True, ""),
        "plugin:notes@market": (True, True, ""),
    }


def test_deepseek_shares_the_codex_inventory(client, settings):
    settings["services"]["deepseek"] = service_entry(["m"], ["mcp:github"])
    state = effective_state(view(client, backend="deepseek"))
    assert state["mcp:github"] == (True, True, "")
    assert state["mcp:files"] == (False, False, NOT_ALLOWED)


def test_claude_native_ask_gates_every_connector_call(client):
    body = view(client, backend="claude", access_mode="ask").json()
    assert body["effective_note"] == ASKS
    assert effective_state(view(client, backend="claude"))["mcp:github"] == (True, True, "")


@pytest.mark.parametrize("backend", ["claude", "codex"])
@pytest.mark.parametrize("access_mode", ["auto", "full"])
def test_unrestricted_shell_runs_connectors_without_asking(client, settings, backend, access_mode):
    settings["services"][backend]["permissions"]["shell"] = True
    body = view(client, backend=backend, access_mode=access_mode).json()
    assert body["effective_note"] == NO_ASK


def test_ask_mode_never_claims_unattended_connector_calls(client, settings):
    settings["services"]["codex"]["permissions"]["shell"] = True
    assert view(client, backend="codex", access_mode="ask").json()["effective_note"] == ""


@pytest.mark.parametrize("backend", ["claude", "codex"])
def test_no_unattended_claim_without_the_shell_grant(client, backend):
    assert view(client, backend=backend, access_mode="full").json()["effective_note"] == ""


@pytest.mark.parametrize("backend", ["claude", "codex"])
def test_read_only_never_claims_unattended_connector_calls(client, settings, backend):
    settings["services"][backend]["permissions"]["shell"] = True
    body = view(client, backend=backend, access_mode="read_only").json()
    assert body["effective_note"] == ""
    assert effective_state(view(client, backend=backend, access_mode="read_only"))[
        "mcp:github"
    ] == (
        True,
        True,
        "",
    )


def test_a_restricted_adapter_never_runs_unattended(client, settings):
    settings["claude"]["unrestricted"] = False
    settings["services"]["claude"]["permissions"]["shell"] = True
    assert view(client, backend="claude", access_mode="full").json()["effective_note"] == ""


def test_gemini_with_internet_uses_allowed_connectors(client):
    assert effective_state(view(client, backend="gemini")) == {"mcp:search": (True, True, "")}


def test_gemini_connectors_need_the_internet_permission(client, settings):
    settings["services"]["gemini"]["permissions"]["internet"] = False
    assert effective_state(view(client, backend="gemini")) == {
        "mcp:search": (True, False, GEMINI_INTERNET)
    }


def test_project_grant_supplies_the_gemini_internet_permission(client, settings):
    settings["services"]["gemini"]["permissions"]["internet"] = False
    settings["projects"]["p"] = {"permissions": {"internet": True}}
    assert effective_state(view(client, backend="gemini"))["mcp:search"] == (True, True, "")


def test_gemini_read_only_turns_connectors_off(client):
    assert effective_state(view(client, backend="gemini", access_mode="read_only")) == {
        "mcp:search": (True, False, GEMINI_READ_ONLY)
    }


def test_gemini_item_that_is_not_allowed_says_so(client, settings):
    settings["services"]["gemini"]["integrations"] = []
    assert effective_state(view(client, backend="gemini")) == {
        "mcp:search": (False, False, NOT_ALLOWED)
    }


def test_local_backend_never_uses_host_connectors(client):
    body = view(client, backend="local").json()
    assert body["execution_mode"] == "scoped"
    assert body["effective_note"] == ISOLATED
    assert {item["effective"] for item in body["items"]} == {False}
    assert {item["reason"] for item in body["items"]} == {ISOLATED}


@pytest.mark.parametrize("backend", ["codex", "claude"])
def test_isolated_conversations_use_no_host_connectors(client, backend):
    body = view(client, backend=backend, execution_mode="scoped", access_mode="full").json()
    assert body["execution_mode"] == "scoped"
    assert body["effective_note"] == ISOLATED
    assert body["items"]
    for item in body["items"]:
        assert (item["effective"], item["reason"]) == (False, ISOLATED)
    # Allowed stays a provider setting: isolation does not rewrite it.
    assert {item["id"]: item["allowed"] for item in body["items"]}["mcp:github"] is True


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
    rows = service.message_repository.tool_usage("a", "p", "claude", since, 10)
    assert [(row["tool"], row["uses"]) for row in rows] == [("Read", 1)]


def test_usage_is_read_through_the_indexes(client):
    service = client.app.state.service
    plan = service.db.execute(
        "EXPLAIN QUERY PLAN " + MessageRepository.TOOL_USAGE, (0.0, "a", "p", "claude", 0.0, 10)
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
