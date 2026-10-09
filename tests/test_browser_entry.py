"""Browser entry must not silently switch the user's identity after a reboot."""

import asyncio
import json
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient
from test_workspaces import config, single_owner_config

from agent_service.app import create_app
from control.server import Manager

REMOTE = "http://machine.example.ts.net:8093/"


def test_real_process_starts_with_redirect_and_stops_cleanly(tmp_path):
    import httpx

    async def scenario():
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        manager = Manager(tmp_path)
        manager.settings["port"] = port
        manager.refresh = AsyncMock()
        cfg = {
            "state_dir": str(tmp_path / "runs"),
            "bind": "127.0.0.1",
            "port": port,
            "clients": {"local": {"sha256": "0" * 64, "projects": []}},
            "projects": {},
            "services": {},
            "origins": [REMOTE.rstrip("/")],
            "browser_url": REMOTE,
        }
        manager.build_runtime_config = AsyncMock(return_value=cfg)
        try:
            await manager.start()
            assert manager.running()
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.get(f"http://127.0.0.1:{port}/")
                assert response.status_code == 307
                assert response.headers["location"] == REMOTE
                assert (await client.get(f"http://127.0.0.1:{port}/ui.css")).status_code == 200
            assert (tmp_path / "autostart").exists()
        finally:
            await manager.stop(force=True)
        assert not manager.running()
        assert (tmp_path / "autostart").exists()

    asyncio.run(scenario())


def test_history_and_attachments_keep_owner_and_survive_restart(tmp_path):
    import httpx

    async def scenario():
        cfg = single_owner_config(tmp_path)
        cfg.update(
            local_access=True,
            browser_url=REMOTE,
            origins=[REMOTE.rstrip("/")],
            tailscale_logins={"fixture@example.test": "local"},
        )
        app = create_app(cfg)
        app.state.service.serve_peer_check = lambda client, port: True
        ids = ("first", "second")
        with app.state.service.db as db:
            for job in ids:
                fid = job + "-image"
                db.execute(
                    "INSERT INTO jobs(id,project,owner,state,created,payload,result,idem,digest) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        job,
                        "p",
                        "local",
                        "completed",
                        1,
                        json.dumps({"prompt": job, "file_ids": [fid]}),
                        "{}",
                        None,
                        job,
                    ),
                )
                db.execute(
                    "INSERT INTO files VALUES(?,?,?,?,?,?,?)",
                    (fid, "p", "photo.png", 7, "fixture", '[{"media_type":"image/png"}]', "local"),
                )
                folder = tmp_path / "files" / "p" / fid
                folder.mkdir(parents=True)
                (folder / "source").write_bytes(b"fixture")
        remote_headers = {
            "host": "machine.example.ts.net:8093",
            "tailscale-user-login": "fixture@example.test",
            "x-forwarded-host": "machine.example.ts.net:8093",
            "x-forwarded-for": "100.101.102.103",
        }
        for restart in (False, True):
            if restart:
                app.state.service.db.close()
                app = create_app(cfg)
                app.state.service.serve_peer_check = lambda client, port: True
            kept = ids[1:] if restart else ids
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 4321)),
                base_url="http://127.0.0.1:8095",
            ) as client:
                for headers in ({}, remote_headers):
                    rows = (await client.get("/v1/conversations", headers=headers)).json()
                    assert sorted(row["id"] for row in rows["conversations"]) == sorted(kept)
                for job in kept:
                    for headers in ({}, remote_headers):
                        preview = await client.get(
                            f"/v1/files/{job}-image/preview", headers=headers
                        )
                        assert preview.content == b"fixture"
                if not restart:
                    assert (await client.delete("/v1/conversations/first")).status_code == 200
        app.state.service.db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("ready", [True, False])
def test_start_readiness_does_not_probe_redirecting_browser_entry(tmp_path, ready):
    from unittest.mock import patch

    async def scenario():
        manager = Manager(tmp_path)
        manager.refresh = AsyncMock()
        manager.build_runtime_config = AsyncMock(return_value={})
        manager.stop = AsyncMock()
        process = SimpleNamespace(returncode=None)

        async def get(url):
            return SimpleNamespace(
                status_code=(200 if ready else 503) if url.endswith("/ui.css") else 307
            )

        with (
            patch("socket.socket"),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)),
            patch("asyncio.sleep", AsyncMock()),
            patch("httpx.AsyncClient") as client,
        ):
            client.return_value.__aenter__.return_value.get = AsyncMock(side_effect=get)
            if ready:
                await manager.start()
            else:
                with pytest.raises(ValueError, match="did not become ready"):
                    await manager.start()
        if ready:
            manager.stop.assert_not_awaited()
            assert (tmp_path / "autostart").exists()
            assert manager.running()
        else:
            manager.stop.assert_awaited_once_with(force=True)
            assert not (tmp_path / "autostart").exists()

    asyncio.run(scenario())


def test_local_browser_redirects_but_api_and_remote_origin_do_not(tmp_path):
    cfg = single_owner_config(tmp_path)
    cfg.update(browser_url=REMOTE, origins=[REMOTE.rstrip("/")])
    app = create_app(cfg)
    with TestClient(app, base_url="http://127.0.0.1:8095") as client:
        for host in ("127.0.0.1", "localhost", "[::1]"):
            response = client.get(
                "/?private=value", headers={"host": f"{host}:8095"}, follow_redirects=False
            )
            assert response.status_code == 307
            assert response.headers["location"] == REMOTE
            assert response.headers["cache-control"] == "no-store"
        assert client.get(REMOTE, follow_redirects=False).status_code == 200
        assert client.get("/ui.js", follow_redirects=False).status_code == 200
        assert client.get("/v1/projects", follow_redirects=False).status_code == 401
        # Removing sharing at runtime restores the local UI without a restart.
        candidate = {**app.state.service.config, "browser_url": None}
        asyncio.run(app.state.service.apply_runtime_config(candidate))
        assert client.get("/", follow_redirects=False).status_code == 200


def test_unshared_or_untrusted_destination_keeps_local_entry(tmp_path):
    cfg = config(tmp_path)
    with TestClient(create_app(cfg)) as client:
        assert client.get("/", follow_redirects=False).status_code == 200
    cfg["browser_url"] = "https://unconfigured.example/"
    with TestClient(create_app(cfg), base_url="http://localhost:8095") as client:
        assert client.get("/", follow_redirects=False).status_code == 200


@pytest.mark.parametrize(
    "host,logins", [(None, ["fixture@example.test"]), ("machine.example.ts.net", [])]
)
def test_sharing_receipt_alone_does_not_redirect(tmp_path, host, logins):
    manager = Manager(tmp_path)
    manager.inventory = {"network": {"hostname": host}, "services": [], "binaries": {}}
    manager.settings["logins"] = logins
    (tmp_path / "tailnet.json").write_text('{"port":8095}')
    assert (
        asyncio.run(manager.build_runtime_config(manager.settings, allow_empty=True))["browser_url"]
        is None
    )


def test_browser_destination_survives_restart_and_tracks_sharing(tmp_path):
    async def scenario():
        manager = Manager(tmp_path)
        manager.inventory = {
            "network": {"hostname": "machine.example.ts.net", "online": True},
            "services": [],
            "binaries": {"tailscale": "tailscale"},
        }
        manager.settings.update(logins=["fixture@example.test"], tailnet_port=8093)
        manager.save(manager.settings)
        assert (await manager.build_runtime_config(manager.settings, allow_empty=True))[
            "browser_url"
        ] is None
        (tmp_path / "tailnet.json").write_text('{"port":8093}')
        cfg = await manager.build_runtime_config(manager.settings, allow_empty=True)
        assert cfg["browser_url"] == REMOTE
        restarted = Manager(tmp_path)
        restarted.inventory = manager.inventory
        assert (await restarted.build_runtime_config(restarted.settings, allow_empty=True))[
            "browser_url"
        ] == REMOTE
        manager._write_runtime(cfg)
        # Disabling sharing must clear the destination in the running config.
        from unittest.mock import patch

        with patch("control.discovery.command", AsyncMock(return_value=(0, ""))):
            await manager.tailnet(False)
        assert manager._previous_runtime()["browser_url"] is None

    asyncio.run(scenario())
