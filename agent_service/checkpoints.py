"""Atomic step checkpoints, bound to the semantic workflow prefix and input values."""

import hashlib
import json
import os
import uuid


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Checkpoints:
    def __init__(self, root, job_id, plan, data):
        self.folder = root / "maestro" / job_id
        self.plan = plan
        self.inputs = {
            key: data.get(key)
            for key in (
                "prompt",
                "workflow_inputs",
                "file_ids",
                "workspace_id",
                "work_item",
                "access_mode",
                "execution_mode",
                "_checkpoint_sources",
            )
        }

    def binding(self, index, prior):
        # A suffix edit does not invalidate the unchanged prefix. The complete
        # workflow digest is retained for provenance alongside this prefix digest.
        header = {
            key: value
            for key, value in self.plan.items()
            if key not in ("steps", "digest", "revision", "source", "workflow_snapshot")
        }
        return digest(
            {
                "workflow": header,
                "steps": self.plan["steps"][:index],
                "inputs": self.inputs,
                "prior": prior,
            }
        )

    def load(self, index, prior, *, source=None):
        path = (source or self.folder) / f"checkpoint-{index}.json"
        try:
            value = json.loads(path.read_text())
            if value["binding"] != self.binding(index, prior) or value["output_digest"] != digest(
                value["record"]
            ):
                return None
            return value["record"]
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, index, record, prior):
        write_json(
            self.folder / f"checkpoint-{index}.json",
            {
                "binding": self.binding(index, prior),
                "workflow_digest": digest(self.plan),
                "output_digest": digest(record),
                "record": record,
            },
        )

    def invalidate(self, index):
        for path in self.folder.glob("checkpoint-*.json"):
            try:
                number = int(path.stem.split("-")[-1])
            except ValueError:
                continue
            if number >= index:
                path.unlink()
