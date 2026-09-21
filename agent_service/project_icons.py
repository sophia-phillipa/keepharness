"""Bounded, local-only discovery of project branding for sidebar images."""
import base64
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

MIMES = {'.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon',
         '.webp': 'image/webp', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}
LIMIT = 128 * 1024
FOLDERS = ('', 'public', 'static', 'assets', 'src/assets', 'public/assets', 'tail_ui/assets')


class IconLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'link' and set(attrs.get('rel', '').lower().split()) & {'icon', 'apple-touch-icon'}:
            if attrs.get('href'):
                self.links.append(attrs['href'])


def discover_project_icon(root):
    if not root:
        return None
    try:
        root = Path(root).resolve()
    except (OSError, ValueError, RuntimeError):
        return None

    def read(path):
        try:
            resolved = path.resolve()
            if not resolved.is_relative_to(root) or not resolved.is_file():
                return None
            with resolved.open('rb') as stream:
                data = stream.read(LIMIT + 1)
            return data if 0 < len(data) <= LIMIT else None
        except (OSError, ValueError, RuntimeError):
            return None

    def image(path, fragment=''):
        mime = MIMES.get(path.suffix.lower())
        if not mime:
            return None
        data = read(path)
        if not data:
            return None
        if mime == 'image/svg+xml':
            try:
                svg = ET.fromstring(data)
                if fragment:
                    symbol = next((node for node in svg.iter() if node.get('id') == fragment), None)
                    if symbol is None:
                        return None
                    svg = ET.Element('{http://www.w3.org/2000/svg}svg', {'viewBox': symbol.get('viewBox', '0 0 24 24')})
                    svg.extend(list(symbol))
                if svg.tag.rsplit('}', 1)[-1] != 'svg':
                    return None
                data = ET.tostring(svg)
            except ET.ParseError:
                return None
        # Render as an img data URL, never inject project SVG into the DOM.
        return {'src': 'data:' + mime + ';base64,' + base64.b64encode(data).decode(),
                'path': path.relative_to(root).as_posix() + ('#' + fragment if fragment else '')}

    for entry in ('index.html', 'public/index.html', 'src/index.html', 'agent_service/index.html'):
        path = root / entry
        data = read(path)
        if not data:
            continue
        parser = IconLinks()
        parser.feed(data.decode('utf-8', errors='replace'))
        for href in parser.links[:8]:
            try:
                url = urlsplit(href)
            except ValueError:
                continue
            if url.scheme or url.netloc:
                continue
            relative = unquote(url.path).lstrip('/')
            bases = (root / 'public', root) if url.path.startswith('/') else (path.parent,)
            for base in bases:
                result = image(base / relative, url.fragment)
                if result:
                    return result
    # ponytail: conventional paths only; add framework manifests when needed.
    for name in ('logo', 'icon', 'favicon', 'apple-touch-icon'):
        for folder in FOLDERS:
            for suffix in MIMES:
                result = image(root / folder / (name + suffix))
                if result:
                    return result
    # A project's named symbol can be its logo inside a shared SVG sprite.
    project_id = re.sub(r'[^a-z0-9]+', '-', root.name.lower()).strip('-')
    for folder in FOLDERS:
        result = image(root / folder / 'icons.svg', project_id)
        if result:
            return result
    return None
