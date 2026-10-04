import asyncio
import hashlib
import json
import threading
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from agent_service import workspaces
from agent_service.app import create_app
from agent_service.approval_sessions import consume_enrollment, issue_enrollment, session_database


@pytest.mark.parametrize("navigate", [False, True])
@pytest.mark.parametrize("change", ["valid", "logout", "rotation", "expiry", "reassigned"])
def test_tree_delivery_revalidates_current_identity(tmp_path, monkeypatch, change, navigate):
    async def run():
        root = tmp_path / "synthetic"
        root.mkdir()
        (root / "SYNTHETIC-PRIVATE-NAME.txt").write_text("fixture")
        cfg = {
            "state_dir": str(tmp_path / "state"),
            "origins": ["http://testserver"],
            "projects": {"p": {"root": str(root)}},
            "services": {},
            "clients": {
                "alice": {"sha256": hashlib.sha256(b"alice").hexdigest(), "projects": ["p"]}
            },
        }
        app = create_app(cfg)
        s = app.state.service
        token = consume_enrollment(cfg, issue_enrollment(cfg, "alice"))
        headers = (
            {"Cookie": "harness_session=" + token}
            if change in ("logout", "expiry")
            else {"Authorization": "Bearer alice"}
        )
        entered, release = threading.Event(), threading.Event()
        original = workspaces.browse_system

        def browse(*args, **kw):
            entered.set()
            assert release.wait(4)
            return original(*args, **kw)

        monkeypatch.setattr(workspaces, "system_root", lambda _: root)
        monkeypatch.setattr(workspaces, "visible_system_roots", lambda: [("home", root)])
        monkeypatch.setattr(workspaces, "browse_system", browse)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers=headers,
            ) as c:
                pending = asyncio.create_task(
                    c.get(
                        "/v1/project-files?view=tree"
                        + ("&navigate_project=1&project_id=p" if navigate else "")
                    )
                )
                assert await asyncio.to_thread(entered.wait, 2)
                if change == "logout":
                    assert (await c.post("/v1/logout")).status_code == 200
                if change in ("rotation", "reassigned"):
                    if change == "reassigned":
                        cfg["clients"]["bob"] = dict(cfg["clients"]["alice"])
                    cfg["clients"]["alice"]["sha256"] = hashlib.sha256(b"new").hexdigest()
                if change == "expiry":
                    with session_database(cfg) as db:
                        db.execute("UPDATE sessions SET expires=0")
                release.set()
                response = await pending
                fresh = await c.get(
                    "/v1/project-files?view=tree"
                    + ("&navigate_project=1&project_id=p" if navigate else "")
                )
                print(
                    json.dumps(
                        {
                            "change": change,
                            "held": response.status_code,
                            "fresh": fresh.status_code,
                            "private_name_delivered": "SYNTHETIC-PRIVATE-NAME" in response.text,
                        }
                    )
                )
                if change == "valid":
                    assert response.status_code == 200
                else:
                    if change != "reassigned":
                        assert fresh.status_code in (401, 403)
                    assert response.status_code in (401, 403), response.text
        finally:
            release.set()
            await s.effects.close()
            s.db.close()

    asyncio.run(run())


@pytest.mark.parametrize("split_control", [False, True])
def test_project_registration_protects_runtime_state(tmp_path, split_control):
    from test_project_browser import config

    cfg = config(tmp_path)
    cfg["project_registration"] = True
    if split_control:
        cfg["control_state_dir"] = str(tmp_path / "control")
        Path(cfg["control_state_dir"]).mkdir()
    app = create_app(cfg)
    s = app.state.service
    s.vault.set("fixture", {"token": "SYNTHETIC-PRIVATE-VAULT-VALUE"})
    try:
        c = TestClient(app, headers={"Authorization": "Bearer a"})
        r = c.post("/v1/projects", json={"root": str(s.root), "name": "Synthetic private state"})
        facts = {
            "split_control": split_control,
            "registration_status": r.status_code,
            "body": r.json(),
        }
        if r.status_code == 201:
            pid = r.json()["project_id"]
            read = c.get(
                "/v1/project-files", params={"project_id": pid, "path": "harness.secrets.json"}
            )
            facts.update(
                read_status=read.status_code,
                secret_delivered="SYNTHETIC-PRIVATE-VAULT-VALUE" in read.text,
                read=read.json(),
            )
        print(json.dumps(facts))
        assert r.status_code == 403, facts
    finally:
        s.db.close()


@pytest.mark.parametrize(
    "private_source",
    [
        "vault",
        "other_owner_attachment",
        "vault_alias",
        "attachment_alias",
        "relocated_attachment",
        "ordinary",
    ],
)
def test_system_attachment_import_rejects_runtime_private_sources(tmp_path, private_source):
    from test_project_browser import config

    cfg = config(tmp_path)
    app = create_app(cfg)
    s = app.state.service
    if private_source in ("vault", "vault_alias"):
        s.vault.set("fixture", {"token": "SYNTHETIC-PRIVATE-IMPORT"})
        source = s.vault.path
    elif private_source in ("other_owner_attachment", "attachment_alias", "relocated_attachment"):
        if private_source == "relocated_attachment":
            relocated = tmp_path / "relocated"
            relocated.mkdir()
            (s.root / "files").symlink_to(relocated, target_is_directory=True)
        source = s.root / "files" / "p" / "bob-file" / "source"
        source.parent.mkdir(parents=True)
        source.write_text("SYNTHETIC-BOB-PRIVATE-ATTACHMENT")
        with s.db:
            s.message_repository.add_file(
                "bob-file",
                "p",
                "bob.txt",
                source.stat().st_size,
                "fixture",
                '[{"page":null,"text":"SYNTHETIC-BOB-PRIVATE-ATTACHMENT"}]',
                "bob",
            )
    else:
        source = tmp_path / "ordinary.txt"
        source.write_text("SYNTHETIC-ORDINARY")
    if private_source.endswith("_alias"):
        import os

        alias = tmp_path / "ordinary-alias.txt"
        os.link(source, alias)
        source = alias
    try:
        c = TestClient(app, headers={"Authorization": "Bearer a"})
        r = c.post(
            "/v1/project-files/attach?project_id=p",
            json={
                "root_id": "system",
                "paths": [source.relative_to("/").as_posix()],
                "backend": "codex",
                "model": "fixture",
            },
        )
        records = [
            dict(row) for row in s.db.execute("SELECT id,owner,pages FROM files WHERE owner='a'")
        ]
        context = ""
        if records:
            data = {
                "prompt": "Review synthetic attachment",
                "backend": "codex",
                "model": "fixture",
                "execution_mode": "scoped",
                "file_ids": [records[0]["id"]],
            }
            with s.db:
                s.conversation_repository.insert(
                    "synthetic-read",
                    "p",
                    "a",
                    "completed",
                    1,
                    json.dumps(data),
                    None,
                    "fixture",
                    "fixture",
                )
            plan = asyncio.run(
                s._prepare_inference(dict(s.conversation_repository.get("synthetic-read")), data)
            )
            context = plan.context
        print(
            json.dumps(
                {
                    "private_source": private_source,
                    "status": r.status_code,
                    "imported_count": len(records),
                    "imported_pages": records,
                    "prepared_provider_context": context,
                    "body": r.json(),
                }
            )
        )
        if private_source == "ordinary":
            assert r.status_code == 200 and len(records) == 1
        else:
            assert not records, "Private bytes became an attacker-owned provider attachment"
    finally:
        s.db.close()


@pytest.mark.parametrize("store", ["secret_vault_path", "effect_credentials_path"])
def test_deletion_preserves_relocated_credentials(tmp_path, store):
    from test_project_browser import config

    from agent_service.errors import APIError

    cfg = config(tmp_path)
    project = tmp_path / "legacy-project"
    project.mkdir()
    secret = project / "private" / "store.json"
    secret.parent.mkdir()
    secret.write_text("{}")
    secret.chmod(0o600)
    cfg[store] = str(secret)
    cfg["projects"]["p"]["root"] = str(project)
    service = create_app(cfg).state.service
    try:
        with pytest.raises(APIError, match="project_directory_forbidden"):
            service.project_folder_deletion(("a", cfg["clients"]["a"]), "p")
        assert secret.exists()
    finally:
        service.db.close()


@pytest.mark.parametrize("catalog", ["catalog_pins", "catalog_runtime"])
def test_scoped_private_scan_preserves_supported_catalog_links(tmp_path, catalog):
    from types import SimpleNamespace

    from agent_service.effect_transport import validate_scoped_private_files

    root = tmp_path / "state"
    root.mkdir()
    control = tmp_path / "control"
    directory = control / catalog / "synthetic"
    directory.mkdir(parents=True)
    (directory / "target").write_text("synthetic")
    (directory / "link").symlink_to("target")
    validate_scoped_private_files(
        SimpleNamespace(root=root, config={"control_state_dir": str(control)})
    )


def test_private_inventory_limit_fails_closed(tmp_path, monkeypatch):
    from agent_service import private_storage
    from agent_service.errors import APIError

    root = tmp_path / "state"
    root.mkdir()
    target = tmp_path / "relocated"
    target.mkdir()
    (root / "files").symlink_to(target, target_is_directory=True)
    for index in range(12):
        (target / str(index)).write_text("private")
    public = tmp_path / "public.txt"
    public.write_text("public")
    monkeypatch.setattr(private_storage, "MAX_PRIVATE_ENTRIES", 8, raising=False)
    with public.open("rb") as stream, pytest.raises(APIError, match="project_file_forbidden"):
        private_storage.validate_attachment_source({}, root, public, stream)
