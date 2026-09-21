"""Provider failures must be actionable without exposing provider payloads."""
import json

import pytest

from Adapters.claude.stream import Stream
from agent_service.tools import ToolError


def failure():
    return {'type': 'result', 'subtype': 'success', 'is_error': True,
            'result': 'private provider details'}


@pytest.mark.parametrize('code,expected', [
    ('authentication_failed', 'claude_authentication_failed'),
    ('rate_limit', 'claude_rate_limit'),
    ('unknown_private_error', 'claude_execution_failed'),
])
def test_provider_failure_uses_safe_code(code, expected):
    events = []
    state = Stream(lambda *args: events.append(args))
    state.consume({'type': 'assistant', 'error': code,
                   'message': {'content': [{'type': 'text', 'text': 'secret=private'}]}})
    with pytest.raises(ToolError, match='^' + expected + '$'):
        state.consume(failure())
    assert not events


def test_success_after_transient_auth_error_is_not_rejected():
    state = Stream(lambda *_: None)
    state.consume({'type': 'assistant', 'error': 'authentication_failed'})
    state.consume({'type': 'result', 'subtype': 'success', 'result': 'Recovered'})
    assert state.finish('sonnet')['answer'] == 'Recovered'


def test_failure_does_not_contaminate_next_turn():
    old = Stream(lambda *_: None)
    old.consume({'type': 'assistant', 'error': 'authentication_failed'})
    new = Stream(lambda *_: None)
    with pytest.raises(ToolError, match='^claude_execution_failed$'):
        new.consume(failure())


def test_incomplete_stream_keeps_its_distinct_error():
    with pytest.raises(ToolError, match='^claude_stream_incomplete$'):
        Stream(lambda *_: None).finish('sonnet')


@pytest.mark.parametrize('error,condition', [
    ('claude_authentication_failed', 'claude_authentication_required'),
    ('claude_rate_limit', 'claude_quota_exhausted'),
])
def test_worker_records_account_conditions_without_execution_failure(tmp_path, error, condition):
    import asyncio
    from unittest.mock import AsyncMock
    from agent_service.app import Service
    from tests.test_shared_projects import config

    async def exercise():
        cfg = config(tmp_path)
        cfg['services']['claude']['models'] = ['sonnet']
        cfg['claude_models'] = ['sonnet']
        service = Service(cfg)
        service.execute = AsyncMock(side_effect=ToolError(error))
        identity = ('a', service.config['clients']['a'])
        job = service.submit(identity, {'project_id': 'sem-projeto', 'backend': 'claude',
                            'model': 'sonnet', 'effort': 'configured', 'prompt': 'hello'})
        worker = asyncio.create_task(service.worker())
        try:
            async with asyncio.timeout(2):
                while service.job(identity, job['job_id'])['state'] in ('queued', 'running'):
                    await asyncio.sleep(.01)
            row = service.job(identity, job['job_id'])
            data = json.loads(row['result'])
            assert row['state'] == 'interrupted'
            assert data['condition'] == condition
            assert 'error' not in data
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            service.db.close()
    asyncio.run(exercise())
