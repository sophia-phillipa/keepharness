"""Multi-folder project contracts: validation, discovery, and executor handoff."""
import asyncio
import copy
import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient
from agent_service.app import Service, create_app
from Adapters import run_native as run
from tests.test_shared_projects import config


def test_named_multiple_roots_persist_and_names_are_unique(tmp_path):
    cfg=config(tmp_path);app=create_app(copy.deepcopy(cfg))
    roots=[tmp_path/'one',tmp_path/'two']
    for root in roots:root.mkdir()
    with TestClient(app,headers={'Authorization':'Bearer a'}) as client:
        response=client.post('/v1/projects',json={'name':'  My   Project  ','paths':list(map(str,roots))+[str(roots[0])]})
        assert response.status_code==201,response.text
        pid=response.json()['project_id']
        spec=client.get('/v1/projects').json()['details'][pid]
        assert spec['label']=='My Project'
        assert spec['root']==str(roots[0]) and spec['additional_roots']==[str(roots[1])]
        duplicate=client.post('/v1/projects',json={'name':'MY PROJECT','paths':[str(roots[1])]})
        assert duplicate.status_code==409 and duplicate.json()['code']=='project_name_exists'
        for name in ('','ab','12a',3):
            assert client.post('/v1/projects',json={'name':name,'paths':[str(roots[0])]}).status_code==422
        assert client.post('/v1/projects',json={'name':'Valid','paths':[]}).status_code==422
        assert client.post('/v1/projects',json={'name':'Valid','paths':[str(roots[0]),str(tmp_path/'missing')]}).status_code==422
    app.state.service.db.close()
    service=Service(copy.deepcopy(cfg))
    assert service.config['projects'][pid]['additional_roots']==[str(roots[1])]
    service.db.close()


def test_directory_picker_filters_before_pagination(tmp_path):
    app=create_app(config(tmp_path));root=tmp_path/'browse';root.mkdir()
    for name in ('Alpha','Beta','Alpine','.hidden'):(root/name).mkdir()
    (root/'Alpha.txt').write_text('not a directory')
    with TestClient(app,headers={'Authorization':'Bearer a'}) as client, \
            patch('agent_service.workspaces.system_roots',return_value=[('home',root)]):
        assert client.get('/v1/project-directories',headers={'Authorization':'Bearer unknown'}).status_code==401
        page=client.get('/v1/project-directories?q=al&limit=1').json()
        assert [e['name'] for e in page['entries']]==['Alpha'] and page['limited']
        assert page['entries'][0]['absolute_path']==str(root/'Alpha')
        assert client.get('/v1/project-directories?q=al&limit=1&start=2').json()['entries'][0]['name']=='Alpine'
        assert client.get('/v1/project-directories',params={'path':str(root/'Beta')}).json()['absolute_path']==str(root/'Beta')
        assert client.get('/v1/project-directories?path=../').status_code==422
    app.state.service.db.close()


@pytest.mark.parametrize('provider',['codex','deepseek','local'])
@pytest.mark.parametrize('read,write',[(True,True),(True,False),(False,False)])
def test_rpc_providers_receive_roots_on_every_turn(tmp_path,provider,read,write):
    recorded={'calls':[],'turns':[]}
    class RPC:
        async def call(self,method,params):recorded['calls'].append((method,params));return {'thread':{'id':'fixture'}}
        async def send(self,method,params):recorded['turns'].append(params)
        async def receive(self):return {'method':'turn/completed','params':{'turn':{'status':'completed'}}}
    @asynccontextmanager
    async def connection(command,**kwargs):recorded['command']=command;yield RPC()
    async def approve(*args):raise AssertionError('No real inference')
    roots=[tmp_path/'one',tmp_path/'two']
    for root in roots:root.mkdir()
    project={'label':'Example','root':str(roots[0]),'additional_roots':[str(roots[1])],
             'permissions':{'read':read,'write':write},'access_mode':'read_only' if not write else 'ask'}
    key=tmp_path/'key';key.write_text('test-key')
    cfg={'binary':'fixture'}
    if provider=='deepseek':cfg['api_provider']={'url':'https://example.invalid/v1','key_file':str(key)}
    if provider=='local':cfg['local_models']={'fixture':{'url':'http://127.0.0.1:9999'}}
    with patch('Adapters.codex.native.connection',connection), \
            patch('Adapters.local.backend.wrap',side_effect=lambda command,*args:command), \
            patch('Adapters.codex.native.configurations',return_value={'codex':{}}), \
            patch('Adapters.codex.native.inventory',return_value={'codex':[]}):
        for _ in range(2):asyncio.run(run(cfg,'Read files',lambda *args:None,project,'fixture','low',tmp_path/'session',provider,approve))
    assert [method for method,_ in recorded['calls']]==['thread/start','thread/resume']
    for turn in recorded['turns']:
        text=turn['input'][0]['text']
        assert (str(roots[1]) in text)==read
        assert turn['cwd']==str(roots[0] if read else tmp_path/'session'/'workspace')
        if write:assert turn['sandboxPolicy']['writableRoots']==list(map(str,roots))
        else:assert 'writableRoots' not in turn['sandboxPolicy']
    if provider=='deepseek':assert recorded['calls'][0][1]['modelProvider']=='tail_api'
    if provider=='local':assert recorded['calls'][0][1]['modelProvider']=='tail_local'


def test_claude_receives_additional_directories(tmp_path):
    roots=[tmp_path/'one',tmp_path/'two',tmp_path/'three']
    for root in roots:root.mkdir()
    exe=tmp_path/'fake-claude'
    exe.write_text('''#!/usr/bin/env python3
import json,sys,os
item=json.loads(sys.stdin.readline())
print(json.dumps({'type':'result','subtype':'success','result':json.dumps({'args':sys.argv,'cwd':os.getcwd(),'input':item}), 'session_id':'fixture'}),flush=True)
''');exe.chmod(0o700)
    async def approve(*args):raise AssertionError('No real inference')
    with patch('Adapters.claude.native.configurations',return_value={'claude':{}}), \
            patch('Adapters.claude.native.inventory',return_value={'claude':[]}):
        result=asyncio.run(run({'binary':str(exe)},'Read files',lambda *args:None,
                              {'root':str(roots[0]),'additional_roots':list(map(str,roots[1:])),'permissions':{'read':True}},
                              'fixture','configured',tmp_path/'session','claude',approve))
    record=json.loads(result['answer'])
    assert record['cwd']==str(roots[0])
    offset=record['args'].index('--add-dir')+1
    assert record['args'][offset:offset+2]==list(map(str,roots[1:]))
    assert str(roots[1]) in record['input']['message']['content'][0]['text']


def test_missing_project_folder_is_not_recreated(tmp_path):
    from agent_service.tools import ToolError
    missing=tmp_path/'removed'
    with pytest.raises(ToolError,match='project_root_unavailable'):
        asyncio.run(run({'binary':'unused'},'Read',lambda *args:None,
                        {'root':str(missing),'permissions':{'read':True}},
                        'fixture','low',tmp_path/'session','codex',None))
    assert not missing.exists()


def test_edit_project_preserves_id_policy_and_persists(tmp_path):
    cfg=config(tmp_path)
    roots=[tmp_path/name for name in ('one','two','three')]
    for root in roots:root.mkdir()
    cfg['projects']['existing']={'label':'Existing','root':str(roots[0]),'permissions':{'write':False},'test_commands':{'check':['true']}}
    cfg['clients']['a']['projects'].append('existing')
    app=create_app(copy.deepcopy(cfg))
    with TestClient(app,headers={'Authorization':'Bearer a'}) as client:
        result=client.patch('/v1/projects',json={'project_id':'existing','name':'Renamed','paths':[str(roots[1]),str(roots[0]),str(roots[2])]})
        assert result.status_code==200,result.text
        assert result.json()['project_id']=='existing'
        spec=app.state.service.config['projects']['existing']
        assert spec['permissions']=={'write':False}
        assert spec['test_commands']=={'check':['true']}
        assert spec['root']==str(roots[1])
        assert spec['additional_roots']==[str(roots[0]),str(roots[2])]
        assert client.patch('/v1/projects',json={'project_id':'existing','name':'ab','paths':[str(roots[0])]}).status_code==422
        assert client.patch('/v1/projects',json={'project_id':'missing','name':'Valid','paths':[str(roots[0])]}).status_code==403
        assert client.patch('/v1/projects',json={'project_id':'sem-projeto','name':'Valid','paths':[str(roots[0])]}).status_code==403
        assert client.patch('/v1/projects',json={'project_id':'existing','name':'Valid','paths':['/']}).status_code==403
        client.post('/v1/projects',json={'name':'Other','paths':[str(roots[0])]})
        assert client.patch('/v1/projects',json={'project_id':'existing','name':'OTHER','paths':[str(roots[0])]}).status_code==409
    app.state.service.db.close()
    service=Service(copy.deepcopy(cfg))
    assert service.config['projects']['existing']==spec
    service.db.close()


@pytest.mark.parametrize('state',['queued','running'])
def test_edit_busy_project_is_atomic(tmp_path,state):
    cfg=config(tmp_path);root=tmp_path/'project';root.mkdir()
    app=create_app(cfg);service=app.state.service
    pid=service.add_project({'name':'Example','paths':[str(root)]})
    before=dict(service.config['projects'][pid])
    with service.db:
        service.db.execute('INSERT INTO jobs(id,project,owner,state,payload) VALUES(?,?,?,?,?)',('busy',pid,'a',state,'{}'))
    # No worker is started; the fixture's job remains in the requested state.
    client=TestClient(app,headers={'Authorization':'Bearer a'})
    response=client.patch('/v1/projects',json={'project_id':pid,'name':'Changed','paths':[str(root)]})
    assert response.status_code==409 and response.json()['code']=='project_busy'
    assert service.config['projects'][pid]==before
    assert client.patch('/v1/projects',json={'project_id':pid},headers={'Authorization':'Bearer unknown'}).status_code==401
    client.close();service.db.close()
