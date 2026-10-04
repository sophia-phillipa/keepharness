import base64
from pathlib import Path

from starlette.testclient import TestClient
from test_project_browser import config

from agent_service.app import create_app
from agent_service.project_icons import discover_project_icon

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M0 0h24v24H0z"/></svg>'


def test_discovery_precedence_limits_and_local_boundaries(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    assert discover_project_icon(root) is None
    (root / "logo.svg").write_text(SVG)
    assert discover_project_icon(root)["path"] == "logo.svg"
    (root / "public").mkdir()
    (root / "public" / "brand.svg").write_text(SVG)
    (root / "index.html").write_text('<link rel="icon" href="/brand.svg">')
    assert discover_project_icon(root)["path"] == "public/brand.svg"
    for href in (
        "https://example.com/logo.svg",
        "//example.com/logo.svg",
        "../private.svg",
        "http://[broken",
    ):
        (tmp_path / "private.svg").write_text(SVG)
        (root / "index.html").write_text('<link rel="icon" href="' + href + '">')
        assert discover_project_icon(root)["path"] == "logo.svg"
    (root / "logo.svg").unlink()
    (root / "logo.svg").symlink_to(tmp_path / "private.svg")
    assert discover_project_icon(root) is None
    (root / "logo.svg").unlink()
    (root / "logo.svg").write_text("x" * 131073)
    assert discover_project_icon(root) is None
    (root / "logo.svg").write_text("invalid SVG")
    assert discover_project_icon(root) is None
    assert discover_project_icon(None) is None


def test_harness_main_logo_is_extracted_from_sprite(tmp_path):
    # Sprite lookup uses the project folder name; a worktree may have any name.
    root = tmp_path / "keepharness"
    assets = root / "harness_ui" / "assets"
    assets.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "harness_ui" / "assets" / "icons.svg"
    (assets / "icons.svg").write_bytes(source.read_bytes())
    result = discover_project_icon(root)
    assert result["path"] == "harness_ui/assets/icons.svg#keepharness"
    svg = base64.b64decode(result["src"].split(",")[1])
    assert b'viewBox="0 0 40 40"' in svg
    assert b"symbol" not in svg


def test_catalog_only_returns_icons_for_authorized_projects(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "logo.svg").write_text(SVG)
    cfg = config(tmp_path)
    cfg["projects"]["p"]["root"] = str(root)
    cfg["projects"]["private"] = {"root": str(root)}
    app = create_app(cfg)
    with TestClient(app, headers={"Authorization": "Bearer a"}) as client:
        response = client.get("/v1/projects")
        assert response.status_code == 200
        details = response.json()["details"]
        assert details["p"]["icon"]["path"] == "logo.svg"
        assert details["sem-projeto"]["icon"] is None
        assert "private" not in details
    app.state.service.db.close()


def test_catalog_groups_legacy_aliases_without_removing_project_ids(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    cfg = config(tmp_path)
    cfg["projects"]["p"] = {"root": str(root), "label": "Project"}
    cfg["projects"]["p2"] = {"root": str(alias), "label": "Project"}
    cfg["projects"]["distinct"] = {"root": str(root), "label": "Different configuration"}
    cfg["clients"]["a"]["projects"] += ["p2", "distinct"]
    app = create_app(cfg)
    with TestClient(app, headers={"Authorization": "Bearer a"}) as client:
        data = client.get("/v1/projects").json()
        assert data["details"]["p2"]["canonical_id"] == "p"
        assert data["details"]["distinct"]["canonical_id"] == "distinct"
        assert "p2" in data["projects"]
    app.state.service.db.close()
