"""The brand icons: both apps link and serve them, the files have the sizes and transparency the
packagers expect, and the in-app mark is the shared sprite symbol."""

import struct
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from control.server import create_app as create_admin_app

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "desktop" / "build"
ASSETS = ROOT / "harness_ui" / "assets"
PAGES = ("agent_service/index.html", "control/index.html")
ICONS = ("/assets/favicon.ico", "/assets/apple-touch-icon.png")
LINUX_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
FAVICON_SIZES = (16, 32, 48)
ICNS_SIZES = {b"ic07": 128, b"ic08": 256, b"ic09": 512, b"ic10": 1024}
NAVY, TEAL, CORAL = "#071f43", "#3ecdbe", "#fc785d"
SVG = "{http://www.w3.org/2000/svg}"


def decode_png(data):
    """(width, height, bytes per pixel, rows) of an 8-bit RGB or RGBA PNG, without an imaging library."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    position, idat = 8, b""
    while position < len(data):
        length, kind = struct.unpack(">I4s", data[position : position + 8])
        body = data[position + 8 : position + 8 + length]
        if kind == b"IHDR":
            width, height, depth, color = struct.unpack(">IIBB", body[:10])
        elif kind == b"IDAT":
            idat += body
        position += 12 + length
    assert depth == 8 and color in (2, 6)
    bpp = 3 if color == 2 else 4
    raw, stride, rows, previous = zlib.decompress(idat), width * bpp, [], bytes(width * bpp)
    for y in range(height):
        kind = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1 : (y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = previous[i]
            c = previous[i - bpp] if i >= bpp else 0
            if kind == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                predictor = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
            else:
                predictor = (0, a, b, (a + b) >> 1)[kind]
            line[i] = (line[i] + predictor) & 255
        rows.append(bytes(line))
        previous = line
    return width, height, bpp, rows


def png_size(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def ico_entries(path):
    data = path.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1)
    sizes = []
    for i in range(count):
        width, _, _, _, _, _, length, offset = struct.unpack("<BBBBHHII", data[6 + 16 * i : 22 + 16 * i])
        assert png_size(data[offset : offset + length]) == (width or 256,) * 2
        sizes.append(width or 256)
    return sizes


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


def test_every_icon_file_has_its_size():
    assert png_size((BUILD / "icon.png").read_bytes()) == (1024, 1024)
    for size in LINUX_SIZES:
        assert png_size((BUILD / "icons" / f"{size}x{size}.png").read_bytes()) == (size, size)
    assert ico_entries(BUILD / "icon.ico") == list(ICO_SIZES)
    assert ico_entries(ASSETS / "favicon.ico") == list(FAVICON_SIZES)
    assert png_size((ASSETS / "apple-touch-icon.png").read_bytes()) == (180, 180)
    icns = (BUILD / "icon.icns").read_bytes()
    assert icns[:4] == b"icns" and struct.unpack(">I", icns[4:8])[0] == len(icns)
    position, found = 8, {}
    while position < len(icns):
        kind, length = struct.unpack(">4sI", icns[position : position + 8])
        found[kind] = png_size(icns[position + 8 : position + length])[0]
        position += length
    assert found == ICNS_SIZES


@pytest.mark.parametrize("size", LINUX_SIZES)
def test_icon_corners_are_transparent_and_the_body_is_not(size):
    width, height, bpp, rows = decode_png((BUILD / "icons" / f"{size}x{size}.png").read_bytes())
    assert bpp == 4
    alpha = lambda x, y: rows[y][x * 4 + 3]  # noqa: E731
    assert [alpha(0, 0), alpha(width - 1, 0), alpha(0, height - 1), alpha(width - 1, height - 1)] == [0] * 4
    # Full bleed: the squircle touches the middle of every side and fills the centre.
    middle = width // 2
    assert [alpha(middle, 0), alpha(0, middle), alpha(width - 1, middle), alpha(middle, height - 1)] == [255] * 4
    assert alpha(middle, middle) == 255


def test_apple_touch_icon_is_opaque_and_full_bleed_navy():
    width, height, bpp, rows = decode_png((ASSETS / "apple-touch-icon.png").read_bytes())
    assert bpp == 3  # iOS paints transparency black and rounds the corners itself
    navy = tuple(int(NAVY[i : i + 2], 16) for i in (1, 3, 5))
    for x, y in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
        assert tuple(rows[y][x * 3 : x * 3 + 3]) == navy


def test_the_in_app_mark_is_the_brand_symbol_in_the_shared_sprite():
    sprite = ET.parse(ASSETS / "icons.svg").getroot()
    symbol = next(node for node in sprite.iter(f"{SVG}symbol") if node.get("id") == "keepharness")
    assert symbol.get("viewBox") == "0 0 40 40"
    fills = {node.get("fill") for node in symbol.iter(f"{SVG}circle")}
    strokes = {node.get("stroke") for node in symbol.iter()}
    assert TEAL in fills and CORAL in strokes and TEAL in strokes
    assert len(list(symbol.iter(f"{SVG}mask"))) == 1
    assert "currentColor" not in ET.tostring(symbol, encoding="unicode")
    for page in PAGES:
        assert 'href="/assets/icons.svg#keepharness"' in (ROOT / page).read_text(encoding="utf-8")
    assert "⌘</span><span data-product-name>" not in (ROOT / "control/index.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["keepharness-mark.svg", "keepharness-mark-only.svg"])
def test_the_vector_marks_hold_the_brand_geometry(name):
    root = ET.parse(BUILD / name).getroot()
    assert root.get("viewBox") == "0 0 1024 1024"
    circles = [node for node in root.iter(f"{SVG}circle") if node.get("fill") == TEAL]
    assert len(circles) == 3  # the three nodes
    assert len(list(root.iter(f"{SVG}line"))) == 3  # joined in a triangle
    assert len(list(root.iter(f"{SVG}mask"))) == 1  # the gaps cut around the nodes
    has_squircle = any(node.get("fill") == NAVY for node in root.iter(f"{SVG}rect"))
    assert has_squircle == (name == "keepharness-mark.svg")
