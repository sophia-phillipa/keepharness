import asyncio
import json
import sqlite3
import time
from unittest.mock import patch

from control.dashboard import DashboardReader, snapshot


def test_concurrent_viewers_share_one_nonblocking_snapshot(tmp_path):
    def slow(_):
        time.sleep(.05)
        return {'available':True}

    async def check():
        reader=DashboardReader(tmp_path)
        with patch('control.dashboard.snapshot',side_effect=slow) as read:
            tasks=[asyncio.create_task(reader.read()) for _ in range(12)]
            await asyncio.sleep(.01)
            assert not tasks[0].done()  # event loop continues while disk work runs
            assert all(r['available'] for r in await asyncio.gather(*tasks))
            await reader.read()
            assert read.call_count==1
    asyncio.run(check())


def test_large_queue_has_bounded_dashboard_payload(tmp_path):
    root=tmp_path/'runs';root.mkdir()
    with sqlite3.connect(root/'jobs.sqlite3') as db:
        db.executescript('CREATE TABLE jobs(id TEXT,project TEXT,state TEXT,created REAL,payload TEXT,result TEXT); CREATE TABLE events(id INTEGER PRIMARY KEY,job TEXT,time REAL,type TEXT,data TEXT); CREATE INDEX event_lookup ON events(job,type,time);')
        db.executemany('INSERT INTO jobs VALUES(?,?,?,?,?,?)',[(str(i),'p','queued',1000,json.dumps({'prompt':'x'*2000,'model':'test'}),None) for i in range(2000)])
    start=time.monotonic();data=snapshot(tmp_path,1001)
    print('dashboard 2000 requests seconds:',round(time.monotonic()-start,4))
    assert data['queued']==data['recent_count']==2000
    assert len(data['recent'])==data['recent_limit']==100
    assert len(json.dumps(data))<50000
