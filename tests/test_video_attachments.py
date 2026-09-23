"""MP4 admission and visual/speech forwarding without provider inference."""
import asyncio
import json
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from agent_service.app import APIError, Service, create_app
from agent_service import tools
from test_workspaces import config


def test_mp4_extracts_sampled_frames_and_speech(tmp_path, monkeypatch):
    source = tmp_path / 'source'; source.write_bytes(b'fake mp4')
    calls = []

    async def process(argv, timeout=30, **kwargs):
        calls.append(argv)
        if 'ffprobe' in argv:
            return 0, json.dumps({'format': {'duration': '8', 'format_name': 'mov,mp4,m4a,3gp,3g2,mj2'}, 'streams': [{'codec_type': 'video'}, {'codec_type': 'audio'}]})
        frame = source.parent / ('frame-' + str(len([c for c in calls if 'ffmpeg' in c])) + '.jpg')
        frame.write_bytes(b'jpeg')
        return 0, ''

    monkeypatch.setattr(tools, 'process', process)
    monkeypatch.setattr(tools.shutil, 'which', lambda _: '/usr/bin/tool')
    monkeypatch.setattr(tools, 'transcribe_audio', AsyncMock(return_value=[{'page': None, 'text': 'speech'}]))
    pages = asyncio.run(tools.extract(source, 'clip.mp4'))
    assert [p['media_type'] for p in pages if p.get('media_type')] == ['image/jpeg'] * 4
    assert any(p['text'] == 'speech' for p in pages)
    assert all((source.parent / p['frame']).exists() for p in pages if p.get('frame'))


def test_mp4_requires_verified_vision_before_extraction(tmp_path):
    cfg = config(tmp_path); cfg['services']['local']['mode'] = 'scoped'
    app = create_app(cfg)
    with TestClient(app, headers={'Authorization': 'Bearer a'}) as client:
        with patch.object(app.state.service, 'validate_images', AsyncMock(side_effect=Exception('must not probe images'))), \
             patch.object(app.state.service, 'validate_video', AsyncMock(side_effect=RuntimeError('not supported'))), \
             patch('agent_service.app.tools.extract', AsyncMock()) as extract:
            try:
                client.post('/v1/files?project_id=p&backend=local&model=installed-model&execution_mode=scoped',
                            content=b'fake mp4', headers={'X-Filename': 'clip.mp4'})
            except RuntimeError:
                pass
            extract.assert_not_awaited()
    app.state.service.db.close()


def test_upload_limit_is_100_mib(tmp_path):
    cfg = config(tmp_path)
    app = create_app(cfg)
    with TestClient(app, headers={'Authorization': 'Bearer a'}) as client:
        too_large = client.post('/v1/files?project_id=p&backend=codex&model=gpt-6-astra',
                                content=b'x' * (100 * 1024 * 1024 + 1), headers={'X-Filename': 'large.txt'})
        assert too_large.status_code == 413
        assert not list((tmp_path / 'files').rglob('source'))
    app.state.service.db.close()


def test_long_video_transcript_keeps_frames_and_handoff_keeps_speech(tmp_path):
    cfg = config(tmp_path)
    cfg['codex'] = {}
    service = Service(cfg)
    pages = [{'page': None, 'text': 'spoken marker ' * 800},
             *[{'page': n, 'text': f'frame {n}', 'media_type': 'image/jpeg', 'frame': f'frame-{n}.jpg'} for n in range(1, 5)]]
    service.db.execute('INSERT INTO files(id,project,name,size,hash,pages,owner) VALUES(?,?,?,?,?,?,?)',
                       ('video', 'p', 'clip.mp4', 100, 'digest', json.dumps(pages), 'a'))
    data = {'project_id': 'p', 'backend': 'codex', 'model': 'gpt-6-astra', 'prompt': 'Analyze',
            'file_ids': ['video'], 'execution_mode': 'native'}
    service.db.execute('INSERT INTO jobs(id,project,owner,state,created,payload) VALUES(?,?,?,?,?,?)',
                       ('job', 'p', 'a', 'running', 1, json.dumps(data)))
    service.db.commit()
    row = dict(service.db.execute("SELECT * FROM jobs WHERE id='job'").fetchone())

    async def supported(*args):
        assert len(args[3]['_images']) == 4
        assert args[3]['_images'][0]['path'].endswith('frame-1.jpg')
        assert 'spoken marker' in args[1] and 'full_text_path' in args[1]
        return {'answer': 'read'}

    async def unsupported(*args):
        assert not args[3]['_images']
        assert 'spoken marker' in args[1]
        assert 'Quadros do arquivo' in args[1]
        return {'answer': 'speech only'}

    with patch.object(service, 'validate_video', AsyncMock()), patch.object(service, 'quota', AsyncMock(return_value=None)), patch('Adapters.run_native', side_effect=supported):
        asyncio.run(service.infer(row, data))
    with patch.object(service, 'validate_video', AsyncMock(side_effect=APIError('model_video_unavailable'))), \
         patch.object(service, 'quota', AsyncMock(return_value=None)), \
         patch('Adapters.run_native', side_effect=unsupported):
        asyncio.run(service.infer(row, data))
    service.db.close()


def test_video_capability_uses_live_model_metadata(tmp_path):
    cfg = config(tmp_path); cfg['codex'] = {'binary': '/usr/bin/codex'}
    service = Service(cfg)
    model_list = {'data': [
        {'id': 'gpt-6-astra', 'inputModalities': ['text', 'image']},
        {'id': 'text-only', 'inputModalities': ['text']}], 'nextCursor': None}
    props = type('Response', (), {'raise_for_status': lambda self: None,
                                 'json': lambda self: {'modalities': {'vision': True}}})()
    with patch('agent_service.app.tools.video_tools_available', return_value=True), \
         patch('agent_service.app.tools.transcription_available', return_value=True), \
         patch('agent_service.app.codex_rpc.metadata', AsyncMock(return_value=model_list)) as codex, \
         patch('httpx.AsyncClient.get', AsyncMock(return_value=props)):
        models = asyncio.run(service.models_with_context('p'))
        codex.assert_awaited_once()
        assert next(m for m in models if m['backend'] == 'codex')['capabilities']['video_execution_modes'] == ['native']
        assert next(m for m in models if m['backend'] == 'local')['capabilities']['video_execution_modes'] == ['scoped']
        asyncio.run(service.validate_video('codex', 'gpt-6-astra', 'native'))
        try:
            asyncio.run(service.validate_video('codex', 'text-only', 'native'))
        except APIError as exc:
            assert exc.code == 'model_video_unavailable'
        else:
            raise AssertionError('text-only model admitted video')
    service.db.close()


def test_claude_video_only_for_current_cli_aliases(tmp_path):
    cfg = config(tmp_path)
    cfg['services']['local']['enabled'] = False
    cfg['services']['codex']['enabled'] = False
    cfg['services']['claude'] = {'enabled': True, 'models': ['sonnet', 'custom-alias'],
                                 'projects': ['p'], 'permissions': {'read': True, 'upload': True}}
    cfg['claude'] = {'binary': '/usr/bin/claude'}
    service = Service(cfg)
    picker = {'models': [{'value': 'sonnet', 'resolvedModel': 'claude-sonnet-4-6'},
                         {'value': 'custom-alias', 'resolvedModel': 'claude-sonnet-4-6'}]}
    with patch('agent_service.app.tools.video_tools_available', return_value=True), \
         patch('agent_service.app.claude_account.metadata', AsyncMock(return_value=picker)):
        models = asyncio.run(service.models_with_context('p'))
        assert next(m for m in models if m['id'] == 'sonnet')['capabilities']['video'] is True
        assert next(m for m in models if m['id'] == 'custom-alias')['capabilities']['video'] is False
        asyncio.run(service.validate_video('claude', 'sonnet', 'native'))
        try:
            asyncio.run(service.validate_video('claude', 'custom-alias', 'native'))
        except APIError as exc:
            assert exc.code == 'model_video_unavailable'
        else:
            raise AssertionError('unknown alias admitted video')
    service.db.close()


def test_cancelled_folder_video_import_cleans_source(tmp_path):
    cfg = config(tmp_path); service = Service(cfg)
    source = tmp_path/'clip.mp4'; source.write_bytes(b'fake mp4')
    with patch.object(service, 'validate_video', AsyncMock()), \
         patch('agent_service.app.tools.extract', AsyncMock(side_effect=asyncio.CancelledError)):
        try:
            asyncio.run(service.attach_project_files(('a', cfg['clients']['a']), 'p',
                [('clip.mp4', source)], [], 'codex', 'gpt-6-astra', 'native'))
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError('import cancellation was swallowed')
    assert source.read_bytes() == b'fake mp4'
    assert not list((tmp_path/'files').rglob('source'))
    service.db.close()
