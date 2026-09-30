"""Durable publication state machine; only the harness dispatches approved artifacts."""

import asyncio
import hashlib
import json
import time
import uuid

from ..errors import APIError
from ..integrations import CredentialStore, integration_contract, validate_request
from ..jira_effects import JiraEffectDriver
from ..persistence.db import encoded
from .budgets import timeout_seconds


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def binding(request):
    return dict(
        operation=request["operation"],
        destination=request["destination"],
        arguments_digest=digest(request["arguments"]),
        artifact_digest=digest(request["artifact"]),
    )


class EffectService:
    def __init__(self, service):
        self.service = service
        self.db = service.db
        self.credentials = CredentialStore(
            service.config.get(
                "effect_credentials_path", service.root / "harness.effect_credentials.json"
            )
        )
        self.driver = JiraEffectDriver()
        self.tasks = {}
        for row in self.db.execute(
            "SELECT effect_id,status FROM effects WHERE status IN ('prepared','executing')"
        ).fetchall():
            self._status(
                row["effect_id"],
                "unknown" if row["status"] == "executing" else "invalidated",
                reason="service_restarted",
            )

    def get(self, effect_id):
        row = self.db.execute("SELECT * FROM effects WHERE effect_id=?", (effect_id,)).fetchone()
        if row is None:
            raise APIError("effect_not_found", 404)
        request = json.loads(row["request"])
        artifact = json.loads(row["artifact"])
        stored_binding = json.loads(row["binding"])
        return dict(
            request,
            arguments_digest=stored_binding["arguments_digest"],
            artifact_digest=stored_binding["artifact_digest"],
            effect_id=effect_id,
            request_id=effect_id,
            idempotency_key=effect_id,
            endpoint=json.loads(row["contract"])["endpoint"],
            job_id=row["job_id"],
            gate_id=row["gate_id"],
            status=row["status"],
            artifact=artifact,
            artifact_preview=canonical(artifact),
            execution_id=row["execution_id"],
            enforcement=row["enforcement"],
            approved_by=row["approved_by"],
            receipt=json.loads(row["receipt"]) if row["receipt"] else None,
            next_reconcile_at=row["next_reconcile_at"],
        )

    def for_job(self, job_id):
        return [
            self.get(row[0])
            for row in self.db.execute(
                "SELECT effect_id FROM effects WHERE job_id=? ORDER BY rowid", (job_id,)
            ).fetchall()
        ]

    def _event(self, effect_id, kind, **extra):
        effect = self.get(effect_id)
        self.service.event(effect["job_id"], kind, {**effect, **extra})

    def _status(self, effect_id, status, receipt=None, **extra):
        with self.db:
            self.db.execute(
                "UPDATE effects SET status=?,receipt=? WHERE effect_id=?",
                (status, encoded(receipt) if receipt else None, effect_id),
            )
            self._event(effect_id, "effect_" + status, **extra)

    async def prepare(self, job_id, request, *, execution_id=None, enforcement="unenforced"):
        job = self.service.conversation_repository.get(job_id)
        if not job or job["state"] != "running":
            raise APIError("effect_execution_inactive", 409)
        contract = integration_contract(
            self.service.config, request.get("integration") if isinstance(request, dict) else None
        )
        validate_request(contract, request)
        self.credentials.get(contract["credential_binding"])
        request = json.loads(canonical(request))
        artifact = request.pop("artifact")
        action_binding = binding({**request, "artifact": artifact})
        execution_id = execution_id or job_id
        prepare_limit = self.service.config.get("effect_prepare_limit", 5)
        if type(prepare_limit) is not int or prepare_limit < 1:
            raise ValueError("invalid_effect_prepare_limit")
        effect_id, gate_id = uuid.uuid4().hex, uuid.uuid4().hex
        wait_limit = timeout_seconds(self.service.config, "approval_timeout_seconds", 1800)
        with self.db:
            if self.db.execute(
                "SELECT 1 FROM effects WHERE job_id=? AND binding=? "
                "AND status IN ('unknown','executing','done') LIMIT 1",
                (job_id, canonical(action_binding)),
            ).fetchone():
                raise APIError("effect_duplicate_outcome_pending", 409)
            count = self.db.execute(
                "SELECT COUNT(*) FROM effects WHERE job_id=? AND execution_id=?",
                (job_id, execution_id),
            ).fetchone()[0]
            if count >= prepare_limit:
                raise APIError("effect_prepare_limit", 429)
            self.db.execute(
                "INSERT INTO effects(effect_id,job_id,gate_id,status,request,artifact,binding,contract,execution_id,enforcement) VALUES(?,?,?,'prepared',?,?,?,?,?,?)",
                (
                    effect_id,
                    job_id,
                    gate_id,
                    canonical(request),
                    canonical(artifact),
                    canonical(action_binding),
                    canonical(contract),
                    execution_id,
                    enforcement,
                ),
            )
        effect = self.get(effect_id)
        spec = dict(
            effect,
            kind="publish",
            publish=True,
            question="Approve publication?",
            options=[{"id": "approve", "label": "Approve"}, {"id": "deny", "label": "Deny"}],
            multi_select=False,
            risk="high",
            timeout_at=time.time() + wait_limit,
            on_timeout="deny",
            evidence=[
                {
                    "operation": effect["operation"],
                    "destination": effect["destination"],
                    "artifact_digest": effect["artifact_digest"],
                    "arguments_digest": effect["arguments_digest"],
                }
            ],
        )
        future = asyncio.get_running_loop().create_future()
        with self.db:
            self.service.gates.repository.create(gate_id, job_id, spec)
            self._event(effect_id, "effect_prepared")
        self.service.approvals[gate_id] = (job_id, future)
        self.service.gates.progress[gate_id] = lambda kind, data: self.service.event(
            job_id, kind, data
        )
        self.service.event(job_id, "gate_required", spec)
        task = asyncio.create_task(self._wait(effect_id, future, wait_limit))
        self.tasks[effect_id] = task
        task.add_done_callback(lambda _task: self.tasks.pop(effect_id, None))
        return effect

    async def _wait(self, effect_id, future, wait_limit):
        effect = self.get(effect_id)
        gate_id = effect["gate_id"]
        try:
            try:
                resolution = await asyncio.wait_for(future, wait_limit)
            except TimeoutError:
                with self.db:
                    self.service.gates.repository.close(gate_id, "expired")
                self.service.event(effect["job_id"], "gate_expired", {"gate_id": gate_id})
                self._status(effect_id, "denied", reason="gate_expired")
                return
            if resolution["choice"] != "approve":
                self._status(effect_id, "denied")
                return
            await self.execute(effect_id)
        except asyncio.CancelledError:
            current = self.get(effect_id)
            if current["status"] == "executing":
                self._status(effect_id, "unknown", reason="execution_interrupted")
            elif current["status"] == "prepared":
                self._status(effect_id, "invalidated", reason="service_stopped")
            raise
        finally:
            self.service.approvals.pop(gate_id, None)
            self.service.gates.progress.pop(gate_id, None)
            with self.db:
                changed = self.service.gates.repository.close(gate_id, "invalidated")
            if changed:
                self.service.event(
                    effect["job_id"],
                    "gate_invalidated",
                    {"gate_id": gate_id, "reason": "execution_ended", "reask": True},
                )

    async def execute(self, effect_id):
        """Not exposed to HTTP/MCP. The committed human gate is the only authority."""
        effect = self.get(effect_id)
        if effect["status"] != "prepared":
            raise APIError("effect_already_used", 409)
        gate = self.service.gates.repository.get(effect["gate_id"])
        if gate["state"] != "resolved" or json.loads(gate["choice"] or "null") != "approve":
            raise APIError("effect_approval_required", 403)
        spec = json.loads(gate["spec"])
        job = self.service.conversation_repository.get(effect["job_id"])
        try:
            contract = integration_contract(self.service.config, effect["integration"])
            stored = self.db.execute(
                "SELECT contract,binding FROM effects WHERE effect_id=?", (effect_id,)
            ).fetchone()
            request = {
                key: effect[key]
                for key in ("integration", "operation", "destination", "arguments", "artifact")
            }
            validate_request(contract, request)
            expected = binding(request)
            if (
                canonical(contract) != stored["contract"]
                or canonical(expected) != stored["binding"]
                or any(spec.get(key) != value for key, value in expected.items())
                or gate["resolved_by"] != job["owner"]
                or time.time() >= spec["timeout_at"]
                or job["state"] in ("cancelled", "interrupted", "failed")
            ):
                raise APIError("effect_binding_changed")
            credentials = self.credentials.get(contract["credential_binding"])
        except (APIError, ValueError, TypeError, KeyError):
            self._status(effect_id, "invalidated", reason="effect_binding_changed")
            return self.get(effect_id)
        # No await between validation, exclusive consumption and durable intent. This
        # connection's transaction must commit before the first network instruction.
        with self.db:
            changed = self.db.execute(
                "UPDATE effects SET status='executing',approved_by=? WHERE effect_id=? AND status='prepared'",
                (gate["resolved_by"], effect_id),
            ).rowcount
            if changed != 1:
                raise APIError("effect_already_used", 409)
            self._event(effect_id, "effect_approved", approved_by=gate["resolved_by"])
            self._event(effect_id, "effect_intent", idempotency_key=effect_id)
        self._event(effect_id, "effect_execution")
        try:
            status, receipt = await self.driver.create(contract, credentials, effect)
        except asyncio.CancelledError:
            self._status(effect_id, "unknown", reason="execution_interrupted")
            raise
        except Exception:
            # Neither exception strings nor Jira bodies are trusted diagnostic content.
            status, receipt = "unknown", None
        self._status(effect_id, status, receipt=receipt)
        return self.get(effect_id)

    async def reconcile(self, effect_id, identity, decision):
        effect = self.get(effect_id)
        job = self.service.job(identity, effect["job_id"])
        if job["owner"] != identity[0]:
            raise APIError("approval_owner_denied", 403)
        if effect["status"] != "unknown":
            raise APIError("effect_not_unknown", 409)
        if decision not in ("check", "keep_unknown"):
            raise APIError("effect_reconcile_invalid")
        if decision == "check" and time.time() < effect["next_reconcile_at"]:
            raise APIError("effect_reconcile_backoff", 409)
        self._event(
            effect_id, "effect_reconciliation_requested", decision=decision, resolved_by=identity[0]
        )
        receipt = None
        if decision == "check":
            # Durable backoff also serializes concurrent reconciliation requests.
            attempts = self.db.execute(
                "SELECT reconcile_attempts FROM effects WHERE effect_id=?", (effect_id,)
            ).fetchone()[0]
            with self.db:
                self.db.execute(
                    "UPDATE effects SET reconcile_attempts=reconcile_attempts+1,next_reconcile_at=? WHERE effect_id=?",
                    (time.time() + min(300, 2 ** min(attempts + 1, 9)), effect_id),
                )
            try:
                contract = integration_contract(self.service.config, effect["integration"])
                stored = self.db.execute(
                    "SELECT contract FROM effects WHERE effect_id=?", (effect_id,)
                ).fetchone()[0]
                if canonical(contract) == stored:
                    receipt = await self.driver.reconcile(
                        contract, self.credentials.get(contract["credential_binding"]), effect
                    )
            except Exception:
                receipt = None
        if receipt:
            self._status(effect_id, "done", receipt=receipt)
        self._event(
            effect_id,
            "effect_reconciled",
            decision=decision,
            resolved_by=identity[0],
            evidence="marker_found" if receipt else "inconclusive",
        )
        return self.get(effect_id)

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
