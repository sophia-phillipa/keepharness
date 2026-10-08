"""Read-only skills listing for Customize > Skills: whitelisted fields, no paths, never a 500."""

import asyncio
import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from starlette.testclient import TestClient

from agent_service import resources
from agent_service.catalog import catalog
from control.catalog_admin import catalog_config
from control.server import create_app
from tests.owner_session import sign_in

SECRET = "SECRET_TOKEN_VALUE"


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def put_skill(base: Path, name: str, description: str) -> None:
    folder = base / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\nBody")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    for variable in ("HOME", "GEMINI_CLI_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        monkeypatch.setenv(variable, str(tmp_path / "real-home"))
    project = tmp_path / "project"
    project.mkdir()
    catalog = tmp_path / "catalog"
    with patch("control.manager.Manager.refresh", AsyncMock()):
        app = create_app(tmp_path / "state", 8094)
        manager = app.state.manager
        manager.settings["projects"] = [{"id": "p", "root": str(project), "catalogs": ["demo"]}]
        manager.settings["catalogs"] = [
            {"id": "demo", "namespace": "demo", "kind": "git", "root": str(catalog), "trusted": True}
        ]
        for name in ("codex", "claude", "gemini"):
            manager.settings["services"][name].update(enabled=True, projects=["p", "sem-projeto"])
        put_skill(project / ".agents/skills", "shared", "Offered to codex and gemini")
        put_skill(project / ".claude/skills", "only-claude", "Claude only")
        put_skill(catalog / "skills", "review", "From the catalog")
        git(catalog, "init", "-q")
        git(catalog, "add", ".")
        git(catalog, "-c", "user.email=f@example.invalid", "-c", "user.name=F", "commit", "-qm", "x")
        put_skill(tmp_path / "real-home/skills", "mine", "Personal skill")
        with TestClient(app, base_url="http://127.0.0.1:8094") as client:
            sign_in(client).get("/")
            yield client, tmp_path


def listing(client, **params):
    response = client.get("/api/customize-skills", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_items_are_whitelisted_deduplicated_and_pathless(setup):
    client, tmp_path = setup
    body = listing(client, project_id="p")
    assert set(body) == {"items", "warnings"}
    assert all(set(item) == {"name", "description", "scope", "providers"} for item in body["items"])
    by_name = {item["name"]: item for item in body["items"]}
    assert by_name["shared"] == {
        "name": "shared",
        "description": "Offered to codex and gemini",
        "scope": "project",
        # Gemini discovers the file but its adapter does not load skills, so it is not offered.
        "providers": ["codex"],
    }
    assert by_name["only-claude"]["providers"] == ["claude"]
    assert by_name["mine"]["scope"] == "user"
    catalog_item = next(item for item in body["items"] if item["scope"] == "catalog")
    assert catalog_item["description"] == "From the catalog"
    assert {item["scope"] for item in body["items"]} <= {"project", "catalog", "user"}
    assert len([item for item in body["items"] if item["name"] == "shared"]) == 1
    text = json.dumps(body)
    for leaked in (str(tmp_path), "/home", "source", "resource_id", "command", "env"):
        assert leaked not in text


def test_without_project_or_with_unknown_project_the_listing_still_answers(setup):
    client, tmp_path = setup
    none = listing(client)
    assert none["warnings"] == []
    assert [item["name"] for item in none["items"]] == ["mine"]
    unknown = listing(client, project_id=str(tmp_path / "nope"))
    assert [item["name"] for item in unknown["items"]] == ["mine"]
    assert len(unknown["warnings"]) == 1
    assert str(tmp_path) not in json.dumps(unknown)


def test_discovery_failure_is_a_warning_not_a_500(setup):
    client, tmp_path = setup
    real = resources.discover

    def flaky(config, project_id, backend, *args, **kwargs):
        if backend == "claude":
            raise OSError(13, "Permission denied", str(tmp_path / SECRET))
        return real(config, project_id, backend, *args, **kwargs)

    with patch("control.customize_skills.discover", flaky):
        body = listing(client, project_id="p")
    assert "shared" in [item["name"] for item in body["items"]]
    assert "only-claude" not in [item["name"] for item in body["items"]]
    assert len(body["warnings"]) == 1 and "claude" in body["warnings"][0]
    assert SECRET not in json.dumps(body) and str(tmp_path) not in json.dumps(body)


@pytest.mark.parametrize(
    "failure",
    [subprocess.TimeoutExpired("git", 60), FileNotFoundError(2, "Git missing", "git")],
    ids=["git-timeout", "git-missing"],
)
def test_catalog_git_failure_preserves_project_and_user_skills(setup, failure):
    client, tmp_path = setup
    config = catalog_config(client.app.state.manager)
    with patch("agent_service.catalog_pin._git", side_effect=failure):
        body = listing(client, project_id="p")
        assert {item["name"] for item in body["items"]} == {"shared", "only-claude", "mine"}
        assert {item["scope"] for item in body["items"]} == {"project", "user"}
        assert body["warnings"]
        assert str(tmp_path) not in json.dumps(body)

        # The harness palette and project catalog use discovery without the
        # admin endpoint's broad exception handler, including workflow discovery.
        discovered = resources.discover(config, "p", "codex", owner=True)
        project_catalog = catalog(config, config["projects"]["p"], "p", owner=True)
        for result in (discovered, project_catalog):
            skills = [item for item in result["items"] if item["kind"] == "skill"]
            available = {item["name"] for item in skills if item["selectable"]}
            assert {"shared", "mine"} <= available
            unavailable = [item for item in skills if item["scope"] == "catalog"]
            assert unavailable and all(not item["selectable"] for item in unavailable)
            assert all(str(failure) in item["unavailable_reason"] for item in unavailable)
            assert any("Catalog demo unavailable:" in warning for warning in result["warnings"])


def test_unreadable_resource_warnings_from_discovery_carry_no_path(setup):
    client, tmp_path = setup
    path_warning = f"Could not read the resource {tmp_path}/x/SKILL.md"
    real = resources.discover

    def noisy(*args, **kwargs):
        result = real(*args, **kwargs)
        result["warnings"].append(path_warning)
        return result

    with patch("control.customize_skills.discover", noisy):
        body = listing(client, project_id="p")
    assert body["warnings"] and str(tmp_path) not in json.dumps(body)


def test_listing_starts_no_provider_cli(setup):
    client, _ = setup

    real_popen = subprocess.Popen

    def refuse(*args, **kwargs):
        raise AssertionError("a process was started")

    def only_git(command, *args, **kwargs):
        # Catalog discovery reads the commit with git; a provider CLI must never start.
        if Path(command[0]).name != "git":
            refuse()
        return real_popen(command, *args, **kwargs)

    with (
        patch.object(subprocess, "Popen", only_git),
        patch.object(asyncio, "create_subprocess_exec", refuse),
        patch.object(asyncio, "create_subprocess_shell", refuse),
    ):
        assert listing(client, project_id="p")["items"]


def test_non_owner_requests_are_refused(setup):
    client, _ = setup
    client.cookies.clear()
    assert client.get("/api/customize-skills", params={"project_id": "p"}).status_code == 401
    remote = httpx.ASGITransport(app=client.app, client=("10.0.0.2", 1234))

    async def ask():
        async with httpx.AsyncClient(transport=remote, base_url="http://127.0.0.1:8094") as remote_client:
            return await remote_client.get("/api/customize-skills", params={"project_id": "p"})

    assert asyncio.run(ask()).status_code == 403


def test_same_name_in_two_files_stays_two_items(setup):
    client, tmp_path = setup
    put_skill(tmp_path / "project/.claude/skills", "shared", "Claude's own copy")
    body = listing(client, project_id="p")
    shared = sorted((item["description"], item["providers"]) for item in body["items"] if item["name"] == "shared")
    assert shared == [("Claude's own copy", ["claude"]), ("Offered to codex and gemini", ["codex"])]


def test_skill_without_front_matter_is_named_after_its_folder(setup):
    client, tmp_path = setup
    folder = tmp_path / "project/.claude/skills/bare-skill"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("Does one thing well. More text.")
    item = next(item for item in listing(client, project_id="p")["items"] if item["name"] == "bare-skill")
    assert item["providers"] == ["claude"]
    assert str(tmp_path) not in json.dumps(item) and "SKILL.md" not in json.dumps(item)


def test_unloadable_skills_and_non_native_providers_are_not_offered(setup):
    client, tmp_path = setup
    folder = tmp_path / "project/.claude/skills/hidden"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: hidden\ndescription: x\nuser-invocable: false\n---\nBody")
    client.app.state.manager.settings["services"]["codex"]["mode"] = "api"
    names = {item["name"]: item["providers"] for item in listing(client, project_id="p")["items"]}
    assert "hidden" not in names
    assert "shared" not in names  # only Codex could load it, and Codex is not native now
