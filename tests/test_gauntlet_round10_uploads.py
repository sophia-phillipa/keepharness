import asyncio
import hashlib
import io
import json
import zipfile

import httpx
import pytest

from agent_service.app import create_app
from agent_service.approval_sessions import consume_enrollment, issue_enrollment, session_database


@pytest.mark.parametrize("stage", ["body", "lock", "extraction"])
@pytest.mark.parametrize("kind", ["files", "workspaces"])
@pytest.mark.parametrize(
    "change",
    [
        "logout",
        "token_rotation",
        "upload_disabled",
        "project_revoked",
        "expired",
        "reassigned",
        "valid",
    ],
)
def test_upload_rechecks_authority(tmp_path, monkeypatch, kind, change, stage):
    async def run():
        cfg = {
            "state_dir": str(tmp_path / "state"),
            "origins": ["http://testserver"],
            "projects": {"p": {}},
            "uploads_enabled": True,
            "clients": {
                "alice": {"sha256": hashlib.sha256(b"alice").hexdigest(), "projects": ["p"]}
            },
            "services": {
                "codex": {
                    "enabled": True,
                    "models": ["fixture"],
                    "projects": ["p"],
                    "permissions": {"upload": True, "read": True},
                }
            },
            "codex_models": {"fixture": ["low"]},
        }
        app = create_app(cfg)
        s = app.state.service
        token = consume_enrollment(cfg, issue_enrollment(cfg, "alice"))
        headers = (
            {"Cookie": "harness_session=" + token}
            if change in ("logout", "expired")
            else {"Authorization": "Bearer alice"}
        )
        data = b"Synthetic post-revocation upload"
        if kind == "workspaces":
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w") as z:
                z.writestr("fixture.txt", data)
            data = out.getvalue()
        headers["X-Filename"] = "fixture.zip" if kind == "workspaces" else "fixture.txt"
        entered, release = asyncio.Event(), asyncio.Event()

        async def body():

            if stage == "body":
                entered.set()
                await release.wait()
            yield data

        if stage == "lock":
            await s.upload_lock.acquire()
        if stage == "extraction":
            from agent_service import tools, workspaces

            module, name = (
                (tools, "extract") if kind == "files" else (workspaces, "prepare_documents")
            )
            original = getattr(module, name)

            async def held(*args):
                entered.set()
                await release.wait()
                return await original(*args)

            monkeypatch.setattr(module, name, held)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers=headers,
            ) as c:
                task = asyncio.create_task(c.post("/v1/" + kind + "?project_id=p", content=body()))
                if stage == "lock":
                    await asyncio.sleep(0.02)
                    assert not task.done()
                else:
                    await asyncio.wait_for(entered.wait(), 2)
                if change == "logout":
                    assert (await c.post("/v1/logout")).status_code == 200
                elif change == "token_rotation":
                    cfg["clients"]["alice"]["sha256"] = hashlib.sha256(b"rotated").hexdigest()
                elif change == "upload_disabled":
                    cfg["services"]["codex"]["permissions"]["upload"] = False
                elif change == "project_revoked":
                    cfg["clients"]["alice"]["projects"] = []
                if change == "expired":
                    with session_database(cfg) as db:
                        db.execute("UPDATE sessions SET expires=0")
                if change == "reassigned":
                    cfg["clients"]["bob"] = dict(cfg["clients"]["alice"])
                    cfg["clients"]["alice"]["sha256"] = hashlib.sha256(b"rotated").hexdigest()
                release.set()
                if stage == "lock":
                    s.upload_lock.release()
                r = await task
                fresh = await c.post("/v1/" + kind + "?project_id=p", content=data)
                count = s.db.execute("SELECT COUNT(*) FROM " + kind).fetchone()[0]
                print(
                    json.dumps(
                        {
                            "route": kind,
                            "change": change,
                            "held_status": r.status_code,
                            "fresh_status": fresh.status_code,
                            "rows": count,
                            "response": r.json(),
                        }
                    )
                )
                if change == "valid":
                    assert r.status_code == 201
                    return
                if change != "reassigned":
                    assert fresh.status_code in (401, 403)
                assert r.status_code in (401, 403), "Revoked upload persisted: " + r.text
                assert count == (1 if change == "reassigned" else 0)
                if change == "reassigned":
                    return
                assert not list((s.root / kind).rglob("source"))
                assert not list((s.root / kind).rglob("original.zip"))
        finally:
            await s.effects.close()
            s.db.close()

    asyncio.run(run())


@pytest.mark.parametrize("media", ["image", "video"])
def test_selected_upload_provider_is_revalidated(tmp_path, monkeypatch, media):
    from agent_service import tools

    async def run():
        cfg = {
            "state_dir": str(tmp_path / "state"),
            "projects": {"p": {}},
            "clients": {
                "alice": {"sha256": hashlib.sha256(b"alice").hexdigest(), "projects": ["p"]}
            },
            "uploads_enabled": True,
            "services": {
                "codex": {
                    "enabled": True,
                    "models": ["selected", "alternative"],
                    "projects": ["p"],
                    "permissions": {"upload": True, "read": True},
                }
            },
            "codex_models": {"selected": ["low"], "alternative": ["low"]},
        }
        app = create_app(cfg)
        service = app.state.service
        entered, release = asyncio.Event(), asyncio.Event()

        async def extract(*args):
            return [{"media_type": "image/png"}] if media == "image" else [{"text": "video"}]

        async def validate(*args):
            entered.set()
            await release.wait()

        monkeypatch.setattr(tools, "extract", extract)
        monkeypatch.setattr(
            service, "validate_images" if media == "image" else "validate_video", validate
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers={
                    "Authorization": "Bearer alice",
                    "X-Filename": "fixture.png" if media == "image" else "fixture.mp4",
                },
            ) as client:
                task = asyncio.create_task(
                    client.post(
                        "/v1/files?project_id=p&backend=codex&model=selected",
                        content=b"synthetic media",
                    )
                )
                await asyncio.wait_for(entered.wait(), 2)
                cfg["services"]["codex"]["models"].remove("selected")
                release.set()
                response = await task
                assert response.status_code in (403, 422), response.text
                assert service.db.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
                assert not list((service.root / "files").rglob("source"))
        finally:
            await service.effects.close()
            service.db.close()

    asyncio.run(run())
