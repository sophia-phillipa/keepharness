"""Admission limits across identities; in-process ASGI, no worker or inference."""
import asyncio
from collections import Counter
import hashlib

import httpx

from agent_service.app import create_app
from test_workspaces import config


def test_ten_identities_share_bounded_queue_and_cancel_releases_capacity(tmp_path):
    cfg = config(tmp_path)
    cfg['clients'] = {f'user-{i}': {
        'sha256': hashlib.sha256(f'user-{i}'.encode()).hexdigest(), 'projects': ['p']
    } for i in range(10)}
    # This fixture only exposes a dummy local provider; no cloud account is used.
    cfg['services'].pop('codex')
    app = create_app(cfg)
    service = app.state.service

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test') as client:
            payload = {'project_id': 'p', 'backend': 'local', 'model': 'installed-model',
                       'prompt': 'Admission-only fixture'}

            async def submit(owner, sequence):
                response = await client.post('/v1/jobs', json=payload, headers={
                    'Authorization': 'Bearer ' + owner,
                    'Idempotency-Key': f'{owner}-{sequence}'})
                return owner, response

            results = await asyncio.gather(*(submit(owner, n)
                for owner in cfg['clients'] for n in range(10)))
            counts = Counter(response.status_code for _, response in results)
            assert counts == {202: 32, 429: 68}, counts
            accepted = Counter(owner for owner, response in results if response.status_code == 202)
            assert max(accepted.values()) <= 10
            for _, response in results:
                if response.status_code == 429:
                    assert response.json()['code'] in ('queue_full', 'owner_queue_full')
                    assert int(response.headers['Retry-After']) >= 1
            assert service.db.execute("SELECT count(*) FROM jobs WHERE state='queued'").fetchone()[0] == 32
            assert service.task is None  # No lifespan/background worker was started.

            owner, response = next((owner, response) for owner, response in results
                                   if response.status_code == 202)
            jid = response.json()['job_id']
            headers = {'Authorization': 'Bearer ' + owner}
            assert (await client.get('/v1/jobs/' + jid, headers=headers)).status_code == 200
            cancelled = await client.post('/v1/jobs/' + jid + '/cancel', headers=headers)
            assert cancelled.status_code == 200
            assert service.db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0] == 31

            beneficiary = next(name for name in cfg['clients'] if accepted[name] < 10)
            _, replacement = await submit(beneficiary, 'replacement')
            assert replacement.status_code == 202, replacement.text
            _, overflow = await submit(beneficiary, 'overflow')
            assert overflow.status_code == 429
            assert overflow.json()['code'] == 'queue_full'
            queue = dict(service.db.execute("SELECT owner,count(*) FROM jobs WHERE state IN ('queued','running') GROUP BY owner"))
            assert sum(queue.values()) == 32 and max(queue.values()) <= 10
            print('100 admission requests from 10 identities:', dict(counts),
                  '; one cancellation released one slot; no inference executed')

    try:
        asyncio.run(scenario())
    finally:
        service.db.close()
