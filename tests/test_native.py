import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from agent_service.native_backend import run
from control.operations import Operations
from control.local_models import discover

class NativeTest(unittest.IsolatedAsyncioTestCase):
 async def test_codex_approval_stream_and_resume(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);exe=root/'fake';log=root/'requests.jsonl'
   exe.write_text('#!'+sys.executable+'\n'+'''import sys,json
log='''+repr(str(log))+'''
def emit(x): print(json.dumps(x),flush=True)
for line in sys.stdin:
 x=json.loads(line)
 with open(log,'a') as f:f.write(json.dumps(x)+'\\n')
 method=x.get('method')
 if method=='initialize':emit({'id':x['id'],'result':{}})
 elif method in ('thread/start','thread/resume'):emit({'id':x['id'],'result':{'thread':{'id':'session-1'}}})
 elif method=='turn/start':emit({'id':90,'method':'item/commandExecution/requestApproval','params':{'command':'test-command'}})
 elif x.get('id')==90:
  emit({'method':'item/agentMessage/delta','params':{'delta':x['result']['decision']}})
  emit({'method':'thread/tokenUsage/updated','params':{'tokenUsage':{'total':{'inputTokens':12,'outputTokens':1}}}})
  emit({'method':'turn/completed','params':{'turn':{'status':'completed'}}})
''');exe.chmod(0o700)
   requests=[];events=[]
   async def approve(k,p):requests.append((k,p));return {'approved':False}
   with patch('agent_service.native_backend.configurations',return_value={'codex':{}}),patch('agent_service.native_backend.inventory',return_value={'codex':[]}):
    for i in range(2):
     result=await run({'binary':str(exe)},'hello',lambda k,v:events.append(k),{'permissions':{}},'test','low',root/'session','codex',approve)
     self.assertEqual(result['answer'],'decline')
   self.assertEqual(len(requests),2);self.assertIn('session_resumed',events);self.assertIn('context_usage',events)
   messages=[json.loads(x) for x in log.read_text().splitlines()]
   turn=next(x['params'] for x in messages if x.get('method')=='turn/start')
   self.assertFalse(turn['sandboxPolicy']['networkAccess']);self.assertEqual(turn['sandboxPolicy']['type'],'readOnly')
 async def test_claude_stdio_permission_and_resume(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);exe=root/'fake';exe.write_text('#!'+sys.executable+'\n'+'''import sys,json
json.loads(sys.stdin.readline())
print(json.dumps({'type':'control_request','request_id':'1','request':{'subtype':'can_use_tool','tool_name':'Read','input':{'file_path':'README.md'}}}),flush=True)
x=json.loads(sys.stdin.readline());decision=x['response']['response']['behavior']
print(json.dumps({'type':'result','subtype':'success','result':decision,'session_id':'session-claude','usage':{'input_tokens':10,'output_tokens':1}}),flush=True)
''');exe.chmod(0o700)
   requests=[]
   async def approve(k,p):requests.append(p);return {'approved':True}
   with patch('agent_service.native_backend.configurations',return_value={'claude':{}}),patch('agent_service.native_backend.inventory',return_value={'claude':[]}):
    result=await run({'binary':str(exe)},'hello',lambda *x:None,{'permissions':{'read':True}},'sonnet','configured',root/'session','claude',approve)
   self.assertEqual(result['answer'],'allow');self.assertEqual(result['thread_id'],'session-claude');self.assertEqual(requests[0]['tool_name'],'Read')
 async def test_operation_authorization_url_and_exit(self):
  ops=Operations();job=ops.launch([sys.executable,'-c','print("https://example.test/authorize?code=fixture")'])
  await asyncio.gather(*ops.tasks)
  self.assertEqual(job['state'],'completed');self.assertIn('https://example.test/authorize',job['output'])
 async def test_local_discovery_without_secret_export(self):
  import httpx
  with tempfile.TemporaryDirectory() as d:
   key=Path(d)/'key';key.write_text('private-test-token')
   class Client:
    async def __aenter__(self):return self
    async def __aexit__(self,*a):pass
    async def get(self,url,headers):
     self.assertion=headers['Authorization']=='Bearer private-test-token'
     if not self.assertion:raise AssertionError()
     return httpx.Response(200,json={'data':[{'id':'local-model','meta':{'n_ctx':65536}}]},request=httpx.Request('GET',url))
   class Factory(Client):
    def __init__(self,**kwargs):pass
   with patch('control.local_models.processes',return_value=[{'url':'http://127.0.0.1:8080','key_file':str(key)}]),patch('control.local_models.httpx.AsyncClient',Factory):result=await discover()
   self.assertEqual(result[0]['id'],'local-model');self.assertNotIn('private-test-token',json.dumps(result))
