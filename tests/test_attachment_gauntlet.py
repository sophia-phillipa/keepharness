"""15 attachment contracts x 10 varied rounds; no workers or model inference."""
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

import pytest
from starlette.testclient import TestClient
from agent_service.app import create_app
from test_project_browser import config


@pytest.fixture
def api(tmp_path):
    cfg=config(tmp_path)
    cfg['clients']['b']={'sha256':hashlib.sha256(b'b').hexdigest(),'projects':['p','sem-projeto']}
    app=create_app(cfg)
    client=TestClient(app,headers={'Authorization':'Bearer a'})
    yield client,app.state.service,tmp_path
    client.close();app.state.service.db.close()


def upload(client, data=b'hello', name='note.txt', **kwargs):
    return client.post('/v1/files?project_id=p&backend=codex&model=fixture',
                       content=data,headers={'X-Filename':quote(name,safe='')},**kwargs)


def submit(client, ids, prompt='Read only the attached content'):
    return client.post('/v1/jobs',json={'project_id':'p','backend':'codex','model':'fixture',
                                      'effort':'low','prompt':prompt,'file_ids':ids})


@pytest.mark.parametrize('round_no',range(1,11),ids=lambda r:f'round{r:02}')
@pytest.mark.parametrize('profile',range(1,16),ids=lambda p:f'P{p:02}')
def test_attachment_profile(api,round_no,profile):
    client,service,root=api
    content=('Anexo contextual 🐋 '+str(round_no)+'\n').encode()
    if profile==1: # Real text upload, content-type does not override bytes.
        response=upload(client,content,name=f'revisão-{round_no}.CSV')
        assert response.status_code==201,response.text
        data=response.json();assert data['sha256']==hashlib.sha256(content).hexdigest()
        assert service.file('p',data['file_id'],'a')['size']==len(content)
    elif profile==2: # Quantity limits and exact request ownership.
        fid=upload(client,content).json()['file_id'];count=[0,1,2,19,20,21,1,20,21,2][round_no-1]
        response=submit(client,[fid]*count)
        assert response.status_code==(422 if count>20 else 202),response.text
    elif profile==3: # A reference in text must not silently become an attachment.
        unattached=root/f'private-{round_no}.txt';unattached.write_text('UNATTACHED_CONTENT_MARKER')
        response=submit(client,[],prompt=f'Leia {unattached}')
        assert response.status_code==202,response.text
        row=service.db.execute('SELECT payload FROM jobs').fetchone()
        assert json.loads(row['payload'])['file_ids']==[]
        assert service.db.execute('SELECT count(*) FROM files').fetchone()[0]==0
        assert submit(client,[f'missing-{round_no}']).status_code==404
    elif profile==4: # Ordinary desktop/document names must not require renaming.
        names=['relatório (1).txt','cópia [final].txt','nota, revisão.txt','nota + versão.txt','resumo 🐋.txt',"Sophia's note.txt",'a & b.txt','a@b.txt','100%.txt','arquivo;final.txt']
        response=upload(client,content,names[round_no-1]);assert response.status_code==201,response.text
    elif profile==5: # Failed binary upload leaves no orphan, then recovery works.
        response=upload(client,b'\x00'+bytes([round_no]),name='broken.txt')
        assert response.status_code==422
        assert not list((service.root/'files').rglob('source'))
        assert upload(client,content).status_code==201
    elif profile==6: # Streamed bytes above the configured bound are rejected.
        from unittest.mock import patch
        with patch('agent_service.app.tools.MAX_ATTACHMENT_BYTES', 1024*1024):
            response=upload(client,b'x'*(1024*1024+round_no),name='large.txt')
        assert response.status_code==413,response.text
        assert service.db.execute('SELECT count(*) FROM files').fetchone()[0]==0
        assert not list((service.root/'files').rglob('source'))
    elif profile==7: # Mixed folder yields successful files plus explicit skipped errors.
        folder=root/'mixed';folder.mkdir();(folder/'good.txt').write_bytes(content);(folder/'bad.bin').write_bytes(b'\x00\xff')
        response=client.post('/v1/project-files/attach?project_id=p',json={'root_id':'system','paths':[str(folder).lstrip('/')],'backend':'codex','model':'fixture'})
        assert response.status_code==200,response.text
        data=response.json();assert len(data['attachments'])==1 and len(data['skipped'])==1
    elif profile==8: # Path/control injection in filenames must never write outside upload dir.
        bad=['../escape.txt','a/b.txt','a\\b.txt','.', '..','a\x00b.txt','a\nb.txt','a\rb.txt','/absolute.txt','a\tb.txt'][round_no-1]
        assert upload(client,content,bad).status_code==422
        assert service.db.execute('SELECT count(*) FROM files').fetchone()[0]==0
    elif profile==9: # Same identity cannot attach another project's file; another owner cannot use it.
        fid=upload(client,content).json()['file_id']
        response=client.post('/v1/jobs',json={'project_id':'sem-projeto','backend':'codex','model':'fixture','prompt':'read','file_ids':[fid]})
        assert response.status_code==404
        client.headers['Authorization']='Bearer b';assert submit(client,[fid]).status_code==404
    elif profile==10: # Empty/small attachments are byte-exact, without synthetic contents.
        raw=b'' if round_no%2 else b'x'*round_no
        response=upload(client,raw);assert response.status_code==201,response.text
        assert response.json()['bytes']==len(raw)
    elif profile==11: # Hostile file_ids shapes must produce controlled validation, never SQLite 500.
        bad=[{},[],True,False,1,1.5,None,{'id':'x'},[['x']],{'$ref':'file'}][round_no-1]
        response=submit(client,[bad]);assert response.status_code==422,response.text
        assert service.db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
    elif profile==12: # Attachments remain scoped to their recorded owner in history metadata.
        fid=upload(client,content).json()['file_id'];response=submit(client,[fid])
        assert response.status_code==202
        row=dict(service.db.execute('SELECT * FROM jobs').fetchone())
        assert [f['id'] for f in service.message_attachments(row)]==[fid]
        row['owner']='b';assert service.message_attachments(row)==[]
    elif profile==13: # Symlink and duplicate selections never silently import outsiders twice.
        folder=root/'tree';folder.mkdir();(folder/'note.txt').write_bytes(content)
        (folder/'link.txt').symlink_to(folder/'note.txt')
        base=str(folder).lstrip('/')
        response=client.post('/v1/project-files/attach?project_id=p',json={'root_id':'system','paths':[base+'/note.txt',base+'/note.txt',base+'/link.txt']})
        assert response.status_code==200,response.text
        assert len(response.json()['attachments'])==1
        assert response.json()['skipped'][0]['reason']=='symlink_denied'
    elif profile==14: # Text masquerading as active HTML/SVG is never served as preview.
        raw=f'<svg onload="alert({round_no})"></svg>'.encode()
        response=upload(client,raw,'active.svg');assert response.status_code==201,response.text
        assert 'preview_url' not in response.json()
        assert client.get('/v1/files/'+response.json()['file_id']+'/preview').status_code==404
    else: # Bypass attempt via declared Content-Length does not make binary data valid.
        response=client.post('/v1/files?project_id=p',content=b'\x00'*round_no,
                             headers={'X-Filename':'fake.csv','Content-Length':'0','Content-Type':'text/plain'})
        assert response.status_code==422
        assert not list((service.root/'files').rglob('source'))

@pytest.mark.parametrize('root_id',[[],{},['home']])
def test_malformed_folder_root_is_controlled(api,root_id):
    client,_,_=api
    response=client.post('/v1/project-files/attach?project_id=p',json={'root_id':root_id,'paths':['anything']})
    assert response.status_code==422,response.text


def test_folder_replaced_by_symlink_after_selection_is_not_read(api):
    import asyncio
    client,service,root=api
    source=root/'selected.txt';source.write_text('selected')
    outside=root/'outside.txt';outside.write_text('NOT_SELECTED')
    # Simulate replacement after selection was validated, before copying begins.
    source.unlink();source.symlink_to(outside)
    identity=('a',service.config['clients']['a'])
    result=asyncio.run(service.attach_project_files(identity,'p',[('selected.txt',source)],[],'codex','fixture'))
    assert result['attachments']==[],result
    assert service.db.execute('SELECT count(*) FROM files').fetchone()[0]==0

@pytest.mark.parametrize('replacement',['directory_symlink','fifo','missing'])
def test_attachment_source_changes_are_bounded(api,replacement):
    import asyncio,os
    _,service,root=api
    folder=root/'chosen';folder.mkdir();source=folder/'note.txt';source.write_text('safe')
    source.unlink()
    if replacement=='directory_symlink':
        folder.rmdir();outside=root/'outside';outside.mkdir();(outside/'note.txt').write_text('NOT_SELECTED');folder.symlink_to(outside)
    elif replacement=='fifo':os.mkfifo(source)
    result=asyncio.run(service.attach_project_files(('a',service.config['clients']['a']),'p',
                        [('chosen/note.txt',source)],[],'codex','fixture'))
    assert result['attachments']==[]
    assert result['skipped'][0]['reason']=='attachment_source_unavailable'
    assert not list((service.root/'files').rglob('source'))


def test_exact_document_limit_is_accepted(api):
    client,service,_=api
    content=b'x'*(100*1024*1024)
    response=upload(client,content,'boundary.txt')
    assert response.status_code==201,response.text
    assert response.json()['bytes']==len(content)
    # Content stays exact; extraction is not claimed to be full model context.
    assert service.file('p',response.json()['file_id'],'a')['hash']==hashlib.sha256(content).hexdigest()


def test_streamed_audio_over_limit_is_rejected_before_transcription(api):
    import asyncio,httpx
    from unittest.mock import AsyncMock,patch
    client,service,_=api
    async def run():
        async def chunks():
            for _ in range(256):yield b'\0'*(1024*1024)
            yield b'!'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app),base_url='http://testserver',headers={'Authorization':'Bearer a'}) as session:
            return await session.post('/v1/files?project_id=p',headers={'X-Filename':'large.wav'},content=chunks())
    with patch('agent_service.app.tools.extract',new=AsyncMock()) as extract:
        response=asyncio.run(run());assert response.status_code==413,response.text
        extract.assert_not_awaited()
    assert not list((service.root/'files').rglob('source'))
