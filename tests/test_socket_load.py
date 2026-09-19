"""Loopback HTTP burst; lifespan is disabled so no inference worker can start."""
import asyncio
from collections import Counter
import socket
import httpx
import uvicorn
from agent_service.app import create_app
from test_workspaces import config


def test_loopback_burst_and_recovery(tmp_path):
    app=create_app(config(tmp_path))
    async def scenario():
        listener=socket.socket()
        listener.bind(('127.0.0.1',0))
        port=listener.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,lifespan='off',limit_concurrency=64,access_log=False,log_level='error'))
        task=asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():await task
                    await asyncio.sleep(.01)
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}',limits=httpx.Limits(max_connections=20,max_keepalive_connections=20),timeout=30,headers={'Authorization':'Bearer a'}) as client:
                payload={'project_id':'p','backend':'local','model':'installed-model','prompt':'network fixture'}
                async with asyncio.timeout(45):
                    responses=await asyncio.gather(*(client.post('/v1/jobs',json=payload) for _ in range(500)))
                counts=Counter(r.status_code for r in responses)
                assert counts=={202:10,429:490},counts
                assert all(r.headers.get('Retry-After') for r in responses if r.status_code==429)
                job=next(r.json()['job_id'] for r in responses if r.status_code==202)
                assert (await client.get('/v1/jobs/'+job)).status_code==200
                assert (await client.post('/v1/jobs/'+job+'/cancel')).status_code==200
                assert (await client.post('/v1/jobs',json=payload,headers={'Authorization':'Bearer b'})).status_code==202
                assert app.state.service.task is None
        finally:
            server.should_exit=True
            try:
                await asyncio.wait_for(task,5)
            finally:listener.close()
    try:asyncio.run(scenario())
    finally:app.state.service.db.close()
