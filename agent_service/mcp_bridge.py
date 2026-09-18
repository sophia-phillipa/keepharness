"""Official MCP SDK over stdio; calls the authenticated durable HTTP API."""
import json
import os
from pathlib import Path
import httpx
from mcp.server.fastmcp import FastMCP

mcp=FastMCP('tail-harness')

def config():
    path=Path(os.environ.get('LOCAL_AGENT_CLIENT',str(Path.home()/'.config/tail-harness/client.json')))
    cfg=json.loads(path.read_text()) if path.exists() else {}
    cfg['url']=os.environ.get('LOCAL_AGENT_URL',cfg.get('url','http://127.0.0.1:8095'))
    return cfg

async def call(method,path,data=None,headers=None,content=None):
    cfg=config()
    token=Path(cfg['key_file']).read_text().strip() if cfg.get('key_file') else None
    async with httpx.AsyncClient(timeout=90,trust_env=False) as client:
        response=await client.request(method,cfg['url'].rstrip('/')+path,json=data,content=content,
            headers={**({'Authorization':'Bearer '+token} if token else {}),**(headers or {})})
        if response.is_error:
            return {'error':response.json(),'http_status':response.status_code}
        return response.json()

@mcp.tool()
async def local_capabilities() -> dict:
    """Read tested/experimental/unavailable capabilities; never infer capabilities from model claims."""
    return await call('GET','/.well-known/agent-capabilities.json')

@mcp.tool()
async def conversations(conversation_id:str|None=None) -> dict:
    """List conversations or read all turns. Continue using the latest job id as parent_job_id."""
    return await call('GET','/v1/conversations'+('/'+conversation_id if conversation_id else ''))

@mcp.tool()
async def usage_limits() -> dict:
    """Read current shared ChatGPT/Codex quota. Local inference does not consume this quota."""
    return await call('GET','/v1/usage')

@mcp.tool()
async def available_models() -> dict:
    """Read enabled Codex, Claude and local models and compatible efforts."""
    return await call('GET','/v1/models')

@mcp.tool()
async def local_projects() -> dict:
    """List projects authorized for this client."""
    return await call('GET','/v1/projects')

@mcp.tool()
async def assess(project_id:str,prompt:str,backend:str,model:str,effort:str='configured',kind:str='infer') -> dict:
    """Assess scope before delegation. Review permissions before delegation."""
    return await call('POST','/v1/assess',locals())

@mcp.tool()
async def submit_job(project_id:str,prompt:str='',kind:str='infer',file_ids:list[str]|None=None,
                     arguments:dict|None=None,max_tokens:int=6500,idempotency_key:str|None=None,backend:str='codex',model:str='gpt-6-astra',effort:str='low',parent_job_id:str|None=None) -> dict:
    """Queue inference with backend codex, claude or local. Use available_models first.

    Native mode uses CLI tools and permissions selected in administration. Approvals
    arrive through job_events; use resolve_approval or the web interface to respond.
    Use the latest job id as parent_job_id to keep the conversation context.
    """
    data=locals().copy();data.pop('idempotency_key');data['file_ids']=file_ids or [];data['arguments']=arguments or {}
    return await call('POST','/v1/jobs',data,{'Idempotency-Key':idempotency_key} if idempotency_key else {})

@mcp.tool()
async def resolve_approval(approval_id:str,approved:bool,answers:dict|None=None) -> dict:
    """Respond to a pending native CLI approval for this client's job.

    Obtain explicit user approval for the described action before approving it.
    Use approval_id from an approval_required event; false rejects the request.
    """
    return await call('POST','/v1/approvals/'+approval_id,{'approved':approved,'answers':answers or {}})

@mcp.tool()
async def job_status(job_id:str) -> dict:
    """Read state, result and real/null metrics. Completed can still have incomplete=true; inspect finish_reason."""
    return await call('GET','/v1/jobs/'+job_id)

@mcp.tool()
async def cancel_job(job_id:str) -> dict:
    """Cancel queued/running work and preserve partial events."""
    return await call('POST','/v1/jobs/'+job_id+'/cancel',{})

@mcp.tool()
async def get_artifact(job_id:str) -> dict:
    """Obtain result.json including evidence, metrics or proposed diff."""
    return await call('GET','/v1/jobs/'+job_id+'/artifacts/result.json')

@mcp.tool()
async def job_events(job_id:str,after:int=0) -> dict:
    """Read a bounded batch of persisted progress events. Reconnect using last_event_id; thinking only if provided."""
    cfg=config();token=Path(cfg['key_file']).read_text().strip() if cfg.get('key_file') else None;events=[]
    async with httpx.AsyncClient(timeout=15,trust_env=False) as client:
        async with client.stream('GET',cfg['url'].rstrip('/')+'/v1/jobs/'+job_id+'/events',
            headers={**({'Authorization':'Bearer '+token} if token else {}),'Last-Event-ID':str(after)}) as response:
            if response.is_error: return {'http_status':response.status_code}
            async for line in response.aiter_lines():
                if line.startswith('data: '): events.append(json.loads(line[6:]))
                if len(events)>=50 or line.startswith(': heartbeat'): break
    return {'events':events,'last_event_id':events[-1]['id'] if events else after}

@mcp.tool()
async def upload_text(project_id:str,filename:str,text:str) -> dict:
    """Upload UTF-8 text, at most 100000 characters. Large files/PDF use the explicit upload CLI, not model context."""
    if len(text)>100000: return {'error':'use_upload_cli'}
    import urllib.parse
    return await call('POST','/v1/files?project_id='+urllib.parse.quote(project_id,safe=''),
                      headers={'X-Filename':urllib.parse.quote(filename,safe='')},content=text.encode())

def main():
    import sys
    if len(sys.argv)>1 and sys.argv[1]=='upload':
        # The model-facing MCP tool cannot read arbitrary client paths. Only explicit CLI invocation does so.
        import asyncio,urllib.parse
        project,filename=sys.argv[2:4];path=Path(filename)
        if path.stat().st_size>50*1024*1024: raise SystemExit('50 MiB limit')
        print(json.dumps(asyncio.run(call('POST','/v1/files?project_id='+urllib.parse.quote(project,safe=''),
            headers={'X-Filename':urllib.parse.quote(path.name,safe='')},content=path.read_bytes()))))
    else:
        mcp.run(transport='stdio')


if __name__=='__main__':main()
