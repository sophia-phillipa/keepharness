"""Local owner provisioning is write-only and survives settings/runtime round trips."""
import copy

from starlette.testclient import TestClient

from control.runtime_config import base_config
from control.server import create_app


def client_for(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    app = create_app(tmp_path / 'state')
    manager = app.state.manager
    manager.settings['projects'] = [{'id': 'demo', 'root': str(project), 'catalogs': []}]
    client = TestClient(app, base_url='http://127.0.0.1:8094')
    client.get('/')
    return client, manager


def test_vault_write_only_and_bindings_round_trip(tmp_path):
    client, manager = client_for(tmp_path)
    contract = {'integration': 'issue-reader', 'consumers': ['codex'], 'environment': {'ISSUE_TOKEN': 'token'}, 'precedence': 'vault', 'mediated': False}
    secret = 'fake-secret-vault-admin-123456'
    response = client.post('/api/vault', headers={'X-Harness-Admin': '1'}, json={'action': 'set', 'binding': 'demo-reader', 'values': {'token': secret}, 'project_id': 'demo', 'integration': 'issue-reader', 'contract': contract})
    assert response.status_code == 200, response.text
    assert secret not in response.text
    status = client.get('/api/vault')
    assert status.json()['credentials'] == [{'binding': 'demo-reader', 'fields': ['token']}]
    assert secret not in client.post('/api/settings-export', headers={'X-Harness-Admin': '1'}, json={}).text
    draft = copy.deepcopy(manager.settings)
    validated = manager.validate(draft)
    runtime = base_config(validated, manager.state, 8094, 'http://localhost/', {})
    assert runtime['integration_bindings'][0]['project_id'] == 'demo'
    assert runtime['integrations'] == [contract]
    assert secret not in str(runtime)
    assert (manager.state / 'harness.secrets.json').stat().st_mode & 0o777 == 0o600


def test_vault_denies_missing_admin_header_and_unknown_scope(tmp_path):
    client, manager = client_for(tmp_path)
    assert client.post('/api/vault', json={}).status_code == 400
    response = client.post('/api/vault', headers={'X-Harness-Admin': '1'}, json={'action': 'set', 'binding': 'x', 'values': {'token': 'fake-not-stored'}, 'project_id': 'other', 'integration': 'x'})
    assert response.status_code == 400
    assert not (manager.state / 'harness.secrets.json').exists()
