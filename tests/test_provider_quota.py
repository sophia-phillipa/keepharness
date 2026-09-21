"""Claude quota is a reported fraction, never inferred from missing data."""
import json
import time
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient
from agent_service.app import create_app
from Adapters.claude.stream import Stream, rate_limit_update
from tests.test_shared_projects import config
from Adapters.claude import account
from unittest.mock import AsyncMock
import asyncio


@pytest.mark.parametrize('value,expected',[(0,0),(0.786,78.6),(1,100),(None,None),('0.5',None),(True,None),(42,None),(float('nan'),None)])
def test_quota_fraction_is_explicit(value,expected):
    update=rate_limit_update({'rate_limit_info':{'rateLimitType':'five_hour','utilization':value}})
    actual=update['rateLimitsByLimitId']['five_hour']['primary']['usedPercent']
    assert actual is None if expected is None else actual==pytest.approx(expected)
    assert update['available']==(expected is not None)


def test_claude_stream_quota_cache_is_private_and_expires(tmp_path):
    app=create_app(config(tmp_path));service=app.state.service
    with service.db:
        service.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',
                           ('quota-job','sem-projeto','a','completed',1,json.dumps({}),json.dumps({}),None,'fixture'))
    stream=Stream(lambda kind,data:service.event('quota-job',kind,data))
    with TestClient(app,headers={'Authorization':'Bearer a'}) as client:
        assert not client.get('/v1/usage?backend=claude').json()['available']
        assert client.get('/v1/usage?backend=deepseek').json()=={'provider':'deepseek','available':False,'reason':'quota_not_reported'}
        reset=time.time()+3600
        stream.consume({'type':'rate_limit_event','rate_limit_info':{'rateLimitType':'five_hour','utilization':.25,'resetsAt':reset}})
        stream.consume({'type':'rate_limit_event','rate_limit_info':{'rateLimitType':'seven_day','utilization':.6,'resetsAt':reset}})
        result=client.get('/v1/usage?backend=claude').json()
        assert result['available'] and len(result['rateLimitsByLimitId'])==2
        assert result['rateLimitsByLimitId']['five_hour']['primary']['usedPercent']==25
        assert not service.observed_claude_quota('other-owner')['available']
        with patch('agent_service.app.time.time',return_value=time.time()+301):
            assert not client.get('/v1/usage?backend=claude').json()['available']
        stream.consume({'type':'rate_limit_event','rate_limit_info':{'rateLimitType':'five_hour','status':'allowed'}})
        result=client.get('/v1/usage?backend=claude').json()
        assert result['rateLimitsByLimitId']['five_hour']['primary']['usedPercent'] is None
        assert result['available']  # the reported weekly window remains available
    service.db.close()


@pytest.mark.parametrize('value,expected', [(0, 0), (0.5, 0.5), (25, 25), (100, 100),
                                         (None, None), (True, None), (101, None), (float('nan'), None)])
def test_account_quota_is_percent_not_stream_fraction(value, expected):
    result = account.quota_snapshot({'rate_limits': {'five_hour': {'utilization': value,
        'resets_at': '2026-09-22T12:00:00Z'}}})
    assert result['available'] == (expected is not None)
    if expected is not None:
        window = result['rateLimitsByLimitId']['five_hour']['primary']
        assert window['usedPercent'] == expected
        assert window['windowDurationMins'] == 300 and window['resetsAt'] > 0


def test_account_quota_before_first_inference_is_cached_and_recovers(tmp_path):
    cfg = config(tmp_path)
    cfg['claude'] = {'binary': '/fixture', 'use_cli_login': True}
    app = create_app(cfg)
    service = app.state.service
    usage = {'rate_limits': {'five_hour': {'utilization': 30}, 'seven_day': {'utilization': 60}}}
    with patch.object(account, 'metadata', AsyncMock(return_value=usage)) as probe:
        with TestClient(app, headers={'Authorization': 'Bearer a'}) as client:
            for _ in range(2):
                result = client.get('/v1/usage?backend=claude').json()
                assert result['available'] and result['source'] == 'cli_usage'
                assert result['rateLimitsByLimitId']['five_hour']['primary']['usedPercent'] == 30
            assert probe.await_count == 1
            service.claude_usage_cache = None
            probe.side_effect = ValueError('unavailable')
            assert not client.get('/v1/usage?backend=claude').json()['available']
    service.db.close()
