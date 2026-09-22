"""Follow-ups wait for their parent and use its completed context."""
import pytest
from agent_service.app import APIError
from test_execution_modes import service

@pytest.mark.parametrize('state', ['queued', 'running'])
def test_active_parent_accepts_followup_and_preserves_order(tmp_path, state):
    instance, identity = service(tmp_path)
    try:
        request = dict(project_id='p', backend='codex', model='gpt-6-astra', prompt='first')
        first = instance.submit(identity, request)['job_id']
        instance.db.execute('UPDATE jobs SET state=? WHERE id=?', (state, first))
        child = instance.submit(identity, dict(request, prompt='follow up', parent_job_id=first))['job_id']
        with pytest.raises(APIError, match='conversation_has_newer_turn'):
            instance.submit(identity, dict(request, prompt='stale', parent_job_id=first))
        # Even equal/reversed timestamps cannot dispatch a child before its parent.
        instance.db.execute('UPDATE jobs SET created=0 WHERE id=?', (child,))
        assert (instance.next_job()['id'] if state == 'queued' else instance.next_job()) == (first if state == 'queued' else None)
        instance.finish(first, 'completed', {'answer': 'parent answer'})
        row = instance.next_job()
        assert row['id'] == child
        assert instance.context_turns(row, {'parent_job_id': first})[0][1]['answer'] == 'parent answer'
    finally:
        instance.db.close()
