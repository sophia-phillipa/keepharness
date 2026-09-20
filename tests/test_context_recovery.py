import asyncio
import json
from unittest.mock import AsyncMock, patch

from agent_service.app import Service, context_overflow
from Adapters.local.sandbox import ISOLATION_VERSION
from test_workspaces import config


def test_overflow_excludes_inherited_files_and_resets_native_session_once(tmp_path):
    cfg=config(tmp_path);cfg['services']['local']['mode']='native';cfg['local']={}
    service=Service(cfg)
    payloads=[{'prompt':'Previous valid question','file_ids':['old-csv']},
              {'prompt':'Huge rejected request','parent_job_id':'a','file_ids':['new-csv']},
              {'prompt':'Continue normally','parent_job_id':'b','backend':'local','model':'installed-model','project_id':'p'}]
    error='codex_execution_failed: {"type":"exceed_context_size_error","n_prompt_tokens":90412,"n_ctx":65536}'
    for ident,payload,result in zip(['a','b','c'],payloads,[{'answer':'Previous answer'},{'error':error},{}]):
        service.db.execute('INSERT INTO jobs(id,project,owner,state,created,payload,result) VALUES(?,?,?,?,?,?,?)',(ident,'p','a','failed' if ident=='b' else 'completed',1,json.dumps(payload),json.dumps(result)))
    service.db.commit();row=dict(service.db.execute("SELECT * FROM jobs WHERE id='c'").fetchone())
    turns=service.context_turns(row,payloads[-1])
    assert all(not p['file_ids'] for p,_ in turns)
    assert turns[-1][0]['prompt']==''
    assert json.loads(service.db.execute("SELECT payload FROM jobs WHERE id='b'").fetchone()[0])['file_ids']==['new-csv']
    session=tmp_path/'sessions/a/local';session.mkdir(parents=True)
    marker=session/'native-thread.json';marker.write_text(json.dumps({'id':'poisoned'}))
    async def run(*args):
        assert 'old-csv' not in args[1] and 'new-csv' not in args[1]
        assert 'Huge rejected request' not in args[1]
        assert 'Previous answer' in args[1]
        assert not marker.exists()
        marker.write_text(json.dumps({'id':'healthy','isolation':ISOLATION_VERSION}))
        return {'answer':'Recovered','thread_id':'healthy'}
    with patch('Adapters.run_native',side_effect=run):
        assert asyncio.run(service.infer(row,payloads[-1]))['answer']=='Recovered'
    assert (session/'native-thread.json.before-context-recovery').exists()
    service.finish('c','completed',{'answer':'Recovered','thread_id':'healthy'})
    next_payload={**payloads[-1],'parent_job_id':'c','prompt':'Next request'}
    service.db.execute('INSERT INTO jobs(id,project,owner,state,created,payload) VALUES(?,?,?,?,?,?)',('d','p','a','running',2,json.dumps(next_payload)))
    service.db.commit();next_row=dict(service.db.execute("SELECT * FROM jobs WHERE id='d'").fetchone())
    with patch('Adapters.run_native',AsyncMock(return_value={'answer':'Still healthy'})):
        asyncio.run(service.infer(next_row,next_payload))
    assert json.loads(marker.read_text())['id']=='healthy'
    service.db.close()


def test_only_context_failures_trigger_recovery():
    assert context_overflow('request (90412 tokens) exceeds the available context size (65536 tokens)')
    assert context_overflow('context_limit_exceeded')
    assert context_overflow('source_context_limit')
    assert not context_overflow('authentication_required')
