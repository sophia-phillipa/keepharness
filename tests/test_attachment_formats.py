import asyncio
import io
import zipfile
from unittest.mock import AsyncMock, patch
import pytest
from agent_service import tools
from agent_service.app import Service, APIError
from test_workspaces import config


def test_utf8_any_extension_and_binary_denied(tmp_path):
    file=tmp_path/'source';file.write_text('fn main() {}')
    assert asyncio.run(tools.extract(file,'main.rs'))[0]['text']=='fn main() {}'
    file.write_bytes(b'\xff\x00')
    with pytest.raises(tools.ToolError):asyncio.run(tools.extract(file,'file.bin'))


def test_images_preserve_binary_outside_prompt(tmp_path):
    file=tmp_path/'source';file.write_bytes(b'\x89PNG\r\n\x1a\n'+b'test')
    assert asyncio.run(tools.extract(file,'image.png'))==[{'page':None,'text':'','media_type':'image/png'}]
    file.write_bytes(b'\xff\xd8\xff'+b'0'*(5*1024*1024))
    with pytest.raises(tools.ToolError,match='image_size_limit'):asyncio.run(tools.extract(file,'image.jpg'))


def test_office_text_and_entity_rejection(tmp_path):
    file=tmp_path/'source'
    with zipfile.ZipFile(file,'w') as z:z.writestr('word/document.xml','<root><t>Evidence</t></root>')
    assert 'Evidence' in asyncio.run(tools.extract(file,'notes.docx'))[0]['text']
    with zipfile.ZipFile(file,'w') as z:z.writestr('word/document.xml','<!DOCTYPE root><root/>')
    with pytest.raises(tools.ToolError,match='unsafe_document_xml'):asyncio.run(tools.extract(file,'notes.docx'))


def test_vision_requires_actual_local_runtime(tmp_path):
    cfg=config(tmp_path);cfg['services']['local']['mode']='native'
    service=Service(cfg)
    response=type('Response',(),{'raise_for_status':lambda s:None,'json':lambda s:{'modalities':{'vision':False}}})()
    with patch('httpx.AsyncClient.get',AsyncMock(return_value=response)):
        with pytest.raises(APIError,match='local_vision_not_enabled'):asyncio.run(service.validate_images('local','installed-model'))
    response.json=lambda:{'modalities':{'vision':True}}
    with patch('httpx.AsyncClient.get',AsyncMock(return_value=response)):
        asyncio.run(service.validate_images('local','installed-model'))
    service.db.close()


def test_audio_uses_local_transcriber(tmp_path):
    file=tmp_path/'source';file.write_bytes(b'RIFFtestWAVE')
    with patch('agent_service.tools.transcribe_audio',AsyncMock(return_value=[{'text':'speech','page':None}])) as transcribe:
        assert asyncio.run(tools.extract(file,'recording.wav'))[0]['text']=='speech'
        transcribe.assert_awaited_once_with(file)


def test_epub_follows_spine_and_ignores_scripts(tmp_path):
    file=tmp_path/'source'
    with zipfile.ZipFile(file,'w') as z:
        z.writestr('META-INF/container.xml','<container><rootfile full-path="book.opf"/></container>')
        z.writestr('book.opf','<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="a" href="a.xhtml"/><item id="b" href="b.xhtml"/></manifest><spine><itemref idref="b"/><itemref idref="a"/></spine></package>')
        z.writestr('a.xhtml','<p>Second</p><script>ignore()</script>')
        z.writestr('b.xhtml','<p>First</p>')
    result=asyncio.run(tools.extract(file,'book.epub'))
    assert [p['text'] for p in result]==['First','Second']


def test_xlsx_resolves_shared_strings(tmp_path):
    file=tmp_path/'source'
    with zipfile.ZipFile(file,'w') as z:
        z.writestr('xl/sharedStrings.xml','<sst><si><t>Customer</t></si></sst>')
        z.writestr('xl/worksheets/sheet1.xml','<worksheet><row><c r="A1" t="s"><v>0</v></c><c r="B1"><v>42</v></c></row></worksheet>')
    result=asyncio.run(tools.extract(file,'sheet.xlsx'))
    assert 'A1=Customer\tB1=42' in result[-1]['text']


def test_large_csv_keeps_full_content_outside_prompt(tmp_path):
    import json
    cfg=config(tmp_path);cfg['services']['local']['mode']='native';cfg['local']={}
    service=Service(cfg)
    content='id,value\n'+('1,example\n'*20000)+'2,FINAL-MARKER\n'
    service.db.execute('INSERT INTO files(id,project,name,size,hash,pages,owner) VALUES(?,?,?,?,?,?,?)',('csv','p','large.csv',len(content),'digest',json.dumps([{'page':None,'text':content}]),'a'))
    data={'project_id':'p','backend':'local','model':'installed-model','prompt':'Read last line','file_ids':['csv']}
    service.db.execute('INSERT INTO jobs(id,project,owner,state,created,payload) VALUES(?,?,?,?,?,?)',('j','p','a','running',1,json.dumps(data)));service.db.commit()
    row=dict(service.db.execute("SELECT * FROM jobs WHERE id='j'").fetchone())
    async def run(*args):
        assert 'FINAL-MARKER' not in args[1]
        assert 'excerpt' in args[1] and 'full_text_path' in args[1]
        extracted=tmp_path/'sessions/j/local/attachments/csv.txt'
        assert extracted.read_text()==content
        assert str(extracted.resolve()) in args[1]
        return {'answer':'ok'}
    with patch('Adapters.run_native',side_effect=run):asyncio.run(service.infer(row,data))
    service.db.close()
