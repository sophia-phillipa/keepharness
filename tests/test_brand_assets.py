"""Both apps link the brand icons in their pages and serve them as images."""

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from control.server import create_app as create_admin_app

ROOT = Path(__file__).resolve().parents[1]
PAGES = ("agent_service/index.html", "control/index.html")
ICONS = ("/assets/favicon.ico", "/assets/apple-touch-icon.png")


@pytest.fixture(params=["harness", "admin"])
def app_client(request, tmp_path):
    if request.param == "harness":
        yield request.getfixturevalue("client")
        return
    with TestClient(create_admin_app(str(tmp_path)), base_url="http://127.0.0.1:8094") as admin:
        yield admin


@pytest.mark.parametrize("page", PAGES)
def test_page_links_the_brand_icons(page):
    html = (ROOT / page).read_text(encoding="utf-8")
    for href in ICONS:
        assert f'href="{href}"' in html


@pytest.mark.parametrize("path", ICONS)
def test_brand_icon_is_served_as_an_image(app_client, path):
    response = app_client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/")
    assert response.content
