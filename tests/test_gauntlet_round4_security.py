"""Round-four synthetic regression boundaries; all state is temporary."""

import asyncio
import copy
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from test_workspaces import config

from adapters.local.sandbox import wrap
from agent_service import resources
from agent_service.app import create_app
from agent_service.catalog_pin import CatalogPinError, effective_catalogs, pin_catalog
from agent_service.secret_vault import SecretStream, SecretVault
from agent_service.tools import ToolError


@pytest.mark.parametrize("channel", ["answer_delta", "reasoning_delta"])
@pytest.mark.parametrize("prefix", ["nonce", "harness_session"])
def test_registered_secret_authority_prefix_never_reaches_events(tmp_path, channel, prefix):
    async def scenario():
        cfg = config(tmp_path / "state")
        project = tmp_path / "project"
        project.mkdir()
        cfg["projects"]["p"]["root"] = str(project)
        cfg["codex"] = {"binary": "synthetic"}
        app = create_app(cfg)
        service = app.state.service
        identity = ("a", cfg["clients"]["a"])
        secret = prefix + "-synthetic-private-token-481"
        service.vault.set("fixture", {"token": secret})
        job = service.submit(
            identity,
            {
                "project_id": "p",
                "backend": "codex",
                "model": "gpt-6-astra",
                "prompt": "Synthetic diagnostics",
                "execution_mode": "native",
            },
        )["job_id"]
        row = service.job(identity, job)

        async def provider(settings, prompt, progress, *args):
            for part in ["Token: " + prefix, secret[len(prefix) :], " end"]:
                progress(channel, {"text": part})
            return {"answer": "Synthetic complete"}

        try:
            with (
                patch("adapters.run_native", side_effect=provider),
                patch.object(service, "quota", AsyncMock(return_value=None)),
            ):
                result = await service.infer(row, json.loads(row["payload"]))
            service.finish(job, "completed", result)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost",
                headers={"Authorization": "Bearer a"},
            ) as client:
                response = await client.get("/v1/jobs/" + job + "/events?format=json")
            assert response.status_code == 200
            text = "".join(
                e["data"].get("text", "") for e in response.json()["events"] if e["type"] == channel
            )
            with sqlite3.connect(Path(cfg["state_dir"]) / "jobs.sqlite3") as persisted:
                durable = "".join(
                    json.loads(r[0]).get("text", "")
                    for r in persisted.execute(
                        "SELECT data FROM events WHERE job=? AND type=? ORDER BY id", (job, channel)
                    )
                )
            assert durable == text
            print(
                json.dumps(
                    {
                        "scenario": "A3-E2",
                        "channel": channel,
                        "prefix": prefix,
                        "event_text": text,
                        "persisted_equal": durable == text,
                    }
                )
            )
            assert secret not in text
        finally:
            await service.effects.close()
            service.db.close()

    asyncio.run(scenario())


def test_local_sandbox_hardlink_cannot_modify_outside_file(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    session = tmp_path / "session"
    session.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("original")
    os.link(outside, root / "alias.txt")
    script = "from pathlib import Path; Path(%r).write_text('changed')" % str(root / "alias.txt")
    try:
        command = wrap(
            ["/usr/bin/python3", "-c", script],
            session,
            root,
            {"root": str(root), "permissions": {"read": True, "write": True}},
        )
    except ToolError as error:
        assert str(error) == "local_project_hardlink_denied"
        assert outside.read_text() == "original"
        return
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    print(
        json.dumps(
            {
                "scenario": "A3-E3",
                "returncode": result.returncode,
                "outside_after": outside.read_text(),
            }
        )
    )
    assert result.returncode == 0, result.stderr
    assert outside.read_text() == "original"


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_pinned_catalog_detects_changed_bytes_with_git_index_flags(tmp_path, flag):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(path, *args):
        return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()

    git(repo, "init", "-q")
    git(repo, "config", "user.email", "synthetic@example.invalid")
    git(repo, "config", "user.name", "Synthetic")
    (repo / "commands").mkdir()
    (repo / "commands/task.md").write_text("Approved bytes")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "synthetic")
    catalog = {"id": "demo", "root": str(repo), "kind": "git", "trusted": True}
    state = tmp_path / "state"
    pin = pin_catalog(catalog, state, "HEAD", owner=True)
    target = Path(pin["root"])
    resource = target / "commands/task.md"
    git(target, "update-index", flag, "commands/task.md")
    resource.chmod(0o644)
    resource.write_text("Unapproved bytes")
    print(
        json.dumps(
            {
                "scenario": "A3-E4",
                "flag": flag,
                "git_status": git(target, "status", "--porcelain", "--ignored"),
                "actual": resource.read_text(),
                "committed": git(target, "show", "HEAD:commands/task.md"),
            }
        )
    )
    with pytest.raises(CatalogPinError, match="catalog_pin_modified"):
        effective_catalogs(
            {"state_dir": str(state), "catalogs": [catalog]},
            {"catalogs": ["demo"], "catalog_pins": {"demo": pin}},
        )


@pytest.mark.parametrize("prefix", ["nonce", "harness_session"])
def test_registered_authority_prefix_at_every_split(tmp_path, prefix):
    vault = SecretVault(tmp_path / "vault.json")
    secret = prefix + "-synthetic-private-token-481"
    vault.set("test", {"token": secret})
    text = "Token: " + secret + " end"
    for boundary in range(1, len(text)):
        stream = SecretStream()
        output = stream.feed("answer", text[:boundary]) + stream.feed("answer", text[boundary:])
        assert secret not in output, (boundary, output)
        assert "[redacted]" in output


async def scenario(endpoint):
    with tempfile.TemporaryDirectory(prefix="a3-r1-") as temporary:
        root = Path(temporary)
        project = root / "project"
        source = project / "workflows" / "private.json"
        source.parent.mkdir(parents=True)
        document = {
            "version": 1,
            "id": "private",
            "description": "BEFORE_REVOKE",
            "steps": [
                {
                    "id": "draft",
                    "kind": "builtin",
                    "resource_id": "builtin/roles/writer",
                    "args": "Synthetic",
                    "backend": "codex",
                    "model": "fixture",
                    "effort": "low",
                }
            ],
        }
        source.write_text(json.dumps(document))
        cfg = {
            "state_dir": str(root / "state"),
            "origins": [],
            "projects": {"p": {"root": str(project)}, "q": {}},
            "clients": {
                "local": {"sha256": hashlib.sha256(b"alice").hexdigest(), "projects": ["p", "q"]}
            },
            "services": {
                "codex": {
                    "enabled": True,
                    "mode": "sdk",
                    "models": ["fixture"],
                    "projects": ["p", "q"],
                    "permissions": {"read": True},
                }
            },
            "codex_models": {"fixture": ["low"]},
        }
        app = create_app(cfg)
        service = app.state.service
        entered, release = threading.Event(), threading.Event()
        original_read = resources.read

        def held_read(path):
            if Path(path) == source:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("synthetic read gate timeout")
            return original_read(path)

        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers={"Authorization": "Bearer alice"},
            ) as client:
                with (
                    patch.object(resources, "read", held_read),
                    patch.object(resources, "roots", return_value=(root / "synthetic-engine", [])),
                ):
                    held = asyncio.create_task(client.get(endpoint))
                    assert await asyncio.to_thread(entered.wait, 3)
                    candidate = copy.deepcopy(service.config)
                    candidate["clients"]["local"]["projects"] = ["q"]
                    await service.apply_runtime_config(candidate)
                    fresh = await client.get(endpoint)
                    assert fresh.status_code == 403, fresh.text
                    document["description"] = "SYNTHETIC_CREATED_AFTER_REVOCATION"
                    source.write_text(json.dumps(document))
                    release.set()
                    response = await held
                leaked = document["description"] in response.text
                print(
                    json.dumps(
                        {
                            "endpoint": endpoint,
                            "fresh_status": fresh.status_code,
                            "held_status": response.status_code,
                            "after_revocation_metadata_returned": leaked,
                            "expected": "403 without metadata",
                            "observed_item_names": [
                                item["name"] for item in response.json().get("items", [])
                            ],
                        }
                    )
                )
                assert response.status_code == 403, response.text
                assert not leaked
        finally:
            release.set()
            await service.effects.close()
            service.db.close()


@pytest.mark.parametrize(
    "endpoint",
    ["/v1/catalog?project_id=p", "/v1/resources?project_id=p&backend=codex&model=fixture"],
)
def test_discovery_rechecks_grants(endpoint):
    asyncio.run(scenario(endpoint))


@pytest.mark.parametrize("secret", ["nonce", "non", ":", "harness_session"])
def test_vault_literals_cannot_disable_authority_redaction(tmp_path, secret):
    vault = SecretVault(tmp_path / "vault.json")
    vault.set("fixture", {"value": secret})
    for field in ["nonce:", "harness_session="]:
        text = field + "unregistered-sensitive-approval-token "
        for boundary in range(1, len(text)):
            stream = SecretStream()
            output = stream.feed("answer", text[:boundary]) + stream.feed("answer", text[boundary:])
            assert "unregistered-sensitive-approval-token" not in output


def test_pin_ignores_mutable_replacement_objects(tmp_path):
    from test_catalog_pin import git

    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Fixture")
    (root / "task.md").write_text("Approved")
    git(root, "add", ".")
    git(root, "commit", "-qm", "approved")
    catalog = {"id": "test", "root": str(root), "kind": "git", "trusted": True}
    pin = pin_catalog(catalog, tmp_path / "state", "HEAD", owner=True)
    (root / "task.md").write_text("Unapproved")
    git(root, "commit", "-qam", "unapproved")
    replacement = git(root, "rev-parse", "HEAD")
    git(root, "replace", pin["commit"], replacement)
    target = Path(pin["root"])
    (target / "task.md").chmod(0o644)
    (target / "task.md").write_text("Unapproved")
    git(target, "add", ".")
    with pytest.raises(CatalogPinError, match="catalog_pin_modified"):
        effective_catalogs(
            {"state_dir": str(tmp_path / "state"), "catalogs": [catalog]},
            {"catalogs": ["test"], "catalog_pins": {"test": pin}},
        )


@pytest.mark.parametrize("location", ["project", "additional", "session"])
@pytest.mark.parametrize("writable", [True, False])
def test_all_writable_mounts_reject_hardlinks(tmp_path, location, writable):
    roots = {name: tmp_path / name for name in ("project", "additional", "session")}
    for root in roots.values():
        root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("original")
    os.link(outside, roots[location] / "alias")
    command = ["/usr/bin/python3", "-c", "pass"]
    project = {
        "root": str(roots["project"]),
        "additional_roots": [str(roots["additional"])],
        "permissions": {"read": True, "write": writable},
    }
    if writable or location == "session":
        with pytest.raises(ToolError, match="local_project_hardlink_denied"):
            wrap(command, roots["session"], roots["project"], project)
    else:
        assert "--ro-bind" in wrap(command, roots["session"], roots["project"], project)
    assert outside.read_text() == "original"


@pytest.mark.parametrize("change", ["deleted", "symlink", "directory"])
def test_pin_checks_tracked_dependencies_independent_of_index(tmp_path, change):
    from test_catalog_pin import git

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    (repo / "dependency.txt").write_text("approved")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "fixture")
    catalog = {"id": "test", "kind": "git", "trusted": True, "root": str(repo)}
    state = tmp_path / "state"
    pin = pin_catalog(catalog, state, "HEAD", owner=True)
    target = Path(pin["root"])
    git(target, "update-index", "--skip-worktree", "dependency.txt")
    target.chmod(0o755)
    item = target / "dependency.txt"
    item.unlink()
    if change == "symlink":
        item.symlink_to(repo / "dependency.txt")
    elif change == "directory":
        item.mkdir()
    from agent_service.catalog_pin import snapshot_catalogs

    cfg = {"state_dir": str(state), "catalogs": [catalog]}
    project = {"catalogs": ["test"], "catalog_pins": {"test": pin}}
    for operation in [
        lambda: pin_catalog(catalog, state, pin["commit"], owner=True),
        lambda: effective_catalogs(cfg, project),
        lambda: snapshot_catalogs(cfg, project),
    ]:
        with pytest.raises(CatalogPinError, match="catalog_pin_modified"):
            operation()
