"""One identity controls runtime names; state cannot cross product lineages."""
import json
from dataclasses import replace

import pytest

from control.product import PRODUCT, ensure_lineage


def test_current_identity_and_paths(tmp_path):
    assert (PRODUCT.name, PRODUCT.slug, PRODUCT.env_prefix) == (
        "Tail Harness", "tail-harness", "TAIL_HARNESS"
    )
    assert PRODUCT.state_path(tmp_path) == tmp_path / ".local/share/tail-harness"
    assert PRODUCT.config_path(tmp_path) == tmp_path / ".config/tail-harness"
    assert PRODUCT.mcp_name == "tail-harness"


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
