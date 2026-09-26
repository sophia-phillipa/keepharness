import base64
"""Authenticated previews are limited to validated raster uploads."""
import hashlib
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from agent_service.app import create_app


def config(tmp_path):
    return {
        'state_dir': str(tmp_path), 'origins': [], 'uploads_enabled': True,
        'projects': {'p': {}},
        'clients': {name: {'sha256': hashlib.sha256(name.encode()).hexdigest(), 'projects': ['p']}
                    for name in ('alice', 'bob')},
        'services': {'codex': {'enabled': True, 'models': ['fixture'], 'projects': ['p'],
                               'permissions': {'read': True, 'upload': True}}},
        'codex_models': {'fixture': ['low']},
    }


@pytest.mark.requires_media_sandbox
def test_preview_is_private_validated_image_bytes_only(tmp_path):
    app = create_app(config(tmp_path))
    with TestClient(app, headers={'Authorization': 'Bearer alice'}) as client, \
            patch.object(app.state.service, 'validate_images', new=AsyncMock()):
        image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aJ1sAAAAASUVORK5CYII=')
        upload = client.post('/v1/files?project_id=p&backend=codex&model=fixture',
                             headers={'X-Filename': 'preview.png'}, content=image)
        assert upload.status_code == 201, upload.text
        payload = upload.json()
        assert payload['media_type'] == 'image/png'
        assert payload['preview_url'] == '/v1/files/'+payload['file_id']+'/preview'
        preview = client.get(payload['preview_url'])
        assert preview.status_code == 200
        assert preview.content == image
        assert preview.headers['content-type'] == 'image/png'
        assert preview.headers['cache-control'] == 'no-store'
        assert preview.headers['x-content-type-options'] == 'nosniff'
        assert client.get(payload['preview_url'], headers={'Authorization': 'Bearer bob'}).status_code == 404
        anonymous = TestClient(app)
        assert anonymous.get(payload['preview_url']).status_code == 401
        anonymous.close()
        text = client.post('/v1/files?project_id=p', headers={'X-Filename': 'note.txt'}, content=b'plain text')
        assert text.status_code == 201, text.text
        assert 'preview_url' not in text.json()
        assert client.get('/v1/files/'+text.json()['file_id']+'/preview').status_code == 404
    app.state.service.db.close()
