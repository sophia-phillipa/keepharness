"""P5 §10: real, opt-in, end-to-end runs against the actual ``claude``/``codex`` CLIs.

Skipped unless ``TAIL_HARNESS_LIVE=1`` (see ``tests/conftest.py``). This is the one file
in the suite that spends real tokens: it uses the cheapest model available on the
account and the lowest effort, caps every job at 60s and at most 10 jobs per provider
(4 are actually used here), and always stops the harness in teardown.

Real ``HOME`` (for the CLI's own login state), a temporary ``--state``, and two free
ports: ``P`` for the admin panel (``python -m control``) and ``H`` for the harness it
spawns (``Manager.start``, via ``POST /api/start``).
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
PYTHON = sys.executable

JOB_TIMEOUT_SECONDS = 60
PROVIDER_BUDGET_SECONDS = 300
MAX_JOBS_PER_PROVIDER = 10


def _free_port():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def _pick_model(provider, models):
    """The cheapest model/effort combination available on this account."""
    if provider == "claude":
        model = next((m for m in models if re.search("haiku", m, re.I)), next(iter(models)))
        effort = "configured" if "configured" in models[model] else models[model][0]
        return model, effort
    model = next(
        (m for m in models if re.search("mini|nano|luna|small", m, re.I)),
        list(models)[-1],
    )
    efforts = models[model]
    effort = "low" if "low" in efforts else (min(efforts, key=len) if efforts else "low")
    return model, effort


class LiveHarness:
    """One admin panel + one spawned agent harness, both real subprocesses."""

    def __init__(self, tmp_path, admin_port, harness_port):
        self.state = tmp_path / "state"
        self.admin_port = admin_port
        self.harness_port = harness_port
        self.admin_base = f"http://127.0.0.1:{admin_port}"
        self.harness_base = f"http://127.0.0.1:{harness_port}"
        self.admin_process = None
        # Saving settings that enable a provider also re-checks it and starts the harness.
        self.client = httpx.Client(base_url=self.admin_base, timeout=90)

    def start_admin(self):
        self.admin_process = subprocess.Popen(
            [PYTHON, "-m", "control", "--port", str(self.admin_port), "--state", str(self.state)],
            cwd=REPOSITORY_ROOT,
            env=os.environ.copy(),
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if self.client.get("/").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        raise RuntimeError("admin panel did not come up in time")

    def admin_post(self, path, json_body):
        response = self.client.post(path, json=json_body, headers={"X-Harness-Admin": "1"})
        assert response.status_code == 200, (path, response.text)
        return response.json()

    def admin_get(self, path):
        response = self.client.get(path)
        response.raise_for_status()
        return response.json()

    def wait_for_harness(self, timeout=30):
        deadline = time.monotonic() + timeout
        with httpx.Client(base_url=self.harness_base, timeout=5) as harness_client:
            while time.monotonic() < deadline:
                try:
                    if harness_client.get("/v1/version").status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
        raise RuntimeError("harness did not come up in time")

    def stop(self):
        try:
            self.admin_post("/api/stop", {})
        except (httpx.HTTPError, RuntimeError, AssertionError):
            pass
        self.client.close()
        if self.admin_process is not None:
            self.admin_process.terminate()
            try:
                self.admin_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.admin_process.kill()
                self.admin_process.wait()


@pytest.fixture
def live_harness(tmp_path):
    harness = LiveHarness(tmp_path, _free_port(), _free_port())
    harness.start_admin()
    try:
        yield harness
    finally:
        harness.stop()


def _submit_and_wait(client, payload, timeout=JOB_TIMEOUT_SECONDS):
    response = client.post("/v1/jobs", json=payload)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = client.get(f"/v1/jobs/{job_id}").json()
        if info["state"] in ("completed", "failed", "cancelled", "interrupted"):
            return job_id, info
        time.sleep(1)
    client.post(f"/v1/jobs/{job_id}/cancel")
    raise TimeoutError(f"job {job_id} did not settle within {timeout}s")


def _result_text(client, job_id):
    response = client.get(f"/v1/jobs/{job_id}/artifacts/result.json")
    response.raise_for_status()
    return json.dumps(response.json())


def _wait_for_output(client, job_id, timeout=JOB_TIMEOUT_SECONDS):
    """Follow the job's SSE stream until the provider has produced output."""
    output = {"answer_delta", "reasoning_delta", "tool_start"}
    deadline = time.monotonic() + timeout
    with client.stream("GET", f"/v1/jobs/{job_id}/events") as stream:
        for line in stream.iter_lines():
            if line.startswith("event: ") and line[7:].strip() in output:
                return line[7:].strip()
            if time.monotonic() > deadline:
                break
    return None


@pytest.mark.live
@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_live_provider_end_to_end(provider, live_harness):
    """Discovery, auth check, a turn, a resume, a cancel, and (if possible) isolated mode."""
    harness = live_harness
    started_at = time.monotonic()

    def remaining_budget():
        return PROVIDER_BUDGET_SECONDS - (time.monotonic() - started_at)

    # 1. Discovery.
    scan = harness.admin_post("/api/scan", {})
    entry = next(s for s in scan["services"] if s["id"] == provider)
    if not entry["found"]:
        pytest.skip(f"{provider} CLI not found on this account/machine")
    if not entry["credential_present"]:
        pytest.skip(f"{provider} has no stored credential on this account")

    # 2. Auth + catalog check.
    check = harness.admin_post("/api/check", {"provider": provider})
    assert check["authenticated"] is True, check
    assert check["models"], "expected at least one model in the catalog"

    # 3. Cheapest model/effort.
    model, effort = _pick_model(provider, check["models"])

    # 4. Enable natively, point the harness at a free port, start it.
    settings = harness.admin_get("/api/state")["settings"]
    settings["services"][provider] = {
        **settings["services"][provider],
        "enabled": True,
        "mode": "native",
        "models": [model],
        "projects": ["sem-projeto"],
    }
    settings["port"] = harness.harness_port
    harness.admin_post("/api/settings", settings)
    harness.admin_post("/api/start", {})
    harness.wait_for_harness()

    with httpx.Client(base_url=harness.harness_base, timeout=JOB_TIMEOUT_SECONDS + 5) as client:
        base_payload = {
            "project_id": "sem-projeto",
            "backend": provider,
            "model": model,
            "effort": effort,
            "execution_mode": "native",
        }

        # 5. A real turn.
        root_id, root_info = _submit_and_wait(
            client, {**base_payload, "prompt": "Reply with exactly: PONG"}
        )
        assert root_info["state"] == "completed", root_info
        # Exact: an empty "SOURCES:" trailer on the prompt was once echoed back.
        assert root_info["result"]["answer"].strip() == "PONG", root_info["result"]

        # 6. Resume: the provider must recall its own previous reply. A continuation
        # inherits the conversation's execution mode and must not name one.
        continuation = {k: v for k, v in base_payload.items() if k != "execution_mode"}
        resume_id, resume_info = _submit_and_wait(
            client,
            {
                **continuation,
                "parent_job_id": root_id,
                "prompt": "What word did you reply with? Answer with just that word.",
            },
        )
        assert resume_info["state"] == "completed", resume_info
        assert "PONG" in _result_text(client, resume_id)

        # 7. Cancel mid-stream: wait for the provider's first output, then cancel.
        cancel_payload = {
            **base_payload,
            "prompt": "Count from 1 to 300, one number per line, nothing else.",
        }
        submitted = client.post("/v1/jobs", json=cancel_payload)
        submitted.raise_for_status()
        cancel_job_id = submitted.json()["job_id"]
        assert _wait_for_output(client, cancel_job_id), "no provider output before cancelling"
        client.post(f"/v1/jobs/{cancel_job_id}/cancel").raise_for_status()
        deadline = time.monotonic() + 15
        cancelled_state = None
        while time.monotonic() < deadline:
            cancelled_state = client.get(f"/v1/jobs/{cancel_job_id}").json()["state"]
            if cancelled_state in ("cancelled", "interrupted", "completed", "failed"):
                break
            time.sleep(0.5)
        assert cancelled_state == "cancelled", cancelled_state

        # 8. Isolated (scoped) mode is chosen per conversation; it needs bubblewrap.
        # The harness itself resolves an npm wrapper to its native binary.
        if sys.platform == "linux" and shutil.which("bwrap") and remaining_budget() > 90:
            scoped_id, scoped_info = _submit_and_wait(
                client,
                {
                    **base_payload,
                    "execution_mode": "scoped",
                    "prompt": "Reply with exactly the word PONG",
                },
            )
            assert scoped_info["state"] == "completed", scoped_info
            assert scoped_info["request"]["execution_mode"] == "scoped", scoped_info
            assert "PONG" in _result_text(client, scoped_id)
