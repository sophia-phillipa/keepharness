"""Audio duration and cancellation contracts without speech inference."""
import asyncio
import os
from pathlib import Path
import sys

import pytest
from agent_service import tools


@pytest.fixture
def media(tmp_path, monkeypatch):
    runtime=tmp_path/'runtime'
    binary=runtime/'build/bin/whisper-cli';binary.parent.mkdir(parents=True);binary.write_text('fixture')
    model=runtime/'models/ggml-base.bin';model.parent.mkdir();model.write_text('fixture')
    monkeypatch.setenv('TAIL_HARNESS_WHISPER_DIR', str(runtime))
    source=tmp_path/'source';source.write_bytes(b'SYNTHETIC AUDIO')
    return source


def fake_pipeline(monkeypatch, source, duration, *, cancel_at=None):
    calls=[]
    async def process(argv, timeout=30, **kwargs):
        calls.append((argv,timeout))
        if 'ffprobe' in argv:return 0,str(duration)
        if 'ffmpeg' in argv:
            (source.parent/'audio.wav').write_bytes(b'PCM fixture')
            if cancel_at=='decode':raise asyncio.CancelledError
            return 0,''
        (source.parent/'transcription.txt').write_text('Synthetic speech')
        if cancel_at=='transcribe':raise asyncio.CancelledError
        return 0,''
    monkeypatch.setattr(tools,'process',process)
    return calls


@pytest.mark.parametrize('duration', [14225.536, 14400])
def test_four_hour_audio_accepted_without_changing_byte_budgets(media, monkeypatch, duration):
    calls=fake_pipeline(monkeypatch,media,duration)
    result=asyncio.run(tools.transcribe_audio(media))
    assert 'Synthetic speech' in result[0]['text']
    assert tools.MAX_AUDIO_BYTES==256*1024*1024
    assert len(calls)==3
    assert '--fsize=536870912' in calls[1][0]
    assert media.read_bytes()==b'SYNTHETIC AUDIO'
    assert not (media.parent/'audio.wav').exists()
    assert not (media.parent/'transcription.txt').exists()


@pytest.mark.parametrize('duration', [14400.001, 20000, 0, -1, 'nan', 'inf'])
def test_invalid_or_over_four_hour_duration_rejected_before_decode(media, monkeypatch, duration):
    calls=fake_pipeline(monkeypatch,media,duration)
    with pytest.raises(tools.ToolError,match='audio_duration_limit'):
        asyncio.run(tools.transcribe_audio(media))
    assert len(calls)==1


def test_malformed_duration_rejected_before_decode(media, monkeypatch):
    calls=fake_pipeline(monkeypatch,media,'not a duration')
    with pytest.raises(tools.ToolError,match='invalid_audio'):
        asyncio.run(tools.transcribe_audio(media))
    assert len(calls)==1


@pytest.mark.parametrize('stage', ['decode','transcribe'])
def test_cancellation_cleans_temporary_audio_and_text(media, monkeypatch, stage):
    fake_pipeline(monkeypatch,media,60,cancel_at=stage)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(tools.transcribe_audio(media))
    assert not (media.parent/'audio.wav').exists()
    assert not (media.parent/'transcription.txt').exists()
    assert media.read_bytes()==b'SYNTHETIC AUDIO'


def test_process_cancellation_stops_subprocess(tmp_path):
    marker=tmp_path/'pid'
    async def scenario():
        task=asyncio.create_task(tools.process([sys.executable,'-c',
            'import os,time,pathlib; pathlib.Path('+repr(str(marker))+').write_text(str(os.getpid())); time.sleep(60)']))
        try:
            async with asyncio.timeout(5):
                while not marker.exists():await asyncio.sleep(.01)
            pid=int(marker.read_text())
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            with pytest.raises(ProcessLookupError):os.kill(pid,0)
        finally:
            if not task.done():
                task.cancel()
                try:await task
                except asyncio.CancelledError:pass
    asyncio.run(scenario())


def test_process_cancellation_stops_child_after_parent_exits(tmp_path):
    import signal
    marker=tmp_path/'child-pid'
    async def scenario():
        script='import subprocess,sys,pathlib; child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); pathlib.Path('+repr(str(marker))+').write_text(str(child.pid))'
        task=asyncio.create_task(tools.process([sys.executable,'-c',script]))
        pid=None
        try:
            async with asyncio.timeout(5):
                while not marker.exists():await asyncio.sleep(.01)
            pid=int(marker.read_text())
            await asyncio.sleep(.1)  # Parent exits; child still owns inherited stdout.
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            async with asyncio.timeout(1):
                while Path('/proc/'+str(pid)+'/stat').exists():
                    if Path('/proc/'+str(pid)+'/stat').read_text().split()[2]=='Z':break
                    await asyncio.sleep(.01)
        finally:
            if pid:
                try:os.kill(pid,signal.SIGKILL)
                except ProcessLookupError:pass
            if not task.done():
                task.cancel()
                try:await task
                except asyncio.CancelledError:pass
    asyncio.run(scenario())
