"""Build offline assets from an unpacked Material Icon Theme package and manifest.

Usage: python scripts/vendor-file-icons.py PACKAGE_DIR MANIFEST_JSON
Generate the manifest with the package's generateManifest() export first.
"""
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

package, manifest = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text())
version = json.loads((package / 'package.json').read_text())['version']
keys = ('fileNames', 'fileExtensions', 'folderNames', 'folderNamesExpanded',
        'file', 'folder', 'folderExpanded')
data = {key: manifest[key] for key in keys}
references = set()
for value in data.values():
    references.update(value.values() if isinstance(value, dict) else [value])

namespace = 'http://www.w3.org/2000/svg'
ET.register_namespace('', namespace)
ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')
sprite = ET.Element(f'{{{namespace}}}svg')
for name in sorted(references):
    filename = Path(manifest['iconDefinitions'][name]['iconPath']).name
    symbol = ET.parse(package / 'icons' / filename).getroot()
    symbol.tag = f'{{{namespace}}}symbol'
    symbol.set('id', name)
    symbol.attrib.pop('width', None)
    symbol.attrib.pop('height', None)
    ids = {node.get('id'): name + '-' + node.get('id')
           for node in symbol.iter() if node is not symbol and node.get('id')}
    for node in symbol.iter():
        for attribute, value in list(node.attrib.items()):
            if attribute == 'id' and node is not symbol:
                value = ids[value]
            else:
                for old, new in ids.items():
                    value = value.replace(f'url(#{old})', f'url(#{new})')
                    if value == '#' + old:
                        value = '#' + new
            node.set(attribute, value)
    sprite.append(symbol)

output = Path(__file__).resolve().parents[1] / 'tail_ui' / 'assets'
ET.ElementTree(sprite).write(output / 'file-icons.svg', encoding='unicode')
(output / 'file-icons-data.js').write_text(
    f'// Material Icon Theme {version}, MIT; see file-icons-LICENSE.txt. Generated from generateManifest().\n'
    + 'window.TailFileIcons=' + json.dumps(data, separators=(',', ':')) + ';\n')
(output / 'file-icons-LICENSE.txt').write_text((package / 'LICENSE').read_text())
print(f'Bundled {len(references)} icons from Material Icon Theme {version}.')
