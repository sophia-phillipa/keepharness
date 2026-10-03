"""One identity controls runtime names; state cannot cross product lineages."""
import json
from dataclasses import replace

import pytest

from control.product import PRODUCT, ensure_lineage, migrate_legacy_state

# KeepHarness was named Tail Harness before 0.15.0; its state is adopted once.
LEGACY = {"slug": "tail-harness", "lineage": "tail-harness"}
CURRENT = {"slug": "keepharness", "lineage": "keepharness"}


def test_current_identity_and_paths(tmp_path):
    assert (PRODUCT.name, PRODUCT.slug, PRODUCT.env_prefix) == (
        "KeepHarness", "keepharness", "KEEPHARNESS"
    )
    assert PRODUCT.state_path(tmp_path) == tmp_path / ".local/share/keepharness"
    assert PRODUCT.config_path(tmp_path) == tmp_path / ".config/keepharness"
    assert PRODUCT.mcp_name == "keepharness"


def test_lineage_rejects_cross_identity_before_mutation(tmp_path):
    ensure_lineage(tmp_path)
    before = (tmp_path / "harness.identity.json").read_bytes()
    with pytest.raises(ValueError, match="identity"):
        ensure_lineage(tmp_path, replace(PRODUCT, slug="synthetic-harness", lineage="synthetic"))
    assert (tmp_path / "harness.identity.json").read_bytes() == before


def test_only_original_identity_adopts_unmarked_existing_state(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"projects": []}))
    with pytest.raises(ValueError, match="unmarked"):
        ensure_lineage(tmp_path, replace(PRODUCT, slug="synthetic-harness", lineage="synthetic"))
    ensure_lineage(tmp_path)


def test_lineage_does_not_follow_symlink(tmp_path):
    other = tmp_path / "other"
    other.write_text('{}')
    (tmp_path / "harness.identity.json").symlink_to(other)
    with pytest.raises(ValueError, match="identity"):
        ensure_lineage(tmp_path)
    assert other.read_text() == '{}'


def test_changed_slug_cannot_adopt_unmarked_upstream(tmp_path):
    (tmp_path / 'settings.json').write_text('{}')
    with pytest.raises(ValueError, match='unmarked'):
        ensure_lineage(tmp_path, replace(PRODUCT, slug='synthetic-harness'))


def test_owner_enrollment_rejects_cross_identity(tmp_path, monkeypatch):
    from control import cli
    ensure_lineage(tmp_path)
    (tmp_path / 'runtime.json').write_text(json.dumps({'clients': {'local': {}}, 'state_dir': str(tmp_path / 'runs'), 'port': 8095, 'origins': ['http://127.0.0.1:8095']}))
    monkeypatch.setattr(cli, 'PRODUCT', replace(PRODUCT, slug='synthetic-harness', lineage='synthetic'))
    with pytest.raises(SystemExit):
        cli.main(['--state', str(tmp_path), 'approve-device', '--owner', 'local', '--yes'])
    assert not (tmp_path / 'runs').exists()


def legacy_state(home):
    old = home / ".local/share/tail-harness"
    (old / "runs").mkdir(parents=True)
    for folder in (old, old / "runs"):
        (folder / "harness.identity.json").write_text(json.dumps(LEGACY))
    (old / "settings.json").write_text("{}")
    runtime = {"state_dir": str(old / "runs"), "control_state_dir": str(old), "port": 8095}
    (old / "runtime.json").write_text(json.dumps(runtime))
    return old


def test_tail_harness_state_and_config_move_to_keepharness_once(tmp_path):
    old = legacy_state(tmp_path)
    (tmp_path / ".config/tail-harness").mkdir(parents=True)
    (tmp_path / ".config/tail-harness/client.json").write_text('{"url": "http://vpn:8095"}')
    migrate_legacy_state(tmp_path)
    new = PRODUCT.state_path(tmp_path)
    assert not old.exists() and (new / "settings.json").read_text() == "{}"
    assert json.loads((new / "harness.identity.json").read_text()) == CURRENT
    assert json.loads((new / "runtime.json").read_text()) == {
        "state_dir": str(new / "runs"), "control_state_dir": str(new), "port": 8095
    }
    assert not (tmp_path / ".config/tail-harness").exists()
    assert (PRODUCT.config_path(tmp_path) / "client.json").read_text() == '{"url": "http://vpn:8095"}'
    ensure_lineage(new / "runs")
    assert json.loads((new / "runs/harness.identity.json").read_text()) == CURRENT
    migrate_legacy_state(tmp_path)
    assert sorted(entry.name for entry in new.iterdir()) == [
        "harness.identity.json", "runs", "runtime.json", "settings.json"
    ]


def test_unmarked_tail_harness_state_moves_like_marked_state(tmp_path):
    old = legacy_state(tmp_path)
    (old / "harness.identity.json").unlink()
    migrate_legacy_state(tmp_path)
    assert json.loads((PRODUCT.state_path(tmp_path) / "harness.identity.json").read_text()) == CURRENT


def test_migration_never_merges_into_an_existing_keepharness_folder(tmp_path):
    old = legacy_state(tmp_path)
    new = PRODUCT.state_path(tmp_path)
    new.mkdir(parents=True)
    (tmp_path / ".config/tail-harness").mkdir(parents=True)
    PRODUCT.config_path(tmp_path).mkdir(parents=True)
    migrate_legacy_state(tmp_path)
    assert (old / "settings.json").exists() and list(new.iterdir()) == []
    assert (tmp_path / ".config/tail-harness").exists()


def test_migration_leaves_another_identity_state_alone(tmp_path):
    old = legacy_state(tmp_path)
    other = {"slug": "synthetic-harness", "lineage": "synthetic"}
    (old / "harness.identity.json").write_text(json.dumps(other))
    migrate_legacy_state(tmp_path)
    assert json.loads((old / "harness.identity.json").read_text()) == other
    assert not PRODUCT.state_path(tmp_path).exists()


def test_a_fork_never_takes_tail_harness_state(tmp_path):
    old = legacy_state(tmp_path)
    fork = replace(PRODUCT, slug="synthetic-harness", lineage="synthetic", state_dir=".local/share/synthetic-harness", config_dir=".config/synthetic-harness")
    migrate_legacy_state(tmp_path, fork)
    assert old.exists() and not fork.state_path(tmp_path).exists()
    with pytest.raises(ValueError, match="identity"):
        ensure_lineage(old, fork)
    assert json.loads((old / "harness.identity.json").read_text()) == LEGACY


def test_keepharness_adopts_a_tail_harness_marker_in_place(tmp_path):
    (tmp_path / "harness.identity.json").write_text(json.dumps(LEGACY))
    ensure_lineage(tmp_path)
    assert json.loads((tmp_path / "harness.identity.json").read_text()) == CURRENT
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["harness.identity.json"]


def test_cli_moves_the_default_state_but_not_an_explicit_one(tmp_path, monkeypatch):
    from control import cli
    monkeypatch.setenv("HOME", str(tmp_path))
    old = legacy_state(tmp_path)
    with pytest.raises(SystemExit):
        cli.main(["--state", str(old), "approve-device", "--owner", "local", "--yes"])
    assert old.is_dir() and not PRODUCT.state_path(tmp_path).exists()
    assert json.loads((old / "harness.identity.json").read_text()) == CURRENT
    with pytest.raises(SystemExit):
        cli.main(["approve-device", "--owner", "local", "--yes"])
    assert not old.exists() and (PRODUCT.state_path(tmp_path) / "runs").is_dir()
