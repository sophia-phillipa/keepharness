"""Durable option-gate records; transactions belong to the caller."""

from ..secret_vault import redact_secrets
from .db import encoded


class GateRepository:
    def __init__(self, db):
        self.db = db

    def get(self, gate_id):
        return self.db.execute("SELECT * FROM gates WHERE gate_id=?", (gate_id,)).fetchone()

    def pending(self):
        return self.db.execute("SELECT * FROM gates WHERE state='pending'").fetchall()

    def for_job(self, job_id):
        return self.db.execute(
            "SELECT * FROM gates WHERE job_id=? ORDER BY rowid", (job_id,)
        ).fetchall()

    def create(self, gate_id, job_id, spec):
        self.db.execute(
            "INSERT INTO gates(gate_id,job_id,state,spec,public_spec) VALUES(?,?,'pending',?,?)",
            (gate_id, job_id, encoded(spec), encoded(redact_secrets({**spec, "choice": None}))),
        )

    def resolve(self, gate_id, choice, owner, at, *, spec=None):
        import json

        public = json.loads(self.get(gate_id)["public_spec"])
        public = redact_secrets({**(spec if spec is not None else public), "choice": choice})
        return (
            self.db.execute(
                "UPDATE gates SET state='resolved',choice=?,resolved_by=?,resolved_at=?,spec=COALESCE(?,spec),public_spec=? "
                "WHERE gate_id=? AND state='pending'",
                (
                    encoded(choice),
                    owner,
                    at,
                    encoded(spec) if spec is not None else None,
                    encoded(public),
                    gate_id,
                ),
            ).rowcount
            == 1
        )

    def close(self, gate_id, state):
        return (
            self.db.execute(
                "UPDATE gates SET state=? WHERE gate_id=? AND state='pending'", (state, gate_id)
            ).rowcount
            == 1
        )
