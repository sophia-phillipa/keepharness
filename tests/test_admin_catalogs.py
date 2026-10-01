"""Pins move only after a current owner preview; admin drift uses effective roots."""
import subprocess

from starlette.testclient import TestClient

from control.server import create_app


def test_pin_preview_move_and_stale_preview(tmp_path):
    root = tmp_path / 'catalog'
    root.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
    git('init', '-q')
    git('config', 'user.email', 'fixture@example.invalid')
    git('config', 'user.name', 'Fixture')
    (root / 'commands').mkdir()
    command = root / 'commands/check.md'
    command.write_text('Check the first revision.')
    git('add', '.')
    git('commit', '-qm', 'first')
    first = git('rev-parse', 'HEAD')
    command.write_text('Check the second revision.')
    git('commit', '-qam', 'second')
    second = git('rev-parse', 'HEAD')
    project = tmp_path / 'project'
    project.mkdir()
    app = create_app(tmp_path / 'state')
    manager = app.state.manager
    manager.settings['catalogs'] = [{'id': 'demo', 'namespace': 'demo', 'kind': 'git', 'root': str(root), 'trusted': True}]
    manager.settings['projects'] = [{'id': 'p', 'root': str(project), 'catalogs': ['demo']}]
    client = TestClient(app, base_url='http://127.0.0.1:8094')
    client.get('/')
    def post(action, **data):
        return client.post('/api/catalog-pin', headers={'X-Harness-Admin': '1'}, json={'action': action, 'project_id': 'p', 'catalog_id': 'demo', **data})
    pinned = post('pin', ref=first)
    assert pinned.status_code == 200, pinned.text
    preview = post('preview', ref=second)
    assert preview.status_code == 200, preview.text
    assert preview.json()['diff']
    assert manager.settings['projects'][0]['catalog_pins']['demo']['commit'] == first
    assert post('update', preview_token='untrusted').status_code == 400
    assert post('update', preview_token=preview.json()['preview_token']).status_code == 200
    assert manager.settings['projects'][0]['catalog_pins']['demo']['commit'] == second
    assert post('update', preview_token=preview.json()['preview_token']).status_code == 400
    status = client.get('/api/catalogs')
    assert status.status_code == 200, status.text
    assert status.json()['projects'][0]['catalog_pins']['demo']['commit'] == second
