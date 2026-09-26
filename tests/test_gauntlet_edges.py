"""Adversarial additions for the 2026-09-21 gauntlet; no inference."""
import pytest
from agent_service.app import APIError
from test_execution_modes import service, payload


@pytest.mark.parametrize('mode', [None, '', 'NATIVE', ' native ', 'isolated', [], {}, False, 1])
def test_malformed_mode_never_enqueues_or_silently_defaults(tmp_path, mode):
    instance, identity = service(tmp_path)
    try:
        with pytest.raises(APIError, match='execution_mode_unsupported'):
            instance.submit(identity, dict(project_id='p', backend='codex', model='gpt-6-astra', prompt='🐋', execution_mode=mode))
        assert instance.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0
    finally:
        instance.db.close()


def test_incompatible_handoff_preserves_native_root_and_unicode_title(tmp_path):
    instance, identity = service(tmp_path)
    try:
        title = 'Café 🐋 <naïve> & façade'
        root = instance.submit(identity, dict(project_id='p', backend='codex', model='gpt-6-astra', prompt=title))['job_id']
        instance.finish(root, 'completed', {'answer': 'done'})
        with pytest.raises(APIError, match='execution_mode_unsupported'):
            instance.submit(identity, dict(project_id='p', backend='local', model='installed-model', prompt='next', parent_job_id=root))
        assert instance.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1
        assert payload(instance, identity, root)['execution_mode'] == 'native'
        assert instance.conversation_title(instance.job(identity, root)) == title
    finally:
        instance.db.close()
