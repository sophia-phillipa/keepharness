"""Conversation execution-mode admission and dispatch contracts."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from agent_service.app import APIError, Service
from agent_service.app import create_app
from starlette.testclient import TestClient
from test_workspaces import config


def service(tmp_path):
    cfg = config(tmp_path)
    cfg['services']['codex']['mode'] = 'scoped'  # legacy fallback only
    cfg['services']['local']['mode'] = 'native'  # local remains intrinsically isolated
    cfg['local'] = {'binary': 'fixture'}
    result = Service(cfg)
    return result, ('a', cfg['clients']['a'])


def payload(instance, identity, job_id):
    return json.loads(instance.job(identity, job_id)['payload'])


def test_new_conversation_defaults_to_native_but_local_is_explicitly_scoped(tmp_path):
    instance, identity = service(tmp_path)
    try:
        codex = instance.submit(identity, {'project_id': 'p', 'backend': 'codex',
                                           'model': 'gpt-6-astra', 'prompt': 'native'})
        local = instance.submit(identity, {'project_id': 'p', 'backend': 'local',
                                           'model': 'installed-model', 'prompt': 'isolated'})
        assert payload(instance, identity, codex['job_id'])['execution_mode'] == 'native'
        assert payload(instance, identity, local['job_id'])['execution_mode'] == 'scoped'
    finally:
        instance.db.close()


def test_continuation_inherits_root_mode_and_cannot_select_again(tmp_path):
    instance, identity = service(tmp_path)
    try:
        first = instance.submit(identity, {'project_id': 'p', 'backend': 'codex',
                                           'model': 'gpt-6-astra', 'prompt': 'first',
                                           'execution_mode': 'scoped'})['job_id']
        instance.finish(first, 'completed', {'answer': 'done'})
        next_job = instance.submit(identity, {'project_id': 'p', 'backend': 'codex',
                                              'model': 'gpt-6-astra', 'prompt': 'next',
                                              'parent_job_id': first})['job_id']
        assert payload(instance, identity, next_job)['execution_mode'] == 'scoped'
        with pytest.raises(APIError, match='conversation_execution_mode_locked'):
            instance.submit(identity, {'project_id': 'p', 'backend': 'codex',
                                       'model': 'gpt-6-astra', 'prompt': 'nope',
                                       'parent_job_id': next_job,
                                       'execution_mode': 'scoped'})
    finally:
        instance.db.close()


def test_legacy_conversation_uses_its_configured_service_mode(tmp_path):
    instance, identity = service(tmp_path)
    try:
        with instance.db:
            instance.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',
                                ('legacy', 'p', 'a', 'completed', 1,
                                 json.dumps({'project_id': 'p', 'backend': 'codex',
                                             'model': 'gpt-6-astra', 'prompt': 'old'}),
                                 json.dumps({'answer': 'done'}), None, 'legacy'))
        child = instance.submit(identity, {'project_id': 'p', 'backend': 'codex',
                                           'model': 'gpt-6-astra', 'prompt': 'continue',
                                           'parent_job_id': 'legacy'})
        assert payload(instance, identity, child['job_id'])['execution_mode'] == 'scoped'
    finally:
        instance.db.close()


def test_legacy_mode_stays_stable_after_its_first_continuation(tmp_path):
    instance, identity = service(tmp_path)
    try:
        with instance.db:
            instance.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',
                                ('legacy', 'p', 'a', 'completed', 1,
                                 json.dumps({'project_id': 'p', 'backend': 'codex',
                                             'model': 'gpt-6-astra', 'prompt': 'old'}),
                                 json.dumps({'answer': 'done'}), None, 'legacy'))
        first = instance.submit(identity, {'project_id': 'p', 'backend': 'codex',
                                           'model': 'gpt-6-astra', 'prompt': 'once',
                                           'parent_job_id': 'legacy'})['job_id']
        instance.finish(first, 'completed', {'answer': 'done'})
        instance.config['services']['codex']['mode'] = 'native'
        second = instance.submit(identity, {'project_id': 'p', 'backend': 'codex',
                                            'model': 'gpt-6-astra', 'prompt': 'again',
                                            'parent_job_id': first})['job_id']
        assert payload(instance, identity, second)['execution_mode'] == 'scoped'
    finally:
        instance.db.close()


def test_local_legacy_native_configuration_is_reported_as_scoped(tmp_path):
    instance, identity = service(tmp_path)
    try:
        with instance.db:
            instance.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',
                                ('legacy-local', 'p', 'a', 'completed', 1,
                                 json.dumps({'project_id': 'p', 'backend': 'local',
                                             'model': 'installed-model', 'prompt': 'old'}),
                                 json.dumps({'answer': 'done'}), None, 'legacy-local'))
        assert instance.conversation_execution_mode(instance.job(identity, 'legacy-local')) == 'scoped'
    finally:
        instance.db.close()


def test_legacy_handoff_freezes_the_latest_provider_mode(tmp_path):
    instance, identity = service(tmp_path)
    try:
        instance.config['services']['codex']['mode'] = 'native'
        with instance.db:
            instance.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',
                                ('local-root', 'p', 'a', 'completed', 1,
                                 json.dumps({'project_id': 'p', 'backend': 'local',
                                             'model': 'installed-model', 'prompt': 'old'}),
                                 json.dumps({'answer': 'done'}), None, 'legacy-root'))
            instance.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',
                                ('codex-child', 'p', 'a', 'completed', 2,
                                 json.dumps({'project_id': 'p', 'backend': 'codex',
                                             'model': 'gpt-6-astra', 'prompt': 'handoff',
                                             'parent_job_id': 'local-root'}),
                                 json.dumps({'answer': 'done'}), None, 'legacy-child'))
        next_job = instance.submit(identity, {'project_id': 'p', 'backend': 'codex',
                                              'model': 'gpt-6-astra', 'prompt': 'continue',
                                              'parent_job_id': 'codex-child'})['job_id']
        assert payload(instance, identity, next_job)['execution_mode'] == 'native'
        assert payload(instance, identity, 'local-root')['execution_mode'] == 'native'
    finally:
        instance.db.close()


def test_unknown_and_internal_execution_mode_inputs_are_rejected_cleanly(tmp_path):
    instance, identity = service(tmp_path)
    try:
        with pytest.raises(APIError, match='default_or_requested_executor_not_available_for_task'):
            instance.submit(identity, {'project_id': 'p', 'backend': 'unknown',
                                       'model': 'fixture', 'prompt': 'bad'})
        with pytest.raises(APIError, match='invalid_internal_field'):
            instance.submit(identity, {'project_id': 'p', 'backend': 'codex',
                                       'model': 'gpt-6-astra', 'prompt': 'bad',
                                       '_maestro_stage': 'spoof'})
    finally:
        instance.db.close()


def test_model_catalog_and_conversation_responses_report_effective_mode(tmp_path):
    instance, identity = service(tmp_path)
    try:
        modes = {model['backend']: model['execution_modes'] for model in instance.models('p')}
        assert modes['codex'] == ['native', 'scoped']
        assert modes['local'] == ['scoped']
        job = instance.submit(identity, {'project_id': 'p', 'backend': 'local',
                                         'model': 'installed-model', 'prompt': 'isolated'})['job_id']
        row = instance.job(identity, job)
        assert instance.execution(row)['execution_mode'] == 'scoped'
    finally:
        instance.db.close()


def test_local_scoped_mode_uses_its_isolated_native_adapter(tmp_path):
    instance, identity = service(tmp_path)
    try:
        job = instance.submit(identity, {'project_id': 'p', 'backend': 'local',
                                         'model': 'installed-model', 'prompt': 'isolated'})['job_id']
        row = instance.job(identity, job)
        data = payload(instance, identity, job)
        with patch('agent_service.app.adapters.run_native', AsyncMock(return_value={'answer': 'ok'})) as native, \
             patch('agent_service.app.adapters.run_scoped', AsyncMock()) as scoped:
            asyncio.run(instance.infer(row, data))
        native.assert_awaited_once()
        scoped.assert_not_awaited()
    finally:
        instance.db.close()


def test_job_and_conversation_api_report_the_locked_mode(tmp_path):
    cfg = config(tmp_path)
    app = create_app(cfg)
    with TestClient(app, headers={'Authorization': 'Bearer a'}) as client:
        created = client.post('/v1/jobs', json={'project_id': 'p', 'backend': 'codex',
                                                'model': 'gpt-6-astra', 'prompt': 'native'})
        assert created.status_code == 202
        job = created.json()['job_id']
        assert created.json()['execution_mode'] == 'native'
        assert client.get('/v1/jobs/' + job).json()['request']['execution_mode'] == 'native'
        conversation = client.get('/v1/conversations/' + job).json()
        assert conversation['execution_mode'] == 'native'
    app.state.service.db.close()
