"""Isolated real HTTP enrollment and gate fixture for the browser regression."""

import asyncio
import json
import socket
import sys
import tempfile
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_service.app import create_app
from agent_service.approval_sessions import issue_enrollment


async def main():
    with tempfile.TemporaryDirectory(prefix="enrollment-test-") as directory:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            origin = f"http://127.0.0.1:{port}"
            config = dict(
                state_dir=directory,
                local_access=True,
                services={},
                clients={"local": {"sha256": "0" * 64, "projects": ["sem-projeto"]}},
                projects={"sem-projeto": {}},
                origins=[origin],
            )
            app = create_app(config)
            service = app.state.service
            with service.db:
                service.conversation_repository.insert(
                    "fixture",
                    "sem-projeto",
                    "local",
                    "running",
                    1,
                    "{}",
                    None,
                    "fixture",
                    "fixture",
                )
            gate = asyncio.create_task(
                service.gates.ask(
                    "fixture",
                    {"question": "Continue fixture?", "options": [{"id": "yes", "label": "Yes"}]},
                    lambda *_: None,
                )
            )
            await asyncio.sleep(0)
            gate_id = service.db.execute("SELECT gate_id FROM gates").fetchone()[0]
            nonce = issue_enrollment(config, "local")
            server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error"))
            task = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError("Enrollment fixture failed to start")
                    await asyncio.sleep(0.01)
                print(json.dumps(dict(origin=origin, nonce=nonce, gate_id=gate_id)), flush=True)
                await task
            finally:
                gate.cancel()
                await asyncio.gather(gate, return_exceptions=True)
                service.db.close()


if __name__ == "__main__":
    asyncio.run(main())
