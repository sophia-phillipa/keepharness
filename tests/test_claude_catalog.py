import asyncio
import json
import sys
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from Adapters.claude import account, native
from agent_service.app import create_app
from agent_service import maestro
from control.server import Manager
from tests.test_shared_projects import config
from contextlib import contextmanager
from types import SimpleNamespace
from Adapters.claude import scoped


CATALOG = {"models": [
    {"value": "opus", "resolvedModel": "claude-opus-5", "supportsEffort": True,
     "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]},
    {"value": "haiku", "resolvedModel": "claude-haiku-4-5-20251001"},
    {"value": "disabled", "disabled": True},
]}


def test_discovery_runtime_and_admission_preserve_versions_and_efforts(tmp_path):
    manager = Manager(tmp_path / 'control')
    manager.inventory = {'network': {}, 'services': [
        {'id': 'claude', 'found': True, 'binary': sys.executable, 'auth_file': '/missing'}]}
    manager.settings['services']['claude'].update(enabled=True, models=['claude-opus-5', 'haiku'])
    with patch('control.server.command', AsyncMock(return_value=(0, '{"loggedIn":true}'))), \
         patch.object(account, 'metadata', AsyncMock(return_value=CATALOG)):
        runtime = asyncio.run(manager.build_runtime_config(manager.settings))
    assert runtime['claude_models']['claude-opus-5'] == ['configured', 'low', 'medium', 'high', 'xhigh', 'max']
    assert 'disabled' not in manager.provider_models['claude']
    assert manager.provider_models['claude']['opus'] == runtime['claude_models']['claude-opus-5']
    cfg = config(tmp_path)
    cfg['services']['claude'] = runtime['services']['claude']
    cfg['claude_models'] = runtime['claude_models']
    app = create_app(cfg)
    with TestClient(app, headers={'Authorization': 'Bearer a'}) as client:
        models = client.get('/v1/models?project_id=sem-projeto').json()['models']
        assert next(m for m in models if m['id'] == 'claude-opus-5')['efforts'][-1] == 'max'
        available = maestro.candidates(cfg, 'sem-projeto')
        assert next(m for m in available if m['model'] == 'haiku')['efforts'] == ['configured']
        for model, effort, expected in [('claude-opus-5', 'max', 'accept'), ('haiku', 'max', 'unsupported')]:
            result = client.post('/v1/assess', json={'project_id': 'sem-projeto', 'backend': 'claude',
                'model': model, 'effort': effort, 'prompt': 'test', 'execution_mode': 'native'}).json()
            assert result['decision'] == expected, result


def test_control_probe_sends_no_user_prompt_and_stops(tmp_path):
    cli = tmp_path / 'claude'
    cli.write_text('#!' + sys.executable + '\n' + '''import json,sys
for line in sys.stdin:
    message=json.loads(line)
    assert message['type']=='control_request'
    kind=message['request']['subtype']
    value={'models': []} if kind=='initialize' else {'rate_limits': {'five_hour': {'utilization': 25}}}
    print(json.dumps({'type':'control_response','response':{'subtype':'success','request_id':kind,'response':value}}),flush=True)
''')
    cli.chmod(0o700)
    result = asyncio.run(account.metadata({'binary': str(cli)}, 'get_usage'))
    assert account.quota_snapshot(result)['rateLimitsByLimitId']['five_hour']['primary']['usedPercent'] == 25


@pytest.mark.parametrize('effort', ['configured', 'max'])
def test_native_transmits_effort_and_records_it(tmp_path, effort):
    cli = tmp_path / 'claude'
    cli.write_text('#!' + sys.executable + '\n' + f'''import json,sys
args=sys.argv[1:]
assert ('--effort' in args)=={effort != 'configured'}
if '--effort' in args: assert args[args.index('--effort')+1]=={effort!r}
sys.stdin.readline()
print(json.dumps({{'type':'result','subtype':'success','result':'ok'}}),flush=True)
''')
    cli.chmod(0o700)
    with patch.object(native, 'configurations', return_value={'claude': {}}), \
         patch.object(native, 'inventory', return_value={'claude': []}):
        result = asyncio.run(native.run({'binary': str(cli)}, 'test', lambda *_: None,
            tmp_path, 'opus', tmp_path, {}, [], AsyncMock(), effort=effort))
    assert result['effort'] == effort


def test_all_active_legacy_versions_and_only_cli_efforts():
    models = account.model_catalog(CATALOG)
    assert set(account.LEGACY_MODELS) <= models.keys()
    assert models['claude-opus-4-5-20251101'] == ['configured']
    assert models['claude-sonnet-4-5-20250929'] == ['configured']
    assert models['claude-opus-4-6'] == ['configured', 'low', 'medium', 'high', 'max']
    assert 'xhigh' in models['claude-opus-4-8']
    assert 'claude-opus-4-1-20250805' not in models
    disabled = {'models': [*CATALOG['models'], {'value': 'claude-opus-4-8', 'disabled': True}]}
    assert 'claude-opus-4-8' not in account.model_catalog(disabled)


def test_scoped_execution_transmits_effort(tmp_path):
    @contextmanager
    def workspace(*args):
        yield SimpleNamespace(command=['bwrap'], bridge=tmp_path)
    stream = AsyncMock(return_value={'effort': 'high'})
    with patch.object(scoped, 'prepare_scoped', workspace), \
         patch.object(scoped, 'collect_changes', return_value={}), \
         patch.object(scoped, 'stream', stream):
        result = asyncio.run(scoped.run({}, 'test', lambda *_: None, model='claude-opus-4-6', effort='high'))
    args = stream.await_args.args
    assert args[0][-2:] == ['--effort', 'high'] and args[-1] == 'high'
    assert result['effort'] == 'high'


def test_extended_context_model_ids_survive_settings_validation(tmp_path):
    manager = Manager(tmp_path)
    manager.settings['services']['claude']['models'] = ['claude-opus-5[1m]']
    assert manager.validate(manager.settings)['services']['claude']['models'] == ['claude-opus-5[1m]']
    manager.settings['services']['claude']['models'] = ['claude-opus-5[anything]']
    with pytest.raises(ValueError, match='Invalid model list'):
        manager.validate(manager.settings)
