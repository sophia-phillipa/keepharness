import asyncio
import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient
from test_catalog_pin import git, repository  # noqa: F401
from test_workspaces import config

from adapters.local.sandbox import wrap
from agent_service import workspaces
from agent_service.app import create_app
from agent_service.approval_sessions import consume_enrollment, issue_enrollment
from agent_service.catalog_pin import effective_catalogs, pin_catalog
from agent_service.tools import ToolError


@pytest.mark.parametrize("kind", ["file", "directory"])
@pytest.mark.parametrize("writable", [False, True])
def test_nested_private_symlink_target_not_exposed(tmp_path, monkeypatch, kind, writable):
    install = tmp_path / "install"
    private = install / "local_ai" / "config"
    private.mkdir(parents=True)
    root = tmp_path / "project"
    root.mkdir()
    relocated = root / "relocated-provider"
    relocated.mkdir()
    secret = relocated / "provider.json"
    secret.write_text("SYNTHETIC_PRIVATE_NESTED_TARGET")
    (private / "provider").symlink_to(secret if kind == "file" else relocated)
    assert secret.stat().st_nlink == 1
    session = tmp_path / "session"
    session.mkdir()
    monkeypatch.setenv("TAIL_HARNESS_ROOT", str(install))
    try:
        command = wrap(
            [
                "/usr/bin/python3",
                "-c",
                f'from pathlib import Path; p=Path({str(secret)!r}); print(p.read_text() if p.exists() else "hidden")',
            ],
            session,
            root,
            {"root": str(root), "permissions": {"read": True, "write": writable}},
        )
    except ToolError as error:
        assert str(error) == "local_project_hardlink_denied"
        return
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    print(
        json.dumps(
            {
                "case": "nested-private-target",
                "kind": kind,
                "writable": writable,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
    )
    assert result.returncode == 0, result.stderr
    assert "SYNTHETIC_PRIVATE_NESTED_TARGET" not in result.stdout


def test_pin_check_does_not_execute_mutable_fsmonitor(repository, tmp_path):  # noqa: F811
    catalog, _ = repository
    root = Path(catalog["root"])
    state = tmp_path / "state"
    pin = pin_catalog(catalog, state, "HEAD", owner=True)
    marker = tmp_path / "HOST_EXECUTED"
    hook = tmp_path / "synthetic-monitor.sh"
    hook.write_text("#!/bin/sh\nprintf synthetic > " + str(marker) + '\nprintf "token\\0"\n')
    hook.chmod(0o700)
    git(root, "config", "core.fsmonitor", str(hook))
    result = effective_catalogs(
        {"state_dir": str(state), "catalogs": [catalog]},
        {"catalogs": ["demo"], "catalog_pins": {"demo": pin}},
    )
    print(
        json.dumps(
            {
                "case": "mutable-fsmonitor",
                "accepted_pin": bool(result),
                "host_marker_written": marker.exists(),
            }
        )
    )
    assert not marker.exists(), (
        "Verifying an immutable pin executed a hook from mutable source Git configuration"
    )


SENTINEL = "SYNTHETIC_PRIVATE_FILE_AFTER_REVOCATION"


@pytest.fixture
def file_app(tmp_path):
    cfg = config(tmp_path / "state")
    root = tmp_path / "project"
    root.mkdir()
    (root / (SENTINEL + ".txt")).write_text(SENTINEL)
    cfg["projects"]["p"]["root"] = str(root)
    app = create_app(cfg)
    yield app
    app.state.service.db.close()


@pytest.mark.parametrize("surface", ["authorized", "inspect", "workspace", "download"])
@pytest.mark.parametrize("revocation", ["project", "owner", "read"])
def test_ISO_S1_revoked_file_delivery(file_app, monkeypatch, surface, revocation):
    service = file_app.state.service
    client = TestClient(file_app, headers={"Authorization": "Bearer a"})
    if surface in ("workspace", "download"):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as out:
            out.writestr("notes.txt", SENTINEL)
        uploaded = client.post("/v1/workspaces?project_id=p", content=archive.getvalue())
        assert uploaded.status_code == 201, uploaded.text
        wid = uploaded.json()["workspace_id"]
        url = (
            "/v1/workspaces/" + wid + ("/download" if surface == "download" else "?path=notes.txt")
        )
    else:
        url = "/v1/project-files?project_id=p" + (
            "&view=authorized" if surface == "authorized" else "&path=" + SENTINEL + ".txt"
        )
    operation = {
        "authorized": "browse_project",
        "inspect": "inspect",
        "workspace": "inspect",
        "download": "pack",
    }[surface]
    real = getattr(workspaces, operation)

    def revoke_after_read(*args, **kwargs):
        result = real(*args, **kwargs)
        if revocation == "project":
            service.config["clients"]["a"]["projects"] = []
        elif revocation == "owner":
            del service.config["clients"]["a"]
        else:
            for settings in service.config["services"].values():
                settings["permissions"]["read"] = False
        return result

    monkeypatch.setattr(workspaces, operation, revoke_after_read)
    response = client.get(url)
    fresh = client.get(url)
    delivered = SENTINEL.encode() in response.content
    if surface == "download" and response.status_code == 200:
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            delivered = SENTINEL.encode() in archive.read("notes.txt")
    print(
        json.dumps(
            {
                "surface": surface,
                "revocation": revocation,
                "status": response.status_code,
                "fresh_status": fresh.status_code,
                "private_delivered": delivered,
            }
        )
    )
    assert fresh.status_code in (401, 403)
    assert response.status_code in (401, 403), (
        "Revoked authorization must be revalidated before delivering threaded file results"
    )
    assert not delivered


def test_ISO_S2_attachment_rechecks_read_permission(file_app, monkeypatch):
    service = file_app.state.service
    client = TestClient(file_app, headers={"Authorization": "Bearer a"})
    real = workspaces.selected_project_files

    def revoke_after_select(*args, **kwargs):
        result = real(*args, **kwargs)
        for settings in service.config["services"].values():
            settings["permissions"]["read"] = False
        return result

    monkeypatch.setattr(workspaces, "selected_project_files", revoke_after_select)
    response = client.post(
        "/v1/project-files/attach?project_id=p",
        json={
            "project_root_id": "root",
            "paths": [SENTINEL + ".txt"],
        },
    )
    files = service.db.execute("SELECT id, pages FROM files").fetchall()
    fresh = client.post(
        "/v1/project-files/attach?project_id=p",
        json={
            "project_root_id": "root",
            "paths": [SENTINEL + ".txt"],
        },
    )
    print(
        json.dumps(
            {
                "surface": "attach",
                "revocation": "read",
                "status": response.status_code,
                "fresh_status": fresh.status_code,
                "files_persisted": len(files),
                "private_persisted": any(SENTINEL in row["pages"] for row in files),
            }
        )
    )
    assert fresh.status_code == 403
    assert response.status_code == 403
    assert not files


@pytest.mark.parametrize("human_peer", ["192.0.2.10", "192.0.2.20"])
def test_unauthenticated_cookie_attempts_do_not_block_enrolled_human(tmp_path, human_peer):
    config = {
        "state_dir": str(tmp_path),
        "local_access": False,
        "origins": ["http://testserver"],
        "projects": {"p": {}},
        "clients": {
            "human": {
                "sha256": hashlib.sha256(b"synthetic-api-token").hexdigest(),
                "projects": ["p"],
            }
        },
        "services": {},
    }
    app = create_app(config)
    s = app.state.service
    token = consume_enrollment(config, issue_enrollment(config, "human"))

    async def check():
        with s.db:
            s.conversation_repository.insert(
                "job", "p", "human", "running", 1, json.dumps({}), None, "job", "job"
            )
        pending = asyncio.get_running_loop().create_future()
        s.approvals["job"] = ("job", pending)
        transport = httpx.ASGITransport(app=app, client=("192.0.2.10", 1234))
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            cookies={"harness_session": "synthetic-invalid"},
        ) as invalid:
            statuses = []
            for _ in range(240):
                statuses.append((await invalid.get("/v1/conversations")).status_code)
            assert set(statuses) == {401}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=(human_peer, 5678)),
            base_url="http://testserver",
            cookies={"harness_session": token},
        ) as human:
            r = await human.post("/v1/approvals/job", json={"approved": False})
            print(
                "240 invalid cookie requests:",
                set(statuses),
                "enrolled human denial:",
                r.status_code,
                r.text,
                "pending:",
                not pending.done(),
            )
            assert r.status_code == 200, (
                "Unauthenticated callers exhausted the shared session lane before valid human authentication"
            )

    try:
        asyncio.run(check())
    finally:
        s.db.close()


@pytest.mark.parametrize("change", ["revoked", "expired", "disabled"])
def test_session_budget_index_never_grants_authority(file_app, change):
    from agent_service.approval_sessions import session_database, token_digest

    service = file_app.state.service
    token = consume_enrollment(service.config, issue_enrollment(service.config, "a"))
    client = TestClient(file_app, cookies={"harness_session": token})
    assert client.get("/v1/conversations").status_code == 200
    with session_database(service.config) as db:
        if change == "revoked":
            db.execute("DELETE FROM sessions WHERE digest=?", (token_digest(token),))
        elif change == "expired":
            db.execute("UPDATE sessions SET expires=0 WHERE digest=?", (token_digest(token),))
        else:
            db.execute(
                "UPDATE sessions SET approval_capable=0 WHERE digest=?", (token_digest(token),)
            )
    assert client.get("/v1/conversations").status_code == 401
    assert len(service.requests[("a", "session")]) == 1
    assert len(service.requests[("public", "session")]) == 1


def test_pin_verification_does_not_execute_mutable_clean_filter(repository, tmp_path):  # noqa: F811
    import os

    from agent_service.catalog_pin import snapshot_catalogs

    catalog, _ = repository
    root = Path(catalog["root"])
    (root / ".gitattributes").write_text("* filter=synthetic\n")
    git(root, "add", ".gitattributes")
    git(root, "commit", "-m", "test: declare synthetic filter")
    state = tmp_path / "state"
    pin = pin_catalog(catalog, state, "HEAD", owner=True)
    marker = tmp_path / "FILTER_EXECUTED"
    hook = tmp_path / "filter.sh"
    hook.write_text("#!/bin/sh\nprintf synthetic > " + str(marker) + "\ncat\n")
    hook.chmod(0o700)
    git(root, "config", "filter.synthetic.clean", str(hook))
    for path in Path(pin["root"]).rglob("*"):
        if path.is_file() and path.name != ".git":
            os.utime(path, (1, 1))
    effective_catalogs(
        {"state_dir": str(state), "catalogs": [catalog]},
        {"catalogs": ["demo"], "catalog_pins": {"demo": pin}},
    )
    snapshot_catalogs(
        {"state_dir": str(state), "catalogs": [catalog]},
        {"catalogs": ["demo"], "catalog_pins": {"demo": pin}},
    )
    assert not marker.exists()
