"""Live resource discovery, identities and admission; no model inference."""

import pytest

from agent_service import resources


def put(root, path, text):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return target


def cfg(root, backend="codex"):
    return {
        "projects": {"p": {"root": str(root), "permissions": {"delegate": True}}},
        "services": {backend: {"mode": "native"}},
    }


def catalog_cfg(root, catalog, backend="codex"):
    config = cfg(root, backend)
    config["catalogs"] = [
        {
            "id": "demo",
            "root": str(catalog),
            "kind": "folder",
            "trusted": True,
            "namespace": "demo",
        }
    ]
    config["projects"]["p"]["catalogs"] = ["demo"]
    return config


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
    assert [(i["name"], i["scope"]) for i in before["items"]] == [("reviewer", "project")]
    put(root, ".agents/skills/new/SKILL.md", "---\nname: new\ndescription: New skill\n---\nDo work")
    after = resources.discover(cfg(root), "p", "codex")
    assert any(i["name"] == "new" for i in after["items"])
    assert all(i["origin"] != "claude" for i in after["items"])


def test_trusted_catalog_precedence_relative_ids_and_symlink_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    catalog = tmp_path / "catalog"
    outside = tmp_path / "outside"
    put(tmp_path / "home", ".codex/agents/reviewer.toml", 'name="demo--reviewer"\ndeveloper_instructions="User"')
    put(catalog, "agents/reviewer.toml", 'name="demo--reviewer"\ndeveloper_instructions="Catalog"')
    put(project, ".codex/agents/reviewer.toml", 'name="demo--reviewer"\ndeveloper_instructions="Project"')
    put(catalog, "skills/shared/SKILL.md", "---\nname: shared\n---\nInside")
    alias = catalog / "skills/alias"
    alias.symlink_to(catalog / "skills/shared")
    put(outside, "SKILL.md", "---\nname: escaped\n---\nOutside")
    (catalog / "skills/escaped").symlink_to(outside)

    items = resources.discover(catalog_cfg(project, catalog), "p", "codex")["items"]
    reviewer = next(item for item in items if item["name"] == "demo--reviewer")
    shared = next(item for item in items if item["name"] == "shared")
    assert reviewer["scope"] == "project"
    assert reviewer["source"].endswith(".codex/agents/reviewer.toml")
    assert reviewer["resource_id"] == reviewer["id"]
    assert reviewer["resource_id"] == "project/p/.codex/agents/reviewer.toml"
    assert shared["resource_id"] == "catalog/demo/skills/shared/SKILL.md"
    assert [item["name"] for item in items].count("shared") == 1
    assert not any(item["name"] == "escaped" for item in items)


def test_large_body_metadata_fields_and_fenced_expansion_detection(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    body = "First sentence describes the command. More detail.\n" + "x" * 100_000
    put(
        project,
        ".codex/prompts/review.md",
        "---\nargument_hint: <file>\n---\n" + body + "\n```sh\necho '!{safe example}'\n```\n",
    )
    item = resources.discover(cfg(project), "p", "codex")["items"][0]
    assert item["description"] == "First sentence describes the command."
    assert item["argument_hint"] == "<file>"
    assert item["selectable"] is True
    put(project, ".codex/prompts/blocked.md", "Run !`unsafe` now")
    blocked = next(
        value
        for value in resources.discover(cfg(project), "p", "codex")["items"]
        if value["name"] == "blocked"
    )
    assert blocked["selectable"] is False
    assert blocked["unavailable_reason"]
    assert blocked["preflight_hint"]


def test_catalog_kinds_namespace_maintenance_hints_and_agent_slash(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    catalog = tmp_path / "catalog"
    put(catalog, "agents/writer.md", "---\nname: writer\ndescription: Writes.\n---\nWrite")
    put(catalog, "commands/deploy.md", "Deploy. Example: /deploy <environment>")
    put(catalog, "commands/install.md", "Install the catalog")
    put(catalog, "commands/demo--update.md", "Update the catalog")
    put(catalog, "rules/paths.md", "---\npaths: src/**\n---\nRule")
    put(catalog, "context/guide.md", "Background context")
    config = catalog_cfg(project, catalog, "claude")
    items = resources.discover(config, "p", "claude", private=True)["items"]
    writer = next(item for item in items if item["kind"] == "agent")
    deploy = next(item for item in items if item["name"] == "deploy")
    install = next(item for item in items if item["name"] == "install")
    update = next(item for item in items if item["name"] == "demo--update")
    assert writer["name"] == "demo--writer"
    assert writer["namespace"] == "demo"
    assert writer["selectable"] is True
    assert writer["preflight_hint"]
    assert deploy["argument_hint"] == "<environment>"
    assert install["maintenance"] is True and install["group"] == "Maintenance"
    assert update["maintenance"] is True and update["group"] == "Maintenance"
    assert deploy["native_command"] is False
    assert {item["kind"] for item in items if not item["selectable"]} >= {"rule", "context"}
    reference = next(item for item in items if item["kind"] == "context")
    assert reference["unavailable_reason"] == "Reference resource; cannot be invoked directly."
    assert "source" in reference["preflight_hint"].lower()

    # Slash is canonical for palette agents; @ remains accepted for old clients.
    for token in ("/demo--writer", "@demo--writer"):
        data = {
            "project_id": "p",
            "backend": "claude",
            "prompt": token + " draft",
            "resource_selections": [
                {"id": writer["id"], "revision": writer["revision"], "token": token}
            ],
        }
        selected = resources.resolve(config, data)
        assert "Delegate this task" in resources.prepare_prompt(data["prompt"], selected)

    config["projects"]["p"]["permissions"]["delegate"] = False
    unavailable = next(
        item
        for item in resources.discover(config, "p", "claude")["items"]
        if item["kind"] == "agent"
    )
    assert unavailable["selectable"] is False
    assert "delegation" in unavailable["unavailable_reason"].lower()


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
        with pytest.raises(resources.ResourceError, match="harness_resources_unavailable"):
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


def test_single_leading_command_expands_all_verbatim_arguments():
    item = {"kind": "command", "name": "inspect", "_body": "ARGS=[$ARGUMENTS]"}
    assert (
        resources.prepare_prompt("/inspect first\nsecond  ", [item])
        == "ARGS=[first\nsecond  ]"
    )


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

    from adapters.codex.native import resource_inputs
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

    from adapters.claude.native import build_command

    with (
        patch("adapters.claude.native.configurations", return_value={"claude": {}}),
        patch("adapters.claude.native.inventory", return_value={"claude": []}),
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
        with patch("adapters.run_native", AsyncMock(return_value={"answer": "done"})) as run:
            asyncio.run(service.infer(row, data))
            assert "$a work" in run.call_args.args[1]
            assert run.call_args.args[3]["_resources"][0]["source"] == str(path)
        path.unlink()
        with patch("adapters.run_native", AsyncMock()) as run:
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


def provider_home_config(root, state, backend, **extra):
    return {**cfg(root, backend), "control_state_dir": str(state), **extra}


def user_items(config, backend):
    items = resources.discover(config, "p", backend)["items"]
    return {(i["kind"], i["name"]): i for i in items if i["scope"] == "user"}


def skill(name):
    return f"---\nname: {name}\ndescription: {name} skill\n---\nDo {name}"


def test_claude_user_skills_and_commands_are_unavailable_with_a_reason(tmp_path, monkeypatch):
    owner, state, root = tmp_path / "owner", tmp_path / "state", tmp_path / "project"
    monkeypatch.setenv("HOME", str(owner))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    harness = state / "providers/home/.claude"
    for base in (harness, owner / ".claude"):
        put(base, f"skills/{base.parent.name}/SKILL.md", skill(base.parent.name))
        put(base, f"commands/{base.parent.name}.md", "---\ndescription: cmd\n---\nRun")
    put(root, ".claude/skills/local/SKILL.md", skill("local"))
    for extra in ({}, {"personal_setup": True}):
        config = provider_home_config(root, state, "claude", **extra)
        items = user_items(config, "claude")
        assert items, extra
        assert all(not i["selectable"] and i["unavailable_reason"] for i in items.values())
        assert all("project" in i["preflight_hint"] for i in items.values())
        project = [i for i in resources.discover(config, "p", "claude")["items"] if i["name"] == "local"]
        assert [i["selectable"] for i in project] == [True]
    assert ("skill", "home") in user_items(provider_home_config(root, state, "claude"), "claude")
    assert ("skill", "owner") in user_items(
        provider_home_config(root, state, "claude", personal_setup=True), "claude"
    )


def test_codex_user_skills_follow_the_home_the_cli_reads(tmp_path, monkeypatch):
    owner, state, root = tmp_path / "owner", tmp_path / "state", tmp_path / "project"
    monkeypatch.setenv("HOME", str(owner))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    put(state / "providers/home/.codex", "skills/harness/SKILL.md", skill("harness"))
    put(owner / ".codex", "skills/owner/SKILL.md", skill("owner"))
    items = user_items(provider_home_config(root, state, "codex"), "codex")
    assert [(k, i["selectable"]) for k, i in items.items()] == [(("skill", "harness"), True)]
    items = user_items(provider_home_config(root, state, "codex", personal_setup=True), "codex")
    assert [(k, i["selectable"]) for k, i in items.items()] == [(("skill", "owner"), False)]
    assert items[("skill", "owner")]["unavailable_reason"]


def test_deepseek_lists_its_own_home_not_the_codex_one(tmp_path, monkeypatch):
    owner, state, root = tmp_path / "owner", tmp_path / "state", tmp_path / "project"
    monkeypatch.setenv("HOME", str(owner))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    put(state / "providers/deepseek", "skills/seek/SKILL.md", skill("seek"))
    put(state / "providers/home", ".agents/skills/shared/SKILL.md", skill("shared"))
    put(state / "providers/home/.codex", "skills/codexonly/SKILL.md", skill("codexonly"))
    put(owner / ".codex", "skills/owner/SKILL.md", skill("owner"))
    for extra in ({}, {"personal_setup": True}):
        items = user_items(provider_home_config(root, state, "deepseek", **extra), "deepseek")
        assert {name: i["selectable"] for (_, name), i in items.items()} == {
            "seek": True,
            "shared": True,
        }


def test_local_user_skills_stay_unavailable_in_the_isolated_executor(tmp_path, monkeypatch):
    owner, state, root = tmp_path / "owner", tmp_path / "state", tmp_path / "project"
    monkeypatch.setenv("HOME", str(owner))
    put(state / "providers/home/.codex", "skills/harness/SKILL.md", skill("harness"))
    items = user_items(provider_home_config(root, state, "local"), "local")
    assert [i["selectable"] for i in items.values()] == [False]
