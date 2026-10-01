import asyncio
import hashlib
import json
import logging
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from starlette.testclient import TestClient
from test_workspaces import config

from adapters.shared.scoped import prepare_scoped
from agent_service.app import create_app
from agent_service.catalog_pin import effective_catalogs, pin_catalog, preview_update
from agent_service.effect_transport import scoped_enforcement
from agent_service.log_config import RedactingFilter


@pytest.mark.parametrize("linked", [False, True])
def test_app_private_database_alias_is_not_mediated(tmp_path, linked):
    cfg = {
        "state_dir": str(tmp_path / "state"),
        "origins": [],
        "projects": {"other": {}},
        "clients": {
            "other_owner": {"sha256": hashlib.sha256(b"fixture").hexdigest(), "projects": ["other"]}
        },
        "services": {},
    }
    app = create_app(cfg)
    s = app.state.service
    with s.db:
        s.conversation_repository.insert(
            "private-job",
            "other",
            "other_owner",
            "completed",
            1,
            json.dumps({"prompt": "SYNTHETIC-R13-OTHER-OWNER"}),
            None,
            "fixture",
            "fixture",
        )
    project = tmp_path / "project"
    project.mkdir()
    database = s.root / "jobs.sqlite3"
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin" / "python").symlink_to("/usr/bin/python3")
    auth = tmp_path / "provider.json"
    auth.write_text("{}")
    adapter = {
        "python": str(runtime / "bin" / "python"),
        "binary": "/usr/bin/python3",
        "auth_file": str(auth),
    }
    if linked:
        os.link(database, project / "database-alias.db")
    try:
        with prepare_scoped(
            adapter, {"root": str(project)}, {}, None, "codex", "auth.json"
        ) as scoped:
            outcome = scoped_enforcement(s, scoped.command, copied_paths=[auth])
            mounted = any(
                scoped.command[i : i + 3] == ["--ro-bind", str(project), "/sources/project"]
                for i in range(len(scoped.command) - 2)
            )
        print(
            json.dumps(
                {
                    "linked": linked,
                    "enforcement": outcome,
                    "project_mounted_readonly": mounted,
                    "same_inode": linked
                    and database.stat().st_ino == (project / "database-alias.db").stat().st_ino,
                    "repository_seeded_owner": s.conversation_repository.get("private-job")[
                        "owner"
                    ],
                }
            )
        )
        assert mounted
        assert outcome == ("unenforced" if linked else "mediated")
    finally:
        s.db.close()


TAIL_PREFIX = "SYNTHETIC-R13-PRIVATE-PASSWORD-TAIL"


@pytest.mark.parametrize("collision", [False, True])
@pytest.mark.parametrize("channel", ["answer_delta", "reasoning_delta", "tool_end"])
def test_registered_password_is_private_in_events_spans_and_logs(tmp_path, collision, channel):
    TAIL = TAIL_PREFIX + "-" + channel + "-" + str(collision)

    async def run():
        cfg = config(tmp_path / "state")
        project = tmp_path / "project"
        project.mkdir()
        cfg["projects"]["p"]["root"] = str(project)
        cfg["codex"] = {"binary": "synthetic"}
        app = create_app(cfg)
        s = app.state.service
        ident = ("a", cfg["clients"]["a"])
        secret = ("nonce=0&" if collision else "") + TAIL
        s.vault.set("fixture", {"password": secret})
        job = s.submit(
            ident,
            {
                "project_id": "p",
                "backend": "codex",
                "model": "gpt-6-astra",
                "prompt": "Synthetic review",
                "execution_mode": "native",
            },
        )["job_id"]
        row = s.job(ident, job)

        async def provider(settings, prompt, progress, *args):
            if channel == "tool_end":
                progress("tool_start", {"tool": "Read", "tool_call_id": "fixture", "input": {}})
                progress(
                    "tool_end",
                    {
                        "tool": "Read",
                        "tool_call_id": "fixture",
                        "result": {"text": secret},
                        "status": "completed",
                    },
                )
            else:
                # Ordinary and collision values traverse the actual inference progress callback.
                progress(channel, {"text": secret + "\n"})
            return {"answer": "Synthetic complete"}

        try:
            with (
                patch("adapters.run_native", side_effect=provider),
                patch.object(s, "quota", AsyncMock(return_value=None)),
            ):
                result = await s.infer(row, json.loads(row["payload"]))
            s.finish(job, "completed", result)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost",
                headers={"Authorization": "Bearer a"},
            ) as c:
                events = await c.get(f"/v1/jobs/{job}/events?format=json")
                spans = await c.get(f"/v1/jobs/{job}/spans?include_content=true")
            assert events.status_code == spans.status_code == 200
            durable = json.dumps(
                [dict(r) for r in s.db.execute("SELECT data FROM events WHERE job=?", (job,))]
            )
            record = logging.LogRecord(
                "fixture", logging.ERROR, "fixture", 1, "Diagnostic %s", (secret,), None
            )
            RedactingFilter().filter(record)
            facts = {
                "channel": channel,
                "collision": collision,
                "events_leak": TAIL in events.text,
                "spans_leak": TAIL in spans.text,
                "sqlite_leak": TAIL in durable,
                "log_leak": TAIL in record.getMessage(),
                "log": record.getMessage(),
            }
            print(json.dumps(facts))
            assert not any(
                facts[k] for k in ("events_leak", "spans_leak", "sqlite_leak", "log_leak")
            ), facts
        finally:
            await s.effects.close()
            s.db.close()

    asyncio.run(run())


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.mark.parametrize("changed_index", [False, True])
def test_update_preview_uses_immutable_files_not_mutable_index(tmp_path, changed_index):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "synthetic@example.invalid")
    git(root, "config", "user.name", "Synthetic")
    (root / "task.md").write_text("First revision")
    git(root, "add", ".")
    git(root, "commit", "-qm", "first")
    catalog = dict(id="synthetic", root=str(root), kind="git", trusted=True)
    state = tmp_path / "state"
    before = pin_catalog(catalog, state, "HEAD", owner=True)
    (root / "task.md").write_text("Second revision with changed instructions")
    git(root, "commit", "-qam", "second")
    after = pin_catalog(catalog, state, "HEAD", owner=True)
    # Only the indexes change; both approved immutable working trees remain exact.
    for pin in (before, after):
        if changed_index:
            git(pin["root"], "update-index", "--force-remove", "task.md")
        cfg = dict(state_dir=str(state), catalogs=[catalog])
        project = dict(catalogs=["synthetic"], catalog_pins={"synthetic": pin})
        assert effective_catalogs(cfg, project)[0]["pin"] == pin
    result = preview_update(catalog, before, state, "HEAD", owner=True, fetch=False)
    print(
        json.dumps(
            dict(
                changed_index=changed_index,
                before=(Path(before["root"]) / "task.md").read_text(),
                after=(Path(after["root"]) / "task.md").read_text(),
                diff=result["diff"],
            )
        )
    )
    assert [item["resource_id"] for item in result["diff"]] == ["catalog/synthetic/task.md"]


@pytest.mark.parametrize("route", ["login", "work_item", "title"])
@pytest.mark.parametrize("value", ["ascii-control", "valid-\U0001f600", "\ud800"])
def test_invalid_unicode_is_controlled_before_mutation(tmp_path, route, value):
    cfg = {
        "state_dir": str(tmp_path / "state"),
        "origins": ["http://testserver"],
        "projects": {"p": {}},
        "clients": {"alice": {"sha256": hashlib.sha256(b"alice").hexdigest(), "projects": ["p"]}},
        "services": {},
    }
    app = create_app(cfg)
    s = app.state.service
    with s.db:
        s.conversation_repository.insert(
            "job", "p", "alice", "completed", 1, '{"prompt":"synthetic"}', None, "job", "job"
        )
    c = TestClient(
        app,
        raise_server_exceptions=False,
        headers={"Authorization": "Bearer alice", "Origin": "http://testserver"},
    )
    before = dict(s.conversation_repository.get("job"))
    try:
        if route == "login":
            r = c.post("/v1/login", content=json.dumps({"token": value}))
        elif route == "work_item":
            r = c.patch("/v1/jobs/job/work-item", content=json.dumps({"work_item": value}))
        else:
            r = c.patch("/v1/conversations/job", content=json.dumps({"title": value}))
        alive = c.get("/v1/jobs/job")
        assert alive.status_code == 200
        print(
            json.dumps(
                {
                    "route": route,
                    "value": ascii(value),
                    "status": r.status_code,
                    "code": r.json().get("code"),
                    "followup_status": alive.status_code,
                }
            )
        )
        if value == "\ud800":
            assert dict(s.conversation_repository.get("job")) == before
            assert r.status_code == 422, r.text
        else:
            assert r.status_code == (401 if route == "login" else 200), r.text
    finally:
        c.close()
        s.db.close()


@pytest.mark.parametrize("prefix", ["nonce=0&", "Bearer short;", "token: value "])
def test_colliding_literal_masks_every_stream_boundary(tmp_path, prefix):
    from agent_service.secret_vault import SecretStream, SecretVault, redact_secrets

    vault = SecretVault(tmp_path / "vault.json")
    tail = "SYNTHETIC-PRIVATE-TAIL-" + str(len(prefix))
    secret = prefix + tail
    vault.set("fixture", {"password": secret})
    text = "start " + secret + " end "
    assert tail not in redact_secrets(text)
    for boundary in range(len(text) + 1):
        stream = SecretStream()
        output = stream.feed("answer", text[:boundary]) + stream.feed("answer", text[boundary:])
        assert tail not in output, (boundary, output)
        assert "[redacted]" in output
    stream = SecretStream()
    output = []
    for ch in text:
        output.append(stream.feed("answer", ch))
        raw, parts = stream.vault_pending.get("answer", ("", []))
        assert len(raw) < len(secret)
        assert len(raw) == len(parts)
    assert tail not in "".join(output)


@pytest.mark.parametrize(
    "name", ["jobs.sqlite3", "jobs.sqlite3-wal", "jobs.sqlite3-shm", "nested/private.json"]
)
def test_scoped_launch_rejects_private_inode_alias(tmp_path, name):
    from types import SimpleNamespace

    from agent_service.effect_transport import validate_scoped_private_files
    from agent_service.errors import APIError

    root = tmp_path / "state"
    path = root / name
    path.parent.mkdir(parents=True)
    path.write_text("Synthetic private state")
    service = SimpleNamespace(root=root, config={})
    validate_scoped_private_files(service)
    os.link(path, tmp_path / "alias")
    with pytest.raises(APIError, match="scoped_private_file_linked"):
        validate_scoped_private_files(service)


@pytest.mark.parametrize("effects", [False, True])
def test_private_alias_blocks_scoped_provider_dispatch(tmp_path, effects):
    from agent_service.errors import APIError

    async def scenario():
        cfg = config(tmp_path / "state")
        project = tmp_path / "project"
        project.mkdir()
        cfg["projects"]["p"]["root"] = str(project)
        cfg["codex"] = {"binary": "synthetic"}
        if effects:
            from test_effect_executor import configure_effects

            configure_effects(cfg)
        service = create_app(cfg).state.service
        identity = ("a", cfg["clients"]["a"])
        job = service.submit(
            identity,
            {
                "project_id": "p",
                "backend": "codex",
                "model": "gpt-6-astra",
                "prompt": "Synthetic scoped review",
                "execution_mode": "scoped",
            },
        )["job_id"]
        row = service.job(identity, job)
        os.link(service.root / "jobs.sqlite3", project / "alias.db")
        try:
            with (
                patch("adapters.run_scoped", AsyncMock()) as provider,
                patch.object(service, "quota", AsyncMock(return_value=None)),
            ):
                with pytest.raises(APIError, match="scoped_private_file_linked"):
                    await service.infer(row, json.loads(row["payload"]))
                provider.assert_not_awaited()
        finally:
            await service.effects.close()
            service.db.close()

    asyncio.run(scenario())
