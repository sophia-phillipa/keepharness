"""Codex provider state, read side (issue #39): the adapter against a fake ``codex app-server``.

Fake homes only (the autouse ``isolated_provider_homes`` fixture); the fake CLI in
``tests/fixtures/fake-codex`` is put first on PATH and never touches the owner's ``~/.codex``.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

import adapters.codex.state as codex_state
from adapters.codex.state import CodexStateAdapter
from adapters.shared.provider_state import (
    ProviderCommandError,
    ProviderStateAdapter,
    ProviderStateConflictError,
    ProviderStateSchemaError,
    ProviderStateUnsupportedError,
    SecretStr,
    fingerprint,
)

FAKE_DIR = Path(__file__).parent / "fixtures" / "fake-codex"
USER_SKILL = "/skills/user/notes/SKILL.md"
REPO_SKILL = "/work/repo/.agents/skills/lint/SKILL.md"

USER_CONFIG = """\
# Owner's Codex config (this comment must survive)
model = "gpt-5.6-sol"

[plugins."github@openai-curated"]
enabled = false  # turned off in the terminal

[mcp_servers.linear]
command = "npx"
args = ["-y", "linear-mcp"]
env = { LINEAR_API_KEY = "lin_secret_value" }
http_headers = { Authorization = "Bearer hdr_secret_value" }

[mcp_servers.paused]
command = "paused-mcp"
enabled = false

[apps.calendar]
enabled = false

[[skills.config]]
path = "/skills/user/notes/SKILL.md"
enabled = false

[experimental_unknown]
flag = true
"""


@pytest.fixture
def codex_home(isolated_provider_homes, monkeypatch):
    monkeypatch.setenv("PATH", f"{FAKE_DIR}{os.pathsep}{os.environ['PATH']}")
    return isolated_provider_homes / ".codex"


@pytest.fixture
def adapter():
    return CodexStateAdapter()


def configure(home, **settings):
    (home / "fake-app-server.json").write_text(json.dumps(settings))


def reconfigure(home, **settings):
    """Add fake-CLI settings to what ``seed`` already wrote."""
    current = json.loads((home / "fake-app-server.json").read_text())
    configure(home, **{**current, **settings})


def seed(home):
    """The user config of USER_CONFIG plus a CLI that lists two plugins, two apps and four skills."""
    (home / "config.toml").write_text(USER_CONFIG)
    configure(
        home,
        plugins=[
            {"id": "github@openai-curated", "name": "github", "marketplace": "openai-curated"},
            {"id": "docs@openai-curated", "name": "docs", "marketplace": "openai-curated"},
            {
                "id": "ghost@openai-curated",
                "name": "ghost",
                "marketplace": "openai-curated",
                "installed": False,
            },
        ],
        apps=[{"id": "calendar", "name": "Calendar"}, {"id": "mail", "name": "Mail"}],
        skills=[
            {"name": "notes", "path": USER_SKILL, "scope": "user"},
            {"name": "lint", "path": REPO_SKILL, "scope": "repo"},
            {"name": "imagegen", "path": "/sys/imagegen/SKILL.md", "scope": "system"},
            {"name": "audit", "path": "/etc/audit/SKILL.md", "scope": "admin"},
        ],
    )


def project_with(tmp_path, home, config, trust=None):
    project = tmp_path / "proj"
    (project / ".codex").mkdir(parents=True)
    (project / ".codex" / "config.toml").write_text(config)
    if trust:
        with (home / "config.toml").open("a") as stream:
            stream.write(f'\n[projects."{project}"]\ntrust_level = "{trust}"\n')
    return project


def rows(snapshot):
    return {item.id: item for item in snapshot.items}


def calls(home):
    log = home / "fake-app-server.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def talk(home, *requests):
    """Raw JSON-RPC against the fake: one answer per request, in order."""
    lines = [{"id": 1, "method": "initialize", "params": {}}]
    lines += [{"id": n, "method": m, "params": p} for n, (m, p) in enumerate(requests, 2)]
    done = subprocess.run(
        [str(FAKE_DIR / "codex"), "app-server", "--listen", "stdio://"],
        input="".join(json.dumps(line) + "\n" for line in lines),
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
        env={**os.environ, "CODEX_HOME": str(home)},
    )
    return [json.loads(line) for line in done.stdout.splitlines()][1:]


# --- every row, user layer ---------------------------------------------------------------------


def test_the_adapter_satisfies_the_protocol(adapter):
    assert isinstance(adapter, ProviderStateAdapter)


def test_reads_plugins_of_the_user_layer(adapter, codex_home):
    seed(codex_home)
    snapshot = adapter.read_state(None)
    github, docs = (
        rows(snapshot)["plugin:github@openai-curated"],
        rows(snapshot)["plugin:docs@openai-curated"],
    )
    assert (github.kind, github.name, github.enabled, github.scope) == (
        "plugin",
        "github",
        False,
        "user",
    )
    assert github.writable and github.reason == ""
    assert github.source == str(codex_home / "config.toml")
    assert (docs.enabled, docs.scope, docs.writable) == (
        True,
        "user",
        True,
    )  # default: no layer sets it
    assert "plugin:ghost@openai-curated" not in rows(snapshot)  # listed but not installed


def test_reads_mcp_servers_of_the_user_layer(adapter, codex_home):
    seed(codex_home)
    found = rows(adapter.read_state(None))
    assert (
        found["mcp:linear"].enabled,
        found["mcp:linear"].scope,
        found["mcp:linear"].writable,
    ) == (True, "user", True)
    assert found["mcp:paused"].enabled is False
    assert found["mcp:linear"].name == "linear"


def test_reads_apps_of_the_user_layer(adapter, codex_home):
    seed(codex_home)
    found = rows(adapter.read_state(None))
    assert found["app:calendar"].enabled is False and found["app:calendar"].scope == "user"
    assert found["app:mail"].enabled is True and found["app:mail"].writable


def test_reads_skills_by_scope(adapter, codex_home):
    seed(codex_home)
    found = rows(adapter.read_state(None))
    user, repo = found[f"skill:{USER_SKILL}"], found[f"skill:{REPO_SKILL}"]
    assert (user.name, user.enabled, user.scope, user.writable) == ("notes", False, "user", True)
    # a repo skill is switched in the user config, where the CLI writes it
    assert (repo.enabled, repo.scope, repo.writable) == (True, "project", True)
    system, admin = found["skill:/sys/imagegen/SKILL.md"], found["skill:/etc/audit/SKILL.md"]
    for locked in (system, admin):
        assert (locked.scope, locked.writable) == ("managed", False) and locked.reason


def test_snapshot_metadata(adapter, codex_home, tmp_path):
    seed(codex_home)
    snapshot = adapter.read_state(tmp_path)
    assert (snapshot.provider, snapshot.engine) == ("codex", "codex")
    assert snapshot.project_root == str(tmp_path)
    assert adapter.read_state(None).project_root is None
    assert snapshot.cli_version == "0.157.1" and snapshot.warnings == ()


def test_an_empty_home_has_no_config_rows_and_defaults_to_enabled(adapter, codex_home):
    configure(codex_home, plugins=[{"id": "docs@openai-curated"}])
    snapshot = adapter.read_state(None)
    assert [item.id for item in snapshot.items] == ["plugin:docs@openai-curated"]
    assert snapshot.items[0].enabled is True


def test_a_server_without_enabled_defaults_to_enabled(adapter, codex_home):
    (codex_home / "config.toml").write_text('[mcp_servers.plain]\ncommand = "x"\n')
    assert rows(adapter.read_state(None))["mcp:plain"].enabled is True


# --- layers ------------------------------------------------------------------------------------


def test_a_trusted_project_layer_overrides_the_user_layer(adapter, codex_home, tmp_path):
    seed(codex_home)
    project = project_with(
        tmp_path,
        codex_home,
        '[plugins."github@openai-curated"]\nenabled = true\n'
        "[mcp_servers.linear]\nenabled = false\n"
        '[mcp_servers.local_only]\ncommand = "x"\nenv = { PROJECT_TOKEN = "proj_secret" }\n',
        trust="trusted",
    )
    found = rows(adapter.read_state(project))
    github = found["plugin:github@openai-curated"]
    assert (github.enabled, github.scope, github.writable) == (True, "project", False)
    assert "project config" in github.reason and "configLayerReadonly" in github.reason
    assert github.source == str(project / ".codex" / "config.toml")
    assert (found["mcp:linear"].enabled, found["mcp:linear"].scope) == (False, "project")
    assert (found["mcp:local_only"].enabled, found["mcp:local_only"].scope) == (True, "project")
    assert found["mcp:paused"].scope == "user"  # untouched by the project
    assert "proj_secret" not in repr(adapter.read_state(project))


def test_a_project_server_switched_in_the_user_config_is_decided_by_the_user_layer(
    adapter, codex_home, tmp_path
):
    seed(codex_home)
    project = project_with(
        tmp_path, codex_home, '[mcp_servers.paused]\ncommand = "x"\n', trust="trusted"
    )
    # the user layer sets enabled=false and is the one the CLI can write
    assert rows(adapter.read_state(project))["mcp:paused"].scope == "user"


@pytest.mark.parametrize("trust", [None, "untrusted"])
def test_an_untrusted_project_layer_is_skipped(adapter, codex_home, tmp_path, trust):
    seed(codex_home)
    project = project_with(
        tmp_path,
        codex_home,
        '[plugins."github@openai-curated"]\nenabled = true\n[mcp_servers.local_only]\ncommand = "x"\n',
        trust=trust,
    )
    snapshot = adapter.read_state(project)
    found = rows(snapshot)
    assert found["plugin:github@openai-curated"].enabled is False
    assert found["plugin:github@openai-curated"].scope == "user"
    assert "mcp:local_only" not in found
    assert any("project" in warning and "trust" in warning for warning in snapshot.warnings)
    assert adapter.is_project_trusted(project) is False


def test_is_project_trusted_reads_the_trust_level(adapter, codex_home, tmp_path):
    seed(codex_home)
    project = project_with(tmp_path, codex_home, "", trust="trusted")
    assert adapter.is_project_trusted(project) is True
    assert adapter.is_project_trusted(tmp_path / "unknown") is False
    assert [c["method"] for c in calls(codex_home) if c["method"].startswith("config/")] == [
        "config/read"
    ] * 2


def test_is_project_trusted_is_false_when_the_app_server_is_down(adapter, codex_home, tmp_path):
    configure(codex_home, fail_start=True)
    assert adapter.is_project_trusted(tmp_path) is False


def test_profile_and_system_layers_are_read_only(adapter, codex_home):
    seed(codex_home)
    configure(
        codex_home,
        plugins=[{"id": "p@m"}],
        profile_layers=[
            {
                "profile": "work",
                "config": {
                    "plugins": {
                        "p@m": {"enabled": False},
                        "github@openai-curated": {"enabled": True},
                    }
                },
            }
        ],
        system={
            "file": "/etc/codex/config.toml",
            "config": {"mcp_servers": {"corp": {"command": "c", "enabled": False}}},
        },
    )
    found = rows(adapter.read_state(None))
    assert (
        found["plugin:p@m"].scope,
        found["plugin:p@m"].enabled,
        found["plugin:p@m"].writable,
    ) == ("profile", False, False)
    assert "work" in found["plugin:p@m"].reason
    assert found["plugin:github@openai-curated"].enabled is True  # the profile beats the user layer
    corp = found["mcp:corp"]
    assert (corp.scope, corp.enabled, corp.writable, corp.source) == (
        "managed",
        False,
        False,
        "/etc/codex/config.toml",
    )
    assert corp.reason


# --- degraded app-server -----------------------------------------------------------------------


def test_a_missing_method_keeps_the_rest_but_locks_every_row(adapter, codex_home):
    seed(codex_home)
    configure(codex_home, missing_methods=["skills/list", "plugin/list"], apps=[{"id": "mail"}])
    snapshot = adapter.read_state(None)
    found = rows(snapshot)
    assert "mcp:linear" in found and "app:mail" in found and "plugin:github@openai-curated" in found
    assert not any(key.startswith("skill:") for key in found)
    assert all(not item.writable and item.reason for item in snapshot.items)
    assert any("skills/list" in w for w in snapshot.warnings)
    assert any("plugin/list" in w for w in snapshot.warnings)


def test_an_unreadable_config_gives_a_warning_and_no_config_rows(adapter, codex_home):
    seed(codex_home)
    (codex_home / "config.toml").write_text("this is = not [valid toml\n")
    snapshot = adapter.read_state(None)
    assert not any(item.kind == "mcp" for item in snapshot.items)
    assert any("config/read" in w for w in snapshot.warnings)
    assert "plugin:docs@openai-curated" in rows(snapshot)
    assert all(not item.writable for item in snapshot.items)
    assert "not [valid" not in repr(snapshot)


def test_nothing_readable_is_a_schema_error(adapter, codex_home):
    configure(codex_home, fail_start=True)
    with pytest.raises(ProviderStateSchemaError):
        adapter.read_state(None)


def test_every_method_missing_is_a_schema_error(adapter, codex_home):
    configure(codex_home, missing_methods=["config/read", "skills/list", "plugin/list", "app/list"])
    with pytest.raises(ProviderStateSchemaError):
        adapter.read_state(None)


def test_a_missing_cli_is_a_schema_error(adapter, codex_home, monkeypatch):
    monkeypatch.setenv("PATH", str(codex_home))  # no codex on it
    with pytest.raises(ProviderStateSchemaError):
        adapter.read_state(None)


# --- secrets, symlinks, calls ------------------------------------------------------------------


def test_no_secret_value_reaches_the_snapshot(adapter, codex_home):
    seed(codex_home)
    text = repr(adapter.read_state(None))
    for secret in (
        "lin_secret_value",
        "hdr_secret_value",
        "LINEAR_API_KEY",
        "Bearer",
        "http_headers",
    ):
        assert secret not in text


def test_reading_is_read_only_and_asks_the_right_cwds(adapter, codex_home, tmp_path):
    seed(codex_home)
    before = (codex_home / "config.toml").read_bytes()
    adapter.read_state(None)
    adapter.read_state(tmp_path)
    assert (codex_home / "config.toml").read_bytes() == before
    made = calls(codex_home)
    assert {c["method"] for c in made} <= {
        "initialize",
        "initialized",
        "config/read",
        "skills/list",
        "plugin/list",
        "app/list",
    }
    skill_cwds = [c["params"]["cwds"] for c in made if c["method"] == "skills/list"]
    assert skill_cwds == [[str(Path.home())], [str(tmp_path)]]
    reads = [c["params"] for c in made if c["method"] == "config/read"]
    assert "cwd" not in reads[0] and reads[1]["cwd"] == str(tmp_path)
    assert all(read["includeLayers"] is True for read in reads)


def test_a_home_reached_through_a_symlink(adapter, isolated_provider_homes, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", f"{FAKE_DIR}{os.pathsep}{os.environ['PATH']}")
    real = tmp_path / "dotfiles" / "codex"
    real.mkdir(parents=True)
    link = isolated_provider_homes / "linked-codex"
    link.symlink_to(real)
    monkeypatch.setenv("CODEX_HOME", str(link))
    seed(real)
    snapshot = adapter.read_state(None)
    assert rows(snapshot)["mcp:paused"].enabled is False
    assert rows(snapshot)["plugin:github@openai-curated"].source == str(link / "config.toml")
    assert adapter.watch_paths(None)[0] == link / "config.toml"
    before = fingerprint(adapter.watch_paths(None))
    (real / "config.toml").write_text(USER_CONFIG + "\n# edited\n")
    assert fingerprint(adapter.watch_paths(None)) != before


def test_watch_paths(adapter, codex_home, tmp_path):
    assert adapter.watch_paths(None) == (codex_home / "config.toml", codex_home / "skills")
    assert adapter.watch_paths(tmp_path) == (
        codex_home / "config.toml",
        codex_home / "skills",
        tmp_path / ".codex" / "config.toml",
        tmp_path / ".agents" / "skills",
    )
    assert not any("auth" in path.name for path in adapter.watch_paths(tmp_path))
    assert calls(codex_home) == []  # pure: no app-server involved


def test_watch_paths_default_to_the_dot_codex_folder_of_the_home(
    adapter, isolated_provider_homes, monkeypatch
):
    monkeypatch.delenv("CODEX_HOME")
    assert adapter.watch_paths(None)[0] == isolated_provider_homes / ".codex" / "config.toml"


# --- fingerprint and version -------------------------------------------------------------------


def test_the_fingerprint_follows_the_layer_versions_and_the_lists(adapter, codex_home):
    seed(codex_home)
    first = adapter.read_state(None).fingerprint
    assert adapter.read_state(None).fingerprint == first
    (codex_home / "config.toml").write_text(USER_CONFIG + "\n# a new comment\n")
    second = adapter.read_state(None).fingerprint
    assert second != first
    configure(codex_home, skills=[{"name": "extra", "path": "/x/SKILL.md"}])
    assert adapter.read_state(None).fingerprint != second


def test_a_project_layer_version_changes_the_fingerprint(adapter, codex_home, tmp_path):
    seed(codex_home)
    project = project_with(tmp_path, codex_home, "", trust="trusted")
    first = adapter.read_state(project).fingerprint
    (project / ".codex" / "config.toml").write_text('[mcp_servers.x]\ncommand = "y"\n')
    assert adapter.read_state(project).fingerprint != first


@pytest.mark.parametrize("version", ["0.158.0", "0.156.9", "1.0.0"])
def test_a_version_outside_the_tested_range_warns(adapter, codex_home, version):
    seed(codex_home)
    configure(codex_home, version=version, plugins=[{"id": "p@m"}])
    snapshot = adapter.read_state(None)
    assert snapshot.cli_version == version
    assert any(version in w and ">=0.157,<0.158" in w for w in snapshot.warnings)
    assert "plugin:p@m" in rows(snapshot)  # reads still show


def test_a_patch_release_inside_the_range_does_not_warn(adapter, codex_home):
    configure(codex_home, version="0.157.9", plugins=[{"id": "p@m"}])
    assert adapter.read_state(None).warnings == ()


# --- what is not part of #39 -------------------------------------------------------------------


def test_later_issues_are_unsupported(adapter, tmp_path):
    with pytest.raises(ProviderStateUnsupportedError, match="#44"):
        adapter.trust_project(tmp_path)
    for call in (
        lambda: adapter.run_environment(tmp_path, True, ()),
        lambda: adapter.login_command(False),
        lambda: adapter.login_status(),
    ):
        with pytest.raises(ProviderStateUnsupportedError, match="#45"):
            call()
    with pytest.raises(ProviderStateUnsupportedError):
        adapter.set_api_key(SecretStr("k"))
    assert adapter.approved_project_servers(tmp_path) == frozenset()
    assert adapter.credential_isolation() is None


# --- the fake itself (package 2 drives its write side) -----------------------------------------


def test_the_fake_applies_a_key_path_edit_and_keeps_comments(codex_home):
    seed(codex_home)
    layers = talk(codex_home, ("config/read", {"includeLayers": True}))[0]["result"]["layers"]
    (answer,) = talk(
        codex_home,
        (
            "config/batchWrite",
            {
                "edits": [
                    {
                        "keyPath": "plugins.github@openai-curated.enabled",
                        "value": True,
                        "mergeStrategy": "replace",
                    }
                ],
                "expectedVersion": layers[0]["version"],
            },
        ),
    )
    assert (
        answer["result"]["status"] == "ok" and answer["result"]["version"] != layers[0]["version"]
    )
    # a one-line diff: comments, key order and the unknown table stay
    assert (codex_home / "config.toml").read_text() == USER_CONFIG.replace(
        "enabled = false  # turned off in the terminal", "enabled = true", 1
    )


def test_the_fake_inserts_under_an_existing_header_and_appends_a_missing_table(codex_home):
    seed(codex_home)
    talk(
        codex_home,
        (
            "config/batchWrite",
            {
                "edits": [
                    {
                        "keyPath": "mcp_servers.linear.enabled",
                        "value": False,
                        "mergeStrategy": "replace",
                    },
                    {"keyPath": "apps.mail.enabled", "value": False, "mergeStrategy": "replace"},
                ]
            },
        ),
    )
    after = (codex_home / "config.toml").read_text()
    assert "[mcp_servers.linear]\nenabled = false\ncommand" in after
    assert after.endswith("[apps.mail]\nenabled = false\n")


def test_the_fake_reports_the_pinned_conflict_and_readonly_errors(codex_home, tmp_path):
    seed(codex_home)
    edit = [{"keyPath": "plugins.x@y.enabled", "value": False, "mergeStrategy": "replace"}]
    conflict, readonly = talk(
        codex_home,
        ("config/batchWrite", {"edits": edit, "expectedVersion": "sha256:deadbeef"}),
        (
            "config/batchWrite",
            {"edits": edit, "filePath": str(tmp_path / "proj/.codex/config.toml")},
        ),
    )
    assert conflict["error"]["data"]["config_write_error_code"] == "configVersionConflict"
    assert readonly["error"]["data"]["config_write_error_code"] == "configLayerReadonly"
    assert readonly["error"]["code"] == -32600
    assert (codex_home / "config.toml").read_text() == USER_CONFIG


def test_the_fake_writes_skill_switches_without_a_version(codex_home):
    seed(codex_home)
    (off,) = talk(codex_home, ("skills/config/write", {"path": REPO_SKILL, "enabled": False}))
    assert off["result"] == {"effectiveEnabled": False}
    assert (
        (codex_home / "config.toml")
        .read_text()
        .endswith(f'[[skills.config]]\npath = "{REPO_SKILL}"\nenabled = false\n')
    )
    talk(codex_home, ("skills/config/write", {"name": "notes", "enabled": True}))
    assert USER_SKILL not in (codex_home / "config.toml").read_text()
    listed = talk(codex_home, ("skills/list", {"cwds": ["/w"], "forceReload": True}))[0]["result"]
    flags = {s["name"]: s["enabled"] for s in listed["data"][0]["skills"]}
    assert flags["lint"] is False and flags["notes"] is True


def test_the_fake_has_a_version_and_unknown_methods(codex_home):
    done = subprocess.run(
        [str(FAKE_DIR / "codex"), "--version"],
        capture_output=True,
        text=True,
        env={**os.environ, "CODEX_HOME": str(codex_home)},
        check=True,
    )
    assert done.stdout.strip() == "codex-cli 0.157.1"
    (answer,) = talk(codex_home, ("nope/never", {}))
    assert answer["error"]["code"] == -32601


# --- the write side ----------------------------------------------------------------------------

GITHUB = "plugin:github@openai-curated"
DOCS = "plugin:docs@openai-curated"
LINEAR = "mcp:linear"
PAUSED = "mcp:paused"
MAIL, CALENDAR = "app:mail", "app:calendar"


def switch(adapter, item_id, enabled, project=None, scope="user"):
    """``set_enabled`` with the fingerprint a UI would hold: the fresh read."""
    return adapter.set_enabled(
        item_id,
        scope,
        enabled,
        adapter.read_state(project).fingerprint,
        project_root=project,
    )


def writes(home):
    return [c for c in calls(home) if c["method"] in ("config/batchWrite", "skills/config/write")]


@pytest.mark.parametrize(
    ("item_id", "key_path", "enabled", "line_before", "line_after"),
    [
        (
            GITHUB,
            "plugins.github@openai-curated.enabled",
            True,
            "enabled = false  # turned off in the terminal",
            "enabled = true",
        ),
        (
            DOCS,
            "plugins.docs@openai-curated.enabled",
            False,
            None,
            '[plugins."docs@openai-curated"]\nenabled = false\n',
        ),
        (
            LINEAR,
            "mcp_servers.linear.enabled",
            False,
            'command = "npx"',
            'enabled = false\ncommand = "npx"',
        ),
        (
            PAUSED,
            "mcp_servers.paused.enabled",
            True,
            "enabled = false\n\n[apps",
            "enabled = true\n\n[apps",
        ),
        (MAIL, "apps.mail.enabled", False, None, "[apps.mail]\nenabled = false\n"),
        (
            CALENDAR,
            "apps.calendar.enabled",
            True,
            "[apps.calendar]\nenabled = false",
            "[apps.calendar]\nenabled = true",
        ),
    ],
)
def test_a_switch_goes_through_batch_write_and_changes_one_line(
    adapter, codex_home, item_id, key_path, enabled, line_before, line_after
):
    seed(codex_home)
    before = codex_home.joinpath("config.toml").read_text()
    version = talk(codex_home, ("config/read", {"includeLayers": True}))[0]["result"]["layers"][0][
        "version"
    ]
    snapshot = switch(adapter, item_id, enabled)
    (write,) = writes(codex_home)
    assert write["method"] == "config/batchWrite"
    assert write["params"]["edits"] == [
        {"keyPath": key_path, "value": enabled, "mergeStrategy": "upsert"}
    ]
    assert write["params"]["expectedVersion"] == version
    assert "filePath" not in write["params"]
    after = codex_home.joinpath("config.toml").read_text()
    if line_before:  # one changed line; comments, the skills array and unknown tables stay
        assert after == before.replace(line_before, line_after, 1)
    else:
        assert after.startswith(before.rstrip("\n")) and after.endswith(line_after)
    assert "# Owner's Codex config (this comment must survive)" in after
    assert "[[skills.config]]" in after and "[experimental_unknown]\nflag = true" in after
    assert rows(snapshot)[item_id].enabled is enabled
    assert rows(adapter.read_state(None))[item_id].enabled is enabled
    assert snapshot.fingerprint != "" and snapshot == adapter.read_state(None)


def test_a_skill_switch_goes_through_skills_config_write_with_a_listed_path(adapter, codex_home):
    seed(codex_home)
    off = switch(adapter, f"skill:{REPO_SKILL}", False)
    (write,) = writes(codex_home)
    assert write == {
        "method": "skills/config/write",
        "params": {"path": REPO_SKILL, "enabled": False},
    }
    assert rows(off)[f"skill:{REPO_SKILL}"].enabled is False
    on = switch(adapter, f"skill:{USER_SKILL}", True)
    assert rows(on)[f"skill:{USER_SKILL}"].enabled is True
    assert writes(codex_home)[-1]["params"] == {"path": USER_SKILL, "enabled": True}
    after = codex_home.joinpath("config.toml").read_text()
    assert f'path = "{REPO_SKILL}"' in after and f'path = "{USER_SKILL}"' not in after
    assert "# Owner's Codex config (this comment must survive)" in after


def test_a_skill_switch_rereads_the_user_layer_right_before_writing(adapter, codex_home):
    seed(codex_home)
    switch(adapter, f"skill:{REPO_SKILL}", False)
    methods = [c["method"] for c in calls(codex_home)]
    assert methods[methods.index("skills/config/write") - 1] == "config/read"


def test_an_unlisted_skill_path_is_refused_and_nothing_is_sent(adapter, codex_home):
    seed(codex_home)
    with pytest.raises(ProviderStateUnsupportedError):
        switch(adapter, "skill:/home/evil/SKILL.md", False)
    assert writes(codex_home) == []
    assert codex_home.joinpath("config.toml").read_text() == USER_CONFIG


def test_a_stale_fingerprint_is_a_conflict_and_nothing_is_written(adapter, codex_home):
    seed(codex_home)
    stale = adapter.read_state(None).fingerprint
    switch(adapter, DOCS, False)  # somebody else switched something meanwhile
    writes_before = len(writes(codex_home))
    with pytest.raises(ProviderStateConflictError):
        adapter.set_enabled(GITHUB, "user", True, stale)
    with pytest.raises(ProviderStateConflictError):
        adapter.set_enabled(f"skill:{USER_SKILL}", "user", True, stale)
    assert len(writes(codex_home)) == writes_before
    assert rows(adapter.read_state(None))[GITHUB].enabled is False


def stale_user_version(monkeypatch):
    """The user config changes between the read and the write: the read hands out an old version."""
    original = CodexStateAdapter._read

    def read(self, project_root):
        snapshot, _ = original(self, project_root)
        return snapshot, "sha256:deadbeef"

    monkeypatch.setattr(CodexStateAdapter, "_read", read)


def test_the_cli_version_conflict_becomes_a_state_conflict(adapter, codex_home, monkeypatch):
    seed(codex_home)
    stale_user_version(monkeypatch)
    with pytest.raises(ProviderStateConflictError):
        switch(adapter, GITHUB, True)
    assert codex_home.joinpath("config.toml").read_text() == USER_CONFIG


def test_the_pinned_conflict_error_shape_is_recognised(adapter, codex_home):
    seed(codex_home)
    conflict = {
        "code": -32600,
        "message": "Configuration was modified since last read. Fetch latest version and retry.",
        "data": {"config_write_error_code": "configVersionConflict"},
    }
    reconfigure(codex_home, errors={"config/batchWrite": conflict})
    with pytest.raises(ProviderStateConflictError):
        switch(adapter, MAIL, False)


def test_a_skill_switch_refuses_when_the_user_layer_changed_since_the_read(
    adapter, codex_home, monkeypatch
):
    seed(codex_home)
    stale_user_version(monkeypatch)
    with pytest.raises(ProviderStateConflictError):
        switch(adapter, f"skill:{REPO_SKILL}", False)
    assert [c["method"] for c in writes(codex_home)] == []  # the write was never sent


def test_a_confirm_read_with_another_value_is_a_conflict(adapter, codex_home, monkeypatch):
    seed(codex_home)

    async def nothing_happens(*args, **kwargs):
        return None

    monkeypatch.setattr(codex_state, "_write", nothing_happens)
    with pytest.raises(ProviderStateConflictError):
        switch(adapter, GITHUB, True)


def test_project_layer_rows_are_unsupported_and_nothing_is_written(adapter, codex_home, tmp_path):
    seed(codex_home)
    project = project_with(
        tmp_path,
        codex_home,
        '[plugins."github@openai-curated"]\nenabled = true\n[mcp_servers.linear]\nenabled = false\n',
        trust="trusted",
    )
    for item_id, scope in ((GITHUB, "user"), (GITHUB, "project"), (LINEAR, "user")):
        with pytest.raises(ProviderStateUnsupportedError, match="project config"):
            switch(adapter, item_id, True, project, scope)
    assert writes(codex_home) == []


def test_profile_and_managed_rows_are_unsupported(adapter, codex_home):
    seed(codex_home)
    reconfigure(
        codex_home,
        profile_layers=[{"profile": "work", "config": {"apps": {"mail": {"enabled": False}}}}],
        system={
            "file": "/etc/codex/config.toml",
            "config": {"plugins": {"s@m": {"enabled": True}}},
        },
    )
    for item_id in (
        MAIL,
        "plugin:s@m",
        "skill:/sys/imagegen/SKILL.md",
        "skill:/etc/audit/SKILL.md",
    ):
        with pytest.raises(ProviderStateUnsupportedError):
            switch(adapter, item_id, False)
    assert writes(codex_home) == []


def test_a_scope_that_does_not_match_the_row_is_refused(adapter, codex_home):
    seed(codex_home)
    for bad in ("local", "managed", "profile", "project"):
        with pytest.raises(ProviderStateUnsupportedError, match="scope"):
            switch(adapter, GITHUB, True, scope=bad)
    switch(adapter, f"skill:{REPO_SKILL}", False, scope="project")  # the row's own scope
    assert len(writes(codex_home)) == 1


def test_unknown_kinds_ids_and_unaddressable_ids_are_refused(adapter, codex_home):
    seed(codex_home)
    with (codex_home / "config.toml").open("a") as stream:
        stream.write('\n[mcp_servers."dotted.name"]\ncommand = "x"\n')
    for item_id in ("hook:x", "plugin:never@listed", "nonsense", LINEAR + "x", "mcp:dotted.name"):
        with pytest.raises(ProviderStateUnsupportedError):
            switch(adapter, item_id, False)
    assert writes(codex_home) == []


def test_an_app_server_that_cannot_start_makes_switches_unsupported(adapter, codex_home):
    seed(codex_home)
    reconfigure(codex_home, fail_start=True)
    with pytest.raises(ProviderStateUnsupportedError):
        adapter.set_enabled(GITHUB, "user", True, "fp")
    assert writes(codex_home) == []


def test_an_app_server_without_config_read_makes_switches_unsupported(adapter, codex_home):
    seed(codex_home)
    reconfigure(codex_home, missing_methods=["config/read"])
    with pytest.raises(ProviderStateUnsupportedError, match="read-only"):
        switch(adapter, MAIL, False)
    assert writes(codex_home) == []


def test_a_degraded_app_server_keeps_rows_read_only_for_writes(adapter, codex_home):
    seed(codex_home)
    reconfigure(codex_home, missing_methods=["plugin/list"])
    with pytest.raises(ProviderStateUnsupportedError, match="read-only"):
        switch(adapter, MAIL, False)
    assert writes(codex_home) == []


def test_a_missing_write_method_is_unsupported(adapter, codex_home):
    seed(codex_home)
    reconfigure(codex_home, missing_methods=["config/batchWrite", "skills/config/write"])
    for item_id in (MAIL, f"skill:{USER_SKILL}"):
        with pytest.raises(ProviderStateUnsupportedError, match="/(batchWrite|write)"):
            switch(adapter, item_id, True)


def test_a_readonly_layer_answer_is_unsupported(adapter, codex_home):
    seed(codex_home)
    readonly = {
        "code": -32600,
        "message": "Only writes to the user config are allowed",
        "data": {"config_write_error_code": "configLayerReadonly"},
    }
    reconfigure(codex_home, errors={"config/batchWrite": readonly})
    with pytest.raises(ProviderStateUnsupportedError, match="user config"):
        switch(adapter, MAIL, False)


def test_another_rpc_error_is_a_redacted_command_error(adapter, codex_home):
    seed(codex_home)
    leak = "denied Authorization: Bearer hdr_secret_value sk-live_abcdefgh " + "x" * 800
    reconfigure(codex_home, errors={"config/batchWrite": {"code": -32000, "message": leak}})
    with pytest.raises(ProviderCommandError) as caught:
        switch(adapter, MAIL, False)
    message = str(caught.value)
    assert "hdr_secret_value" not in message and "sk-live_abcdefgh" not in message
    assert "denied" in message and len(message) <= 500
    assert "lin_secret_value" not in message and str(codex_home) not in message


def test_switching_is_allowed_outside_the_tested_versions(adapter, codex_home):
    seed(codex_home)
    reconfigure(codex_home, version="0.160.0")
    snapshot = switch(adapter, MAIL, False)
    assert rows(snapshot)[MAIL].enabled is False and snapshot.warnings


def test_a_home_reached_through_a_symlink_keeps_the_link_after_a_write(
    adapter, isolated_provider_homes, monkeypatch, tmp_path
):
    # Only the folder link is asserted: the fake replaces config.toml itself with os.replace, so a
    # symlinked config.toml file is not something this fake can vouch for (the CLI is the writer).
    real = tmp_path / "dotfiles" / "codex"
    real.mkdir(parents=True)
    link = isolated_provider_homes / "linked-codex"
    link.symlink_to(real)
    monkeypatch.setenv("CODEX_HOME", str(link))
    monkeypatch.setenv("PATH", f"{FAKE_DIR}{os.pathsep}{os.environ['PATH']}")
    seed(link)
    switch(adapter, MAIL, False)
    assert link.is_symlink() and link.resolve() == real.resolve()
    assert "[apps.mail]\nenabled = false" in (real / "config.toml").read_text()
