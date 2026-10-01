import asyncio
import hashlib
import json
import subprocess
import threading

import httpx
import pytest

from agent_service.app import create_app
from agent_service.approval_sessions import consume_enrollment, issue_enrollment


@pytest.mark.parametrize("route", ["resources", "catalog", "project-git"])
@pytest.mark.parametrize("revocation", ["logout", "token_rotation", "project", "valid"])
def test_read_revalidates_after_thread(tmp_path, monkeypatch, route, revocation):
    async def run():
        cfg = {
            "state_dir": str(tmp_path / "state"),
            "origins": [],
            "projects": {"p": {"root": str(tmp_path)}},
            "clients": {
                "alice": {"sha256": hashlib.sha256(b"alice").hexdigest(), "projects": ["p"]}
            },
            "services": {},
        }
        marker = "SYNTHETIC_PRIVATE_RESOURCE_AFTER_REVOCATION"
        project = tmp_path / "project"
        project.mkdir()
        skill = project / ".agents/skills" / marker / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "---\nname: "
            + marker
            + "\ndescription: Synthetic private catalog entry.\n---\nSynthetic instruction."
        )
        subprocess.run(["git", "init", "-q", "-b", marker, str(project)], check=True)
        cfg["projects"]["p"]["root"] = str(project)
        cfg["services"] = {
            "codex": {
                "enabled": True,
                "mode": "native",
                "models": ["gpt-6-astra"],
                "projects": ["p"],
                "permissions": {"read": True},
            }
        }
        cfg["codex_models"] = {"gpt-6-astra": ["low"]}
        monkeypatch.setenv("HOME", str(tmp_path / "synthetic-home"))
        app = create_app(cfg)
        s = app.state.service
        from agent_service.routes import projects as project_routes

        original = (
            s.resource_catalog
            if route == "resources"
            else (
                project_routes.project_catalog_items
                if route == "catalog"
                else project_routes.project_git
            )
        )
        entered, release = threading.Event(), threading.Event()
        marker = "SYNTHETIC_PRIVATE_RESOURCE_AFTER_REVOCATION"

        def held(*args, **kwargs):
            value = original(*args, **kwargs)
            entered.set()
            assert release.wait(5)
            return value

        if route == "resources":
            monkeypatch.setattr(s, "resource_catalog", held)
        else:
            monkeypatch.setattr(
                "agent_service.routes.projects."
                + ("project_catalog_items" if route == "catalog" else "project_git"),
                held,
            )
        session = consume_enrollment(cfg, issue_enrollment(cfg, "alice"))
        headers = (
            {"Cookie": "harness_session=" + session}
            if revocation == "logout"
            else {"Authorization": "Bearer alice"}
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers=headers,
            ) as c:
                task = asyncio.create_task(
                    c.get(
                        "/v1/"
                        + route
                        + "?project_id=p&backend=codex&model=gpt-6-astra&execution_mode=native"
                    )
                )
                assert await asyncio.to_thread(entered.wait, 3)
                if revocation == "logout":
                    assert (await c.post("/v1/logout")).status_code == 200
                elif revocation == "token_rotation":
                    cfg["clients"]["alice"]["sha256"] = hashlib.sha256(b"rotated").hexdigest()
                elif revocation == "project":
                    cfg["clients"]["alice"]["projects"] = []
                fresh = await c.get("/v1/activity?project_id=p", headers=headers)
                release.set()
                result = await task
                print(
                    json.dumps(
                        {
                            "route": route,
                            "revocation": revocation,
                            "fresh": fresh.status_code,
                            "held": result.status_code,
                            "leaked": marker in result.text,
                        }
                    )
                )
                if revocation == "valid":
                    assert result.status_code == 200 and marker in result.text
                else:
                    assert fresh.status_code in (401, 403)
                    assert result.status_code in (401, 403), result.text
                    assert marker not in result.text
        finally:
            release.set()
            await s.effects.close()
            s.db.close()

    asyncio.run(run())
