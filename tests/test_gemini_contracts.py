"""ACP lifecycle and permissions tested without provider inference."""
import asyncio
import json
import sys
from unittest.mock import AsyncMock

import pytest
from Adapters.gemini.backend import run_native
from Adapters.gemini.native import AcpConnection, AcpStream
from agent_service.tools import ToolError


class Input:
    def __init__(self):self.values=[]
    def write(self,value):self.values.append(json.loads(value))
    async def drain(self):pass


@pytest.mark.parametrize('mode,kind,permissions,expected',[
    ('read_only','read',{'read':True},'once'),
    ('read_only','execute',{'shell':True},'deny'),
    ('auto','execute',{'shell':False},'deny'),
    ('auto','execute',{'shell':True},'once'),
    ('full','mystery',{'shell':True},'deny'),
])
def test_permissions_never_persist_or_exceed_grants(mode,kind,permissions,expected):
    from types import SimpleNamespace
    stdin=Input();approve=AsyncMock(return_value={'approved':True})
    rpc=AcpConnection(SimpleNamespace(stdin=stdin),AcpStream(lambda *_:None),approve,permissions,mode)
    asyncio.run(rpc._handle_request({'id':42,'method':'session/request_permission','params':{'toolCall':{'kind':kind},'options':[{'kind':'allow_always','optionId':'always'},{'kind':'allow_once','optionId':'once'},{'kind':'reject_once','optionId':'deny'}]}}))
    assert stdin.values[-1]['result']['outcome']['optionId']==expected
    approve.assert_not_awaited()


def test_real_process_fixture_resume_image_and_cancel(tmp_path):
    executable=tmp_path/'fake-gemini';log=tmp_path/'wire.jsonl'
    executable.write_text('#!'+sys.executable+'\nLOG='+repr(str(log))+'\n'+'''import json,sys,time
for line in sys.stdin:
 item=json.loads(line)
 with open(LOG,'a') as f:f.write(json.dumps(item)+'\\n')
 method=item.get('method');result={}
 if method=='initialize':result={'agentCapabilities':{'loadSession':True}}
 elif method=='session/new':result={'sessionId':'fixture-session'}
 elif method=='session/load':print(json.dumps({'method':'session/update','params':{'update':{'sessionUpdate':'agent_message_chunk','content':{'text':'OLD-ANSWER'}}}}),flush=True)
 elif method=='session/prompt':
  if item['params']['prompt'][0]['text']=='wait':time.sleep(30)
  print(json.dumps({'method':'session/update','params':{'update':{'sessionUpdate':'agent_message_chunk','content':{'text':'NEW-ANSWER'}}}}),flush=True)
  result={'stopReason':'end_turn'}
 print(json.dumps({'id':item['id'],'result':result}),flush=True)
''');executable.chmod(0o700)
    attachment=tmp_path/'image.png';attachment.write_bytes(b'image-fixture')
    async def scenario():
        args=({'binary':str(executable)},'hello',lambda *_:None,{'permissions':{},'_images':[{'path':str(attachment),'media_type':'image/png'}]},'auto-gemini-3','configured',tmp_path/'session',AsyncMock())
        assert (await run_native(*args))['answer']=='NEW-ANSWER'
        assert (await run_native(*args))['answer']=='NEW-ANSWER'
        events=[]
        task=asyncio.create_task(run_native(args[0],'wait',lambda *e:events.append(e),*args[3:]))
        await asyncio.sleep(.15)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(task,5)
    asyncio.run(scenario())
    messages=[json.loads(line) for line in log.read_text().splitlines()]
    image=next(m['params']['prompt'][1] for m in messages if m['method']=='session/prompt')
    assert image=={'type':'image','data':'aW1hZ2UtZml4dHVyZQ==','mimeType':'image/png'}
    methods=[m['method'] for m in messages]
    assert methods.count('session/new')==1 and methods.count('session/load')>=1


def test_invalid_protocol_output_is_structured():
    from types import SimpleNamespace
    async def scenario():
        out=asyncio.StreamReader();out.feed_data(b'[]\n');out.feed_eof()
        rpc=AcpConnection(SimpleNamespace(stdin=Input(),stdout=out),AcpStream(lambda *_:None),None,{},'ask')
        with pytest.raises(ToolError,match='gemini_acp_invalid'):await rpc.call('initialize',{})
    asyncio.run(scenario())
