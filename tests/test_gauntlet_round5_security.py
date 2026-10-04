"""Round-five isolation and decision regressions, using temporary synthetic state."""

import asyncio
import os
import subprocess
from pathlib import Path

import pytest
from test_approval_authority import ORIGIN, client_for, pending_approval
from test_approval_authority import approval_app as approval_app
from test_catalog_pin import git
from test_catalog_pin import repository as repository

from adapters.local.sandbox import wrap
from agent_service.approval_sessions import issue_enrollment
from agent_service.catalog_pin import effective_catalogs, pin_catalog, snapshot_catalogs
from agent_service.tools import ToolError


@pytest.mark.parametrize("filename", ["unreviewed.md", "ignored.local.md"])
def test_pin_enumerates_untracked_despite_git_configuration(repository, tmp_path, filename):
    catalog, _ = repository
    root = Path(catalog["root"])
    (root / ".gitignore").write_text("*.local.md\n")
    git(root, "add", ".gitignore")
    git(root, "commit", "-qm", "ignore synthetic local files")
    state = tmp_path / "state"
    pin = pin_catalog(catalog, state, "HEAD", owner=True)
    cfg = {"state_dir": str(state), "catalogs": [catalog]}
    project = {"catalogs": ["demo"], "catalog_pins": {"demo": pin}}
    git(root, "config", "status.showUntrackedFiles", "no")
    assert effective_catalogs(cfg, project)
    target = Path(pin["root"]) / "commands"
    target.chmod(0o755)
    (target / filename).write_text("Unapproved synthetic instruction")
    with pytest.raises(ValueError, match="catalog_pin_modified"):
        effective_catalogs(cfg, project)
    (root / "commands" / filename).write_text("Unapproved synthetic instruction")
    assert snapshot_catalogs(cfg, {"catalogs": ["demo"]})[0]["dirty"] is True


@pytest.mark.parametrize("private_name", ["config", "migration-backup"])
@pytest.mark.parametrize("additional", [False, True])
def test_readonly_private_hardlink_is_not_readable(tmp_path, monkeypatch, private_name, additional):
    install = tmp_path / "install"
    private = install / "local_ai" / private_name
    private.mkdir(parents=True)
    source = private / "private.txt"
    source.write_text("synthetic-runtime-private")
    root = tmp_path / "project"
    root.mkdir()
    extra = tmp_path / "additional"
    extra.mkdir()
    alias = (extra if additional else root) / "alias.txt"
    os.link(source, alias)
    session = tmp_path / "session"
    session.mkdir()
    monkeypatch.setenv("KEEPHARNESS_ROOT", str(install))
    try:
        command = wrap(
            ["/usr/bin/python3", "-c", f"print(open({str(alias)!r}).read())"],
            session,
            root,
            {"root": str(root), "additional_roots": [str(extra)], "permissions": {"read": True}},
        )
    except ToolError as error:
        assert str(error) == "local_project_hardlink_denied"
        return
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert "synthetic-runtime-private" not in result.stdout


@pytest.mark.parametrize("private_name", ["config", "migration-backup"])
@pytest.mark.parametrize("location", ["root", "child", "additional"])
def test_relocated_private_root_is_not_readable(tmp_path, monkeypatch, private_name, location):
    install = tmp_path / "install"
    runtime = install / "local_ai"
    runtime.mkdir(parents=True)
    relocated = tmp_path / "relocated"
    (relocated / "child").mkdir(parents=True)
    (runtime / private_name).symlink_to(relocated, target_is_directory=True)
    exposed = relocated / "child" if location == "child" else relocated
    sentinel = exposed / "private.txt"
    sentinel.write_text("synthetic-relocated-runtime-private")
    ordinary = tmp_path / "project"
    ordinary.mkdir()
    root = (
        ordinary
        if location == "additional"
        else runtime / private_name / ("child" if location == "child" else "")
    )
    session = tmp_path / "session"
    session.mkdir()
    monkeypatch.setenv("KEEPHARNESS_ROOT", str(install))
    try:
        command = wrap(
            ["/usr/bin/python3", "-c", f"print(open({str(sentinel)!r}).read())"],
            session,
            root,
            {
                "root": str(root),
                "additional_roots": [str(exposed)] if location == "additional" else [],
                "permissions": {"read": True},
            },
        )
    except ToolError as error:
        assert str(error) == "local_project_scope_invalid"
        return
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert "synthetic-relocated-runtime-private" not in result.stdout


@pytest.mark.parametrize("first", [True, False])
def test_native_decision_loser_receives_conflict(approval_app, first):
    async def scenario():
        future = pending_approval(approval_app, "local")
        nonce = issue_enrollment(approval_app.state.service.config, "local")
        async with client_for(approval_app) as client:
            assert (
                await client.post("/approve-device?nonce=" + nonce, headers={"Origin": ORIGIN})
            ).status_code == 303
            accepted = await client.post("/v1/approvals/job", json={"approved": first})
            loser = await client.post("/v1/approvals/job", json={"approved": not first})
        assert accepted.status_code == 200
        assert future.result()["approved"] is first
        assert loser.status_code == 409
        assert loser.json()["code"] == "approval_already_resolved"

    asyncio.run(scenario())
