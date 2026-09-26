"""Live resource discovery, identities and admission; no model inference."""

import pytest

from agent_service import resources


def put(root, path, text):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return target


def cfg(root, backend="codex"):
    return {"projects": {"p": {"root": str(root)}}, "services": {backend: {"mode": "native"}}}


def test_live_engine_filter_and_scope_order(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = tmp_path / "project"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    put(
        root,
        ".codex/agents/reviewer.toml",
        'name="reviewer"\ndescription="Project review"\ndeveloper_instructions="Review"',
    )
    put(
        home,
        ".codex/agents/reviewer.toml",
        'name="reviewer"\ndescription="Global review"\ndeveloper_instructions="Review"',
    )
    put(root, ".claude/agents/other.md", "---\nname: other\n---\nReview")
    before = resources.discover(cfg(root), "p", "codex")
    assert [(i["name"], i["scope"]) for i in before["items"]] == [
        ("reviewer", "project"),
        ("reviewer", "global"),
    ]
    put(root, ".agents/skills/new/SKILL.md", "---\nname: new\ndescription: New skill\n---\nDo work")
    after = resources.discover(cfg(root), "p", "codex")
    assert any(i["name"] == "new" for i in after["items"])
    assert all(i["origin"] != "claude" for i in after["items"])


def test_global_symlink_dedup_and_project_escape(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setenv("HOME", str(home))
    skill = put(home, ".codex/skills/one/SKILL.md", "---\nname: one\n---\nDo it")
    alias = home / ".agents/skills/one"
    alias.parent.mkdir(parents=True)
    alias.symlink_to(skill.parent)
    escape = root / ".agents/skills/leak"
    escape.parent.mkdir(parents=True)
    escape.symlink_to(home)
    items = resources.discover(cfg(root), "p", "codex")["items"]
    assert [i["name"] for i in items] == ["one"]


def test_selection_revision_removed_and_wrong_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    path = put(root, ".agents/skills/test/SKILL.md", "---\nname: test\n---\nDo it")
    config = cfg(root)
    item = resources.discover(config, "p", "codex")["items"][0]
    selection = {k: item[k] for k in ("id", "revision")}
    selection["token"] = "/test"
    data = {
        "project_id": "p",
        "backend": "codex",
        "prompt": "/test now",
        "resource_selections": [selection],
    }
    assert resources.resolve(config, data)[0]["name"] == "test"
    path.write_text("---\nname: test\n---\nChanged")
    with pytest.raises(resources.ResourceError, match="resource_changed"):
        resources.resolve(config, data)
    path.unlink()
    with pytest.raises(resources.ResourceError, match="resource_unavailable"):
        resources.resolve(config, data)


def test_no_read_or_scoped_and_reserved_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    put(root, ".codex/agents/a.toml", 'name="a"\ndeveloper_instructions="Do"')
    config = cfg(root)
    config["services"]["codex"]["mode"] = "scoped"
    assert not resources.discover(config, "p", "codex")["items"]
    for prefix in ("@@a", "//skill"):
        with pytest.raises(resources.ResourceError, match="tail_resources_unavailable"):
            resources.resolve(config, {"project_id": "p", "backend": "codex", "prompt": prefix})


def test_command_expansion_no_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    put(root, ".gemini/commands/review.toml", 'description="Review"\nprompt="Review {{args}}"')
    item = resources.discover(cfg(root, "gemini"), "p", "gemini")["items"][0]
    selection = {"id": item["id"], "revision": item["revision"], "token": "/review"}
    data = {
        "project_id": "p",
        "backend": "gemini",
        "prompt": "/review module.py",
        "resource_selections": [selection],
    }
    items = resources.resolve(cfg(root, "gemini"), data)
    assert "Review module.py" in resources.prepare_prompt(data["prompt"], items)
    put(root, ".gemini/commands/review.toml", 'prompt="!{touch forbidden}"')
    new = resources.discover(cfg(root, "gemini"), "p", "gemini")["items"][0]
    assert not new["selectable"]


def test_api_permissions_and_revalidation_before_queue(tmp_path, monkeypatch):
    from starlette.testclient import TestClient
    from test_workspaces import config

    from agent_service.app import create_app

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    conf = config(tmp_path)
    conf["projects"]["p"]["root"] = str(tmp_path / "project")
    conf["services"]["codex"]["mode"] = "native"
    path = put(tmp_path / "project", ".agents/skills/a/SKILL.md", "---\nname: a\n---\nDo")
    app = create_app(conf)
    client = TestClient(app, headers={"Authorization": "Bearer a"})
    try:
        url = "/v1/resources?project_id=p&backend=codex&model=gpt-6-astra"
        response = client.get(url)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        item = response.json()["items"][0]
        assert client.get(url.replace("project_id=p", "project_id=other")).status_code == 403
        assert client.get(url.replace("model=gpt-6-astra", "model=other")).status_code == 403
        path.write_text("---\nname: a\n---\nUpdated")
        data = {
            "project_id": "p",
            "backend": "codex",
            "model": "gpt-6-astra",
            "effort": "low",
            "prompt": "/a",
            "resource_selections": [
                {"id": item["id"], "revision": item["revision"], "token": "/a"}
            ],
        }
        assert client.post("/v1/jobs", json=data).status_code == 409
        assert app.state.service.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    finally:
        client.close()
        app.state.service.db.close()


def test_codex_native_skill_reload_and_structured_input(tmp_path):
    import asyncio

    from Adapters.codex.native import resource_inputs
    from agent_service.tools import ToolError

    path = put(tmp_path, "skill/SKILL.md", "---\nname: test\n---\nDo")
    project = {
        "_resources": [
            {
                "kind": "skill",
                "name": "test",
                "source": str(path),
                "revision": __import__("hashlib").sha256(path.read_bytes()).hexdigest(),
            }
        ]
    }

    class RPC:
        async def call(self, method, params):
            assert method == "skills/list" and params == {
                "cwds": [str(tmp_path)],
                "forceReload": True,
            }
            return {"data": [{"skills": [{"name": "test", "path": str(path), "enabled": True}]}]}

    assert asyncio.run(resource_inputs(RPC(), project, tmp_path)) == [
        {"type": "skill", "name": "test", "path": str(path)}
    ]
    path.write_text("changed")
    with pytest.raises(ToolError, match="resource_changed"):
        asyncio.run(resource_inputs(RPC(), project, tmp_path))


def test_claude_skill_tool_is_only_enabled_for_explicit_selection(tmp_path):
    from unittest.mock import patch

    from Adapters.claude.native import build_command

    with (
        patch("Adapters.claude.native.configurations", return_value={"claude": {}}),
        patch("Adapters.claude.native.inventory", return_value={"claude": []}),
    ):
        basic = build_command(
            {"binary": "claude"}, "sonnet", tmp_path, {"read": True}, [], "ask", []
        )
        selected = build_command(
            {"binary": "claude", "resource_skills": ["review"]},
            "sonnet",
            tmp_path,
            {"read": True},
            [],
            "ask",
            [],
        )
    assert "Skill" not in basic[basic.index("--tools") + 1].split(",")
    assert "Skill" in selected[selected.index("--tools") + 1].split(",")
    assert "Agent" not in selected[selected.index("--tools") + 1].split(",")


def test_execution_rechecks_selection_and_preserves_stored_prompt(tmp_path, monkeypatch):
    import asyncio
    import json
    from unittest.mock import AsyncMock, patch

    from test_workspaces import config

    from agent_service.app import APIError, Service

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    conf = config(tmp_path)
    root = tmp_path / "project"
    conf["projects"]["p"]["root"] = str(root)
    conf["services"]["codex"]["mode"] = "native"
    conf["codex"] = {"binary": "unused"}
    path = put(root, ".agents/skills/a/SKILL.md", "---\nname: a\n---\nDo")
    service = Service(conf)
    identity = ("a", conf["clients"]["a"])
    try:
        item = service.resource_catalog(identity, "p", "codex", "gpt-6-astra")["items"][0]
        data = {
            "project_id": "p",
            "backend": "codex",
            "model": "gpt-6-astra",
            "effort": "low",
            "prompt": "/a work",
            "resource_selections": [
                {"id": item["id"], "revision": item["revision"], "token": "/a"}
            ],
        }
        job = service.submit(identity, data)["job_id"]
        row = service.job(identity, job)
        assert json.loads(row["payload"])["prompt"] == "/a work"
        with patch(
            "agent_service.app.adapters.run_native", AsyncMock(return_value={"answer": "done"})
        ) as run:
            asyncio.run(service.infer(row, data))
            assert "$a work" in run.call_args.args[1]
            assert run.call_args.args[3]["_resources"][0]["source"] == str(path)
        path.unlink()
        with patch("agent_service.app.adapters.run_native", AsyncMock()) as run:
            with pytest.raises(APIError, match="resource_unavailable"):
                asyncio.run(service.infer(row, data))
            run.assert_not_called()
    finally:
        service.db.close()


def test_malformed_reference_and_disabled_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "project"
    config = cfg(root)
    path = put(root, ".agents/skills/a/SKILL.md", "---\nname: a\n---\nDo")
    put(
        tmp_path / "home",
        ".codex/config.toml",
        "[[skills.config]]\npath=" + __import__("json").dumps(str(path)) + "\nenabled=false",
    )
    assert not resources.discover(config, "p", "codex")["items"][0]["selectable"]
    with pytest.raises(resources.ResourceError, match="invalid_resource_selections"):
        resources.resolve(
            config,
            {
                "project_id": "p",
                "backend": "codex",
                "prompt": "/a",
                "resource_selections": [{"id": []}],
            },
        )


@pytest.mark.parametrize("configured_mode", ["native", "scoped"])
def test_resources_follow_conversation_mode_not_service_default(
    tmp_path, monkeypatch, configured_mode
):
    from starlette.testclient import TestClient
    from test_workspaces import config

    from agent_service.app import create_app

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    conf = config(tmp_path)
    root = tmp_path / "project"
    conf["projects"]["p"]["root"] = str(root)
    conf["services"]["codex"]["mode"] = configured_mode
    put(root, ".agents/skills/a/SKILL.md", "---\nname: a\n---\nDo")
    app = create_app(conf)
    client = TestClient(app, headers={"Authorization": "Bearer a"})
    try:
        url = "/v1/resources?project_id=p&backend=codex&model=gpt-6-astra"
        native = client.get(url + "&execution_mode=native")
        assert native.status_code == 200
        assert len(native.json()["items"]) == 1
        scoped = client.get(url + "&execution_mode=scoped")
        assert scoped.status_code == 200
        assert scoped.json()["items"] == []
        assert scoped.json()["warnings"]
        assert client.get(url + "&execution_mode=invalid").status_code == 422
        item = native.json()["items"][0]
        data = {
            "project_id": "p",
            "backend": "codex",
            "model": "gpt-6-astra",
            "effort": "low",
            "prompt": "/a",
            "execution_mode": "scoped",
            "resource_selections": [
                {"id": item["id"], "revision": item["revision"], "token": "/a"}
            ],
        }
        assert client.post("/v1/jobs", json=data).status_code == 409
        assert app.state.service.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        data["execution_mode"] = "native"
        assert client.post("/v1/jobs", json=data).status_code == 202
    finally:
        client.close()
        app.state.service.db.close()
