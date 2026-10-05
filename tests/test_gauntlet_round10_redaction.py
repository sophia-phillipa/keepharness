import asyncio
import hashlib
import json
import subprocess
import sys

import httpx
import pytest

from agent_service.app import create_app

SECRET = "SYNTHETIC-OLD-VAULT-SECRET-481"
WRITER = r"""
import asyncio, hashlib, json, sys
from agent_service.app import create_app
async def main():
    cfg=json.load(open(sys.argv[1])); app=create_app(cfg); s=app.state.service
    with s.db: s.conversation_repository.insert('job','p','alice','completed',1,'{}',None,'job','job')
    s.effects.credentials.set('fixture',{'email':'fixture@example.invalid','token':'SYNTHETIC-OLD-VAULT-SECRET-481'})
    secret='SYNTHETIC-OLD-VAULT-SECRET-481'
    plan={'steps':[{'role':'reviewer','backend':'codex','model':'gpt-6-astra','effort':'low','task':'Original '+secret,'reason':secret}]} if sys.argv[3]=='plan' else None
    choice='approve' if plan else 'ok'
    task=asyncio.create_task(s.gates.ask('job',{'question':'Review diagnostic SYNTHETIC-OLD-VAULT-SECRET-481','options':[{'id':choice,'label':'Continue '+secret,'description':secret}], 'evidence':[{'text':secret}]},lambda kind,data:s.event('job',kind,data)))
    await asyncio.sleep(.01)
    gate=s.gates.repository.for_job('job')[0]
    if plan: plan['steps'][0]['task']='Edited '+secret
    s.gates.resolve(gate['gate_id'], ('alice',cfg['clients']['alice']), {'choice':choice, **({'plan':plan} if plan else {})})
    assert (await task)['choice']==choice
    if sys.argv[2] != 'new':
        with s.db:
            if 'public_spec' in {r[1] for r in s.db.execute('PRAGMA table_info(gates)')}:
                s.db.execute('ALTER TABLE gates DROP COLUMN public_spec')
                s.db.execute('UPDATE schema_version SET version=version-1')
            if sys.argv[2] == 'missing': s.db.execute('DELETE FROM events')
            if sys.argv[2] == 'mismatched': s.db.execute("UPDATE events SET job='another-job'")
            if sys.argv[2] == 'malformed': s.db.execute("UPDATE events SET data='[]'")
    s.effects.credentials.set('fixture',{'email':'fixture@example.invalid','token':'SYNTHETIC-NEW-VAULT-SECRET-927'})
    await s.effects.close(); s.db.close()
asyncio.run(main())
"""


@pytest.mark.parametrize("kind", ["ordinary"])  # plan gates were removed with the Maestro planner
@pytest.mark.parametrize("history", ["new", "legacy", "missing", "mismatched", "malformed"])
def test_gate_replay_preserves_redaction_across_restart(tmp_path, history, kind):
    cfg = {
        "state_dir": str(tmp_path / "state"),
        "origins": [],
        "projects": {"p": {}},
        "services": {
            "codex": {
                "enabled": True,
                "models": ["gpt-6-astra"],
                "projects": ["p"],
                "permissions": {"read": True},
            }
        },
        "codex_models": {"gpt-6-astra": ["low"]},
        "clients": {"alice": {"sha256": hashlib.sha256(b"alice").hexdigest(), "projects": ["p"]}},
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg))
    subprocess.run([sys.executable, "-c", WRITER, str(path), history, kind], check=True)

    async def run():
        app = create_app(cfg)
        s = app.state.service
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers={"Authorization": "Bearer alice"},
            ) as c:
                events = await c.get("/v1/jobs/job/events?format=json")
                job = await c.get("/v1/jobs/job")
                conversation = await c.get("/v1/conversations/job")
                assert events.status_code == job.status_code == conversation.status_code == 200
                print(
                    json.dumps(
                        {
                            "events_leak": SECRET in events.text,
                            "job_gate_leak": SECRET in job.text,
                            "conversation_gate_leak": SECRET in conversation.text,
                            "gate_question": job.json()["gates"][0]["question"],
                        }
                    )
                )
                assert SECRET not in events.text
                assert SECRET not in job.text and SECRET not in conversation.text
                gate = job.json()["gates"][0]
                assert "execution_id" not in gate and "schema_version" not in gate
                if history in ("missing", "mismatched", "malformed"):
                    assert gate["question"] == "Historical question content unavailable."
                    assert gate["choice"] is None
                else:
                    assert gate["question"].startswith("Review diagnostic ")
                    assert gate["choice"] == ("approve" if kind == "plan" else "ok")
                    assert gate["options"][0]["id"] == gate["choice"]
                    if kind == "plan":
                        assert gate["plan"]["steps"][0]["task"].startswith("Edited ")
                assert SECRET in s.gates.repository.for_job("job")[0]["spec"]
        finally:
            await s.effects.close()
            s.db.close()

    asyncio.run(run())


def test_known_secret_option_identifier_is_rejected(tmp_path):
    from agent_service.errors import APIError
    from agent_service.secret_vault import SecretVault
    from agent_service.services.gate_service import validate_options

    vault = SecretVault(tmp_path / "vault.json")
    vault.set("fixture", {"token": SECRET})
    with pytest.raises(APIError, match="invalid_gate_options"):
        validate_options(
            {"question": "Safe question", "options": [{"id": SECRET, "label": "Safe label"}]}
        )
