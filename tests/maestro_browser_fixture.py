"""Temporary real HTTP harness with deterministic planning; no provider is invoked."""

import json
import socket
import sys
from pathlib import Path

import uvicorn

from agent_service.app import create_app
from agent_service.approval_sessions import consume_enrollment, issue_enrollment

root = Path(sys.argv[1])
project = root / "project"
project.mkdir()
agents = project / ".codex" / "agents"
agents.mkdir(parents=True)
(agents / "reviewer.toml").write_text('name = "reviewer"\ndescription = "Synthetic reviewer"\ndeveloper_instructions = "Review synthetic facts"\n')
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
port = sock.getsockname()[1]
config = {
    "state_dir": str(root / "state"),
    "local_access": True,
    "origins": [f"http://127.0.0.1:{port}"],
    "projects": {"sem-projeto": {"root": str(project)}},
    "clients": {"local": {"sha256": "0" * 64, "projects": ["sem-projeto"]}},
    "services": {
        "codex": {
            "enabled": True,
            "models": ["gpt-6-astra"],
            "projects": ["sem-projeto"],
            "permissions": {"read": True},
        }
    },
    "codex_models": {"gpt-6-astra": ["low"]},
    "maestro_plan_policy": "review",
}
app = create_app(config)
service = app.state.service
inference_stages = []


async def infer(row, data):
    inference_stages.append(data.get("_maestro_stage", "direct"))
    (root / "inference.json").write_text(json.dumps(inference_stages))
    if data.get("_maestro_stage") == "plan":
        return {
            "answer": json.dumps(
                {
                    "steps": [
                        {
                            "role": "reviewer",
                            "backend": "codex",
                            "model": "gpt-6-astra",
                            "effort": "low",
                            "task": "Review synthetic facts",
                            "reason": "Check evidence",
                        }
                    ]
                }
            )
        }
    return {"answer": "Synthetic review complete"}


async def models(project_id=None):
    return service.models(project_id)


async def quota(*args):
    return {"available": False, "reason": "synthetic_fixture"}


service.infer = infer
service.models_with_context = models
service.quota = quota
session = consume_enrollment(config, issue_enrollment(config, "local"))
(root / "ready.json").write_text(json.dumps({"port": port, "session": session}))
uvicorn.Server(uvicorn.Config(app, log_level="error", proxy_headers=False)).run(sockets=[sock])
