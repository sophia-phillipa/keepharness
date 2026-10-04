import asyncio
import hashlib
import json
import threading

import httpx
import pytest

from agent_service import workspaces
from agent_service.app import create_app
from agent_service.approval_sessions import consume_enrollment, issue_enrollment, session_database


@pytest.mark.parametrize(
    "route", ["project-directories", "usage?backend=codex", "usage?backend=claude"]
)
@pytest.mark.parametrize(
    "change", ["logout", "token_rotation", "expired", "disabled", "reassigned", "valid"]
)
def test_read_after_revocation(tmp_path, monkeypatch, route, change):
    async def run():
        root = tmp_path / "synthetic"
        root.mkdir()
        (root / "private-project-name").mkdir()
        cfg = {
            "state_dir": str(tmp_path / "state"),
            "origins": ["http://testserver"],
            "project_registration": True,
            "projects": {"p": {}},
            "services": {},
            "clients": {
                "local": {"sha256": hashlib.sha256(b"local").hexdigest(), "projects": ["p"]}
            },
        }
        app = create_app(cfg)
        s = app.state.service
        token = consume_enrollment(cfg, issue_enrollment(cfg, "local"))
        headers = (
            {"Cookie": "harness_session=" + token}
            if change in ("logout", "expired")
            else {"Authorization": "Bearer local"}
        )
        entered, release = threading.Event(), threading.Event()
        if route == "project-directories":
            original = workspaces.browse_system

            def browse(*a, **kw):
                entered.set()
                assert release.wait(4)
                return original(*a, **kw)

            monkeypatch.setattr(workspaces, "system_root", lambda _: root)
            monkeypatch.setattr(workspaces, "system_roots", lambda: [("home", root)])
            monkeypatch.setattr(workspaces, "browse_system", browse)
        else:

            async def quota(*args):
                entered.set()
                await asyncio.to_thread(release.wait, 4)
                return {"available": True, "synthetic_account": "private-account-details"}

            monkeypatch.setattr(s, "claude_quota" if route.endswith("claude") else "quota", quota)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers=headers,
            ) as c:
                task = asyncio.create_task(c.get("/v1/" + route))
                assert await asyncio.to_thread(entered.wait, 2)
                if change == "logout":
                    assert (await c.post("/v1/logout")).status_code == 200
                if change == "token_rotation":
                    cfg["clients"]["local"]["sha256"] = hashlib.sha256(b"rotated").hexdigest()
                if change == "expired":
                    with session_database(cfg) as db:
                        db.execute("UPDATE sessions SET expires=0")
                if change == "disabled":
                    cfg["project_registration"] = False
                if change == "reassigned":
                    cfg["clients"]["bob"] = dict(cfg["clients"]["local"])
                    cfg["clients"]["local"]["sha256"] = hashlib.sha256(b"rotated").hexdigest()
                release.set()
                r = await task
                fresh = await c.get("/v1/" + route)
                print(
                    json.dumps(
                        {
                            "route": route,
                            "change": change,
                            "held_status": r.status_code,
                            "fresh_status": fresh.status_code,
                            "body": r.json(),
                        }
                    )
                )
                if change == "valid" or (change == "disabled" and route != "project-directories"):
                    assert r.status_code == 200
                    return
                if change != "reassigned":
                    assert fresh.status_code in (401, 403)
                assert r.status_code in (401, 403), "Revoked read delivered data: " + r.text
        finally:
            release.set()
            await s.effects.close()
            s.db.close()

    asyncio.run(run())
