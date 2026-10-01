import asyncio
import hashlib
import json

import httpx
import pytest

from agent_service.app import create_app
from agent_service.approval_sessions import consume_enrollment, issue_enrollment


@pytest.fixture
def app(tmp_path):
    cfg = {
        "state_dir": str(tmp_path / "state"),
        "origins": ["http://testserver"],
        "projects": {"p": {"service_units": ["synthetic-private.service"]}},
        "clients": {"alice": {"sha256": hashlib.sha256(b"alice").hexdigest(), "projects": ["p"]}},
        "services": {
            "codex": {
                "enabled": True,
                "models": ["gpt-6-astra"],
                "projects": ["p"],
                "mode": "native",
                "permissions": {"shell": True},
            }
        },
        "codex_models": {"gpt-6-astra": ["low"]},
    }
    a = create_app(cfg)
    yield a
    a.state.service.db.close()


@pytest.mark.parametrize("change", ["valid", "logout", "project", "shell", "rotation", "owner"])
def test_service_status_revalidates_before_delivery(app, monkeypatch, change):
    async def scenario():
        s = app.state.service
        entered = asyncio.Event()
        release = asyncio.Event()
        token = consume_enrollment(s.config, issue_enrollment(s.config, "alice"))
        headers = (
            {"Cookie": "harness_session=" + token}
            if change == "logout"
            else {"Authorization": "Bearer alice"}
        )

        async def synthetic_process(argv, timeout):
            entered.set()
            await release.wait()
            return 0, "Id=synthetic-private.service\nLoadState=loaded\nActiveState=active"

        monkeypatch.setattr("agent_service.service_control.process", synthetic_process)
        monkeypatch.setattr(
            "agent_service.service_control.shutil.which", lambda _: "/synthetic/systemctl"
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=headers
        ) as client:
            task = asyncio.create_task(
                client.post("/v1/services", json={"project_id": "p", "action": "list"})
            )
            await asyncio.wait_for(entered.wait(), 2)
            if change == "logout":
                assert (await client.post("/v1/logout")).status_code == 200
            elif change == "project":
                s.config["clients"]["alice"]["projects"] = []
            elif change == "shell":
                s.config["services"]["codex"]["permissions"]["shell"] = False
            elif change == "rotation":
                s.config["clients"]["alice"]["sha256"] = hashlib.sha256(b"new-token").hexdigest()
            elif change == "owner":
                del s.config["clients"]["alice"]
            # Snapshot same request credentials; request starts only after change.
            fresh = await client.post(
                "/v1/services", headers=headers, json={"project_id": "p", "action": "invalid"}
            )
            release.set()
            response = await task
            print(
                json.dumps(
                    {
                        "change": change,
                        "fresh_status": fresh.status_code,
                        "held_status": response.status_code,
                        "response": response.json(),
                    }
                )
            )
            audit = [
                json.loads(line)
                for line in (s.root / "service-actions.jsonl").read_text().splitlines()
            ]
            assert len(audit) == 1
            assert audit[0]["action"] == "list" and audit[0]["owner"] == "alice"
            assert audit[0]["services"][0]["status"]["ActiveState"] == "active"
            if change == "valid":
                assert response.status_code == 200
            else:
                assert fresh.status_code in (401, 403)
                assert response.status_code in (401, 403), response.text
                assert "synthetic-private" not in response.text

    asyncio.run(scenario())
