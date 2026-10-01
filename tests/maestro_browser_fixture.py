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
(agents / "reviewer.toml").write_text(
    'name = "reviewer"\ndescription = "Synthetic reviewer"\ndeveloper_instructions = "Review synthetic facts"\n'
)
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
if len(sys.argv) > 2 and sys.argv[2] == "local":
    config["services"]["local"] = {
        "enabled": True,
        "models": ["installed-model"],
        "projects": ["sem-projeto"],
        "permissions": {"read": True},
    }
    config["maestro_coordinator"] = {"backend": "local", "model": "installed-model"}
    workflows = project / "workflows"
    workflows.mkdir()
    (workflows / "local-review.json").write_text(
        json.dumps(
            {
                "id": "local-review",
                "steps": [
                    {
                        "role": "Reviewer",
                        "task": "Review synthetic facts",
                        "reason": "Check facts",
                        "backend": "local",
                        "model": "installed-model",
                        "effort": "configured",
                    }
                ],
            }
        )
    )
app = create_app(config)
service = app.state.service
inference_stages = []
retry_attempts = {}
round9 = len(sys.argv) > 2 and sys.argv[2] == "round9"
if round9:
    skill = project / ".agents/skills/check/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: check\ndescription: Synthetic check\n---\nCheck synthetic facts")


async def infer(row, data):
    if round9:
        prompt = data.get("prompt", "")
        if "RETRY-" in prompt:
            marker = prompt.split("RETRY-", 1)[1].split()[0]
            retry_attempts[marker] = retry_attempts.get(marker, 0) + 1
            if retry_attempts[marker] == 1:
                raise RuntimeError("synthetic_execution_failure")
        if "ASK-" in prompt:
            await service.gates.ask(row["id"], {"question": "Audience?", "options": [
                {"id": "staff", "label": "Staff"}, {"id": "public", "label": "Public"}
            ]}, lambda kind, value: service.event(row["id"], kind, value))
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
