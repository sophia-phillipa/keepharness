"""All generated associations resolve to packaged, locally served SVG symbols."""

import json
import xml.etree.ElementTree as ET

from harness_ui import ASSETS, asset_response


def test_catalog_has_no_missing_symbols_or_duplicate_ids():
    source = (ASSETS / "file-icons-data.js").read_text()
    manifest = json.loads(source.split("window.HarnessFileIcons=", 1)[1].removesuffix(";\n"))
    root = ET.parse(ASSETS / "file-icons.svg").getroot()
    symbols = {node.get("id") for node in root}
    ids = [node.get("id") for node in root.iter() if node.get("id")]
    assert len(ids) == len(set(ids))
    for value in manifest.values():
        assert set(value.values() if isinstance(value, dict) else [value]) <= symbols
    assert len(symbols) > 1000
    for name in ("file-icons.svg", "file-icons-data.js", "file-icons-LICENSE.txt"):
        assert asset_response("/assets/" + name).status_code == 200
    assert asset_response("/assets/../file-icons.svg").status_code == 404
    assert not root.findall(".//{*}script")
