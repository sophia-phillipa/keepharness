"""Persistent conversation-title API behavior."""
import hashlib
import json

import pytest
from starlette.testclient import TestClient

from agent_service.app import create_app


@pytest.fixture
def api(tmp_path):
    config = {
        'state_dir': str(tmp_path), 'origins': ['http://testserver'],
        'projects': {'shared': {}},
        'clients': {name: {'sha256': hashlib.sha256(name.encode()).hexdigest(),
                           'projects': ['shared']} for name in ('alice', 'bob')},
        'services': {'codex': {'enabled': True, 'models': ['fixture'],
                               'projects': ['shared'], 'permissions': {}}},
        'codex_models': {'fixture': ['low']},
    }
    app = create_app(config)
    client = TestClient(app, headers={'Authorization': 'Bearer alice'})
    with app.state.service.db:
        app.state.service.db.execute(
            'INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',
            ('conversation-1', 'shared', 'alice', 'completed', 1,
             json.dumps({'prompt': 'Original prompt'}), json.dumps({}), None, 'fixture'))
    yield client
    client.close()
    app.state.service.db.close()


def test_rename_persists_in_list_without_changing_prompt(api):
    response = api.patch('/v1/conversations/conversation-1', json={'title': '  Renamed  '})
    assert response.status_code == 200
    assert response.json() == {'id': 'conversation-1', 'title': 'Renamed'}
    assert api.get('/v1/conversations').json()['conversations'][0]['title'] == 'Renamed'
    assert api.get('/v1/conversations/conversation-1').json()['turns'][0]['request']['prompt'] == 'Original prompt'


def test_rename_requires_conversation_owner(api):
    response = api.patch('/v1/conversations/conversation-1',
                         headers={'Authorization': 'Bearer bob'}, json={'title': 'Private'})
    assert response.status_code == 403


@pytest.mark.parametrize('title', ['', '   ', 5, 'x' * 101])
def test_rename_rejects_invalid_title(api, title):
    response = api.patch('/v1/conversations/conversation-1', json={'title': title})
    assert response.status_code == 422
    assert response.json()['code'] == 'invalid_conversation_title'


def test_message_attachment_metadata_survives_history_and_job_read(api):
    service = api.app.state.service
    with service.db:
        for fid, owner, name, pages in [('image', 'alice', 'photo.png', [{'media_type': 'image/png'}]),
                                         ('doc', 'alice', 'notes.txt', []),
                                         ('private', 'bob', 'private.png', [{'media_type': 'image/png'}])]:
            service.db.execute('INSERT INTO files(id,project,owner,name,size,hash,pages) VALUES(?,?,?,?,?,?,?)',
                               (fid, 'shared', owner, name, 1, 'hash', json.dumps(pages)))
        service.db.execute('UPDATE jobs SET payload=? WHERE id=?',
                           (json.dumps({'prompt': 'Inspect', 'file_ids': ['image', 'doc', 'private', 'missing']}), 'conversation-1'))
    expected = [{'id': 'image', 'name': 'photo.png', 'preview_url': '/v1/files/image/preview', 'media_type': 'image/png'},
                {'id': 'doc', 'name': 'notes.txt'}]
    assert api.get('/v1/conversations/conversation-1').json()['turns'][0]['attachments'] == expected
    assert api.get('/v1/jobs/conversation-1').json()['attachments'] == expected
