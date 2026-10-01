"""Human option questions with durable, exclusive, owner-audited resolution."""

import asyncio
import json
import time
import uuid
from contextlib import nullcontext

from ..errors import APIError
from ..persistence.gates import GateRepository
from .budgets import timeout_seconds


def validate_options(request):
    options = request.get("options")
    if not isinstance(request.get("question"), str) or not request["question"].strip():
        raise APIError("invalid_gate_question")
    if not isinstance(options, list) or not 1 <= len(options) <= 100:
        raise APIError("invalid_gate_options")
    ids = set()
    for option in options:
        if not isinstance(option, dict):
            raise APIError("invalid_gate_options")
        identifier = option.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in ids:
            raise APIError("invalid_gate_options")
        if not isinstance(option.get("label"), str) or not option["label"]:
            raise APIError("invalid_gate_options")
        ids.add(identifier)
    if type(request.get("multi_select", False)) is not bool:
        raise APIError("invalid_gate_options")


def validate_choice(spec, choice):
    ids = {option["id"] for option in spec["options"]}
    if spec.get("multi_select"):
        valid = (
            isinstance(choice, list)
            and bool(choice)
            and all(isinstance(item, str) and item in ids for item in choice)
            and len(set(choice)) == len(choice)
        )
    else:
        valid = isinstance(choice, str) and choice in ids
    if not valid:
        raise APIError("invalid_gate_choice")


class GateService:
    def __init__(self, service):
        self.service = service
        self.repository = GateRepository(service.db)
        self.progress = {}

    def invalidate_pending(self):
        for row in self.repository.pending():
            with self.service.db:
                if self.repository.close(row["gate_id"], "invalidated"):
                    self.service.event(
                        row["job_id"],
                        "gate_invalidated",
                        {
                            "gate_id": row["gate_id"],
                            "reason": "service_restarted",
                            "reask": True,
                        },
                    )

    async def ask(self, job_id, request, progress, *, plan=None):
        validate_options(request)
        wait_limit = timeout_seconds(self.service.config, "approval_timeout_seconds", 1800)
        gate_id = uuid.uuid4().hex
        spec = {
            "gate_id": gate_id,
            "step": request.get("step"),
            "question": request["question"],
            "options": request["options"],
            "multi_select": request.get("multi_select", False),
            "risk": request.get("risk", "low"),
            "timeout_at": time.time() + wait_limit,
            "on_timeout": "deny",
            "publish": request.get("publish") is True,
            "evidence": request.get("evidence", []),
            "enforcement": "advisory",
        }
        if plan is not None:
            spec.update(kind="maestro_plan", plan=plan)
        future = asyncio.get_running_loop().create_future()
        with self.service.db:
            self.repository.create(gate_id, job_id, spec)
        self.service.approvals[gate_id] = (job_id, future)
        self.progress[gate_id] = progress
        progress("gate_required", spec)
        try:
            budget = self.service.runtime_budgets.get(job_id)
            with budget.human_wait() if budget else nullcontext():
                try:
                    return await asyncio.wait_for(future, wait_limit)
                except TimeoutError:
                    with self.service.db:
                        changed = self.repository.close(gate_id, "expired")
                    if changed:
                        progress("gate_expired", {"gate_id": gate_id})
                    return {"approved": False, "reason": "gate_expired"}
        finally:
            self.service.approvals.pop(gate_id, None)
            self.progress.pop(gate_id, None)
            with self.service.db:
                changed = self.repository.close(gate_id, "invalidated")
            if changed:
                progress(
                    "gate_invalidated",
                    {"gate_id": gate_id, "reason": "execution_ended", "reask": True},
                )

    def resolve(self, gate_id, identity, data):
        row = self.repository.get(gate_id)
        job = self.service.job(identity, row["job_id"])
        if job["owner"] != identity[0]:
            raise APIError("approval_owner_denied", 403)
        if row["state"] != "pending":
            raise APIError(
                "gate_already_resolved" if row["state"] == "resolved" else "gate_" + row["state"],
                409,
            )
        spec = json.loads(row["spec"])
        pending = self.service.approvals.get(gate_id)
        if not pending or pending[1].done() or time.time() >= spec["timeout_at"]:
            raise APIError("gate_expired", 409)
        choice = data.get("choice")
        validate_choice(spec, choice)
        resolution = {
            "gate_id": gate_id,
            "choice": choice,
            "resolved_by": identity[0],
            "at": time.time(),
        }
        if spec.get("kind") == "maestro_plan" and choice == "approve":
            from ..maestro import candidates, validate_plan

            payload = json.loads(job["payload"])
            available = candidates(
                self.service.config, job["project"],
                bool(payload.get("file_ids") or payload.get("workspace_id")),
            )
            if payload.get("workspace_id"):
                available = [model for model in available if model["permissions"].get("read")]
            edited_plan = data.get("plan", spec["plan"])
            if isinstance(edited_plan, dict) and isinstance(edited_plan.get("steps"), list):
                edited_plan = {
                    **edited_plan,
                    "steps": [
                        {key: value for key, value in step.items() if key != "invocation"}
                        if isinstance(step, dict) else step
                        for step in edited_plan["steps"]
                    ],
                }
            approved_plan = validate_plan(json.dumps(edited_plan), available)
            spec["plan"] = approved_plan
            resolution["plan"] = approved_plan
        with self.service.db:
            if not self.repository.resolve(gate_id, choice, identity[0], resolution["at"], spec=spec):
                raise APIError("gate_already_resolved", 409)
        try:
            self.progress[gate_id]("gate_resolved", resolution)
        finally:
            # A committed answer must reach its waiter even if event delivery fails.
            pending[1].set_result({"approved": True, **resolution})
        return {"resolved": True, **resolution}
