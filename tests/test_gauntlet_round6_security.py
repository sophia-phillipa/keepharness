import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from test_catalog_pin import git
from test_catalog_pin import repository as repository

from adapters.local.sandbox import wrap
from agent_service.app import create_app
from agent_service.catalog_pin import CatalogPinError, effective_catalogs, pin_catalog
from agent_service.resources import discover, prepare_prompt, resolve
from agent_service.tools import ToolError


@pytest.fixture
def app(tmp_path):
    cfg = {
        "state_dir": str(tmp_path / "state"),
        "origins": ["http://testserver"],
        "projects": {"p": {}, "q": {}},
        "clients": {
            n: {"sha256": hashlib.sha256(n.encode()).hexdigest(), "projects": ["p", "q"]}
            for n in ["alice", "bob"]
        },
        "services": {},
    }
    app = create_app(cfg)
    service = app.state.service
    for job, owner, project in [("a", "alice", "p"), ("b", "bob", "p"), ("q", "alice", "q")]:
        with service.db:
            service.conversation_repository.insert(
                job, project, owner, "running", 1, json.dumps({"prompt": job}), None, job, job
            )
        service.event(job, "tool_call", {"name": "synthetic_tool", "arguments": {"sentinel": job}})
    yield app
    service.db.close()


@pytest.mark.parametrize(
    "suffix",
    [
        "",
        "/spans",
        "/spans?include_content=true",
        "/events?format=json",
        "/artifacts/result.json",
        "/effects",
    ],
)
def test_cross_owner_read(app, suffix):
    c = TestClient(app, headers={"Authorization": "Bearer bob"})
    assert c.get("/v1/jobs/a" + suffix).status_code == 403


@pytest.mark.parametrize(
    "route,payload",
    [
        ("/work-item", {"work_item": "SYNTH-1"}),
        ("/resume", {}),
        ("/rerun", {"from_step": 1}),
        ("/save-workflow", {"id": "synthetic"}),
    ],
)
def test_cross_owner_write(app, route, payload):
    c = TestClient(app, headers={"Authorization": "Bearer bob"})
    response = c.request(
        "PATCH" if route == "/work-item" else "POST", "/v1/jobs/a" + route, json=payload
    )
    assert response.status_code == 403


@pytest.mark.parametrize("suffix", ["", "/spans", "/events?format=json", "/effects"])
def test_revoked_project_read(app, suffix):
    app.state.service.config["clients"]["alice"]["projects"] = ["q"]
    c = TestClient(app, headers={"Authorization": "Bearer alice"})
    assert c.get("/v1/jobs/a" + suffix).status_code == 403


@pytest.mark.parametrize(
    "endpoint", ["/v1/activity?project_id=p", "/v1/activity?project_id=p&work_item=SYNTH-1"]
)
def test_activity_project_grant(app, endpoint):
    app.state.service.config["clients"]["alice"]["projects"] = ["q"]
    c = TestClient(app, headers={"Authorization": "Bearer alice"})
    assert c.get(endpoint).status_code == 403


@pytest.mark.parametrize("endpoint", ["/v1/jobs", "/v1/jobs/a/work-item", "/v1/jobs/a/resume"])
def test_size_limit(app, endpoint):
    c = TestClient(app, headers={"Authorization": "Bearer alice"})
    r = c.request(
        "PATCH" if endpoint.endswith("work-item") else "POST",
        endpoint,
        content=b'{"x":"' + b"x" * 200001 + b'"}',
    )
    assert r.status_code == 413


@pytest.mark.parametrize("revocation", ["project", "owner", "session", "expired"])
def test_active_sse_stops_after_revocation(app, revocation):
    async def scenario():
        service = app.state.service
        from agent_service.approval_sessions import (
            consume_enrollment,
            issue_enrollment,
            session_database,
        )

        session = consume_enrollment(service.config, issue_enrollment(service.config, "alice"))
        chunks = []
        revoked = False

        async def receive():
            await asyncio.sleep(5)
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal revoked
            if message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                if not revoked:
                    revoked = True
                    if revocation == "project":
                        service.config["clients"]["alice"]["projects"] = ["q"]
                    elif revocation == "owner":
                        del service.config["clients"]["alice"]
                    else:
                        with session_database(service.config) as db:
                            db.execute(
                                "DELETE FROM sessions"
                                if revocation == "session"
                                else "UPDATE sessions SET expires=0"
                            )
                    service.event(
                        "a",
                        "tool_call",
                        {
                            "name": "after_revoke",
                            "arguments": {"sentinel": "PRIVATE_AFTER_REVOCATION"},
                        },
                    )
                elif b"PRIVATE_AFTER_REVOCATION" in message.get("body", b""):
                    service.finish("a", "completed", {"answer": "done"})

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "method": "GET",
            "path": "/v1/jobs/a/events",
            "raw_path": b"/v1/jobs/a/events",
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 44000),
            "headers": [
                (b"host", b"testserver"),
                (b"cookie", ("harness_session=" + session).encode()),
            ],
            "root_path": "",
        }
        await asyncio.wait_for(app(scope, receive, send), 3)
        result = b"".join(chunks)
        c = TestClient(app, headers={"Authorization": "Bearer alice"})
        denied = c.get("/v1/jobs/a/spans")
        print(revocation, "fresh_status", denied.status_code, result.decode())
        assert denied.status_code == (
            401 if revocation == "owner" else 200 if revocation in {"session", "expired"} else 403
        )
        assert len(service.requests[("alice", "read")]) == (1 if revocation == "owner" else 2)
        assert len(service.requests[("public", "session")]) == 1
        assert service.streams["alice"] == 0
        assert b"PRIVATE_AFTER_REVOCATION" not in result

    asyncio.run(scenario())


@pytest.mark.parametrize("route", ["models", "projects"])
def test_async_metadata_revalidates_revoked_project(app, monkeypatch, route):
    service = app.state.service
    if route == "models":

        async def held(project):
            service.config["clients"]["alice"]["projects"] = ["q"]
            return [{"id": "SYNTHETIC_PRIVATE_PROJECT_MODEL"}]

        monkeypatch.setattr(service, "models_with_context", held)
        url = "/v1/models?project_id=p"
    else:
        from agent_service.routes import projects

        def held(root):
            service.config["clients"]["alice"]["projects"] = ["q"]
            return None

        monkeypatch.setattr(projects, "discover_project_icon", held)
        service.config["projects"]["p"]["root"] = "/tmp/synthetic-private-project"
        url = "/v1/projects"
    c = TestClient(app, headers={"Authorization": "Bearer alice"})
    response = c.get(url)
    print(route, response.status_code, response.text)
    assert response.status_code == 403 or (
        "p" not in response.json().get("details", {})
        and "SYNTHETIC_PRIVATE_PROJECT_MODEL" not in response.text
        and route == "projects"
    )


def test_activity_aggregates_only_current_owner(app):
    c = TestClient(app, headers={"Authorization": "Bearer bob"})
    r = c.get("/v1/activity")
    assert r.status_code == 200
    assert [j["job_id"] for j in r.json()["jobs"]] == ["b"]


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_private_nested_symlink_hardlink_not_readable(tmp_path, monkeypatch, kind):
    install = tmp_path / "install"
    private = install / "local_ai" / "config"
    private.mkdir(parents=True)
    relocated = tmp_path / "relocated"
    relocated.mkdir()
    source = relocated / "private.txt"
    source.write_text("SYNTHETIC_PRIVATE_RUNTIME_CREDENTIAL")
    if kind == "file":
        (private / "provider.json").symlink_to(source)
    else:
        (private / "provider").symlink_to(relocated, target_is_directory=True)
    root = tmp_path / "project"
    root.mkdir()
    alias = root / "alias.txt"
    os.link(source, alias)
    (private / "cycle").symlink_to(private, target_is_directory=True)
    session = tmp_path / "session"
    session.mkdir()
    monkeypatch.setenv("TAIL_HARNESS_ROOT", str(install))
    try:
        command = wrap(
            ["/usr/bin/python3", "-c", f"print(open({str(alias)!r}).read())"],
            session,
            root,
            {"root": str(root), "permissions": {"read": True}},
        )
    except ToolError as error:
        assert str(error) in ("local_project_hardlink_denied", "local_project_scope_invalid")
        return
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    print(kind, "exit", result.returncode, "stdout", result.stdout, "stderr", result.stderr)
    assert result.returncode == 0, "Sandbox unavailable; do not count as a pass"
    assert "SYNTHETIC_PRIVATE_RUNTIME_CREDENTIAL" not in result.stdout


def test_case_alias_addition_cannot_enter_pin(repository, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    catalog, _ = repository
    state = tmp_path / "state"
    pin = pin_catalog(catalog, state, "HEAD", owner=True)
    git(Path(catalog["root"]), "config", "core.ignoreCase", "true")
    folder = Path(pin["root"]) / "commands"
    folder.chmod(0o755)
    (folder / "TASK.md").write_text("SYNTHETIC UNREVIEWED COMMAND")
    project = {"catalogs": ["demo"], "catalog_pins": {"demo": pin}}
    config = {
        "state_dir": str(state),
        "catalogs": [catalog],
        "projects": {"p": project},
        "services": {"claude": {"mode": "native"}},
    }
    print(
        "git-status:",
        repr(git(Path(pin["root"]), "status", "--porcelain", "--ignored", "--untracked-files=all")),
    )
    try:
        effective_catalogs(config, project)
    except CatalogPinError as error:
        assert str(error) == "catalog_pin_modified"
        return
    items = discover(config, "p", "claude")["items"]
    print("discovered:", [(i["id"], i["name"], i["selectable"]) for i in items])
    selected = next(i for i in items if "TASK" in i["id"])
    resolved = resolve(
        config,
        {
            "project_id": "p",
            "backend": "claude",
            "prompt": "/TASK",
            "resource_selections": [
                {"id": selected["id"], "revision": selected["revision"], "token": "/TASK"}
            ],
        },
    )
    prompt = prepare_prompt("/TASK", resolved)
    print("prepared prompt:", prompt)
    assert "SYNTHETIC UNREVIEWED COMMAND" not in str(prompt), (
        "Unreviewed case alias accepted into provider prompt"
    )
