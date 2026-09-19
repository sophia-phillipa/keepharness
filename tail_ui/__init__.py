"""Shared, offline UI assets for the admin and conversation interfaces."""
from pathlib import Path
from starlette.responses import FileResponse, Response

ASSETS = Path(__file__).with_name('assets')
PUBLIC = frozenset(('tabler.min.css', 'tabler.min.js', 'themes.css', 'theme.js', 'components.js', 'icons.svg', 'inter-latin.woff2'))

def asset_response(path):
    name = path.removeprefix('/assets/')
    if name not in PUBLIC:
        return Response(status_code=404)
    return FileResponse(ASSETS / name, headers={'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff'})
