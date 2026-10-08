# ruff: noqa: F401, F811
"""Changes made outside KeepHarness (issue #43): the seen map, notices and ``notices:ack``.

Fake homes only (the autouse ``isolated_provider_homes`` fixture) and the fake ``codex`` and
``claude`` CLIs. An "outside" edit is a file edit in the fake home; ``edit`` bumps the mtime
by hand so two quick edits never share a stat tuple.
"""

import itertools
import json
import logging
import os
import re
import time

import pytest

import control.provider_state as provider_state
from control.persistence import ControlStateRepository
from control.provider_state import COALESCE_SECONDS, NOTICE_CAP, ProviderStateService
from tests.test_admin_provider_state import (
    CLAUDE_PLUGIN,
    HEADERS,
    app,
    claude_dir,
    client,
    codex_home,
    fresh_fingerprint,
    get,
    post,
    project,
    row,
    sessions,
)
from tests.test_provider_state_codex import DOCS, GITHUB, reconfigure, seed

WALL_ZERO = 1_790_000_000.0
TICKS = itertools.count(1_700_000_000_000_000_000, 1_000_000_000)


def edit(path, text):
    path.write_text(text)
    stamp = next(TICKS)
    os.utime(path, ns=(stamp, stamp))


def edit_codex(home, old, new):
    config = home / "config.toml"
    assert old in config.read_text()
    edit(config, config.read_text().replace(old, new))


def touch(home):
    config = home / "config.toml"
    edit(config, config.read_text() + "\n# touched\n")


def clocked(app):
    """The service on a manual clock; the seen file is the real one under the app's state."""
    now = [100.0]
    service = ProviderStateService(
        app.state.manager.state,
        lambda: app.state.manager.settings["projects"],
        app.state.manager.provider_state.adapters,
        clock=lambda: now[0],
        wall=lambda: WALL_ZERO + now[0],
    )
    app.state.manager.provider_state = service
    return now


def later(now, seconds=COALESCE_SECONDS + 1):
    now[0] += seconds


def stamp(now):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(WALL_ZERO + now[0]))


def seen_path(app):
    return app.state.manager.state / "provider-state-seen.json"


def ack(client, ids, provider="codex", project_id="sem-projeto", **extra):
    body = {"provider": provider, "project_id": project_id, "notice_ids": ids, **extra}
    return client.post("/api/provider-state/notices:ack", headers=HEADERS, json=body)


def changes(client, provider="codex", project_id="sem-projeto"):
    return get(client, provider, project_id).json()["external_changes"]


@pytest.fixture
def now(app, codex_home):
    seed(codex_home)
    return clocked(app)


def test_first_read_is_baseline_without_notices(client, app, now):
    body = get(client).json()
    assert body["external_changes"] == []
    entry = json.loads(seen_path(app).read_text())["entries"]["codex|sem-projeto"]
    assert entry["notices"] == [] and entry["writes"] == {}
    assert entry["items"][GITHUB] == {"enabled": False, "source": "config.toml", "name": "github"}
    assert entry["stat"]


def test_external_edit_shows_once_until_ack(client, now, codex_home):
    get(client)
    edit_codex(codex_home, "enabled = false  # turned off", "enabled = true  # turned on")
    later(now)
    (notice,) = changes(client)
    assert re.fullmatch(r"n_[0-9a-f]{16}", notice["id"])
    assert notice == {
        "id": notice["id"],
        "item_id": GITHUB,
        "name": "github",
        "change": "changed",
        "before": False,
        "after": True,
        "source": notice["source"],
        "detected_at": stamp(now),
    }
    assert notice["source"] == "config.toml"
    later(now)
    assert changes(client) == [notice]  # reading marks nothing seen
    response = ack(client, [notice["id"]])
    assert (response.status_code, response.json()) == (200, {})
    assert changes(client) == []
    later(now)
    assert changes(client) == []
    assert ack(client, [notice["id"]]).json() == {}  # idempotent


def test_get_does_not_mark_seen(client, app, now, codex_home):
    get(client)
    edit_codex(codex_home, "enabled = false  # turned off", "enabled = true  # turned on")
    later(now)
    get(client)
    saved = seen_path(app).read_text()
    for _ in range(3):
        later(now)
        assert len(changes(client)) == 1
    assert seen_path(app).read_text() == saved


def test_ack_unknown_ids_is_ok(client, now):
    get(client)
    assert ack(client, ["n_0000000000000000", "whatever"]).json() == {}
    assert ack(client, []).status_code == 200
    assert ack(client, ["n_1"], project_id="sem-projeto").status_code == 200  # no entry is fine


@pytest.mark.parametrize(
    "change",
    [
        {"notice_ids": "n_1"},
        {"notice_ids": None},
        {"notice_ids": [5]},
        {"notice_ids": ["x" * 41]},
        {"notice_ids": ["n_1"] * 101},
        {"project_id": 3},
        {"project_id": None},
    ],
)
def test_ack_invalid_body_400(client, now, change):
    response = ack(client, ["n_1"], **change)
    assert (response.status_code, response.json()) == (400, {"error": "invalid_request"})


def test_ack_checks_provider_and_project_before_the_fields(client, now):
    assert ack(client, [5], provider="deepseek").status_code == 404
    assert ack(client, [5], project_id="nope").json() == {"error": "project_unknown"}
    assert ack(client, ["n_1"] * 100).status_code == 200


def test_ack_requires_the_admin_header(client, now):
    body = {"provider": "codex", "project_id": "sem-projeto", "notice_ids": []}
    assert client.post("/api/provider-state/notices:ack", json=body).status_code == 400


def test_own_write_never_shows(client, now):
    get(client)
    written = post(client, item_id=GITHUB, enabled=True, fingerprint=fresh_fingerprint(client))
    assert written.status_code == 200
    for _ in range(3):
        later(now)
        assert changes(client) == []
    assert (
        post(client, item_id=DOCS, enabled=False, fingerprint=fresh_fingerprint(client)).status_code
        == 200
    )
    later(now)
    assert changes(client) == []


def test_external_change_during_own_write_still_shows(client, app, now, codex_home):
    get(client)
    edit_codex(
        codex_home,
        "[mcp_servers.linear]",
        '[plugins."docs@openai-curated"]\nenabled = false\n\n[mcp_servers.linear]',
    )
    fingerprint = app.state.manager.provider_state.adapters["codex"].read_state(None).fingerprint
    assert post(client, item_id=GITHUB, enabled=True, fingerprint=fingerprint).status_code == 200
    later(now)
    (notice,) = changes(client)
    assert (notice["item_id"], notice["before"], notice["after"]) == (DOCS, True, False)


def test_revert_shows_reverted(client, now, codex_home):
    get(client)
    assert (
        post(client, item_id=DOCS, enabled=False, fingerprint=fresh_fingerprint(client)).status_code
        == 200
    )
    later(now)
    assert changes(client) == []
    config = (codex_home / "config.toml").read_text()
    edit(
        codex_home / "config.toml",
        re.sub(r'(\[plugins\."docs@openai-curated"\]\s+enabled = )false', r"\1true", config),
    )
    later(now)
    (notice,) = changes(client)
    assert (notice["item_id"], notice["change"]) == (DOCS, "reverted")
    assert (notice["before"], notice["after"]) == (
        False,
        True,
    )  # the owner's choice, then the CLI's


def test_claude_revert_shows_reverted(client, app, now, claude_dir):
    settings = claude_dir / "settings.json"
    edit(settings, json.dumps({"enabledPlugins": {"x@y": True}}))
    get(client, "claude")
    assert (
        post(
            client,
            provider="claude",
            item_id=CLAUDE_PLUGIN,
            enabled=False,
            fingerprint=fresh_fingerprint(client, "claude"),
        ).status_code
        == 200
    )
    later(now)
    assert changes(client, "claude") == []
    edit(settings, json.dumps({"enabledPlugins": {"x@y": True}}))
    later(now)
    (notice,) = changes(client, "claude")
    assert (notice["item_id"], notice["change"]) == (CLAUDE_PLUGIN, "reverted")
    assert (notice["before"], notice["after"]) == (False, True)


def plugin(name):
    return {"id": f"{name}@openai-curated", "name": name, "marketplace": "openai-curated"}


def listed(home):
    return json.loads((home / "fake-app-server.json").read_text())["plugins"]


def test_added_and_removed_items(client, now, codex_home):
    get(client)
    reconfigure(codex_home, plugins=[*listed(codex_home), plugin("fresh")])
    touch(codex_home)
    later(now)
    (added,) = changes(client)
    assert (added["item_id"], added["change"], added["before"], added["after"]) == (
        "plugin:fresh@openai-curated",
        "added",
        None,
        True,
    )
    ack(client, [added["id"]])
    reconfigure(codex_home, plugins=[p for p in listed(codex_home) if p["name"] != "docs"])
    touch(codex_home)
    later(now)
    removed = [n for n in changes(client) if n["item_id"] == DOCS]
    assert [(n["change"], n["before"], n["after"]) for n in removed] == [("removed", True, None)]
    assert removed[0]["source"]


def test_change_back_drops_notice(client, now, codex_home):
    get(client)
    edit_codex(codex_home, "enabled = false  # turned off", "enabled = true  # turned on")
    later(now)
    assert len(changes(client)) == 1
    edit_codex(codex_home, "enabled = true  # turned on", "enabled = false  # turned off")
    later(now)
    assert changes(client) == []


def test_a_newer_change_keeps_the_first_before(client, now, codex_home):
    get(client)
    edit_codex(codex_home, "enabled = false  # turned off", "enabled = true  # turned on")
    later(now)
    (first,) = changes(client)
    reconfigure(codex_home, plugins=[p for p in listed(codex_home) if p["name"] != "github"])
    edit_codex(codex_home, '[plugins."github@openai-curated"]\nenabled = true  # turned on in the terminal\n', "")
    later(now)
    (second,) = changes(client)
    assert second["id"] != first["id"] and second["before"] is False and second["after"] is None
    assert second["change"] == "removed"


def test_notice_cap_50(client, now, codex_home):
    get(client)
    names = [f"extra{number:02d}" for number in range(NOTICE_CAP + 10)]
    reconfigure(codex_home, plugins=[*listed(codex_home), *map(plugin, names)])
    touch(codex_home)
    later(now)
    found = changes(client)
    assert len(found) == NOTICE_CAP
    ids = [notice["item_id"] for notice in found]
    assert "plugin:extra59@openai-curated" in ids and "plugin:extra00@openai-curated" not in ids


def test_stat_unchanged_skips_diff(client, now, codex_home, monkeypatch):
    calls = []
    real = provider_state.diff_items
    monkeypatch.setattr(
        provider_state, "diff_items", lambda *a, **k: calls.append(1) or real(*a, **k)
    )
    get(client)
    later(now)
    get(client)
    assert calls == []
    touch(codex_home)
    later(now)
    get(client)
    assert calls == [1]


def test_coalescing_reuses_snapshot_but_not_notices(client, now, codex_home):
    get(client)
    edit_codex(codex_home, "enabled = false  # turned off", "enabled = true  # turned on")
    later(now)
    (notice,) = changes(client)
    reads = len(sessions(codex_home))
    assert ack(client, [notice["id"]]).status_code == 200
    assert get(client).json()["external_changes"] == []  # at once, from memory
    assert len(sessions(codex_home)) == reads  # while the snapshot came from the cache


def test_seen_file_mode_0600_and_atomic(client, app, now, monkeypatch):
    written = []
    real = ControlStateRepository._replace
    monkeypatch.setattr(
        ControlStateRepository,
        "_replace",
        staticmethod(lambda path, text, **k: written.append(path) or real(path, text, **k)),
    )
    get(client)
    assert written == [seen_path(app)]
    assert seen_path(app).stat().st_mode & 0o777 == 0o600
    assert not list(app.state.manager.state.glob("provider-state-seen.*tmp"))


def test_unwritable_seen_file_never_breaks_a_get(client, now, monkeypatch, caplog):
    def refuse(*args, **kwargs):
        raise OSError("disk full: /secret/place")

    monkeypatch.setattr(ControlStateRepository, "_replace", staticmethod(refuse))
    with caplog.at_level(logging.DEBUG):
        assert get(client).status_code == 200
    assert "provider_state_seen_unwritable" in caplog.text and "/secret/place" not in caplog.text


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"version": 2, "entries": {}}',
        '{"version": 1, "entries": []}',
        '{"version": 1, "entries": {"codex|sem-projeto": {"stat": 5, "items": []}}}',
        '{"version": 1, "entries": {"codex|sem-projeto": {"stat": "x", "items": {"a": {"enabled": 1}},'
        ' "writes": {}, "notices": []}}}',
    ],
)
def test_corrupt_seen_file_is_empty(client, app, now, caplog, content):
    seen_path(app).write_text(content)
    with caplog.at_level(logging.DEBUG):
        body = get(client).json()
    assert body["external_changes"] == []
    assert json.loads(seen_path(app).read_text())["entries"]["codex|sem-projeto"]["notices"] == []
    assert content not in caplog.text


def test_corrupt_seen_file_logs_its_code(client, app, now, caplog):
    seen_path(app).write_text("not json")
    with caplog.at_level(logging.DEBUG):
        get(client)
    assert "provider_state_seen_unreadable" in caplog.text


def test_seen_map_survives_a_restart(client, app, now, codex_home):
    get(client)
    edit_codex(codex_home, "enabled = false  # turned off", "enabled = true  # turned on")
    later(now)
    (notice,) = changes(client)
    again = clocked(app)  # a new service reads the same file
    assert changes(client) == [notice] and again


def test_no_credentials_in_seen_or_bodies(client, app, now, claude_dir, project, caplog):
    secrets = ("oauth-secret-1234", "mcp-env-secret-5678", "Bearer header-secret-9012")
    (claude_dir / ".claude.json").write_text(
        json.dumps(
            {
                "oauthAccount": {"emailAddress": secrets[0]},
                "mcpServers": {"s": {"env": {"TOKEN": secrets[1]}, "headers": {"A": secrets[2]}}},
                "projects": {
                    str(project): {
                        "mcpServers": {
                            "p": {"env": {"TOKEN": secrets[1]}, "headers": {"A": secrets[2]}}
                        }
                    }
                },
            }
        )
    )
    settings = claude_dir / "settings.json"
    edit(settings, json.dumps({"enabledPlugins": {"x@y": True}}))
    seen = []
    with caplog.at_level(logging.DEBUG):
        seen.append(get(client, "claude", "p").text)
        edit(settings, json.dumps({"enabledPlugins": {"x@y": False}}))
        later(now)
        seen.append(get(client, "claude", "p").text)
        (notice,) = changes(client, "claude", "p")
        seen.append(ack(client, [notice["id"]], "claude", "p").text)
        seen.append(get(client, "claude", "p").text)
        seen.append(post(client, provider="claude", project_id="p", fingerprint="stale").text)
    seen += [seen_path(app).read_text(), caplog.text]
    assert notice["item_id"] == CLAUDE_PLUGIN
    for secret in secrets:
        assert not any(secret in text for text in seen)


def test_409_body_has_external_changes(client, now, codex_home):
    stale = fresh_fingerprint(client)
    edit_codex(codex_home, "enabled = false  # turned off", "enabled = true  # turned on")
    response = post(client, item_id=DOCS, enabled=False, fingerprint=stale)
    assert response.status_code == 409
    (notice,) = response.json()["external_changes"]
    assert (notice["item_id"], notice["before"], notice["after"]) == (GITHUB, False, True)
    later(now)
    assert changes(client) == [notice]


def test_user_scope_write_does_not_notify_another_project_key(client, now, codex_home):
    get(client)
    get(client, project_id="p")
    fingerprint = fresh_fingerprint(client)
    assert post(client, item_id=GITHUB, enabled=True, fingerprint=fingerprint).status_code == 200
    later(now)
    assert changes(client) == [] and changes(client, project_id="p") == []
